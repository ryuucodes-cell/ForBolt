"""
Database layer — Postgres (Supabase) backend.

Every function preserves the same signature and return shape as the original
SQLite version so the rest of the bot code does not need changes. Rows are
returned as plain dicts (matching the old sqlite3.Row dict-style access).
"""

import os
import re
import json
import secrets
import string
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv

import psycopg2
from psycopg2.extras import RealDictCursor

load_dotenv()

# ── connection ───────────────────────────────────────────────────────────────

DATABASE_URL = os.environ.get("SUPABASE_DB_URL") or os.environ.get("DATABASE_URL", "")

def _connect():
    """Create a new psycopg2 connection from DATABASE_URL."""
    conn = psycopg2.connect(DATABASE_URL)
    conn.autocommit = False
    return conn

@contextmanager
def get_conn():
    """Yield a connection. Commits on success, rolls back on error."""
    conn = _connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

@contextmanager
def get_sold_conn():
    """Sold stock is now in the same Postgres database — same connection."""
    conn = _connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

# threading lock for atomic read-modify-write sequences (same as SQLite version)
_lock = threading.Lock()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _utcnow_iso() -> str:
    """UTC ISO timestamp for comparisons."""
    return _utcnow().strftime("%Y-%m-%d %H:%M:%S")


def _fetchone(conn, sql, params=None):
    """Execute and return one row as a dict, or None."""
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(sql, params or [])
        row = cur.fetchone()
        return dict(row) if row else None


def _fetchall(conn, sql, params=None):
    """Execute and return all rows as a list of dicts."""
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(sql, params or [])
        return [dict(r) for r in cur.fetchall()]


def _execute(conn, sql, params=None):
    """Execute and return the cursor (for rowcount)."""
    with conn.cursor() as cur:
        cur.execute(sql, params or [])
        return cur


def _executemany(conn, sql, params_list):
    with conn.cursor() as cur:
        cur.executemany(sql, params_list)
        return cur


def _lastrowid(conn, table, id_col="id"):
    """Get the last inserted ID via RETURNING or currval."""
    with conn.cursor() as cur:
        cur.execute(f"SELECT lastval()")
        return cur.fetchone()[0]


# ── init ─────────────────────────────────────────────────────────────────────

def init_db():
    """Verify database connectivity. Schema is managed by Supabase migrations."""
    with get_conn() as conn:
        _execute(conn, "SELECT 1")


def init_sold_db():
    """No-op — sold_stock is now a table in the same Postgres database."""
    pass


DB_PATH = ""  # legacy compat (admin_backup.py references this)
DB_SOLD_PATH = ""  # legacy compat


# ── config ───────────────────────────────────────────────────────────────────

def cfg_get(key: str, default=None):
    with get_conn() as conn:
        row = _fetchone(conn, "SELECT value FROM config WHERE key=%s", (key,))
        return row["value"] if row else default


def cfg_set(key: str, value):
    with get_conn() as conn:
        _execute(conn, """
            INSERT INTO config (key, value) VALUES (%s, %s)
            ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value
        """, (key, str(value)))


def cfg_all() -> dict:
    with get_conn() as conn:
        rows = _fetchall(conn, "SELECT key, value FROM config")
        return {r["key"]: r["value"] for r in rows}


# ── users ────────────────────────────────────────────────────────────────────

def ensure_user(telegram_id: int, username: str = None):
    with get_conn() as conn:
        _execute(conn, """
            INSERT INTO users (telegram_id, username) VALUES (%s, %s)
            ON CONFLICT (telegram_id) DO NOTHING
        """, (telegram_id, username or ""))
        if username:
            _execute(conn, "UPDATE users SET username=%s WHERE telegram_id=%s",
                     (username, telegram_id))


def get_user(telegram_id: int):
    with get_conn() as conn:
        return _fetchone(conn, "SELECT * FROM users WHERE telegram_id=%s", (telegram_id,))


def get_balance(telegram_id: int) -> int:
    u = get_user(telegram_id)
    return u["mmk_balance"] if u else 0


def adjust_balance(telegram_id: int, delta: int):
    with get_conn() as conn:
        _execute(conn,
            "UPDATE users SET mmk_balance = mmk_balance + %s WHERE telegram_id=%s",
            (delta, telegram_id))


def adjust_balance_clamped(telegram_id: int, delta: int) -> int:
    with _lock:
        with get_conn() as conn:
            _execute(conn,
                "UPDATE users SET mmk_balance = GREATEST(0, mmk_balance + %s) WHERE telegram_id=%s",
                (delta, telegram_id))
            row = _fetchone(conn, "SELECT mmk_balance FROM users WHERE telegram_id=%s", (telegram_id,))
            return row["mmk_balance"] if row else 0


def deduct_balance_atomic(telegram_id: int, amount: int) -> bool:
    with _lock:
        with get_conn() as conn:
            cur = _execute(conn,
                "UPDATE users SET mmk_balance = mmk_balance - %s "
                "WHERE telegram_id=%s AND mmk_balance >= %s",
                (amount, telegram_id, amount))
            return cur.rowcount == 1


def list_users(offset=0, limit=20):
    with get_conn() as conn:
        return _fetchall(conn,
            "SELECT * FROM users ORDER BY registered_at DESC LIMIT %s OFFSET %s",
            (limit, offset))


def set_notify_opt_out(telegram_id: int, value: bool):
    with get_conn() as conn:
        _execute(conn, "UPDATE users SET notify_opt_out=%s WHERE telegram_id=%s",
                 (1 if value else 0, telegram_id))


def get_notify_opt_out(telegram_id: int) -> bool:
    with get_conn() as conn:
        row = _fetchone(conn, "SELECT notify_opt_out FROM users WHERE telegram_id=%s", (telegram_id,))
        return bool(row["notify_opt_out"]) if row else False


def count_users() -> int:
    with get_conn() as conn:
        row = _fetchone(conn, "SELECT COUNT(*) AS c FROM users")
        return row["c"]


# ── resellers ────────────────────────────────────────────────────────────────

def is_reseller(telegram_id: int) -> bool:
    with get_conn() as conn:
        row = _fetchone(conn, "SELECT is_reseller FROM users WHERE telegram_id=%s", (telegram_id,))
        return bool(row["is_reseller"]) if row else False


def set_reseller(telegram_id: int, value: bool):
    with get_conn() as conn:
        if value:
            _execute(conn,
                "UPDATE users SET is_reseller=1, reseller_since=now(), last_reseller_purchase=now() "
                "WHERE telegram_id=%s", (telegram_id,))
        else:
            _execute(conn,
                "UPDATE users SET is_reseller=0, reseller_since=NULL, last_reseller_purchase=NULL "
                "WHERE telegram_id=%s", (telegram_id,))


def touch_reseller_activity(telegram_id: int):
    with get_conn() as conn:
        _execute(conn,
            "UPDATE users SET last_reseller_purchase=now() WHERE telegram_id=%s AND is_reseller=1",
            (telegram_id,))


def list_resellers():
    with get_conn() as conn:
        return _fetchall(conn,
            "SELECT * FROM users WHERE is_reseller=1 ORDER BY reseller_since DESC")


def count_resellers() -> int:
    with get_conn() as conn:
        row = _fetchone(conn, "SELECT COUNT(*) AS c FROM users WHERE is_reseller=1")
        return row["c"]


def revoke_inactive_resellers(days: int):
    cutoff = _utcnow() - timedelta(days=days)
    with _lock:
        with get_conn() as conn:
            rows = _fetchall(conn,
                "SELECT telegram_id, username FROM users "
                "WHERE is_reseller=1 AND (last_reseller_purchase IS NULL OR last_reseller_purchase < %s)",
                (cutoff,))
            revoked = [(r["telegram_id"], r["username"]) for r in rows]
            if revoked:
                _execute(conn,
                    "UPDATE users SET is_reseller=0, reseller_since=NULL, last_reseller_purchase=NULL "
                    "WHERE is_reseller=1 AND (last_reseller_purchase IS NULL OR last_reseller_purchase < %s)",
                    (cutoff,))
            return revoked


# ── reseller keys ────────────────────────────────────────────────────────────

def create_reseller_key(code: str, created_by: int, note: str = None) -> int:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO reseller_keys (code, created_by, note) VALUES (%s, %s, %s) RETURNING key_id",
                (code, created_by, note))
            return cur.fetchone()[0]


