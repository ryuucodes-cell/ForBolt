"""
Ryuu — Self-service CDK activation.

The customer picks a product, sends their CDK license key (verified against
the product's gateway site), then sends their account credential (session
JSON / user ID / org ID). The gateway activates the subscription automatically.

All prompts are dynamic: a single message is edited in place through the whole
flow, and the customer's reply messages are deleted (keys and sessions are
sensitive — no reason to keep them in chat).
"""

import html
import json
import logging
import re
import threading
import time
from urllib.parse import urlparse

import requests
import telebot
from telebot import types

import db
import emojis
import msg_templates as T
from utils import (
    silent_delete, edit_or_send, set_state, get_state, clear_state,
    check_rate_limit, log_to_channel, mask_name,
)

log = logging.getLogger("cdk_redeem")

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# ── products (alphabetical). Gateway sites are fixed for now. ─────────────────

CDK_PRODUCTS = [
    {"id": "chatgpt_go",      "name": "ChatGPT Go",     "icon": f"{emojis.emoji('zap', '⚡')}",
     "emoji_id": "5246801734638002426", "emoji_key": "cdk_chatgpt_go",
     "provider": "chatgpt",    "site": "https://ph2.891014.best"},
    {"id": "chatgpt_plus",    "name": "ChatGPT Plus",   "icon": "✨",
     "emoji_id": "5246801734638002426", "emoji_key": "cdk_chatgpt_plus",
     "provider": "chatgpt",    "site": "https://ph2.891014.best"},
    {"id": "claude_pro",      "name": "Claude Pro",     "icon": "🟣",
     "emoji_id": "5417984549642601364", "emoji_key": "cdk_claude_pro",
     "provider": "claude",     "site": "https://code.urai.uk"},
    {"id": "perplexity_pro",  "name": "Perplexity Pro", "icon": "🔮",
     "emoji_id": "5413586627685549158", "emoji_key": "cdk_perplexity_pro",
     "provider": "perplexity", "site": "https://perplexity.procdk91.store"},
    {"id": "super_grok",      "name": "SuperGrok 1M",   "icon": "🚀",
     "emoji_id": "5440892071248431491", "emoji_key": "cdk_super_grok",
     "provider": "grok1m",     "site": "https://quickplus.vip/public/grokmonth/"},
    {"id": "supergrok_3m",    "name": "SuperGrok 3M",   "icon": "🪐",
     "emoji_id": "5440892071248431491", "emoji_key": "cdk_supergrok_3m",
     "provider": "grok3m",     "site": "https://www.ipiapcloud.com"},
]


def _product_icon_html(product: dict) -> str:
    """Premium emoji tag for the product title, Unicode fallback included."""
    return emojis.emoji(product["emoji_key"], product["icon"])

_VERIFY_TIMEOUT = 20
_SUBMIT_TIMEOUT = 25
_MAX_FILE_BYTES = 256 * 1024  # session files are tiny; reject anything big


def _find_product(pid: str):
    for p in CDK_PRODUCTS:
        if p["id"] == pid:
            return p
    return None


# ── HTTP helpers ──────────────────────────────────────────────────────────────

def _origin(site: str) -> str:
    u = urlparse(site)
    return f"{u.scheme}://{u.netloc}"


def _headers(site: str) -> dict:
    origin = _origin(site)
    return {
        "content-type": "application/json",
        "accept": "application/json",
        "origin": origin,
        "referer": origin + "/",
        "user-agent": _USER_AGENT,
    }


def _safe_json(resp):
    try:
        return resp.json()
    except Exception:
        return None


def _safe_dict(v):
    """Return v if it's a dict, else {}. Gateways sometimes return a string,
    list or null where we expect an object — never let .get() crash on that."""
    return v if isinstance(v, dict) else {}


def _safe_str(v):
    return v if isinstance(v, str) else ""


_UUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)


def extract_claude_org_id(raw) -> str:
    s = str(raw or "").strip()
    m = _UUID_RE.search(s)
    if m:
        return m.group(0).lower()
    try:
        j = json.loads(s)
        return j.get("organizationId") or j.get("orgId") or j.get("id") or j.get("org") or s
    except Exception:
        return s


def extract_grok_user_id(raw) -> str:
    s = str(raw or "").strip()
    m = _UUID_RE.search(s)
    if m:
        return m.group(0).lower()
    try:
        j = json.loads(s)
        user = j.get("user") or {}
        return j.get("userId") or j.get("user_id") or j.get("id") or user.get("id") or s
    except Exception:
        return s


# ── key verification (one quick call per provider) ────────────────────────────

