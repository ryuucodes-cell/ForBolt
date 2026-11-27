"""
Binance Pay deposit verification.

Calls GET /sapi/v1/pay/transactions with a read-only API key and matches a
customer-supplied Order ID against our incoming Pay transaction history.

Design decisions (confirmed against real API output):
  • Direction: incoming transfers have a POSITIVE `amount`. We only ever match
    positive-amount records, so we can never credit an outgoing transfer.
  • Every record belongs to OUR account (the API key owner), so a positive
    amount always means money received by us — no extra UID check needed.
  • Match the user's input against BOTH `orderId` (numeric) and `transactionId`
    (the "P_..." reference), because different app screens surface different IDs.
  • Authoritative amount comes from the API record, never from user input.
  • `transactionId` is globally unique and is what the caller stores to guard
    against a replayed/re-used Order ID.

Uses only the Python standard library (no extra dependencies).
"""

import hashlib
import hmac
import json
import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request

log = logging.getLogger("stockbot.binance")

BASE = "https://api.binance.com"

# Only credit transfers in these currencies.
_ALLOWED_CURRENCIES = {"USDT"}

# Reject transfers older than this many hours (defense against someone pasting a
# very old received Order ID). Generous by default; a real deposit is confirmed
# within minutes.
_MAX_AGE_HOURS = 72


def _api_key() -> str:
    return os.getenv("BINANCE_API_KEY", "").strip()


def _api_secret() -> str:
    return os.getenv("BINANCE_API_SECRET", "").strip()


def is_configured() -> bool:
    """True only if both API credentials are present."""
    return bool(_api_key() and _api_secret())


def _server_time() -> int:
    """Binance server time in ms — avoids -1021 timestamp/recvWindow errors."""
    try:
        with urllib.request.urlopen(f"{BASE}/api/v3/time", timeout=10) as r:
            return json.loads(r.read())["serverTime"]
    except Exception:
        return int(time.time() * 1000)


def _signed_get(path: str, params: dict):
    """Return (http_status, parsed_json). status -1 means a transport-level error."""
    params = dict(params)
    params["timestamp"] = _server_time()
    params["recvWindow"] = 60000
    query = urllib.parse.urlencode(params)
    signature = hmac.new(_api_secret().encode(), query.encode(), hashlib.sha256).hexdigest()
    url = f"{BASE}{path}?{query}&signature={signature}"

    req = urllib.request.Request(url, headers={"X-MBX-APIKEY": _api_key()})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        try:
            body = json.loads(body)
        except Exception:
            pass
        return e.code, body
    except Exception as e:
        return -1, {"error": str(e)}


def _fetch_page(params: dict):
    """Fetch one page of Pay transactions. Returns (ok, records, error)."""
    status, data = _signed_get("/sapi/v1/pay/transactions", params)
    if status != 200:
        msg = data.get("msg") if isinstance(data, dict) else str(data)
        code = data.get("code") if isinstance(data, dict) else None
        log.warning("Binance pay/transactions failed: status=%s code=%s msg=%s", status, code, msg)
        return False, [], f"Binance API error ({code or status}): {msg or 'unknown'}"
    records = data.get("data", []) if isinstance(data, dict) else []
    return True, records, None


def fetch_pay_transactions(limit: int = 100):
    """Fetch a single recent page (compat helper). Returns (ok, records, error)."""
    if not is_configured():
        return False, [], "Binance API keys are not configured."
    return _fetch_page({"limit": limit})


def health_check() -> dict:
    """Live probe of the API. Returns {'status': ..., 'detail': str}.

    status is one of: ok | unconfigured | blocked | error
    """
    if not is_configured():
        return {"status": "unconfigured", "detail": "API keys are not set in .env."}
    status, data = _signed_get("/sapi/v1/pay/transactions", {"limit": 1})
    if status == 200:
        n = len(data.get("data", [])) if isinstance(data, dict) else 0
        return {"status": "ok", "detail": f"Connected — {n} recent record(s) visible."}
    if status == 451:
        return {
            "status": "blocked",
            "detail": "Region blocked (HTTP 451). The server must run in a Binance-allowed region.",
        }
    if status == -1:
        msg = data.get("error") if isinstance(data, dict) else str(data)
        return {"status": "error", "detail": f"Network error: {msg}"}
    code = data.get("code") if isinstance(data, dict) else None
    msg = data.get("msg") if isinstance(data, dict) else str(data)
    return {"status": "error", "detail": f"Error {code or status}: {msg}"}


def _clean(s) -> str:
    return str(s or "").strip()


def find_deposit_by_order_id(order_id: str, max_pages: int = 3):
    """Look up a customer-supplied Order ID in our incoming Pay history.

    Searches up to `max_pages` pages of 100 transactions (newest first).

    Returns a dict describing the outcome:
      {"status": "matched", "amount_usdt": float, "currency": str,
       "order_id": str, "transaction_id": str, "payer_name": str,
       "payer_id": int|None, "note": str, "txn_time": int}
      {"status": "not_found"}                 — no record with that ID
      {"status": "wrong_currency", ...}       — matched but not an allowed currency
      {"status": "not_incoming", ...}         — matched but it's an outgoing/zero transfer
      {"status": "too_old", ...}              — matched but older than the age limit
      {"status": "api_error", "error": str}   — could not reach/authenticate Binance
    """
    query = _clean(order_id)
    if not query:
        return {"status": "not_found"}
    if not is_configured():
        return {"status": "api_error", "error": "Binance API keys are not configured."}

    # Page backwards through history (newest first) so a buried Order ID still
    # matches under high volume. Stops as soon as it's found or history runs out.
    match = None
    end_time = None
    for _ in range(max_pages):
        params = {"limit": 100}
        if end_time is not None:
            params["endTime"] = end_time
        ok, records, err = _fetch_page(params)
        if not ok:
            return {"status": "api_error", "error": err}
        if not records:
            break
        for rec in records:
            rec_order = _clean(rec.get("orderId"))
            rec_txn = _clean(rec.get("transactionId"))
            if query == rec_order or query.upper() == rec_txn.upper():
                match = rec
                break
        if match or len(records) < 100:
            break
        times = [int(r["transactionTime"]) for r in records if r.get("transactionTime")]
        if not times:
            break
        end_time = min(times) - 1  # next page ends just before the oldest we've seen

    if not match:
        return {"status": "not_found"}

    currency = _clean(match.get("currency")).upper()
    try:
        amount = float(match.get("amount", 0))
    except (TypeError, ValueError):
        amount = 0.0

    common = {
        "order_id": _clean(match.get("orderId")),
        "transaction_id": _clean(match.get("transactionId")),
        "currency": currency,
        "amount_usdt": abs(amount),
        "payer_name": _clean((match.get("payerInfo") or {}).get("name")),
        "payer_id": (match.get("payerInfo") or {}).get("binanceId"),
        "note": _clean(match.get("note")),
        "txn_time": match.get("transactionTime"),
    }

    if amount <= 0:
        return {"status": "not_incoming", **common}
    if currency not in _ALLOWED_CURRENCIES:
        return {"status": "wrong_currency", **common}

    txn_time = match.get("transactionTime")
    if txn_time:
        age_hours = (time.time() * 1000 - float(txn_time)) / 3_600_000
        if age_hours > _MAX_AGE_HOURS:
            return {"status": "too_old", **common}

    return {"status": "matched", **common}