def redeem_reseller_key(code: str, telegram_id: int) -> str:
    with _lock:
        with get_conn() as conn:
            row = _fetchone(conn, "SELECT * FROM reseller_keys WHERE code=%s", (code,))
            if not row:
                return "not_found"
            if row["revoked"]:
                return "revoked"
            if row["used_by"]:
                return "used"
            already = _fetchone(conn, "SELECT is_reseller FROM users WHERE telegram_id=%s", (telegram_id,))
            if already and already["is_reseller"]:
                return "already_reseller"
            cur = _execute(conn,
                "UPDATE reseller_keys SET used_by=%s, used_at=now() WHERE key_id=%s AND used_by IS NULL",
                (telegram_id, row["key_id"]))
            if cur.rowcount != 1:
                return "used"
            _execute(conn,
                "UPDATE users SET is_reseller=1, reseller_since=now(), last_reseller_purchase=now() "
                "WHERE telegram_id=%s", (telegram_id,))
            return "ok"


def list_reseller_keys(only_unused=False, limit=50):
    with get_conn() as conn:
        q = "SELECT * FROM reseller_keys"
        if only_unused:
            q += " WHERE used_by IS NULL AND revoked=0"
        q += " ORDER BY created_at DESC LIMIT %s"
        return _fetchall(conn, q, (limit,))


def revoke_reseller_key(key_id: int) -> bool:
    with get_conn() as conn:
        cur = _execute(conn,
            "UPDATE reseller_keys SET revoked=1 WHERE key_id=%s AND used_by IS NULL AND revoked=0",
            (key_id,))
        return cur.rowcount == 1


def count_unused_reseller_keys() -> int:
    with get_conn() as conn:
        row = _fetchone(conn, "SELECT COUNT(*) AS c FROM reseller_keys WHERE used_by IS NULL AND revoked=0")
        return row["c"]


# ── reseller requests ────────────────────────────────────────────────────────

def create_reseller_request(telegram_id: int, username: str, message: str = None):
    with _lock:
        with get_conn() as conn:
            existing = _fetchone(conn,
                "SELECT request_id FROM reseller_requests WHERE telegram_id=%s AND status='pending'",
                (telegram_id,))
            if existing:
                return None
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO reseller_requests (telegram_id, username, message) VALUES (%s, %s, %s) RETURNING request_id",
                    (telegram_id, username or "", message))
                return cur.fetchone()[0]


def get_reseller_request(request_id: int):
    with get_conn() as conn:
        return _fetchone(conn, "SELECT * FROM reseller_requests WHERE request_id=%s", (request_id,))


def list_reseller_requests(status="pending", limit=20, offset=0):
    with get_conn() as conn:
        if status:
            return _fetchall(conn,
                "SELECT * FROM reseller_requests WHERE status=%s ORDER BY created_at DESC LIMIT %s OFFSET %s",
                (status, limit, offset))
        return _fetchall(conn,
            "SELECT * FROM reseller_requests ORDER BY created_at DESC LIMIT %s OFFSET %s",
            (limit, offset))


def count_reseller_requests(status="pending") -> int:
    with get_conn() as conn:
        if status:
            row = _fetchone(conn, "SELECT COUNT(*) AS c FROM reseller_requests WHERE status=%s", (status,))
        else:
            row = _fetchone(conn, "SELECT COUNT(*) AS c FROM reseller_requests")
        return row["c"]


def resolve_reseller_request_atomic(request_id: int, status: str, handled_by: int):
    with _lock:
        with get_conn() as conn:
            r = _fetchone(conn, "SELECT * FROM reseller_requests WHERE request_id=%s", (request_id,))
            if not r or r["status"] != "pending":
                return None
            cur = _execute(conn,
                "UPDATE reseller_requests SET status=%s, handled_by=%s, handled_at=now() "
                "WHERE request_id=%s AND status='pending'",
                (status, handled_by, request_id))
            if cur.rowcount != 1:
                return None
            return r


# ── products ─────────────────────────────────────────────────────────────────

def add_product(name, category, description, price_mmk, credential_format):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO products (name, category, description, price_mmk, credential_format) "
                "VALUES (%s, %s, %s, %s, %s) RETURNING product_id",
                (name, category, description, price_mmk, credential_format))
            return cur.fetchone()[0]


def get_product(product_id: int):
    with get_conn() as conn:
        return _fetchone(conn, "SELECT * FROM products WHERE product_id=%s", (product_id,))


def list_products(active_only=True):
    with get_conn() as conn:
        q = "SELECT * FROM products"
        if active_only:
            q += " WHERE active=1"
        q += " ORDER BY LOWER(name) ASC"
        rows = _fetchall(conn, q)

    def _natural_key(value: str):
        parts = re.split(r"(\d+)", (value or "").lower())
        key = []
        for part in parts:
            if part.isdigit():
                key.append((0, int(part)))
            else:
                key.append((1, part))
        return key

    return sorted(rows, key=lambda row: _natural_key(row["name"]))


def list_categories(active_only=True):
    with get_conn() as conn:
        q = "SELECT DISTINCT category FROM products"
        if active_only:
            q += " WHERE active=1"
        rows = _fetchall(conn, q)
        return [r["category"] for r in rows]


def update_product(product_id: int, **fields):
    allowed = {"name", "category", "description", "price_mmk", "credential_format",
               "active", "discount_pct", "discount_until", "reseller_price_mmk", "emoji", "emoji_id"}
    fields = {k: v for k, v in fields.items() if k in allowed}
    if not fields:
        return
    sets = ", ".join(f"{k}=%s" for k in fields)
    vals = list(fields.values()) + [product_id]
    with get_conn() as conn:
        _execute(conn, f"UPDATE products SET {sets} WHERE product_id=%s", vals)


def set_discount(product_id: int, pct: int, until_iso: str):
    with get_conn() as conn:
        _execute(conn,
            "UPDATE products SET discount_pct=%s, discount_until=%s WHERE product_id=%s",
            (pct, until_iso, product_id))


def clear_discount(product_id: int):
    with get_conn() as conn:
        _execute(conn,
            "UPDATE products SET discount_pct=0, discount_until=NULL WHERE product_id=%s",
            (product_id,))


def expire_discounts():
    with get_conn() as conn:
        _execute(conn,
            "UPDATE products SET discount_pct=0, discount_until=NULL "
            "WHERE discount_pct > 0 AND discount_until IS NOT NULL AND discount_until <= now()")


def get_effective_price(product) -> int:
    pct = product["discount_pct"] or 0
    until = product["discount_until"]
    if pct > 0 and until and datetime.fromisoformat(str(until).replace("Z", "+00:00")) > _utcnow():
        return max(1, int(product["price_mmk"] * (100 - pct) / 100))
    return product["price_mmk"]


def get_effective_price_for_id(product_id: int) -> int:
    p = get_product(product_id)
    if not p:
        return 0
    return get_effective_price(p)


def product_emoji(product, default="\U0001F4E6") -> str:
    try:
        e = product["emoji"]
    except (IndexError, KeyError):
        return default
    e = (e or "").strip()
    return e if e else default


def product_emoji_id(product) -> str:
    try:
        eid = product["emoji_id"]
    except (IndexError, KeyError):
        return ""
    return (eid or "").strip()


def product_emoji_html(product, default="\U0001F4E6") -> str:
    from utils import tg_emoji
    fallback = product_emoji(product, default=default)
    eid = product_emoji_id(product)
    return tg_emoji(fallback, eid) if eid else fallback


def _reseller_price(product):
    try:
        rp = product["reseller_price_mmk"]
    except (IndexError, KeyError):
        return None
    if rp is None or rp <= 0:
        return None
    return rp


def get_reseller_price(product) -> int:
    rp = _reseller_price(product)
    if rp is not None:
        return rp
    return get_effective_price(product)


def get_price_for_user(product, telegram_id: int) -> int:
    if is_reseller(telegram_id):
        return get_reseller_price(product)
    price = get_effective_price(product)
    _tier_name, disc = get_user_vip_tier(telegram_id)
    if disc > 0:
        price = price * (100 - disc) // 100
    return price


def get_price_for_user_by_id(product_id: int, telegram_id: int) -> int:
    p = get_product(product_id)
    if not p:
        return 0
    return get_price_for_user(p, telegram_id)


def is_discount_active(product) -> bool:
    pct = product["discount_pct"] or 0
    until = product["discount_until"]
    return pct > 0 and bool(until) and datetime.fromisoformat(str(until).replace("Z", "+00:00")) > _utcnow()


def delete_product(product_id: int) -> bool:
    with get_conn() as conn:
        row = _fetchone(conn, "SELECT COUNT(*) AS c FROM stock WHERE product_id=%s", (product_id,))
        if row["c"] > 0:
            return False
        _execute(conn, "DELETE FROM products WHERE product_id=%s", (product_id,))
        return True