def verify_card_key(card_key: str, product: dict):
    """Returns (ok: bool, error_message: str)."""
    key = card_key.strip()
    site = product["site"]
    provider = product["provider"]
    try:
        if provider == "grok1m":
            r = requests.get(
                f"{_origin(site)}/api.php",
                params={"action": "verify_card", "code": key},
                headers=_headers(site), timeout=_VERIFY_TIMEOUT)
            j = _safe_dict(_safe_json(r))
            data = _safe_dict(j.get("data"))
            if j.get("success") and data:
                if data.get("status") == "unused":
                    return True, ""
                if data.get("status") == "used":
                    used_at = data.get("used_at")
                    if used_at:
                        return False, f"This Grok key has already been redeemed on {used_at}."
                    return False, "This Grok key has already been redeemed."
            return False, _safe_str(j.get("message")) or "Grok key is invalid or not found."

        if provider == "perplexity":
            r = requests.post(
                f"{_origin(site)}/api/v1/verify-cardkey",
                json={"cardKey": key},
                headers=_headers(site), timeout=_VERIFY_TIMEOUT)
            j = _safe_dict(_safe_json(r))
            data = _safe_dict(j.get("data"))
            if j.get("code") == 0 and data.get("valid"):
                return True, ""
            return False, _safe_str(data.get("message")) or _safe_str(j.get("message")) or "Perplexity key is invalid."

        if provider == "grok3m":
            r = requests.post(
                f"{_origin(site)}/api/v1/public/recharge/card-info",
                json={"serialNumber": key},
                headers=_headers(site), timeout=_VERIFY_TIMEOUT)
            j = _safe_dict(_safe_json(r))
            if r.ok and j:
                return True, ""
            return False, _safe_str(j.get("message")) or "SuperGrok 3-Month key verification rejected."

        if provider == "claude":
            base = site.rstrip("/")
            r = requests.post(
                f"{base}/api/public/cardQuery",
                json={"code": key},
                headers=_headers(base), timeout=_VERIFY_TIMEOUT)
            j = _safe_dict(_safe_json(r))
            data = _safe_dict(j.get("data"))
            # Live-probed gateway semantics (2026-11-01, code.urai.uk):
            # 'msg' is ALWAYS "success" (it means the QUERY worked, not
            # that the key is valid), status 3 = key does not exist, and
            # a REDEEMED key shows status 1 WITH usedTime set. An unused
            # key's status is NOT 1 — the old check required status == 1
            # as "valid", so every unused key fell through, the gateway's
            # "success" msg was shown as the error, and Step 1 looped
            # forever. Never gate "valid" on a guessed status number:
            # reject only what the reply PROVES is bad (usedTime set, or
            # the known bad statuses) and let the redeem call be the
            # final judge for anything else.
            log.info("claude cardQuery reply: code=%r status=%r usedTime=%r product=%r",
                     j.get("code"), data.get("status"), data.get("usedTime"),
                     data.get("productName"))
            st = str(data.get("status"))
            if j.get("code") not in (0, 200) or not data or st == "3":
                return False, "Claude key does not exist or is invalid."
            if data.get("usedTime") or st in ("1", "2"):
                return False, "Claude key already redeemed or expired."
            return True, ""

        # chatgpt (default)
        base = site.rstrip("/")
        r = requests.post(
            f"{base}/api/redemptions/check",
            json={"cardKey": key},
            headers=_headers(base), timeout=_VERIFY_TIMEOUT)
        j = _safe_dict(_safe_json(r))
        if r.ok and j.get("ok"):
            return True, ""
        err = _safe_dict(j.get("error"))
        code = _safe_str(err.get("code"))
        if code in ("CDK_NOT_FOUND", "CDK_INVALID"):
            return False, "The activation key (CDK) does not exist or is invalid."
        if code in ("CDK_USED", "CDK_ALREADY_USED"):
            return False, "This activation key has already been redeemed."
        if code == "CDK_UNAVAILABLE":
            return False, "The key is temporarily unavailable or expired."
        if code == "ORIGIN_INVALID":
            return False, "Gateway security rejected the request origin."
        if code == "SERVICE_UNAVAILABLE":
            return False, "The provider is busy right now — try again in a bit."
        return False, _safe_str(err.get("message")) or "Invalid key."
    except requests.RequestException:
        return False, "Could not reach the activation server. Please try again in a moment."


# ── activation (submit + poll per provider) ───────────────────────────────────

def _submit_grok1m(key: str, cred: str, site: str, progress=None):
    """Returns (ok, message, ref, status). progress(stage) fires at each real HTTP step."""
    def _pg(stage):
        if progress:
            progress(stage)
    user_id = extract_grok_user_id(cred)
    origin = _origin(site)
    form_headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "origin": origin,
        "referer": origin + "/",
        "user-agent": _USER_AGENT,
    }
    _pg("binding")
    bind = requests.post(
        f"{origin}/api.php",
        data={"action": "bind_user", "card_code": key,
              "claude_user_id": user_id, "product_type": "grokmonth"},
        headers=form_headers, timeout=_SUBMIT_TIMEOUT)
    bj = _safe_dict(_safe_json(bind))
    if not bj.get("success"):
        return False, _safe_str(bj.get("message")) or "Failed to bind Grok user ID.", "", "failed"
    _pg("starting")
    start = requests.post(
        f"{origin}/api.php",
        data={"action": "start_recharge", "claude_user_id": user_id,
              "product_type": "grokmonth"},
        headers=form_headers, timeout=_SUBMIT_TIMEOUT)
    sj = _safe_dict(_safe_json(start))
    task_id = _safe_str(_safe_dict(sj.get("data")).get("task_id"))
    ref = f"TASK-{task_id}" if task_id else ""
    if task_id:
        _pg("waiting")
        for _ in range(12):
            time.sleep(2.5)
            sr = requests.get(
                f"{origin}/api.php",
                params={"action": "status", "task_id": task_id, "product_type": "grokmonth"},
                headers={"origin": origin, "referer": origin + "/",
                         "user-agent": _USER_AGENT},
                timeout=_SUBMIT_TIMEOUT)
            sjson = _safe_dict(_safe_json(sr))
            data = _safe_dict(sjson.get("data"))
            st = _safe_str(data.get("status")).lower()
            if st in ("completed", "success"):
                return True, "SuperGrok subscription successfully activated!", ref, "success"
            if st in ("failed", "error"):
                return False, _safe_str(data.get("error_message")) or "Grok activation failed.", ref, "failed"
            if st in ("deleted", "expired", "cancelled"):
                # The provider cleaned up the task — safe to retry, don't fake success.
                return False, "The activation task expired on the provider side — safe to try again.", ref, "failed"
    # No task id back: we can't confirm anything — pending, never fake success.
    return True, "Your key was sent, but the provider didn't confirm yet — check back in My Activations.", ref, "pending"


def _submit_perplexity(key: str, cred: str, site: str, progress=None):
    if progress:
        progress("sending")
    origin = _origin(site)
    r = requests.post(
        f"{origin}/api/v1/redeem",
        json={"cardKey": key, "accountSession": cred.strip(), "confirmOverride": False},
        headers=_headers(site), timeout=_SUBMIT_TIMEOUT)
    j = _safe_dict(_safe_json(r))
    data = _safe_dict(j.get("data"))
    if j.get("code") == 0 and data.get("success"):
        ref = _safe_str(data.get("orderId")) or _safe_str(data.get("orderNumber"))
        return True, "Perplexity Pro subscription activated successfully!", ref, "success"
    return False, _safe_str(data.get("message")) or _safe_str(j.get("message")) or "Perplexity Pro activation failed.", "", "failed"


def _post_with_retry(url, *, json=None, data=None, headers=None, timeout=25, retries=1):
    """POST with one retry on network failure (ipiap is flaky)."""
    last_exc = None
    for _ in range(retries + 1):
        try:
            return requests.post(url, json=json, data=data, headers=headers, timeout=timeout)
        except requests.RequestException as e:
            last_exc = e
            time.sleep(2)
    raise last_exc


def _submit_grok3m(key: str, cred: str, site: str, progress=None):
    if progress:
        progress("sending")
    base = _origin(site)
    user_id = extract_grok_user_id(cred)
    try:
        session_payload = json.loads(cred)
    except Exception:
        session_payload = {"accessToken": cred.strip()}
    r = _post_with_retry(
        f"{base}/api/v1/public/recharge/orders",
        json={"serialNumber": key, "cardKey": key, "userId": user_id,
              "grokUserId": user_id, "session": session_payload,
              "grokSession": session_payload, "chatGptSession": session_payload},
        headers=_headers(base), timeout=_SUBMIT_TIMEOUT)
    j = _safe_dict(_safe_json(r))
    # Defensive: some panels answer HTTP 200 with an error body — check for it.
    err_body = _safe_str(j.get("error")) or _safe_str(j.get("errMsg"))
    err_code = _safe_str(j.get("code"))
    if err_body or err_code.lower() not in ("", "0", "200", "success", "ok"):
        return False, err_body or _safe_str(j.get("message")) or "SuperGrok 3-Month redemption rejected by the gateway.", "", "failed"
    if r.ok and j:
        ref = _safe_str(j.get("orderId")) or _safe_str(j.get("orderNumber"))
        return True, _safe_str(j.get("message")) or "SuperGrok 3-Month subscription activated successfully!", ref, "success"
    return False, _safe_str(j.get("message")) or "SuperGrok 3-Month redemption submission rejected.", "", "failed"


def _submit_claude(key: str, cred: str, site: str, progress=None):
    def _pg(stage):
        if progress:
            progress(stage)
    base = site.rstrip("/")
    org_id = extract_claude_org_id(cred)
    _pg("sending")
    r = requests.post(
        f"{base}/api/public/redeem",
        json={"product": "claude", "cardCode": key,
              "credential": {"organizationId": org_id}},
        headers=_headers(base), timeout=_SUBMIT_TIMEOUT)
    j = _safe_dict(_safe_json(r))
    data = _safe_dict(j.get("data"))
    if not r.ok or (j.get("code") not in (0, 200) and not data.get("publicToken")):
        return False, _safe_str(j.get("msg")) or "Claude activation failed.", "", "failed"
    public_token = _safe_str(data.get("publicToken")) or _safe_str(data.get("token"))
    # Store the FULL token — the live order lookup needs it (API requires 32+ chars).
    ref = public_token
    if public_token:
        _pg("waiting")
        for _ in range(10):
            time.sleep(2.5)
            sr = requests.get(
                f"{base}/api/public/orders/{public_token}",
                headers=_headers(base), timeout=_SUBMIT_TIMEOUT)
            sj = _safe_dict(_safe_json(sr))
            sdata = _safe_dict(sj.get("data"))
            status_str = _safe_str(sdata.get("status")).lower()
            if sdata.get("orderStatus") == 1 or status_str in ("success", "completed"):
                return True, "Claude Pro subscription activated successfully!", ref, "success"
            if sdata.get("orderStatus") == 2 or status_str in ("failed", "error"):
                return False, _safe_str(sdata.get("message")) or "Claude activation failed.", ref, "failed"
    # Polls ran out with no terminal state — pending, never fake success.
    return True, "Your key was sent to the Claude server, but confirmation is still pending — check back in My Activations.", ref, "pending"


def _submit_chatgpt(key: str, cred: str, site: str, progress=None):
    def _pg(stage):
        if progress:
            progress(stage)
    base = site.rstrip("/")
    try:
        session_payload = json.loads(cred)
    except Exception:
        session_payload = {"accessToken": cred,
                           "user": {"email": "chatgpt-user@internal"}}
    _pg("sending")
    r = requests.post(
        f"{base}/api/redemptions",
        json={"cardKey": key, "session": session_payload, "consent": True},
        headers=_headers(base), timeout=_SUBMIT_TIMEOUT)
    j = _safe_dict(_safe_json(r))
    data = _safe_dict(j.get("data"))
    if not r.ok or not j.get("ok"):
        err = _safe_dict(j.get("error"))
        code = _safe_str(err.get("code"))
        if code == "SERVICE_UNAVAILABLE":
            return False, "The provider is busy right now — try again in a bit.", "", "failed"
        return False, _safe_str(err.get("message")) or "Activation failed", "", "failed"
    poll_token = _safe_str(data.get("pollToken"))
    ref = _safe_str(data.get("orderNumber")) or _safe_str(data.get("orderId"))
    status = _safe_str(data.get("status")) or "PROCESSING"
    if status in ("SUCCESS", "COMPLETED"):
        return True, _safe_str(data.get("message")) or "Success!", ref, "success"
    _pg("waiting")
    for _ in range(15):
        time.sleep(3)
        payload = {"cardKey": key}
        if poll_token:
            payload["pollToken"] = poll_token
        sr = requests.post(
            f"{base}/api/redemptions/status",
            json=payload, headers=_headers(base), timeout=_SUBMIT_TIMEOUT)
        sj = _safe_dict(_safe_json(sr))
        sdata = _safe_dict(sj.get("data"))
        if sj.get("ok") and sdata:
            status = _safe_str(sdata.get("status"))
            ref = _safe_str(sdata.get("orderNumber")) or _safe_str(sdata.get("orderId")) or ref
            if status in ("SUCCESS", "COMPLETED"):
                return True, _safe_str(sdata.get("message")) or "Success!", ref, "success"
            if status in ("FAILED", "ERROR"):
                return False, _safe_str(sdata.get("message")) or "Activation node error.", ref, "failed"
    return True, "Your key was sent, but the provider is taking longer than usual — check back in My Activations.", ref, "pending"


def submit_and_poll(card_key: str, credential_data: str, product: dict, progress=None):
    """Returns (ok, message, ref, status). status: success|failed|pending.
    progress(stage) fires at each real HTTP step. May take ~60s — run in a thread."""
    provider = product["provider"]
    site = product["site"]
    key = card_key.strip()
    try:
        if provider == "grok1m":
            return _submit_grok1m(key, credential_data, site, progress)
        if provider == "perplexity":
            return _submit_perplexity(key, credential_data, site, progress)
        if provider == "grok3m":
            return _submit_grok3m(key, credential_data, site, progress)
        if provider == "claude":
            return _submit_claude(key, credential_data, site, progress)
        return _submit_chatgpt(key, credential_data, site, progress)
    except requests.RequestException:
        return False, "Could not reach the activation server. Please try again in a moment.", "", "failed"