def stock_count(product_id: int) -> int:
    with get_conn() as conn:
        row = _fetchone(conn,
            "SELECT COUNT(*) AS c FROM stock WHERE product_id=%s AND reserved_for IS NULL",
            (product_id,))
        return row["c"]


# ── stock ────────────────────────────────────────────────────────────────────

def add_stock_bulk(product_id: int, credentials: list) -> int:
    clean = [c.strip() for c in credentials if c.strip()]
    if not clean:
        return 0
    rows = [(product_id, c) for c in clean]
    with get_conn() as conn:
        _executemany(conn, "INSERT INTO stock (product_id, credential) VALUES (%s, %s)", rows)
    return len(rows)


def claim_stock(product_id: int, telegram_id: int):
    """Atomically claim one available stock item. Returns credential or None."""
    with _lock:
        with get_conn() as conn:
            credential = None
            stock_id = None
            pname = ""
            while True:
                row = _fetchone(conn,
                    "SELECT * FROM stock WHERE product_id=%s AND reserved_for IS NULL LIMIT 1",
                    (product_id,))
                if not row:
                    return None
                cur = _execute(conn,
                    "DELETE FROM stock WHERE stock_id=%s AND reserved_for IS NULL",
                    (row["stock_id"],))
                if cur.rowcount == 1:
                    credential = row["credential"]
                    stock_id = row["stock_id"]
                    p = _fetchone(conn, "SELECT name FROM products WHERE product_id=%s", (product_id,))
                    pname = p["name"] if p else ""
                    break

        # mirror to sold_stock (same DB now, best-effort)
        _mirror_sold(stock_id, product_id, pname, credential, telegram_id, _utcnow_iso())
        return credential


def _mirror_sold(stock_id, product_id, pname, credential, telegram_id, sold_at):
    """Write a claimed item into sold_stock. Best-effort, never raises."""
    try:
        with get_conn() as conn:
            _execute(conn,
                "INSERT INTO sold_stock (stock_id, product_id, product_name, credential, sold_to, sold_at) "
                "VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (stock_id) DO NOTHING",
                (stock_id, product_id, pname, credential, telegram_id, sold_at))
    except Exception:
        pass


def list_stock_items(product_id: int, limit=50):
    with get_conn() as conn:
        return _fetchall(conn,
            "SELECT * FROM stock WHERE product_id=%s ORDER BY stock_id DESC LIMIT %s",
            (product_id, limit))


def list_sold_items(product_id: int = None, limit=50):
    with get_conn() as conn:
        if product_id:
            return _fetchall(conn,
                "SELECT * FROM sold_stock WHERE product_id=%s ORDER BY sold_at DESC LIMIT %s",
                (product_id, limit))
        return _fetchall(conn,
            "SELECT * FROM sold_stock ORDER BY sold_at DESC LIMIT %s", (limit,))


def delete_stock_item(stock_id: int):
    with get_conn() as conn:
        _execute(conn, "DELETE FROM stock WHERE stock_id=%s", (stock_id,))


# ── orders ───────────────────────────────────────────────────────────────────

def create_order(telegram_id, username, product_id, product_name, amount_mmk, credential, payment_method="balance"):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO orders (telegram_id, username, product_id, product_name, amount_mmk, payment_method, credential) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING order_id",
                (telegram_id, username, product_id, product_name, amount_mmk, payment_method, credential))
            return cur.fetchone()[0]


class _InsufficientFunds(Exception):
    def __init__(self, need, balance):
        self.need = need
        self.balance = balance
        super().__init__("insufficient funds")


def purchase_atomic(product_id, telegram_id, username, unit_price, qty, product_name):
    """Claim up to qty stock items AND deduct balance in one transaction."""
    with _lock:
        claimed = []
        orders = []
        charged = 0
        new_balance = 0
        try:
            with get_conn() as conn:
                attempts = 0
                while len(claimed) < qty:
                    row = _fetchone(conn,
                        "SELECT stock_id, credential FROM stock WHERE product_id=%s AND reserved_for IS NULL LIMIT 1",
                        (product_id,))
                    if not row:
                        break
                    cur = _execute(conn, "DELETE FROM stock WHERE stock_id=%s", (row["stock_id"],))
                    if cur.rowcount == 1:
                        claimed.append((row["stock_id"], row["credential"]))
                    attempts += 1
                    if attempts > qty + 50:
                        break

                if not claimed:
                    return {"ok": False, "reason": "out_of_stock"}

                actual_qty = len(claimed)
                charged = unit_price * actual_qty

                cur = _execute(conn,
                    "UPDATE users SET mmk_balance = mmk_balance - %s "
                    "WHERE telegram_id=%s AND mmk_balance >= %s",
                    (charged, telegram_id, charged))
                if cur.rowcount != 1:
                    bal_row = _fetchone(conn, "SELECT mmk_balance FROM users WHERE telegram_id=%s", (telegram_id,))
                    bal = bal_row["mmk_balance"] if bal_row else 0
                    raise _InsufficientFunds(charged, bal)

                _execute(conn,
                    "UPDATE users SET total_spent = total_spent + %s WHERE telegram_id=%s",
                    (charged, telegram_id))

                for stock_id, credential in claimed:
                    with conn.cursor() as cur:
                        cur.execute(
                            "INSERT INTO orders (telegram_id, username, product_id, product_name, amount_mmk, payment_method, credential) "
                            "VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING order_id",
                            (telegram_id, username, product_id, product_name, unit_price, "balance", credential))
                        orders.append((cur.fetchone()[0], credential))

                new_bal_row = _fetchone(conn, "SELECT mmk_balance FROM users WHERE telegram_id=%s", (telegram_id,))
                new_balance = new_bal_row["mmk_balance"] if new_bal_row else 0
        except _InsufficientFunds as e:
            return {"ok": False, "reason": "insufficient", "need": e.need, "balance": e.balance}

        sold_at = _utcnow_iso()
        for stock_id, credential in claimed:
            _mirror_sold(stock_id, product_id, product_name, credential, telegram_id, sold_at)

        return {
            "ok": True,
            "orders": orders,
            "charged": charged,
            "claimed": len(claimed),
            "new_balance": new_balance,
        }


def list_orders(limit=20, offset=0, telegram_id=None):
    with get_conn() as conn:
        if telegram_id:
            return _fetchall(conn,
                "SELECT * FROM orders WHERE telegram_id=%s ORDER BY created_at DESC LIMIT %s OFFSET %s",
                (telegram_id, limit, offset))
        return _fetchall(conn,
            "SELECT * FROM orders ORDER BY created_at DESC LIMIT %s OFFSET %s", (limit, offset))


def get_order(order_id: int):
    with get_conn() as conn:
        return _fetchone(conn, "SELECT * FROM orders WHERE order_id=%s", (order_id,))


# ── fulfillment v2 ───────────────────────────────────────────────────────────

FULFILLMENT_MODES = ("instant", "preorder")


def get_fulfillment(product) -> str:
    try:
        mode = (product["fulfillment"] or "instant").strip().lower()
    except (KeyError, TypeError, AttributeError):
        return "instant"
    if mode == "manual_activation":
        return "preorder"
    return mode if mode in FULFILLMENT_MODES else "instant"


def set_product_fulfillment(product_id: int, fulfillment: str, manual_fields: str = ""):
    if fulfillment == "manual_activation":
        fulfillment = "preorder"
    if fulfillment not in FULFILLMENT_MODES:
        fulfillment = "instant"
    with get_conn() as conn:
        _execute(conn,
            "UPDATE products SET fulfillment=%s, manual_fields=%s WHERE product_id=%s",
            (fulfillment, manual_fields or "", product_id))


def get_manual_fields(product) -> list:
    try:
        raw = product["manual_fields"] or ""
    except (KeyError, TypeError):
        return []
    if not raw.strip():
        return []
    try:
        fields = json.loads(raw)
    except (ValueError, TypeError):
        return []
    return [str(f).strip() for f in fields if str(f).strip()]


def create_manual_order(telegram_id, username, product_id, product_name, amount_mmk,
                        mode: str = "preorder", payment_method: str = "balance") -> int:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO orders (telegram_id, username, product_id, product_name, amount_mmk, "
                "payment_method, credential, status, fulfillment_mode) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING order_id",
                (telegram_id, username, product_id, product_name, amount_mmk,
                 payment_method, "", "awaiting_fulfillment", "preorder"))
            return cur.fetchone()[0]