def lookup_status(key: str, product: dict, ref: str = None):
    """Live gateway status lookup for a key. Returns (ok, status, message, ref).

    Only ChatGPT, Grok 1M and Claude expose a lookup endpoint — Grok 3M and
    Perplexity don't, so for those the history record is the source of truth."""
    provider = product["provider"]
    site = product["site"]
    key = key.strip()
    try:
        if provider == "grok1m":
            task_id = _safe_str(ref).strip()
            if task_id.upper().startswith("TASK-"):
                task_id = task_id[5:]
            if not task_id:
                return False, "", "Live lookup needs the task reference from your activation receipt — check My Activations.", ""
            r = requests.get(
                f"{_origin(site)}/api.php",
                params={"action": "status", "task_id": task_id, "product_type": "grokmonth"},
                headers={"origin": _origin(site), "referer": _origin(site) + "/",
                         "user-agent": _USER_AGENT},
                timeout=_VERIFY_TIMEOUT)
            j = _safe_dict(_safe_json(r))
            data = _safe_dict(j.get("data"))
            if data:
                st = _safe_str(data.get("status")) or "UNKNOWN"
                msg = _safe_str(data.get("status_desc")) or _safe_str(data.get("message")) or "Status checked."
                if st.lower() in ("deleted", "expired", "cancelled"):
                    msg = "The task is no longer on the provider side — safe to try again."
                return True, st, msg, f"TASK-{task_id}"
            return False, "", "No status found for that key.", ""
        if provider == "claude":
            token = _safe_str(ref).strip()
            if not token:
                return False, "", "Live lookup needs the order reference from your activation receipt — check My Activations.", ""
            r = requests.get(
                f"{site.rstrip('/')}/api/public/orders/{token}",
                headers=_headers(site), timeout=_VERIFY_TIMEOUT)
            j = _safe_dict(_safe_json(r))
            data = _safe_dict(j.get("data"))
            if data:
                return True, _safe_str(data.get("status")) or "UNKNOWN", _safe_str(data.get("message")) or "Status checked.", token
            return False, "", _safe_str(j.get("msg")) or "Order not found.", ""
        if provider == "chatgpt":
            # Key-based lookup only — our stored ref is an order number, not a poll token.
            payload = {"cardKey": key}
            r = requests.post(
                f"{site.rstrip('/')}/api/redemptions/status",
                json=payload, headers=_headers(site), timeout=_VERIFY_TIMEOUT)
            j = _safe_dict(_safe_json(r))
            if r.ok and j.get("ok"):
                d = _safe_dict(j.get("data"))
                return True, _safe_str(d.get("status")) or "UNKNOWN", _safe_str(d.get("message")) or "Status checked.", _safe_str(d.get("orderNumber")) or _safe_str(d.get("orderId"))
            err = _safe_dict(j.get("error"))
            code = _safe_str(err.get("code"))
            if code == "SERVICE_UNAVAILABLE":
                return False, "", "The provider is busy right now — try again in a bit.", ""
            return False, "", _safe_str(err.get("message")) or "No status found for that key.", ""
        return False, "", "Live status isn't available for this product — your My Activations history shows the final result.", ""
    except requests.RequestException:
        return False, "", "Could not reach the activation server. Please try again in a moment.", ""


def _mask_key(key: str) -> str:
    """Mask a CDK key for display: AB12…XY99."""
    k = (key or "").strip()
    return f"{k[:4]}…{k[-4:]}" if len(k) > 8 else k


def gateway_health_check(product: dict) -> bool:
    """Lightweight liveness probe per gateway (fake key, read-only).
    Returns True if the gateway answers in its expected shape."""
    provider = product["provider"]
    site = product["site"]
    fake = "HEALTHCHECK-PROBE-0000"
    try:
        if provider == "chatgpt":
            r = requests.get(f"{site.rstrip('/')}/api/config",
                             headers=_headers(site), timeout=10)
            j = _safe_dict(_safe_json(r))
            return r.ok and j.get("ok") is True
        if provider == "claude":
            r = requests.post(f"{site.rstrip('/')}/api/public/cardQuery",
                              json={"code": fake},
                              headers=_headers(site), timeout=10)
            j = _safe_dict(_safe_json(r))
            return j.get("code") == 0  # any data.status — the API itself answered
        if provider == "grok1m":
            r = requests.get(f"{_origin(site)}/api.php",
                             params={"action": "verify_card", "code": fake},
                             headers=_headers(site), timeout=10)
            j = _safe_dict(_safe_json(r))
            return "success" in j
        if provider == "perplexity":
            r = requests.post(f"{_origin(site)}/api/v1/verify-cardkey",
                              json={"cardKey": fake},
                              headers=_headers(site), timeout=10)
            j = _safe_dict(_safe_json(r))
            return j.get("code") == 0
        if provider == "grok3m":
            r = requests.post(f"{_origin(site)}/api/v1/public/recharge/card-info",
                              json={"serialNumber": fake},
                              headers=_headers(site), timeout=10)
            j = _safe_dict(_safe_json(r))
            # 400 + code field means the API is alive and validating keys.
            return r.status_code == 400 and bool(_safe_str(j.get("code")))
        return False
    except requests.RequestException:
        return False


def _slow_down_nudge(bot: telebot.TeleBot, uid: int):
    """Brief self-deleting 'slow down' note (runs in its own thread)."""
    def _run():
        try:
            m = bot.send_message(uid, "Easy — give it a second and try again.")
            time.sleep(4)
            try:
                bot.delete_message(uid, m.message_id)
            except Exception:
                pass
        except Exception:
            pass
    threading.Thread(target=_run, daemon=True).start()


def _mask_ref(ref: str) -> str:
    """Shorten a long provider ref for display: ABCDEF12…"""
    r = (ref or "").strip()
    return f"{r[:8]}…" if len(r) > 12 else r


# Provider gateways answer in Chinese — map the common ones to friendly English.
_PROVIDER_MSG_MAP = [
    ("卡密不存在", "This key doesn't exist — check for typos and try again."),
    ("卡密格式无效", "This key's format isn't valid — check for typos."),
    ("卡密已被使用", "This key has already been redeemed."),
    ("已被兑换", "This key has already been redeemed."),
    ("任务不存在或已被清理", "The task is no longer on the provider side — safe to try again."),
    ("任务不存在", "The task was not found — safe to try again."),
    ("状态查询成功", "Status checked."),
    ("成功", "Success."),
]


def humanize_provider_message(text: str) -> str:
    """Replace known Chinese provider messages with friendly English."""
    t = (text or "").strip()
    if not t:
        return t
    for cn, en in _PROVIDER_MSG_MAP:
        if cn in t:
            return en
    return t


# ── run tokens: a cancelled/restarted flow must not be overwritten ─────────────

_run_tokens: dict = {}


def _bump_run_token(uid: int) -> int:
    tok = _run_tokens.get(uid, 0) + 1
    _run_tokens[uid] = tok
    return tok


# ── keyboards ─────────────────────────────────────────────────────────────────

def _cancel_kb() -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(emojis.button("✖ Cancel", callback_data="cdk_cancel"))
    return kb


def _done_kb() -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(emojis.button("My Activations", callback_data="cdk_history",
                         emoji_id="5197269100878907942", lead="📋"))
    kb.add(emojis.button("Back to CDK Menu", callback_data="cdk_menu",
                         emoji_id="5042156073516008537", lead="🎮"))
    kb.add(emojis.button("Main Menu", callback_data="main_menu",
                         emoji_id="5042022053356504092", lead="🏠"))
    return kb