def create_preorder_atomic(telegram_id, username, product_id, product_name, total_mmk):
    with _lock:
        with get_conn() as conn:
            cur = _execute(conn,
                "UPDATE users SET mmk_balance = mmk_balance - %s "
                "WHERE telegram_id=%s AND mmk_balance >= %s",
                (total_mmk, telegram_id, total_mmk))
            if cur.rowcount != 1:
                return None
            with conn.cursor() as cur2:
                cur2.execute(
                    "INSERT INTO orders (telegram_id, username, product_id, product_name, amount_mmk, "
                    "payment_method, credential, status, fulfillment_mode) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING order_id",
                    (telegram_id, username, product_id, product_name, total_mmk,
                     "balance", "", "awaiting_fulfillment", "preorder"))
                order_id = cur2.fetchone()[0]
            _execute(conn,
                "UPDATE users SET total_spent = total_spent + %s WHERE telegram_id=%s",
                (total_mmk, telegram_id))
            return order_id


def get_order_fulfillment(order) -> str:
    try:
        mode = (order["fulfillment_mode"] or "").strip().lower()
    except (KeyError, TypeError, AttributeError):
        mode = ""
    if mode in ("preorder", "manual_activation"):
        return "preorder"
    if get_manual_info(order):
        return "preorder"
    try:
        status = order["status"] or ""
    except (KeyError, TypeError):
        status = ""
    if status in ("awaiting_info", "awaiting_fulfillment", "fulfilled"):
        return "preorder"
    return "instant"


def save_manual_info(order_id: int, info: dict):
    with get_conn() as conn:
        _execute(conn,
            "UPDATE orders SET manual_info=%s, status='awaiting_fulfillment' WHERE order_id=%s",
            (json.dumps(info, ensure_ascii=False), order_id))


def update_manual_info(order_id: int, info: dict):
    with get_conn() as conn:
        _execute(conn, "UPDATE orders SET manual_info=%s WHERE order_id=%s",
                 (json.dumps(info, ensure_ascii=False), order_id))


def get_manual_info(order) -> dict:
    try:
        raw = order["manual_info"] or ""
    except (KeyError, TypeError):
        return {}
    if not raw.strip():
        return {}
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return {}


def set_order_status(order_id: int, status: str):
    with get_conn() as conn:
        _execute(conn, "UPDATE orders SET status=%s WHERE order_id=%s", (status, order_id))


def set_order_credential(order_id: int, credential: str):
    with get_conn() as conn:
        _execute(conn, "UPDATE orders SET credential=%s WHERE order_id=%s", (credential, order_id))


def list_fulfillment_orders(limit: int = 50):
    with get_conn() as conn:
        return _fetchall(conn,
            "SELECT o.*, p.fulfillment AS mode FROM orders o "
            "LEFT JOIN products p ON p.product_id=o.product_id "
            "WHERE o.status IN ('awaiting_info','awaiting_fulfillment') "
            "ORDER BY o.created_at DESC LIMIT %s", (limit,))


def count_fulfillment_orders() -> int:
    with get_conn() as conn:
        row = _fetchone(conn,
            "SELECT COUNT(*) AS c FROM orders WHERE status IN ('awaiting_info','awaiting_fulfillment')")
        return row["c"]


def refund_order_atomic(order_id: int, only_statuses=None):
    with _lock:
        with get_conn() as conn:
            o = _fetchone(conn, "SELECT * FROM orders WHERE order_id=%s", (order_id,))
            if not o or o["status"] == "refunded":
                return None
            if only_statuses and o["status"] not in only_statuses:
                return None
            if only_statuses:
                placeholders = ",".join("%s" for _ in only_statuses)
                cur = _execute(conn,
                    f"UPDATE orders SET status='refunded' WHERE order_id=%s AND status IN ({placeholders})",
                    (order_id, *only_statuses))
            else:
                cur = _execute(conn,
                    "UPDATE orders SET status='refunded' WHERE order_id=%s AND status!='refunded'",
                    (order_id,))
            if cur.rowcount != 1:
                return None
            _execute(conn,
                "UPDATE users SET mmk_balance = mmk_balance + %s WHERE telegram_id=%s",
                (o["amount_mmk"], o["telegram_id"]))
            _execute(conn,
                "UPDATE users SET total_spent=GREATEST(0,total_spent-%s) WHERE telegram_id=%s",
                (o["amount_mmk"], o["telegram_id"]))
            return o


def count_orders() -> int:
    with get_conn() as conn:
        row = _fetchone(conn, "SELECT COUNT(*) AS c FROM orders")
        return row["c"]


def count_orders_for_user(telegram_id: int) -> int:
    with get_conn() as conn:
        row = _fetchone(conn, "SELECT COUNT(*) AS c FROM orders WHERE telegram_id=%s", (telegram_id,))
        return row["c"]


def count_deposits_for_user(telegram_id: int) -> int:
    with get_conn() as conn:
        row = _fetchone(conn, "SELECT COUNT(*) AS c FROM deposits WHERE telegram_id=%s", (telegram_id,))
        return row["c"]


def search_user(query: str):
    with get_conn() as conn:
        if query.lstrip("-").isdigit():
            return _fetchone(conn, "SELECT * FROM users WHERE telegram_id=%s", (int(query),))
        return _fetchone(conn, "SELECT * FROM users WHERE username LIKE %s LIMIT 1", (f"%{query}%",))


# ── order problem reports (warranty) ─────────────────────────────────────────

def create_order_report(order_id: int, telegram_id: int, username: str, message: str = None):
    with _lock:
        with get_conn() as conn:
            existing = _fetchone(conn,
                "SELECT report_id FROM order_reports WHERE order_id=%s AND status='pending'",
                (order_id,))
            if existing:
                return None
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO order_reports (order_id, telegram_id, username, message) VALUES (%s, %s, %s, %s) RETURNING report_id",
                    (order_id, telegram_id, username or "", message))
                return cur.fetchone()[0]


def get_order_report(report_id: int):
    with get_conn() as conn:
        return _fetchone(conn, "SELECT * FROM order_reports WHERE report_id=%s", (report_id,))


def order_has_pending_report(order_id: int) -> bool:
    with get_conn() as conn:
        row = _fetchone(conn, "SELECT 1 FROM order_reports WHERE order_id=%s AND status='pending'", (order_id,))
        return row is not None


def list_order_reports(status="pending", limit=10, offset=0):
    with get_conn() as conn:
        if status:
            return _fetchall(conn,
                "SELECT * FROM order_reports WHERE status=%s ORDER BY created_at ASC LIMIT %s OFFSET %s",
                (status, limit, offset))
        return _fetchall(conn,
            "SELECT * FROM order_reports ORDER BY created_at DESC LIMIT %s OFFSET %s",
            (limit, offset))


def count_order_reports(status="pending") -> int:
    with get_conn() as conn:
        if status:
            row = _fetchone(conn, "SELECT COUNT(*) AS c FROM order_reports WHERE status=%s", (status,))
        else:
            row = _fetchone(conn, "SELECT COUNT(*) AS c FROM order_reports")
        return row["c"]


def resolve_order_report_atomic(report_id: int, status: str, handled_by: int):
    with _lock:
        with get_conn() as conn:
            r = _fetchone(conn, "SELECT * FROM order_reports WHERE report_id=%s", (report_id,))
            if not r or r["status"] != "pending":
                return None
            cur = _execute(conn,
                "UPDATE order_reports SET status=%s, handled_by=%s, handled_at=now() "
                "WHERE report_id=%s AND status='pending'",
                (status, handled_by, report_id))
            if cur.rowcount != 1:
                return None
            return r


def reopen_order_report(report_id: int):
    with get_conn() as conn:
        _execute(conn,
            "UPDATE order_reports SET status='pending', handled_by=NULL, handled_at=NULL WHERE report_id=%s",
            (report_id,))


# ── deposits ──────────────────────────────────────────────────────────────────

def create_deposit(telegram_id, username, method, amount_mmk, screenshot_file_id=None, note=None):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO deposits (telegram_id, username, method, amount_mmk, screenshot_file_id, note) "
                "VALUES (%s, %s, %s, %s, %s, %s) RETURNING deposit_id",
                (telegram_id, username, method, amount_mmk, screenshot_file_id, note))
            return cur.fetchone()[0]


def get_deposit(deposit_id: int):
    with get_conn() as conn:
        return _fetchone(conn, "SELECT * FROM deposits WHERE deposit_id=%s", (deposit_id,))


def update_deposit(deposit_id: int, status: str, approved_by=None, note=None):
    with get_conn() as conn:
        _execute(conn,
            "UPDATE deposits SET status=%s, approved_by=%s, note=%s WHERE deposit_id=%s",
            (status, approved_by, note, deposit_id))