def _product_btn(p: dict) -> types.InlineKeyboardButton:
    return emojis.button(p["name"], callback_data=f"cdk_pick_{p['id']}",
                         emoji_id=p["emoji_id"], lead=p["icon"])


def _menu_kb() -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup(row_width=2)
    for i in range(0, len(CDK_PRODUCTS), 2):
        row = [_product_btn(CDK_PRODUCTS[i])]
        if i + 1 < len(CDK_PRODUCTS):
            row.append(_product_btn(CDK_PRODUCTS[i + 1]))
        kb.add(*row)
    kb.add(
        emojis.button("My Activations", callback_data="cdk_history",
                      emoji_id="5197269100878907942", lead="📋"),
        emojis.button("Check Status", callback_data="cdk_status",
                      emoji_id="5039649904264217620", lead="🔍"),
    )
    kb.add(emojis.button("Main Menu", callback_data="main_menu",
                         emoji_id="5042022053356504092", lead="🏠"))
    return kb


def _back_to_menu_kb() -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(emojis.button("Back to CDK Menu", callback_data="cdk_menu",
                         emoji_id="5042156073516008537", lead="🎮"))
    return kb


def _status_product_kb() -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup(row_width=2)
    for i in range(0, len(CDK_PRODUCTS), 2):
        p = CDK_PRODUCTS[i]
        row = [emojis.button(p["name"], callback_data=f"cdk_status_pick_{p['id']}",
                             emoji_id=p["emoji_id"], lead=p["icon"])]
        if i + 1 < len(CDK_PRODUCTS):
            nxt = CDK_PRODUCTS[i + 1]
            row.append(emojis.button(nxt["name"], callback_data=f"cdk_status_pick_{nxt['id']}",
                                     emoji_id=nxt["emoji_id"], lead=nxt["icon"]))
        kb.add(*row)
    kb.add(emojis.button("Back to CDK Menu", callback_data="cdk_menu",
                         emoji_id="5042156073516008537", lead="🎮"))
    return kb


def _show_menu(bot: telebot.TeleBot, uid: int, msg_id: int):
    _bump_run_token(uid)
    clear_state(uid)
    return edit_or_send(bot, uid, msg_id, T.cdk_menu(), reply_markup=_menu_kb())


# ── background activation ─────────────────────────────────────────────────────

def _run_activation(bot: telebot.TeleBot, uid: int, uname: str, product: dict,
                    key: str, credential: str, prompt_id: int, token: int, act_id: int):
    start = time.time()

    def _progress(stage: str):
        try:
            db.cdk_activation_update(act_id, status=stage)
        except Exception:
            log.exception("CDK stage DB update failed")
        if _run_tokens.get(uid) == token:
            try:
                edit_or_send(bot, uid, prompt_id,
                             T.cdk_stage(product["name"], product["provider"], stage))
            except Exception:
                log.exception("CDK stage edit failed")

    try:
        ok, message, ref, status = submit_and_poll(key, credential, product, progress=_progress)
    except Exception:
        log.exception("CDK activation error for %s", product["id"])
        ok, message, ref, status = False, "Something went wrong on our side. Please try again in a moment.", "", "failed"
    duration_ms = int((time.time() - start) * 1000)
    message = humanize_provider_message(message)

    # The outcome is always recorded + logged — even if the user cancelled
    # the chat flow (then we just skip editing their message below).
    try:
        db.cdk_activation_update(act_id, status=status, provider_ref=ref or "",
                                 message=message, duration_ms=duration_ms)
    except Exception:
        log.exception("CDK final DB update failed")
    try:
        buyer = mask_name(uname)
        dur_s = _fmt_dur_s(duration_ms / 1000)
        if status == "success":
            log_to_channel(bot, T.log_cdk_success(product["name"], _mask_key(key), buyer, ref or "", dur_s))
        elif status == "pending":
            # No channel post yet — the watchdog posts the ⏳ at the
            # 2-minute mark if it's still pending by then.
            pass
        else:
            log_to_channel(bot, T.log_cdk_failed(product["name"], _mask_key(key), buyer,
                                                 html.escape(message, quote=False), dur_s))
    except Exception:
        log.exception("CDK log channel post failed")

    # Build AND deliver inside the guard: if rendering or sending ever
    # raises, the customer still gets a plain result message — a finished
    # activation must never end in silence on the spinner screen. (The
    # cdk_success ref-arg TypeError did exactly that until V1.6.6: DB and
    # log said success, the customer saw the waiting stage forever.)
    try:
        safe = html.escape(message, quote=False)
        if status == "success":
            text = T.cdk_success(product["name"], safe, _mask_ref(ref) or None)
        elif status == "pending":
            text = T.cdk_pending(product["name"], safe, _mask_ref(ref) or None)
        else:
            text = T.cdk_failed(product["name"], safe)
        if _run_tokens.get(uid) == token:
            edit_or_send(bot, uid, prompt_id, text, reply_markup=_done_kb())
        else:
            # User navigated away mid-activation — deliver as a fresh message
            # so the result is never lost.
            bot.send_message(uid, text, parse_mode="HTML", reply_markup=_done_kb())
    except Exception:
        log.exception("CDK result delivery failed")
        try:
            mark = {"success": "✅", "pending": "⏳"}.get(status, "❌")
            bot.send_message(
                uid,
                f"{mark} Your <b>{html.escape(product['name'], quote=False)}</b> "
                f"activation finished with status: <b>{status}</b>.\n"
                f"Open My Activations to see the full result.",
                parse_mode="HTML", reply_markup=_done_kb())
        except Exception:
            log.exception("CDK fallback delivery failed")
    # Pending? Keep watching in the background — the provider often confirms
    # a minute or two after our initial poll gives up. If it never answers,
    # the watchdog settles the activation as failed at the 3-minute deadline.
    if status == "pending":
        try:
            _pending_watchdog(bot, uid, uname, product, key, ref, act_id,
                              started_ts=start)
        except Exception:
            log.exception("CDK pending watchdog failed to start")


# ── pending watchdog ────────────────────────────────────────────────────────────