def approve_deposit_atomic(deposit_id: int, approved_by: int):
    with _lock:
        with get_conn() as conn:
            d = _fetchone(conn, "SELECT * FROM deposits WHERE deposit_id=%s", (deposit_id,))
            if not d or d["status"] != "pending":
                return None
            cur = _execute(conn,
                "UPDATE deposits SET status='approved', approved_by=%s WHERE deposit_id=%s AND status='pending'",
                (approved_by, deposit_id))
            if cur.rowcount != 1:
                return None
            bonus = 0
            if cfg_get("deposit_bonus_enabled", "1") == "1":
                pct = get_deposit_bonus_percent(d["amount_mmk"])
                bonus = d["amount_mmk"] * pct // 100
            credit = _execute(conn,
                "UPDATE users SET mmk_balance = mmk_balance + %s, bonus_earned = bonus_earned + %s WHERE telegram_id=%s",
                (d["amount_mmk"] + bonus, bonus, d["telegram_id"]))
            if credit.rowcount != 1:
                raise RuntimeError(f"approve_deposit: user row missing for {d['telegram_id']}")
            return d, bonus


def reject_deposit_atomic(deposit_id: int, rejected_by: int, note=None):
    with _lock:
        with get_conn() as conn:
            d = _fetchone(conn, "SELECT * FROM deposits WHERE deposit_id=%s", (deposit_id,))
            if not d or d["status"] != "pending":
                return None
            cur = _execute(conn,
                "UPDATE deposits SET status='rejected', approved_by=%s, note=%s WHERE deposit_id=%s AND status='pending'",
                (rejected_by, note, deposit_id))
            if cur.rowcount != 1:
                return None
            return d


def is_binance_txn_used(txn_id: str) -> bool:
    with get_conn() as conn:
        row = _fetchone(conn, "SELECT 1 FROM deposits WHERE binance_txn_id=%s", (txn_id,))
        return row is not None


def create_binance_deposit_atomic(telegram_id, username, amount_mmk, txn_id, note=None):
    with _lock:
        with get_conn() as conn:
            existing = _fetchone(conn, "SELECT deposit_id FROM deposits WHERE binance_txn_id=%s", (txn_id,))
            if existing:
                return None
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO deposits (telegram_id, username, method, amount_mmk, status, note, binance_txn_id) "
                        "VALUES (%s, %s, %s, %s, 'approved', %s, %s) RETURNING deposit_id",
                        (telegram_id, username, "binance", amount_mmk, note, txn_id))
                    deposit_id = cur.fetchone()[0]
            except psycopg2.IntegrityError:
                conn.rollback()
                return None
            bonus = 0
            if cfg_get("deposit_bonus_enabled", "1") == "1":
                pct = get_deposit_bonus_percent(amount_mmk)
                bonus = amount_mmk * pct // 100
            credit = _execute(conn,
                "UPDATE users SET mmk_balance = mmk_balance + %s, bonus_earned = bonus_earned + %s WHERE telegram_id=%s",
                (amount_mmk + bonus, bonus, telegram_id))
            if credit.rowcount != 1:
                raise RuntimeError(f"binance credit: user row missing for {telegram_id}")
            return deposit_id, bonus


def list_deposits(status=None, limit=20, offset=0, telegram_id=None):
    with get_conn() as conn:
        if telegram_id and status:
            return _fetchall(conn,
                "SELECT * FROM deposits WHERE telegram_id=%s AND status=%s ORDER BY created_at DESC LIMIT %s OFFSET %s",
                (telegram_id, status, limit, offset))
        if telegram_id:
            return _fetchall(conn,
                "SELECT * FROM deposits WHERE telegram_id=%s ORDER BY created_at DESC LIMIT %s OFFSET %s",
                (telegram_id, limit, offset))
        if status:
            return _fetchall(conn,
                "SELECT * FROM deposits WHERE status=%s ORDER BY created_at DESC LIMIT %s OFFSET %s",
                (status, limit, offset))
        return _fetchall(conn,
            "SELECT * FROM deposits ORDER BY created_at DESC LIMIT %s OFFSET %s", (limit, offset))


def count_deposits(status=None) -> int:
    with get_conn() as conn:
        if status:
            row = _fetchone(conn, "SELECT COUNT(*) AS c FROM deposits WHERE status=%s", (status,))
        else:
            row = _fetchone(conn, "SELECT COUNT(*) AS c FROM deposits")
        return row["c"]


# ── analytics & dashboard ─────────────────────────────────────────────────────

def revenue_today() -> int:
    with get_conn() as conn:
        row = _fetchone(conn,
            "SELECT COALESCE(SUM(amount_mmk),0) AS s FROM orders "
            "WHERE created_at >= CURRENT_DATE AND status!='refunded'")
        return row["s"]


def revenue_total() -> int:
    with get_conn() as conn:
        row = _fetchone(conn, "SELECT COALESCE(SUM(amount_mmk),0) AS s FROM orders WHERE status!='refunded'")
        return row["s"]


def revenue_last_n_days(n: int = 7):
    with get_conn() as conn:
        return _fetchall(conn,
            "SELECT DATE(created_at) as day, COALESCE(SUM(amount_mmk),0) as total "
            "FROM orders WHERE created_at >= now() - make_interval(days => %s) AND status!='refunded' "
            "GROUP BY day ORDER BY day ASC", (n,))


def orders_last_n_days(n: int = 7) -> int:
    with get_conn() as conn:
        row = _fetchone(conn,
            "SELECT COUNT(*) AS c FROM orders WHERE created_at >= now() - make_interval(days => %s) AND status!='refunded'",
            (n,))
        return row["c"]


def new_users_last_n_days(n: int = 7) -> int:
    with get_conn() as conn:
        row = _fetchone(conn,
            "SELECT COUNT(*) AS c FROM users WHERE registered_at >= now() - make_interval(days => %s)", (n,))
        return row["c"]


def top_products(limit: int = 5):
    with get_conn() as conn:
        return _fetchall(conn,
            "SELECT product_name, COUNT(*) as cnt, SUM(amount_mmk) as revenue "
            "FROM orders WHERE status!='refunded' GROUP BY product_name ORDER BY cnt DESC LIMIT %s",
            (limit,))


def products_low_stock():
    threshold = int(cfg_get("low_stock_threshold", "3"))
    with get_conn() as conn:
        rows = _fetchall(conn,
            "SELECT p.name, COUNT(s.stock_id) as cnt "
            "FROM products p JOIN stock s ON p.product_id=s.product_id "
            "WHERE p.active=1 AND s.reserved_for IS NULL "
            "GROUP BY p.product_id "
            "HAVING COUNT(s.stock_id) > 0 AND COUNT(s.stock_id) <= %s",
            (threshold,))
        return [{"name": r["name"], "cnt": r["cnt"]} for r in rows]


def products_out_of_stock():
    with get_conn() as conn:
        rows = _fetchall(conn,
            "SELECT p.name FROM products p "
            "WHERE p.active=1 AND "
            "(SELECT COUNT(*) FROM stock s WHERE s.product_id=p.product_id AND s.reserved_for IS NULL) = 0")
        return [{"name": r["name"]} for r in rows]


# ── user state ────────────────────────────────────────────────────────────────

def state_get(telegram_id: int) -> dict:
    with get_conn() as conn:
        row = _fetchone(conn, "SELECT state_json, updated_at FROM user_state WHERE telegram_id=%s", (telegram_id,))
        if not row:
            return {}
        updated = row["updated_at"]
        if isinstance(updated, str):
            updated = datetime.fromisoformat(updated.replace("Z", "+00:00"))
        if _utcnow() - updated > timedelta(minutes=30):
            _execute(conn, "DELETE FROM user_state WHERE telegram_id=%s", (telegram_id,))
            return {}
        try:
            return json.loads(row["state_json"])
        except Exception:
            return {}


def state_set(telegram_id: int, data: dict):
    with get_conn() as conn:
        _execute(conn,
            "INSERT INTO user_state (telegram_id, state_json, updated_at) VALUES (%s, %s, now()) "
            "ON CONFLICT (telegram_id) DO UPDATE SET state_json=EXCLUDED.state_json, updated_at=now()",
            (telegram_id, json.dumps(data)))


def state_clear(telegram_id: int):
    with get_conn() as conn:
        _execute(conn, "DELETE FROM user_state WHERE telegram_id=%s", (telegram_id,))


def state_expire_all():
    with get_conn() as conn:
        _execute(conn,
            "DELETE FROM user_state WHERE updated_at < now() - INTERVAL '30 minutes'")


# ── giveaways ─────────────────────────────────────────────────────────────────

class _GiveawayNoStock(Exception):
    pass


def create_giveaway(product_id, title, description, winner_slots, end_time, created_by):
    with _lock:
        try:
            with get_conn() as conn:
                existing = _fetchone(conn, "SELECT giveaway_id FROM giveaways WHERE status='active' LIMIT 1")
                if existing:
                    return None
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO giveaways (product_id, title, description, winner_slots, end_time, status, created_by) "
                        "VALUES (%s, %s, %s, %s, %s, 'active', %s) RETURNING giveaway_id",
                        (product_id, title, description, winner_slots, end_time, created_by))
                    gid = cur.fetchone()[0]
                prow = _fetchone(conn, "SELECT fulfillment FROM products WHERE product_id=%s", (product_id,))
                mode = ((prow["fulfillment"] or "instant").strip().lower() if prow else "instant")
                if prow and mode == "instant" and winner_slots > 0:
                    rows = _fetchall(conn,
                        "SELECT stock_id FROM stock WHERE product_id=%s AND reserved_for IS NULL LIMIT %s",
                        (product_id, winner_slots))
                    if len(rows) < winner_slots:
                        raise _GiveawayNoStock()
                    code = f"giveaway_{gid}"
                    _executemany(conn,
                        "UPDATE stock SET reserved_for=%s WHERE stock_id=%s",
                        [(code, r["stock_id"]) for r in rows])
                return gid
        except _GiveawayNoStock:
            return -1


def get_active_giveaway():
    with get_conn() as conn:
        return _fetchone(conn,
            "SELECT * FROM giveaways WHERE status='active' ORDER BY created_at DESC LIMIT 1")


def get_giveaway(giveaway_id: int):
    with get_conn() as conn:
        return _fetchone(conn, "SELECT * FROM giveaways WHERE giveaway_id=%s", (giveaway_id,))


def list_giveaways(limit=50):
    with get_conn() as conn:
        return _fetchall(conn,
            "SELECT * FROM giveaways ORDER BY (status='active') DESC, created_at DESC LIMIT %s", (limit,))


def set_giveaway_status(giveaway_id: int, status: str, drawn_at=None, cancelled_at=None):
    with get_conn() as conn:
        _execute(conn,
            "UPDATE giveaways SET status=%s, drawn_at=COALESCE(%s, drawn_at), cancelled_at=COALESCE(%s, cancelled_at) "
            "WHERE giveaway_id=%s", (status, drawn_at, cancelled_at, giveaway_id))


def add_giveaway_entry(giveaway_id: int, telegram_id: int, username: str = None) -> bool:
    try:
        with get_conn() as conn:
            _execute(conn,
                "INSERT INTO giveaway_entries (giveaway_id, telegram_id, username) VALUES (%s, %s, %s) "
                "ON CONFLICT DO NOTHING",
                (giveaway_id, telegram_id, username or ""))
            with conn.cursor() as cur:
                cur.execute("SELECT 1 FROM giveaway_entries WHERE giveaway_id=%s AND telegram_id=%s",
                            (giveaway_id, telegram_id))
                return cur.fetchone() is not None
    except psycopg2.IntegrityError:
        return False


def has_entered(giveaway_id: int, telegram_id: int) -> bool:
    with get_conn() as conn:
        row = _fetchone(conn,
            "SELECT 1 FROM giveaway_entries WHERE giveaway_id=%s AND telegram_id=%s",
            (giveaway_id, telegram_id))
        return row is not None


def count_entries(giveaway_id: int) -> int:
    with get_conn() as conn:
        row = _fetchone(conn, "SELECT COUNT(*) AS c FROM giveaway_entries WHERE giveaway_id=%s", (giveaway_id,))
        return row["c"]


def list_entrants(giveaway_id: int):
    with get_conn() as conn:
        return _fetchall(conn,
            "SELECT telegram_id, username, entries FROM giveaway_entries WHERE giveaway_id=%s",
            (giveaway_id,))


def get_giveaway_entry_count(giveaway_id: int, telegram_id: int) -> int:
    with get_conn() as conn:
        row = _fetchone(conn,
            "SELECT entries FROM giveaway_entries WHERE giveaway_id=%s AND telegram_id=%s",
            (giveaway_id, telegram_id))
        return row["entries"] if row else 0


def add_giveaway_bonus_entries(giveaway_id: int, telegram_id: int, n: int) -> int:
    with get_conn() as conn:
        cur = _execute(conn,
            "UPDATE giveaway_entries SET entries = entries + %s WHERE giveaway_id=%s AND telegram_id=%s",
            (n, giveaway_id, telegram_id))
        if cur.rowcount != 1:
            return 0
        row = _fetchone(conn,
            "SELECT entries FROM giveaway_entries WHERE giveaway_id=%s AND telegram_id=%s",
            (giveaway_id, telegram_id))
        return row["entries"]


def record_giveaway_invite(giveaway_id: int, inviter_id: int, invitee_id: int) -> bool:
    if inviter_id == invitee_id:
        return False
    try:
        with get_conn() as conn:
            _execute(conn,
                "INSERT INTO giveaway_invites (giveaway_id, inviter_id, invitee_id) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
                (giveaway_id, inviter_id, invitee_id))
            return True
    except Exception:
        return False


def get_giveaway_invite_leaderboard(giveaway_id: int, limit: int = 5):
    with get_conn() as conn:
        return _fetchall(conn,
            "SELECT inviter_id, COUNT(*) AS c FROM giveaway_invites WHERE giveaway_id=%s GROUP BY inviter_id ORDER BY c DESC LIMIT %s",
            (giveaway_id, limit))


def count_giveaway_invites(giveaway_id: int, inviter_id: int) -> int:
    with get_conn() as conn:
        row = _fetchone(conn,
            "SELECT COUNT(*) AS c FROM giveaway_invites WHERE giveaway_id=%s AND inviter_id=%s",
            (giveaway_id, inviter_id))
        return row["c"]


def add_giveaway_winner(giveaway_id: int, telegram_id: int, username: str,
                        credential: str = None, delivered: int = 0, order_id: int = None) -> int:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO giveaway_winners (giveaway_id, telegram_id, username, credential, delivered, order_id) "
                "VALUES (%s, %s, %s, %s, %s, %s) RETURNING winner_id",
                (giveaway_id, telegram_id, username or "", credential, delivered, order_id))
            return cur.fetchone()[0]


def list_winners(giveaway_id: int):
    with get_conn() as conn:
        return _fetchall(conn,
            "SELECT * FROM giveaway_winners WHERE giveaway_id=%s ORDER BY winner_id ASC", (giveaway_id,))


def count_delivered_winners(giveaway_id: int) -> int:
    with get_conn() as conn:
        row = _fetchone(conn,
            "SELECT COUNT(*) AS c FROM giveaway_winners WHERE giveaway_id=%s AND delivered=1",
            (giveaway_id,))
        return row["c"]


def list_giveaways_needing_end_reminder():
    with get_conn() as conn:
        return _fetchall(conn,
            "SELECT * FROM giveaways WHERE status='active' AND end_time IS NOT NULL "
            "AND end_time <= now() AND end_reminded=0")


def mark_giveaway_reminded(giveaway_id: int):
    with get_conn() as conn:
        _execute(conn, "UPDATE giveaways SET end_reminded=1 WHERE giveaway_id=%s", (giveaway_id,))


# ── pending-deposit nudge ──────────────────────────────────────────────────────

def list_stale_pending_deposits(hours: int):
    with get_conn() as conn:
        return _fetchall(conn,
            "SELECT * FROM deposits WHERE status='pending' AND nudge_sent=0 "
            "AND created_at <= now() - make_interval(hours => %s)", (hours,))


def mark_deposit_nudged(deposit_id: int):
    with get_conn() as conn:
        _execute(conn, "UPDATE deposits SET nudge_sent=1 WHERE deposit_id=%s", (deposit_id,))


# ── gifts ─────────────────────────────────────────────────────────────────────

def reserve_stock(product_id: int, gift_code: str):
    with _lock:
        with get_conn() as conn:
            while True:
                row = _fetchone(conn,
                    "SELECT stock_id FROM stock WHERE product_id=%s AND reserved_for IS NULL LIMIT 1",
                    (product_id,))
                if not row:
                    return None
                cur = _execute(conn,
                    "UPDATE stock SET reserved_for=%s WHERE stock_id=%s AND reserved_for IS NULL",
                    (gift_code, row["stock_id"]))
                if cur.rowcount == 1:
                    return row["stock_id"]