# The provider gets 3 minutes (John's call, 2026-10-26) to give a final
# verdict on a pending activation. After that the BOT settles it as failed —
# some providers leave ineligible accounts on PROCESSING forever and never
# send a FAILED, so without a deadline the activation hangs forever.
_PENDING_DEADLINE_S = 180
_PENDING_POLL_S = 15
# The ⏳ post to the log channel waits 2 minutes (John's call, 2026-10-26):
# most pendings resolve before that and never need a pending post at all.
_PENDING_POST_AFTER_S = 120
# Startup-sweep budget: submit+poll takes ~65s before 'pending' is even
# recorded, so a row's total budget is created_at + this many seconds.
_PENDING_SWEEP_BUDGET_S = 65 + _PENDING_DEADLINE_S + 30
_PENDING_SETTLE_MSG = (
    "The provider never confirmed this activation — the account may not be "
    "eligible for it. Please try again with a different account, or contact "
    "us for help."
)
_PENDING_SETTLE_LOG_REASON = (
    "Provider never confirmed within 3 minutes — settled as failed. "
    "The account may not be eligible."
)


def _fmt_dur_s(seconds) -> str:
    """Human duration for log posts: 45s, 2m 10s, 1h 05m."""
    s = max(0, int(round(seconds or 0)))
    if s < 60:
        return f"{s}s"
    m, sec = divmod(s, 60)
    if m < 60:
        return f"{m}m {sec:02d}s"
    h, m = divmod(m, 60)
    return f"{h}h {m:02d}m"


def _watchdog_resolve(bot, uid, uname, product, key, ref, act_id,
                      outcome, message, new_ref, started_ts):
    """Write a pending activation's final outcome everywhere it belongs:
    DB row, customer DM, log channel. No-ops if the row is no longer
    pending (Check Status or the customer may have settled it first).
    outcome: 'success' | 'failed' (provider said so) | 'timeout'."""
    try:
        row = db.cdk_activation_get(act_id, uid)
    except Exception:
        row = None
    if row is not None and row["status"] != "pending":
        return
    dur = _fmt_dur_s(time.time() - started_ts) if started_ts else "—"
    final_ref = new_ref or ref or ""
    if outcome == "success":
        msg = humanize_provider_message(message) or "Success!"
        try:
            db.cdk_activation_update(act_id, status="success",
                                     provider_ref=final_ref, message=msg)
        except Exception:
            log.exception("CDK watchdog success DB update failed")
        try:
            bot.send_message(
                uid,
                f"{emojis.emoji('success', '✅')} <b>Good news!</b> Your "
                f"<b>{product['name']}</b> activation completed — "
                f"key <code>{_mask_key(key)}</code> is now active. Enjoy!",
                parse_mode="HTML")
        except Exception:
            pass
        try:
            log_to_channel(bot, T.log_cdk_success(
                product["name"], _mask_key(key),
                mask_name(uname), final_ref or "", dur))
        except Exception:
            pass
        return
    # failed (provider said so) or timeout (provider never answered)
    msg = (_PENDING_SETTLE_MSG if outcome == "timeout"
           else humanize_provider_message(message) or "Activation failed.")
    log_reason = (_PENDING_SETTLE_LOG_REASON if outcome == "timeout"
                  else html.escape(msg, quote=False))
    try:
        db.cdk_activation_update(act_id, status="failed",
                                 provider_ref=final_ref, message=msg)
    except Exception:
        log.exception("CDK watchdog failure DB update failed")
    try:
        bot.send_message(
            uid,
            f"{emojis.emoji('rejected', '❌')} Your <b>{product['name']}</b> "
            f"activation for key <code>{_mask_key(key)}</code> failed: "
            f"{html.escape(msg, quote=False)}",
            parse_mode="HTML")
    except Exception:
        pass
    try:
        log_to_channel(bot, T.log_cdk_failed(
            product["name"], _mask_key(key), mask_name(uname),
            log_reason, dur))
    except Exception:
        pass


def _pending_watchdog(bot: telebot.TeleBot, uid: int, uname: str, product: dict,
                      key: str, ref: str, act_id: int,
                      deadline_ts: float = None, started_ts: float = None):
    """Background re-check for activations that went to 'pending'.

    Polls the provider until it gives a final verdict or the 3-minute
    deadline passes — then settles the activation as failed itself, so a
    provider that only ever says PROCESSING can never leave a customer
    hanging on 'pending' forever.
    """
    def _run():
        deadline = deadline_ts or (time.time() + _PENDING_DEADLINE_S)
        started = started_ts or time.time()
        watch_start = time.time()
        cur_ref = ref
        pending_posted = False
        while True:
            try:
                ok, status, message, new_ref = lookup_status(key, product, ref)
            except Exception:
                ok, status, message, new_ref = False, "", "", ""
            if ok and new_ref:
                cur_ref = new_ref
            if ok:
                st = (status or "").upper()
                if st in ("SUCCESS", "COMPLETED"):
                    _watchdog_resolve(bot, uid, uname, product, key, ref,
                                      act_id, "success", message, cur_ref,
                                      started)
                    return
                if st in ("FAILED", "ERROR"):
                    _watchdog_resolve(bot, uid, uname, product, key, ref,
                                      act_id, "failed", message, cur_ref,
                                      started)
                    return
            if time.time() >= deadline:
                _watchdog_resolve(bot, uid, uname, product, key, ref,
                                  act_id, "timeout", "", cur_ref, started)
                return
            # Still pending at the 2-minute mark? Now the log channel
            # hears about it — earlier than that, most pendings resolve
            # and the channel stays clean.
            if (not pending_posted
                    and time.time() - watch_start >= _PENDING_POST_AFTER_S):
                pending_posted = True
                try:
                    log_to_channel(bot, T.log_cdk_pending(
                        product["name"], _mask_key(key), mask_name(uname),
                        cur_ref or "", _fmt_dur_s(time.time() - started)))
                except Exception:
                    pass
            time.sleep(min(_PENDING_POLL_S,
                           max(1.0, deadline - time.time())))

    threading.Thread(target=_run, daemon=True).start()


def sweep_pending_activations(bot: telebot.TeleBot) -> int:
    """Startup sweep: re-arm the watchdog for activations a previous run
    left on 'pending' (restart/deploy mid-watch). Rows already past their
    budget settle on the first check; younger rows keep their remaining
    time. Returns how many watchdogs were re-armed."""
    import calendar
    try:
        rows = db.cdk_activation_pending()
    except Exception:
        log.exception("CDK pending sweep: could not read pending rows")
        return 0
    armed = 0
    for row in rows:
        try:
            product = _find_product(row["product_id"])
            if not product:
                continue
            uid = row["user_id"]
            uname = "Customer"
            try:
                u = db.get_user(uid)
                if u and u["username"]:
                    uname = u["username"]
            except Exception:
                pass
            created_ts = None
            try:
                created_ts = calendar.timegm(
                    time.strptime(row["created_at"], "%Y-%m-%d %H:%M:%S"))
            except Exception:
                created_ts = None
            deadline = (created_ts + _PENDING_SWEEP_BUDGET_S) if created_ts \
                else (time.time() + _PENDING_DEADLINE_S)
            _pending_watchdog(bot, uid, uname, product, row["cdk_key"],
                              row["provider_ref"] or "", row["id"],
                              deadline_ts=deadline, started_ts=created_ts)
            armed += 1
        except Exception:
            log.exception("CDK pending sweep: failed to re-arm a watchdog")
    return armed