def claim_reserved_stock(gift_code: str):
    with _lock:
        with get_conn() as conn:
            row = _fetchone(conn,
                "SELECT s.stock_id, s.product_id, s.credential, p.name AS pname "
                "FROM stock s LEFT JOIN products p ON p.product_id = s.product_id "
                "WHERE s.reserved_for=%s LIMIT 1", (gift_code,))
            if not row:
                return None
            _execute(conn, "DELETE FROM stock WHERE stock_id=%s", (row["stock_id"],))
        _mirror_sold(row["stock_id"], row["product_id"], row["pname"] or "", row["credential"],
                     None, _utcnow_iso())
        return row["stock_id"], row["credential"]


def release_stock_reservation(stock_id: int):
    with get_conn() as conn:
        _execute(conn, "UPDATE stock SET reserved_for=NULL WHERE stock_id=%s", (stock_id,))


# ── lucky draw ────────────────────────────────────────────────────────────────

class _LdNoStock(Exception):
    pass


def ld_cap_for(total_slots: int) -> int:
    return 1 if total_slots <= 10 else 3


def create_ld_round(product_id, product_name, prize_mode, prize_value,
                    entry_price, total_slots, deadline, created_by,
                    seed, seed_hash):
    with _lock:
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO ld_rounds (product_id, product_name, prize_mode, prize_value, "
                        "entry_price, total_slots, deadline, status, seed, seed_hash, created_by) "
                        "VALUES (%s, %s, %s, %s, %s, %s, %s, 'open', %s, %s, %s) RETURNING round_id",
                        (product_id, product_name, prize_mode, prize_value,
                         entry_price, total_slots, deadline, seed, seed_hash, created_by))
                    rid = cur.fetchone()[0]
                if prize_mode == "instant":
                    row = _fetchone(conn,
                        "SELECT stock_id FROM stock WHERE product_id=%s AND reserved_for IS NULL LIMIT 1",
                        (product_id,))
                    if not row:
                        raise _LdNoStock()
                    _execute(conn,
                        "UPDATE stock SET reserved_for=%s WHERE stock_id=%s",
                        (f"ld_{rid}", row["stock_id"]))
                return rid
        except _LdNoStock:
            return -1


def get_ld_round(round_id: int):
    with get_conn() as conn:
        return _fetchone(conn, "SELECT * FROM ld_rounds WHERE round_id=%s", (round_id,))


def list_ld_rounds(status=None, limit=50):
    with get_conn() as conn:
        if status:
            return _fetchall(conn,
                "SELECT * FROM ld_rounds WHERE status=%s ORDER BY round_id DESC LIMIT %s",
                (status, limit))
        return _fetchall(conn, "SELECT * FROM ld_rounds ORDER BY round_id DESC LIMIT %s", (limit,))


def ld_open_round_for_product(product_id: int):
    with get_conn() as conn:
        return _fetchone(conn,
            "SELECT * FROM ld_rounds WHERE product_id=%s AND status='open' ORDER BY round_id LIMIT 1",
            (product_id,))


def ld_entries(round_id: int):
    with get_conn() as conn:
        return _fetchall(conn, "SELECT * FROM ld_entries WHERE round_id=%s ORDER BY slot_no", (round_id,))


def ld_entry_count(round_id: int) -> int:
    with get_conn() as conn:
        row = _fetchone(conn, "SELECT COUNT(*) AS c FROM ld_entries WHERE round_id=%s", (round_id,))
        return row["c"]


def ld_user_entries(round_id: int, telegram_id: int):
    with get_conn() as conn:
        return _fetchall(conn,
            "SELECT * FROM ld_entries WHERE round_id=%s AND telegram_id=%s ORDER BY slot_no",
            (round_id, telegram_id))


def ld_user_round_entries(telegram_id: int, limit=50):
    with get_conn() as conn:
        return _fetchall(conn,
            "SELECT e.*, r.product_name, r.prize_mode, r.prize_value, r.entry_price, "
            "r.total_slots, r.status AS round_status, r.winner_id "
            "FROM ld_entries e JOIN ld_rounds r ON r.round_id = e.round_id "
            "WHERE e.telegram_id=%s ORDER BY e.entry_id DESC LIMIT %s",
            (telegram_id, limit))


def ld_buy_slot(round_id: int, telegram_id: int, username: str) -> dict:
    with _lock:
        with get_conn() as conn:
            r = _fetchone(conn, "SELECT * FROM ld_rounds WHERE round_id=%s", (round_id,))
            if not r or r["status"] != "open":
                return {"error": "closed"}
            mine = _fetchone(conn,
                "SELECT COUNT(*) AS c FROM ld_entries WHERE round_id=%s AND telegram_id=%s",
                (round_id, telegram_id))
            if mine["c"] >= ld_cap_for(r["total_slots"]):
                return {"error": "cap"}
            total = _fetchone(conn, "SELECT COUNT(*) AS c FROM ld_entries WHERE round_id=%s", (round_id,))
            if total["c"] >= r["total_slots"]:
                return {"error": "full"}
            cur = _execute(conn,
                "UPDATE users SET mmk_balance = mmk_balance - %s WHERE telegram_id=%s AND mmk_balance >= %s",
                (r["entry_price"], telegram_id, r["entry_price"]))
            if cur.rowcount != 1:
                return {"error": "insufficient"}
            slot_no = total["c"] + 1
            with conn.cursor() as cur2:
                cur2.execute(
                    "INSERT INTO ld_entries (round_id, telegram_id, username, slot_no) VALUES (%s, %s, %s, %s)",
                    (round_id, telegram_id, username, slot_no))
            filled = slot_no >= r["total_slots"]
            if filled:
                _execute(conn,
                    "UPDATE ld_rounds SET status='drawing' WHERE round_id=%s AND status='open'",
                    (round_id,))
            bal = _fetchone(conn, "SELECT mmk_balance FROM users WHERE telegram_id=%s", (telegram_id,))
            return {"ok": True, "slot_no": slot_no, "filled": filled, "balance": bal["mmk_balance"]}


def ld_finish_draw(round_id: int, winner_id: int, winner_slot: int) -> bool:
    with _lock:
        with get_conn() as conn:
            cur = _execute(conn,
                "UPDATE ld_rounds SET status='completed', winner_id=%s, winner_slot=%s, drawn_at=now() "
                "WHERE round_id=%s AND status='drawing'",
                (winner_id, winner_slot, round_id))
            return cur.rowcount == 1


def ld_refund_round(round_id: int, to_status: str):
    with _lock:
        with get_conn() as conn:
            r = _fetchone(conn, "SELECT * FROM ld_rounds WHERE round_id=%s", (round_id,))
            if not r or r["status"] not in ("open", "drawing"):
                return None
            cur = _execute(conn,
                "UPDATE ld_rounds SET status=%s WHERE round_id=%s AND status IN ('open','drawing')",
                (to_status, round_id))
            if cur.rowcount != 1:
                return None
            rows = _fetchall(conn,
                "SELECT telegram_id, COUNT(*) AS n FROM ld_entries WHERE round_id=%s GROUP BY telegram_id",
                (round_id,))
            refunds = []
            for row in rows:
                amt = row["n"] * r["entry_price"]
                _execute(conn,
                    "UPDATE users SET mmk_balance = mmk_balance + %s WHERE telegram_id=%s",
                    (amt, row["telegram_id"]))
                refunds.append((row["telegram_id"], amt, row["n"]))
            _execute(conn, "UPDATE stock SET reserved_for=NULL WHERE reserved_for=%s", (f"ld_{round_id}",))
            return refunds


def release_reservations(code: str) -> int:
    with get_conn() as conn:
        cur = _execute(conn, "UPDATE stock SET reserved_for=NULL WHERE reserved_for=%s", (code,))
        return cur.rowcount


def _gift_code_exists(code: str) -> bool:
    with get_conn() as conn:
        row = _fetchone(conn, "SELECT 1 FROM gifts WHERE code=%s", (code,))
        return row is not None


def mint_gift_code() -> str:
    for _ in range(10):
        code = "".join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(8))
        if not _gift_code_exists(code):
            return code
    raise RuntimeError("could not mint gift code")


def create_gift(code: str, product_id: int, product_name: str, stock_id: int, amount_mmk: int,
                sender_id: int, sender_name: str, expiry_days: int = 30):
    with get_conn() as conn:
        _execute(conn,
            "INSERT INTO gifts (code, product_id, product_name, stock_id, amount_mmk, sender_id, sender_name, status, expires_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, 'unclaimed', now() + INTERVAL '%s days')",
            (code, product_id, product_name, stock_id, amount_mmk, sender_id, sender_name, int(expiry_days)))


def add_total_spent(telegram_id: int, amount_mmk: int):
    with get_conn() as conn:
        _execute(conn, "UPDATE users SET total_spent = total_spent + %s WHERE telegram_id=%s",
                 (amount_mmk, telegram_id))


def sub_total_spent(telegram_id: int, amount_mmk: int):
    with get_conn() as conn:
        _execute(conn, "UPDATE users SET total_spent = GREATEST(0, total_spent - %s) WHERE telegram_id=%s",
                 (amount_mmk, telegram_id))


def get_gift_by_code(code: str):
    with get_conn() as conn:
        return _fetchone(conn, "SELECT * FROM gifts WHERE code=%s", (code.strip().upper(),))


def claim_gift(code: str, recipient_id: int):
    with _lock:
        with get_conn() as conn:
            g = _fetchone(conn, "SELECT * FROM gifts WHERE code=%s", (code.strip().upper(),))
            if not g or g["status"] != "unclaimed":
                return None
            if g["sender_id"] == recipient_id:
                return None
            cur = _execute(conn,
                "UPDATE gifts SET status='claimed', recipient_id=%s, claimed_at=now() "
                "WHERE gift_id=%s AND status='unclaimed'",
                (recipient_id, g["gift_id"]))
            if cur.rowcount != 1:
                return None
            return g


def release_expired_gifts() -> int:
    with get_conn() as conn:
        rows = _fetchall(conn,
            "SELECT gift_id, stock_id FROM gifts WHERE status='unclaimed' AND expires_at <= now()")
        for r in rows:
            _execute(conn, "UPDATE gifts SET status='expired' WHERE gift_id=%s", (r["gift_id"],))
            if r["stock_id"]:
                _execute(conn, "UPDATE stock SET reserved_for=NULL WHERE stock_id=%s", (r["stock_id"],))
        return len(rows)


def list_user_gifts(telegram_id: int, limit: int = 10):
    with get_conn() as conn:
        return _fetchall(conn,
            "SELECT * FROM gifts WHERE sender_id=%s ORDER BY gift_id DESC LIMIT %s",
            (telegram_id, limit))


# ── deposit bonus ─────────────────────────────────────────────────────────────

def get_deposit_bonus_percent(amount_mmk: int) -> int:
    try:
        tiers = json.loads(cfg_get("deposit_bonus_tiers", "[[0, 3], [50000, 5], [200000, 8]]") or "[]")
    except Exception:
        tiers = []
    pct = 0
    for min_amt, p in tiers:
        if amount_mmk >= int(min_amt):
            pct = int(p)
    return pct


# ── VIP tiers ─────────────────────────────────────────────────────────────────

def get_vip_tiers():
    try:
        tiers = json.loads(cfg_get("vip_tiers", "[]") or "[]")
    except Exception:
        tiers = []
    return sorted(tiers, key=lambda t: int(t.get("min", 0)))


def get_vip_tier(total_spent: int):
    if cfg_get("vip_enabled", "1") != "1":
        return None, 0
    name, disc = None, 0
    for t in get_vip_tiers():
        if total_spent >= int(t.get("min", 0)):
            name, disc = t.get("name"), int(t.get("discount", 0))
    return name, disc


def get_user_vip_tier(telegram_id: int):
    with get_conn() as conn:
        row = _fetchone(conn, "SELECT total_spent FROM users WHERE telegram_id=%s", (telegram_id,))
    spent = row["total_spent"] if row else 0
    return get_vip_tier(spent)


# ── CDK activations ───────────────────────────────────────────────────────────

def cdk_activation_create(user_id: int, product_id: str, product_name: str, cdk_key: str) -> int:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO cdk_activations (user_id, product_id, product_name, cdk_key, status) "
                "VALUES (%s, %s, %s, %s, 'sending') RETURNING id",
                (user_id, product_id, product_name, cdk_key))
            return cur.fetchone()[0]


def cdk_activation_update(act_id: int, status: str = None, provider_ref: str = None,
                          message: str = None, duration_ms: int = None):
    sets, vals = [], []
    if status is not None:
        sets.append("status=%s")
        vals.append(status)
    if provider_ref is not None:
        sets.append("provider_ref=%s")
        vals.append(provider_ref)
    if message is not None:
        sets.append("message=%s")
        vals.append(message)
    if duration_ms is not None:
        sets.append("duration_ms=%s")
        vals.append(duration_ms)
    if not sets:
        return
    sets.append("updated_at=now()")
    vals.append(act_id)
    with get_conn() as conn:
        _execute(conn, f"UPDATE cdk_activations SET {', '.join(sets)} WHERE id=%s", vals)


def cdk_activation_list(user_id: int, limit: int = 10):
    with get_conn() as conn:
        return _fetchall(conn,
            "SELECT * FROM cdk_activations WHERE user_id=%s ORDER BY id DESC LIMIT %s",
            (user_id, limit))


def cdk_activation_get(act_id: int, user_id: int):
    with get_conn() as conn:
        return _fetchone(conn,
            "SELECT * FROM cdk_activations WHERE id=%s AND user_id=%s", (act_id, user_id))


def cdk_activation_find_key(user_id: int, cdk_key: str):
    with get_conn() as conn:
        return _fetchone(conn,
            "SELECT * FROM cdk_activations WHERE user_id=%s AND cdk_key=%s ORDER BY id DESC LIMIT 1",
            (user_id, cdk_key.strip()))


def cdk_activation_reap():
    with get_conn() as conn:
        cur = _execute(conn,
            "UPDATE cdk_activations SET status='interrupted', updated_at=now() "
            "WHERE status NOT IN ('success','failed','pending','interrupted')")
        return cur.rowcount


def cdk_activation_pending():
    with get_conn() as conn:
        return _fetchall(conn,
            "SELECT * FROM cdk_activations WHERE status='pending' ORDER BY id ASC")


# ── AM Pro subscriptions ──────────────────────────────────────────────────────

def am_get_subscription(telegram_id: int):
    with get_conn() as conn:
        return _fetchone(conn,
            "SELECT * FROM am_subscriptions WHERE telegram_id=%s AND status='active' "
            "AND expires_at > now() ORDER BY expires_at DESC LIMIT 1", (telegram_id,))


def am_days_left(telegram_id: int) -> int:
    sub = am_get_subscription(telegram_id)
    if not sub:
        return 0
    with get_conn() as conn:
        row = _fetchone(conn,
            "SELECT GREATEST(0, CEIL(EXTRACT(EPOCH FROM (expires_at - now())) / 86400))::INT AS d "
            "FROM am_subscriptions WHERE subscription_id=%s",
            (sub["subscription_id"],))
    return row["d"] if row else 0


def am_create_subscription(telegram_id: int, username: str, amount_paid: int, days: int = 30) -> int:
    with get_conn() as conn:
        existing = _fetchone(conn,
            "SELECT * FROM am_subscriptions WHERE telegram_id=%s AND status='active' "
            "AND expires_at > now() ORDER BY expires_at DESC LIMIT 1", (telegram_id,))
        if existing:
            _execute(conn,
                "UPDATE am_subscriptions SET expires_at=expires_at + INTERVAL '%s days', amount_paid=amount_paid+%s "
                "WHERE subscription_id=%s", (days, amount_paid, existing["subscription_id"]))
            return existing["subscription_id"]
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO am_subscriptions (telegram_id, username, expires_at, amount_paid) "
                "VALUES (%s, %s, now() + INTERVAL '%s days', %s) RETURNING subscription_id",
                (telegram_id, username, days, amount_paid))
            return cur.fetchone()[0]


def am_daily_used(telegram_id: int) -> int:
    with get_conn() as conn:
        row = _fetchone(conn,
            "SELECT count FROM am_usage WHERE telegram_id=%s AND day=CURRENT_DATE", (telegram_id,))
    return row["count"] if row else 0


def am_daily_remaining(telegram_id: int, cap: int = None) -> int:
    if cap is None:
        cap = int(cfg_get("am_daily_cap", "5") or 5)
    return max(0, cap - am_daily_used(telegram_id))


def am_bump_usage(telegram_id: int):
    with get_conn() as conn:
        _execute(conn,
            "INSERT INTO am_usage (telegram_id, day, count) VALUES (%s, CURRENT_DATE, 1) "
            "ON CONFLICT (telegram_id, day) DO UPDATE SET count=count+1",
            (telegram_id,))


def am_list_active(limit: int = 50):
    with get_conn() as conn:
        return _fetchall(conn,
            "SELECT * FROM am_subscriptions WHERE status='active' AND expires_at > now() "
            "ORDER BY expires_at LIMIT %s", (limit,))