# ── handlers ──────────────────────────────────────────────────────────────────

def register(bot: telebot.TeleBot):

    @bot.callback_query_handler(func=lambda c: c.data == "cdk_menu")
    def cb_cdk_menu(call: types.CallbackQuery):
        uid = call.from_user.id
        db.ensure_user(uid, call.from_user.username or "")
        _show_menu(bot, uid, call.message.message_id)
        bot.answer_callback_query(call.id)

    @bot.callback_query_handler(func=lambda c: c.data.startswith("cdk_pick_"))
    def cb_cdk_pick(call: types.CallbackQuery):
        uid = call.from_user.id
        product = _find_product(call.data[len("cdk_pick_"):])
        if not product:
            bot.answer_callback_query(call.id, "Product not found.", show_alert=True)
            return
        _bump_run_token(uid)
        prompt = edit_or_send(
            bot, uid, call.message.message_id,
            T.cdk_tutorial(product["name"], _product_icon_html(product), product["provider"]),
            reply_markup=_cancel_kb(),
        )
        set_state(uid, step="cdk_key", cdk_pid=product["id"], prompt_msg_id=prompt)
        bot.answer_callback_query(call.id)

    @bot.callback_query_handler(func=lambda c: c.data == "cdk_cancel")
    def cb_cdk_cancel(call: types.CallbackQuery):
        uid = call.from_user.id
        _show_menu(bot, uid, call.message.message_id)
        bot.answer_callback_query(call.id, "Cancelled.")

    # ── My Activations (history) ──

    @bot.callback_query_handler(func=lambda c: c.data == "cdk_history")
    def cb_cdk_history(call: types.CallbackQuery):
        uid = call.from_user.id
        recs = db.cdk_activation_list(uid, 10)
        kb = types.InlineKeyboardMarkup(row_width=1)
        for r in recs:
            icon, _label = T.cdk_status_display(r["status"])
            kb.add(types.InlineKeyboardButton(
                f"{r['product_name']} — {_label}",
                callback_data=f"cdk_detail_{r['id']}"))
        kb.add(emojis.button("Back to CDK Menu", callback_data="cdk_menu",
                             emoji_id="5042156073516008537", lead="🎮"))
        edit_or_send(bot, uid, call.message.message_id, T.cdk_history_list(recs),
                     reply_markup=kb)
        bot.answer_callback_query(call.id)

    @bot.callback_query_handler(func=lambda c: c.data.startswith("cdk_detail_"))
    def cb_cdk_detail(call: types.CallbackQuery):
        uid = call.from_user.id
        try:
            act_id = int(call.data[len("cdk_detail_"):])
        except ValueError:
            bot.answer_callback_query(call.id, "Not found.", show_alert=True)
            return
        r = db.cdk_activation_get(act_id, uid)
        if not r:
            bot.answer_callback_query(call.id, "Not found.", show_alert=True)
            return
        kb = types.InlineKeyboardMarkup(row_width=1)
        status = (r["status"] or "").lower()
        if status == "failed":
            kb.add(emojis.button("Try Again", callback_data=f"cdk_retry_{act_id}",
                                 emoji_id="5226702984204797593", lead="🔁"))
        elif status != "success":
            kb.add(emojis.button("Refresh", callback_data=f"cdk_detail_{act_id}",
                                 emoji_id="5226702984204797593", lead="🔄"))
        kb.add(emojis.button("My Activations", callback_data="cdk_history",
                             emoji_id="5197269100878907942", lead="📋"))
        # Escape the stored provider message; mask long refs for display.
        safe_msg = html.escape(humanize_provider_message(r["message"] or ""), quote=False)
        edit_or_send(bot, uid, call.message.message_id,
                     T.cdk_history_detail(r, _mask_key(r["cdk_key"]), safe_msg,
                                          _mask_ref(r["provider_ref"])),
                     reply_markup=kb)
        bot.answer_callback_query(call.id)

    @bot.callback_query_handler(func=lambda c: c.data.startswith("cdk_retry_"))
    def cb_cdk_retry(call: types.CallbackQuery):
        uid = call.from_user.id
        try:
            act_id = int(call.data[len("cdk_retry_"):])
        except ValueError:
            bot.answer_callback_query(call.id, "Not found.", show_alert=True)
            return
        r = db.cdk_activation_get(act_id, uid)
        if not r or (r["status"] or "").lower() != "failed":
            bot.answer_callback_query(call.id, "Not found.", show_alert=True)
            return
        product = _find_product(r["product_id"])
        if not product:
            bot.answer_callback_query(call.id, "Product not found.", show_alert=True)
            return
        _bump_run_token(uid)
        prompt = edit_or_send(
            bot, uid, call.message.message_id,
            T.cdk_ask_credential(product["name"], product["provider"]),
            reply_markup=_cancel_kb())
        set_state(uid, step="cdk_credential", cdk_pid=product["id"],
                  cdk_key=r["cdk_key"], prompt_msg_id=prompt)
        bot.answer_callback_query(call.id)

    # ── Check Status (live gateway lookup) ──

    @bot.callback_query_handler(func=lambda c: c.data == "cdk_status")
    def cb_cdk_status(call: types.CallbackQuery):
        uid = call.from_user.id
        _bump_run_token(uid)
        edit_or_send(bot, uid, call.message.message_id, T.cdk_status_menu(),
                     reply_markup=_status_product_kb())
        bot.answer_callback_query(call.id)

    @bot.callback_query_handler(func=lambda c: c.data.startswith("cdk_status_pick_"))
    def cb_cdk_status_pick(call: types.CallbackQuery):
        uid = call.from_user.id
        product = _find_product(call.data[len("cdk_status_pick_"):])
        if not product:
            bot.answer_callback_query(call.id, "Product not found.", show_alert=True)
            return
        _bump_run_token(uid)
        prompt = edit_or_send(
            bot, uid, call.message.message_id,
            T.cdk_status_ask(product["name"], _product_icon_html(product)),
            reply_markup=_cancel_kb())
        set_state(uid, step="cdk_status_key", cdk_pid=product["id"],
                  prompt_msg_id=prompt)
        bot.answer_callback_query(call.id)

    @bot.message_handler(content_types=["text"],
                         func=lambda m: get_state(m.from_user.id).get("step") == "cdk_status_key")
    def msg_cdk_status_key(msg: types.Message):
        uid = msg.from_user.id
        st = get_state(uid)
        product = _find_product(st.get("cdk_pid"))
        if not product:
            clear_state(uid)
            return
        prompt_id = st.get("prompt_msg_id")
        key = (msg.text or "").strip()
        if key.startswith("/"):
            return
        silent_delete(bot, uid, msg.message_id)
        if not check_rate_limit(uid, "cdk_status", min_seconds=3):
            _slow_down_nudge(bot, uid)
            return
        if len(key) < 4:
            new_id = edit_or_send(
                bot, uid, prompt_id,
                T.cdk_status_ask(product["name"], _product_icon_html(product),
                                 error="That key looks too short — please check it and send it again."),
                reply_markup=_cancel_kb())
            set_state(uid, prompt_msg_id=new_id)
            return
        checking_id = edit_or_send(bot, uid, prompt_id,
                                   T.cdk_checking(product["name"]),
                                   reply_markup=None)
        # Reuse our own receipt if this user activated the same key before —
        # the live lookup for Claude/Grok needs the order reference.
        ref = ""
        try:
            rec = db.cdk_activation_find_key(uid, key)
            if rec and rec["provider_ref"]:
                ref = rec["provider_ref"]
        except Exception:
            log.exception("CDK status receipt lookup failed")
        try:
            ok, status, message, live_ref = lookup_status(key, product, ref or None)
        except Exception:
            log.exception("CDK status lookup crashed for %s", product["id"])
            ok, status, message, live_ref = False, "", "Something went wrong checking the status — please try again in a moment.", ""
        message = humanize_provider_message(message)
        shown_ref = _mask_ref(live_ref or ref)
        new_id = edit_or_send(
            bot, uid, checking_id,
            T.cdk_status_result(product["name"], _product_icon_html(product),
                                _mask_key(key), ok,
                                html.escape(status, quote=False),
                                html.escape(message, quote=False),
                                html.escape(shown_ref, quote=False)),
            reply_markup=_back_to_menu_kb())
        set_state(uid, prompt_msg_id=new_id)

    # ── Step 1: the CDK key ──

    @bot.message_handler(content_types=["text"],
                         func=lambda m: get_state(m.from_user.id).get("step") == "cdk_key")
    def msg_cdk_key(msg: types.Message):
        uid = msg.from_user.id
        st = get_state(uid)
        product = _find_product(st.get("cdk_pid"))
        if not product:
            clear_state(uid)
            return
        prompt_id = st.get("prompt_msg_id")
        key = (msg.text or "").strip()
        if key.startswith("/"):
            return  # let command handlers deal with it
        silent_delete(bot, uid, msg.message_id)
        if not check_rate_limit(uid, "cdk_key", min_seconds=3):
            _slow_down_nudge(bot, uid)
            return
        if len(key) < 4:
            new_id = edit_or_send(
                bot, uid, prompt_id,
                T.cdk_tutorial(product["name"], _product_icon_html(product), product["provider"],
                               error="That key looks too short — please check it and send it again."),
                reply_markup=_cancel_kb())
            set_state(uid, prompt_msg_id=new_id)
            return
        checking_id = edit_or_send(bot, uid, prompt_id,
                                   T.cdk_checking(product["name"]),
                                   reply_markup=None)
        set_state(uid, prompt_msg_id=checking_id)
        try:
            ok, err = verify_card_key(key, product)
        except Exception:
            log.exception("CDK key verify crashed for %s", product["id"])
            ok, err = False, "Something went wrong checking the key — please try again in a moment."
        if not ok:
            err = humanize_provider_message(err)
            new_id = edit_or_send(
                bot, uid, checking_id,
                T.cdk_tutorial(product["name"], _product_icon_html(product), product["provider"],
                               error=html.escape(err, quote=False)),
                reply_markup=_cancel_kb())
            set_state(uid, prompt_msg_id=new_id)
            return
        new_id = edit_or_send(
            bot, uid, checking_id,
            T.cdk_ask_credential(product["name"], product["provider"]),
            reply_markup=_cancel_kb())
        set_state(uid, step="cdk_credential", cdk_key=key, prompt_msg_id=new_id)

    # ── Step 2: the account credential (pasted text or uploaded file) ──

    @bot.message_handler(content_types=["text", "document"],
                         func=lambda m: get_state(m.from_user.id).get("step") == "cdk_credential")
    def msg_cdk_credential(msg: types.Message):
        uid = msg.from_user.id
        st = get_state(uid)
        product = _find_product(st.get("cdk_pid"))
        key = st.get("cdk_key")
        if not product or not key:
            clear_state(uid)
            return
        prompt_id = st.get("prompt_msg_id")
        credential = None

        if msg.content_type == "document":
            doc = msg.document
            if doc.file_size and doc.file_size > _MAX_FILE_BYTES:
                credential = None
            else:
                try:
                    file_info = bot.get_file(doc.file_id)
                    data = bot.download_file(file_info.file_path)
                    credential = data.decode("utf-8", errors="replace").strip()
                except Exception:
                    log.exception("CDK credential file download failed")
                    credential = None
            silent_delete(bot, uid, msg.message_id)
            if not credential:
                new_id = edit_or_send(
                    bot, uid, prompt_id,
                    T.cdk_ask_credential(
                        product["name"], product["provider"],
                        error="Couldn't read that file — please try again or paste the text instead."),
                    reply_markup=_cancel_kb())
                set_state(uid, prompt_msg_id=new_id)
                return
        else:
            credential = (msg.text or "").strip()
            if credential.startswith("/"):
                return  # let command handlers deal with it
            silent_delete(bot, uid, msg.message_id)
            if not credential:
                new_id = edit_or_send(
                    bot, uid, prompt_id,
                    T.cdk_ask_credential(
                        product["name"], product["provider"],
                        error="That looks empty — please send it again."),
                    reply_markup=_cancel_kb())
                set_state(uid, prompt_msg_id=new_id)
                return

        # The credential is sensitive: hand it straight to the worker thread and
        # wipe the stored state so it never sits in the database.
        try:
            rec_id = db.cdk_activation_create(uid, product["id"], product["name"], key)
        except Exception:
            log.exception("CDK activation record create failed")
            rec_id = 0
        token = _bump_run_token(uid)
        clear_state(uid)
        act_msg_id = edit_or_send(bot, uid, prompt_id,
                                  T.cdk_activating(product["name"]),
                                  reply_markup=None)
        uname = msg.from_user.username or ""
        threading.Thread(
            target=_run_activation,
            args=(bot, uid, uname, product, key, credential, act_msg_id, token, rec_id),
            daemon=True,
        ).start()
