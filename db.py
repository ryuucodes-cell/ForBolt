"""
Database layer — SQLite backend (file-based, no server, no subscription).

Every function preserves the same signature and return shape as the previous
Postgres version so the rest of the bot code does not need changes. Rows are
returned as plain dicts (sqlite3.Row).
"""

import os
import re
import json
import secrets
import string
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from contextlib import contextmanager
from dotenv import load_dotenv

load_dotenv()

# ── connection ───────────────────────────────────────────────────────────────

DB_PATH = os.environ.get("DB_PATH", os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "shop.db"))
DB_SOLD_PATH = ""  # legacy compat — sold_stock is in the same DB

os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

_lock = threading.Lock()

def _connect():
    conn = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn

@contextmanager
def get_conn():
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
    conn = _connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

def _utcnow() -> datetime:
    return datetime.now(timezone.utc)

def _utcnow_iso() -> str:
    return _utcnow().strftime("%Y-%m-%d %H:%M:%S")

def _fetchone(conn, sql, params=None):
    cur = conn.execute(sql, params or [])
    row = cur.fetchone()
    return dict(row) if row else None

def _fetchall(conn, sql, params=None):
    cur = conn.execute(sql, params or [])
    return [dict(r) for r in cur.fetchall()]

def _execute(conn, sql, params=None):
    cur = conn.execute(sql, params or [])
    return cur

def _executemany(conn, sql, params_list):
    cur = conn.executemany(sql, params_list)
    return cur

def _lastrowid(conn, table, id_col="id"):
    return conn.execute(f"SELECT lastval()").fetchone()[0]

# ── init ─────────────────────────────────────────────────────────────────────

def init_db():
    """Create all tables if they don't exist, and seed config defaults."""
    with get_conn() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS config (
            key   TEXT PRIMARY KEY,
            value TEXT
        );

        CREATE TABLE IF NOT EXISTS users (
            telegram_id        INTEGER PRIMARY KEY,
            username           TEXT DEFAULT '',
            mmk_balance        INTEGER NOT NULL DEFAULT 0,
            total_spent        INTEGER NOT NULL DEFAULT 0,
            notify_opt_out     INTEGER NOT NULL DEFAULT 0,
            registered_at      TEXT NOT NULL DEFAULT (datetime('now')),
            bonus_earned       INTEGER NOT NULL DEFAULT 0,
            is_reseller        INTEGER NOT NULL DEFAULT 0,
            reseller_since     TEXT,
            last_reseller_purchase TEXT
        );

        CREATE TABLE IF NOT EXISTS products (
            product_id         INTEGER PRIMARY KEY AUTOINCREMENT,
            name               TEXT NOT NULL,
            category           TEXT NOT NULL DEFAULT '',
            description        TEXT NOT NULL DEFAULT '',
            price_mmk          INTEGER NOT NULL,
            credential_format  TEXT NOT NULL DEFAULT '',
            active             INTEGER NOT NULL DEFAULT 1,
            discount_pct       INTEGER NOT NULL DEFAULT 0,
            discount_until     TEXT,
            fulfillment        TEXT NOT NULL DEFAULT 'instant',
            manual_fields      TEXT NOT NULL DEFAULT '',
            reseller_price_mmk INTEGER,
            emoji              TEXT NOT NULL DEFAULT '',
            emoji_id           TEXT NOT NULL DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS stock (
            stock_id    INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id  INTEGER NOT NULL REFERENCES products(product_id) ON DELETE CASCADE,
            credential  TEXT NOT NULL,
            status      TEXT NOT NULL DEFAULT 'available',
            sold_to     INTEGER,
            sold_at     TEXT,
            reserved_for TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_stock_pid ON stock(product_id);

        CREATE TABLE IF NOT EXISTS sold_stock (
            stock_id     INTEGER PRIMARY KEY,
            product_id   INTEGER NOT NULL,
            product_name TEXT NOT NULL DEFAULT '',
            credential   TEXT NOT NULL,
            sold_to      INTEGER,
            sold_at      TEXT
        );

        CREATE TABLE IF NOT EXISTS orders (
            order_id       INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id    INTEGER NOT NULL,
            username       TEXT,
            product_id     INTEGER NOT NULL,
            product_name   TEXT NOT NULL,
            amount_mmk     INTEGER NOT NULL,
            payment_method TEXT NOT NULL DEFAULT 'balance',
            credential     TEXT NOT NULL DEFAULT '',
            status         TEXT NOT NULL DEFAULT 'completed',
            created_at     TEXT NOT NULL DEFAULT (datetime('now')),
            manual_info    TEXT NOT NULL DEFAULT '',
            fulfillment_mode TEXT NOT NULL DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS idx_orders_uid ON orders(telegram_id);

        CREATE TABLE IF NOT EXISTS deposits (
            deposit_id       INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id      INTEGER NOT NULL,
            username         TEXT,
            method           TEXT NOT NULL,
            amount_mmk       INTEGER NOT NULL,
            screenshot_file_id TEXT,
            status           TEXT NOT NULL DEFAULT 'pending',
            approved_by      INTEGER,
            note             TEXT,
            binance_txn_id   TEXT UNIQUE,
            nudge_sent       INTEGER NOT NULL DEFAULT 0,
            created_at       TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_deposits_uid_status ON deposits(telegram_id, status);

        CREATE TABLE IF NOT EXISTS user_state (
            telegram_id  INTEGER PRIMARY KEY,
            state_json   TEXT NOT NULL DEFAULT '{}',
            updated_at   TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_user_state_updated ON user_state(updated_at);

        CREATE TABLE IF NOT EXISTS cdk_activations (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id       INTEGER NOT NULL,
            product_id    TEXT NOT NULL,
            product_name  TEXT NOT NULL,
            cdk_key       TEXT NOT NULL,
            status        TEXT NOT NULL DEFAULT 'key_ok',
            provider_ref  TEXT NOT NULL DEFAULT '',
            message       TEXT NOT NULL DEFAULT '',
            duration_ms   INTEGER NOT NULL DEFAULT 0,
            created_at    TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at    TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_cdk_act_user ON cdk_activations(user_id, id DESC);

        CREATE TABLE IF NOT EXISTS giveaways (
            giveaway_id   INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id    INTEGER NOT NULL,
            title         TEXT NOT NULL,
            description   TEXT NOT NULL DEFAULT '',
            winner_slots  INTEGER NOT NULL,
            end_time      TEXT,
            status        TEXT NOT NULL DEFAULT 'active',
            created_by    INTEGER NOT NULL,
            created_at    TEXT NOT NULL DEFAULT (datetime('now')),
            drawn_at      TEXT,
            cancelled_at  TEXT,
            end_reminded  INTEGER NOT NULL DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_giveaways_status ON giveaways(status);

        CREATE TABLE IF NOT EXISTS giveaway_entries (
            entry_id     INTEGER PRIMARY KEY AUTOINCREMENT,
            giveaway_id  INTEGER NOT NULL REFERENCES giveaways(giveaway_id) ON DELETE CASCADE,
            telegram_id  INTEGER NOT NULL,
            username     TEXT,
            entries      INTEGER NOT NULL DEFAULT 1,
            entered_at   TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(giveaway_id, telegram_id)
        );
        CREATE INDEX IF NOT EXISTS idx_gw_entries_gid ON giveaway_entries(giveaway_id);

        CREATE TABLE IF NOT EXISTS giveaway_winners (
            winner_id    INTEGER PRIMARY KEY AUTOINCREMENT,
            giveaway_id  INTEGER NOT NULL REFERENCES giveaways(giveaway_id) ON DELETE CASCADE,
            telegram_id  INTEGER NOT NULL,
            username     TEXT,
            credential   TEXT,
            delivered    INTEGER NOT NULL DEFAULT 0,
            order_id     INTEGER,
            won_at       TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_gw_winners_gid ON giveaway_winners(giveaway_id);

        CREATE TABLE IF NOT EXISTS giveaway_invites (
            invite_id    INTEGER PRIMARY KEY AUTOINCREMENT,
            giveaway_id  INTEGER NOT NULL REFERENCES giveaways(giveaway_id) ON DELETE CASCADE,
            inviter_id   INTEGER NOT NULL,
            invitee_id   INTEGER NOT NULL,
            created_at   TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(giveaway_id, invitee_id)
        );
        CREATE INDEX IF NOT EXISTS idx_giveaway_invites_g ON giveaway_invites(giveaway_id, inviter_id);

        CREATE TABLE IF NOT EXISTS gifts (
            gift_id      INTEGER PRIMARY KEY AUTOINCREMENT,
            code         TEXT NOT NULL UNIQUE,
            product_id   INTEGER NOT NULL,
            product_name TEXT NOT NULL,
            stock_id     INTEGER,
            amount_mmk   INTEGER NOT NULL DEFAULT 0,
            sender_id    INTEGER NOT NULL,
            sender_name  TEXT NOT NULL DEFAULT '',
            recipient_id INTEGER,
            status       TEXT NOT NULL DEFAULT 'unclaimed',
            created_at   TEXT NOT NULL DEFAULT (datetime('now')),
            expires_at   TEXT NOT NULL,
            claimed_at   TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_gifts_code ON gifts(code);

        CREATE TABLE IF NOT EXISTS ld_rounds (
            round_id     INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id   INTEGER NOT NULL,
            product_name TEXT NOT NULL,
            prize_mode   TEXT NOT NULL DEFAULT 'instant',
            prize_value  INTEGER NOT NULL DEFAULT 0,
            entry_price  INTEGER NOT NULL,
            total_slots  INTEGER NOT NULL,
            deadline     TEXT,
            status       TEXT NOT NULL DEFAULT 'open',
            seed         TEXT NOT NULL DEFAULT '',
            seed_hash    TEXT NOT NULL DEFAULT '',
            winner_id    INTEGER,
            winner_slot  INTEGER,
            created_by   INTEGER NOT NULL DEFAULT 0,
            created_at   TEXT NOT NULL DEFAULT (datetime('now')),
            drawn_at     TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_ld_rounds_status ON ld_rounds(status);

        CREATE TABLE IF NOT EXISTS ld_entries (
            entry_id    INTEGER PRIMARY KEY AUTOINCREMENT,
            round_id    INTEGER NOT NULL REFERENCES ld_rounds(round_id) ON DELETE CASCADE,
            telegram_id INTEGER NOT NULL,
            username    TEXT NOT NULL DEFAULT '',
            slot_no     INTEGER NOT NULL,
            created_at  TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(round_id, slot_no)
        );
        CREATE INDEX IF NOT EXISTS idx_ld_entries_round ON ld_entries(round_id);
        CREATE INDEX IF NOT EXISTS idx_ld_entries_user ON ld_entries(telegram_id);

        CREATE TABLE IF NOT EXISTS reseller_keys (
            key_id      INTEGER PRIMARY KEY AUTOINCREMENT,
            code        TEXT NOT NULL UNIQUE,
            created_by  INTEGER NOT NULL,
            note        TEXT,
            used_by     INTEGER,
            used_at     TEXT,
            revoked     INTEGER NOT NULL DEFAULT 0,
            created_at  TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_reseller_keys_code ON reseller_keys(code);

        CREATE TABLE IF NOT EXISTS reseller_requests (
            request_id   INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id  INTEGER NOT NULL,
            username     TEXT,
            message      TEXT,
            status       TEXT NOT NULL DEFAULT 'pending',
            handled_by   INTEGER,
            handled_at   TEXT,
            created_at   TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_reseller_reqs_status ON reseller_requests(status);

        CREATE TABLE IF NOT EXISTS order_reports (
            report_id    INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id     INTEGER NOT NULL,
            telegram_id  INTEGER NOT NULL,
            username     TEXT,
            message      TEXT,
            status       TEXT NOT NULL DEFAULT 'pending',
            handled_by   INTEGER,
            handled_at   TEXT,
            created_at   TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_order_reports_status ON order_reports(status);
        CREATE INDEX IF NOT EXISTS idx_order_reports_order ON order_reports(order_id);

        CREATE TABLE IF NOT EXISTS am_subscriptions (
            subscription_id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id  INTEGER NOT NULL,
            username     TEXT,
            started_at   TEXT NOT NULL DEFAULT (datetime('now')),
            expires_at   TEXT NOT NULL,
            status       TEXT NOT NULL DEFAULT 'active',
            amount_paid  INTEGER NOT NULL DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_am_subs_user ON am_subscriptions(telegram_id, status);

        CREATE TABLE IF NOT EXISTS am_usage (
            telegram_id INTEGER NOT NULL,
            day         TEXT NOT NULL DEFAULT (date('now')),
            count       INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (telegram_id, day)
        );
        """)

        # seed config defaults
        defaults = [
            ('shop_name', 'StockBot Shop'),
            ('welcome_message', 'Welcome! 👋 Have a look around — everything you buy is delivered instantly.'),
            ('admin_chat_id', ''),
            ('admin_ids', ''),
            ('support_url', ''),
            ('admin_username', ''),
            ('usd_to_mmk_rate', '4800'),
            ('low_stock_threshold', '3'),
            ('kbzpay_enabled', '0'),
            ('kbzpay_phone', ''),
            ('kbzpay_name', ''),
            ('wavepay_enabled', '0'),
            ('wavepay_phone', ''),
            ('wavepay_name', ''),
            ('cbpay_enabled', '0'),
            ('cbpay_phone', ''),
            ('cbpay_name', ''),
            ('uabpay_enabled', '0'),
            ('uabpay_phone', ''),
            ('uabpay_name', ''),
            ('ayapay_enabled', '0'),
            ('ayapay_phone', ''),
            ('ayapay_name', ''),
            ('maintenance_mode', '0'),
            ('channel_url', ''),
            ('channel_username', ''),
            ('channel_url2', ''),
            ('channel_username2', ''),
            ('channel_gate_enabled', '0'),
            ('log_channel_id', ''),
            ('reseller_inactivity_days', '30'),
            ('deposit_nudge_hours', '6'),
            ('shop_tagline', 'Digital products, delivered instantly'),
            ('shop_subtagline', 'Fast · Safe · Simple'),
            ('fulfillment_eta', 'usually within 24 hours'),
            ('giveaway_invite_enabled', '1'),
            ('giveaway_invite_entries', '1'),
            ('deposit_bonus_enabled', '1'),
            ('deposit_bonus_tiers', '[[0, 3], [50000, 5], [200000, 8]]'),
            ('vip_enabled', '1'),
            ('vip_tiers', '[{"name": "Silver", "min": 50000, "discount": 0}, {"name": "Gold", "min": 200000, "discount": 3}, {"name": "Diamond", "min": 500000, "discount": 5}]'),
            ('gift_expiry_days', '30'),
            ('am_enabled', '0'),
            ('am_monthly_price', '0'),
            ('am_daily_cap', '5'),
            ('currency_mode', 'mmk_native'),
        ]
        conn.executemany(
            "INSERT OR IGNORE INTO config (key, value) VALUES (?, ?)",
            defaults,
        )


def init_sold_db():
    """No-op — sold_stock is a table in the same SQLite database."""
    pass


# ── config ───────────────────────────────────────────────────────────────────

def cfg_get(key: str, default=None):
    with get_conn() as conn:
        row = _fetchone(conn, "SELECT value FROM config WHERE key=?", (key,))
        return row["value"] if row else default

def cfg_set(key: str, value):
    with get_conn() as conn:
        _execute(conn, """
            INSERT INTO config (key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value
        """, (key, str(value)))

def cfg_all() -> dict:
    with get_conn() as conn:
        rows = _fetchall(conn, "SELECT key, value FROM config")
        return {r["key"]: r["value"] for r in rows}


# ── users ────────────────────────────────────────────────────────────────────

def ensure_user(telegram_id: int, username: str = None):
    with get_conn() as conn:
        _execute(conn, """
            INSERT INTO users (telegram_id, username) VALUES (?, ?)
            ON CONFLICT(telegram_id) DO NOTHING
        """, (telegram_id, username or ""))
        if username:
            _execute(conn, "UPDATE users SET username=? WHERE telegram_id=?",
                     (username, telegram_id))

def get_user(telegram_id: int):
    with get_conn() as conn:
        return _fetchone(conn, "SELECT * FROM users WHERE telegram_id=?", (telegram_id,))

def get_balance(telegram_id: int) -> int:
    u = get_user(telegram_id)
    return u["mmk_balance"] if u else 0

def adjust_balance(telegram_id: int, delta: int):
    with get_conn() as conn:
        _execute(conn, "UPDATE users SET mmk_balance = mmk_balance + ? WHERE telegram_id=?",
                 (delta, telegram_id))

def adjust_balance_clamped(telegram_id: int, delta: int) -> int:
    with _lock:
        with get_conn() as conn:
            _execute(conn,
                "UPDATE users SET mmk_balance = MAX(0, mmk_balance + ?) WHERE telegram_id=?",
                (delta, telegram_id))
            row = _fetchone(conn, "SELECT mmk_balance FROM users WHERE telegram_id=?", (telegram_id,))
            return row["mmk_balance"] if row else 0

def deduct_balance_atomic(telegram_id: int, amount: int) -> bool:
    with _lock:
        with get_conn() as conn:
            cur = _execute(conn,
                "UPDATE users SET mmk_balance = mmk_balance - ? "
                "WHERE telegram_id=? AND mmk_balance >= ?",
                (amount, telegram_id, amount))
            return cur.rowcount == 1

def list_users(offset=0, limit=20):
    with get_conn() as conn:
        return _fetchall(conn,
            "SELECT * FROM users ORDER BY registered_at DESC LIMIT ? OFFSET ?",
            (limit, offset))

def set_notify_opt_out(telegram_id: int, value: bool):
    with get_conn() as conn:
        _execute(conn, "UPDATE users SET notify_opt_out=? WHERE telegram_id=?",
                 (1 if value else 0, telegram_id))

def get_notify_opt_out(telegram_id: int) -> bool:
    with get_conn() as conn:
        row = _fetchone(conn, "SELECT notify_opt_out FROM users WHERE telegram_id=?", (telegram_id,))
        return bool(row["notify_opt_out"]) if row else False

def count_users() -> int:
    with get_conn() as conn:
        row = _fetchone(conn, "SELECT COUNT(*) AS c FROM users")
        return row["c"]


# ── resellers ────────────────────────────────────────────────────────────────

def is_reseller(telegram_id: int) -> bool:
    with get_conn() as conn:
        row = _fetchone(conn, "SELECT is_reseller FROM users WHERE telegram_id=?", (telegram_id,))
        return bool(row["is_reseller"]) if row else False

def set_reseller(telegram_id: int, value: bool):
    with get_conn() as conn:
        if value:
            _execute(conn,
                "UPDATE users SET is_reseller=1, reseller_since=datetime('now'), last_reseller_purchase=datetime('now') "
                "WHERE telegram_id=?", (telegram_id,))
        else:
            _execute(conn,
                "UPDATE users SET is_reseller=0, reseller_since=NULL, last_reseller_purchase=NULL "
                "WHERE telegram_id=?", (telegram_id,))

def touch_reseller_activity(telegram_id: int):
    with get_conn() as conn:
        _execute(conn,
            "UPDATE users SET last_reseller_purchase=datetime('now') WHERE telegram_id=? AND is_reseller=1",
            (telegram_id,))

def list_resellers():
    with get_conn() as conn:
        return _fetchall(conn, "SELECT * FROM users WHERE is_reseller=1 ORDER BY reseller_since DESC")

def count_resellers() -> int:
    with get_conn() as conn:
        row = _fetchone(conn, "SELECT COUNT(*) AS c FROM users WHERE is_reseller=1")
        return row["c"]

def revoke_inactive_resellers(days: int):
    cutoff = (_utcnow() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    with _lock:
        with get_conn() as conn:
            rows = _fetchall(conn,
                "SELECT telegram_id, username FROM users "
                "WHERE is_reseller=1 AND (last_reseller_purchase IS NULL OR last_reseller_purchase < ?)",
                (cutoff,))
            revoked = [(r["telegram_id"], r["username"]) for r in rows]
            if revoked:
                _execute(conn,
                    "UPDATE users SET is_reseller=0, reseller_since=NULL, last_reseller_purchase=NULL "
                    "WHERE is_reseller=1 AND (last_reseller_purchase IS NULL OR last_reseller_purchase < ?)",
                    (cutoff,))
            return revoked


# ── reseller keys ────────────────────────────────────────────────────────────

def create_reseller_key(code: str, created_by: int, note: str = None) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO reseller_keys (code, created_by, note) VALUES (?, ?, ?)",
            (code, created_by, note))
        return cur.lastrowid

def redeem_reseller_key(code: str, telegram_id: int) -> str:
    with _lock:
        with get_conn() as conn:
            row = _fetchone(conn, "SELECT * FROM reseller_keys WHERE code=?", (code,))
            if not row:
                return "not_found"
            if row["revoked"]:
                return "revoked"
            if row["used_by"]:
                return "used"
            already = _fetchone(conn, "SELECT is_reseller FROM users WHERE telegram_id=?", (telegram_id,))
            if already and already["is_reseller"]:
                return "already_reseller"
            cur = _execute(conn,
                "UPDATE reseller_keys SET used_by=?, used_at=datetime('now') WHERE key_id=? AND used_by IS NULL",
                (telegram_id, row["key_id"]))
            if cur.rowcount != 1:
                return "used"
            _execute(conn,
                "UPDATE users SET is_reseller=1, reseller_since=datetime('now'), last_reseller_purchase=datetime('now') "
                "WHERE telegram_id=?", (telegram_id,))
            return "ok"

def list_reseller_keys(only_unused=False, limit=50):
    with get_conn() as conn:
        q = "SELECT * FROM reseller_keys"
        if only_unused:
            q += " WHERE used_by IS NULL AND revoked=0"
        q += " ORDER BY created_at DESC LIMIT ?"
        return _fetchall(conn, q, (limit,))

def revoke_reseller_key(key_id: int) -> bool:
    with get_conn() as conn:
        cur = _execute(conn,
            "UPDATE reseller_keys SET revoked=1 WHERE key_id=? AND used_by IS NULL AND revoked=0",
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
                "SELECT request_id FROM reseller_requests WHERE telegram_id=? AND status='pending'",
                (telegram_id,))
            if existing:
                return None
            cur = conn.execute(
                "INSERT INTO reseller_requests (telegram_id, username, message) VALUES (?, ?, ?)",
                (telegram_id, username or "", message))
            return cur.lastrowid

def get_reseller_request(request_id: int):
    with get_conn() as conn:
        return _fetchone(conn, "SELECT * FROM reseller_requests WHERE request_id=?", (request_id,))

def list_reseller_requests(status="pending", limit=20, offset=0):
    with get_conn() as conn:
        if status:
            return _fetchall(conn,
                "SELECT * FROM reseller_requests WHERE status=? ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (status, limit, offset))
        return _fetchall(conn,
            "SELECT * FROM reseller_requests ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (limit, offset))

def count_reseller_requests(status="pending") -> int:
    with get_conn() as conn:
        if status:
            row = _fetchone(conn, "SELECT COUNT(*) AS c FROM reseller_requests WHERE status=?", (status,))
        else:
            row = _fetchone(conn, "SELECT COUNT(*) AS c FROM reseller_requests")
        return row["c"]

def resolve_reseller_request_atomic(request_id: int, status: str, handled_by: int):
    with _lock:
        with get_conn() as conn:
            r = _fetchone(conn, "SELECT * FROM reseller_requests WHERE request_id=?", (request_id,))
            if not r or r["status"] != "pending":
                return None
            cur = _execute(conn,
                "UPDATE reseller_requests SET status=?, handled_by=?, handled_at=datetime('now') "
                "WHERE request_id=? AND status='pending'",
                (status, handled_by, request_id))
            if cur.rowcount != 1:
                return None
            return r


# ── products ─────────────────────────────────────────────────────────────────

def add_product(name, category, description, price_mmk, credential_format):
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO products (name, category, description, price_mmk, credential_format) "
            "VALUES (?, ?, ?, ?, ?)",
            (name, category, description, price_mmk, credential_format))
        return cur.lastrowid

def get_product(product_id: int):
    with get_conn() as conn:
        return _fetchone(conn, "SELECT * FROM products WHERE product_id=?", (product_id,))

def list_products(active_only=True):
    with get_conn() as conn:
        q = "SELECT * FROM products"
        if active_only:
            q += " WHERE active=1"
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
    sets = ", ".join(f"{k}=?" for k in fields)
    vals = list(fields.values()) + [product_id]
    with get_conn() as conn:
        _execute(conn, f"UPDATE products SET {sets} WHERE product_id=?", vals)

def set_discount(product_id: int, pct: int, until_iso: str):
    with get_conn() as conn:
        _execute(conn,
            "UPDATE products SET discount_pct=?, discount_until=? WHERE product_id=?",
            (pct, until_iso, product_id))

def clear_discount(product_id: int):
    with get_conn() as conn:
        _execute(conn, "UPDATE products SET discount_pct=0, discount_until=NULL WHERE product_id=?",
                 (product_id,))

def expire_discounts():
    with get_conn() as conn:
        _execute(conn,
            "UPDATE products SET discount_pct=0, discount_until=NULL "
            "WHERE discount_pct > 0 AND discount_until IS NOT NULL AND discount_until <= datetime('now')")

def get_effective_price(product) -> int:
    pct = product["discount_pct"] or 0
    until = product["discount_until"]
    if pct > 0 and until:
        try:
            exp = datetime.fromisoformat(str(until).replace("Z", "+00:00"))
            if exp.tzinfo is None:
                exp = exp.replace(tzinfo=timezone.utc)
            if exp > _utcnow():
                return max(1, int(product["price_mmk"] * (100 - pct) / 100))
        except Exception:
            pass
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
    if not (pct > 0 and until):
        return False
    try:
        exp = datetime.fromisoformat(str(until).replace("Z", "+00:00"))
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        return exp > _utcnow()
    except Exception:
        return False

def delete_product(product_id: int) -> bool:
    with get_conn() as conn:
        row = _fetchone(conn, "SELECT COUNT(*) AS c FROM stock WHERE product_id=?", (product_id,))
        if row["c"] > 0:
            return False
        _execute(conn, "DELETE FROM products WHERE product_id=?", (product_id,))
        return True

def stock_count(product_id: int) -> int:
    with get_conn() as conn:
        row = _fetchone(conn,
            "SELECT COUNT(*) AS c FROM stock WHERE product_id=? AND reserved_for IS NULL",
            (product_id,))
        return row["c"]


# ── stock ────────────────────────────────────────────────────────────────────

def add_stock_bulk(product_id: int, credentials: list) -> int:
    clean = [c.strip() for c in credentials if c.strip()]
    if not clean:
        return 0
    rows = [(product_id, c) for c in clean]
    with get_conn() as conn:
        _executemany(conn, "INSERT INTO stock (product_id, credential) VALUES (?, ?)", rows)
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
                    "SELECT * FROM stock WHERE product_id=? AND reserved_for IS NULL LIMIT 1",
                    (product_id,))
                if not row:
                    return None
                cur = _execute(conn,
                    "DELETE FROM stock WHERE stock_id=? AND reserved_for IS NULL",
                    (row["stock_id"],))
                if cur.rowcount == 1:
                    credential = row["credential"]
                    stock_id = row["stock_id"]
                    p = _fetchone(conn, "SELECT name FROM products WHERE product_id=?", (product_id,))
                    pname = p["name"] if p else ""
                    break

        _mirror_sold(stock_id, product_id, pname, credential, telegram_id, _utcnow_iso())
        return credential

def _mirror_sold(stock_id, product_id, pname, credential, telegram_id, sold_at):
    """Write a claimed item into sold_stock. Best-effort, never raises."""
    try:
        with get_conn() as conn:
            _execute(conn,
                "INSERT OR IGNORE INTO sold_stock (stock_id, product_id, product_name, credential, sold_to, sold_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (stock_id, product_id, pname, credential, telegram_id, sold_at))
    except Exception:
        pass

def list_stock_items(product_id: int, limit=50):
    with get_conn() as conn:
        return _fetchall(conn,
            "SELECT * FROM stock WHERE product_id=? ORDER BY stock_id DESC LIMIT ?",
            (product_id, limit))

def list_sold_items(product_id: int = None, limit=50):
    with get_conn() as conn:
        if product_id:
            return _fetchall(conn,
                "SELECT * FROM sold_stock WHERE product_id=? ORDER BY sold_at DESC LIMIT ?",
                (product_id, limit))
        return _fetchall(conn,
            "SELECT * FROM sold_stock ORDER BY sold_at DESC LIMIT ?", (limit,))

def delete_stock_item(stock_id: int):
    with get_conn() as conn:
        _execute(conn, "DELETE FROM stock WHERE stock_id=?", (stock_id,))


# ── orders ───────────────────────────────────────────────────────────────────

def create_order(telegram_id, username, product_id, product_name, amount_mmk, credential, payment_method="balance"):
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO orders (telegram_id, username, product_id, product_name, amount_mmk, payment_method, credential) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (telegram_id, username, product_id, product_name, amount_mmk, payment_method, credential))
        return cur.lastrowid

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
                        "SELECT stock_id, credential FROM stock WHERE product_id=? AND reserved_for IS NULL LIMIT 1",
                        (product_id,))
                    if not row:
                        break
                    cur = _execute(conn, "DELETE FROM stock WHERE stock_id=?", (row["stock_id"],))
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
                    "UPDATE users SET mmk_balance = mmk_balance - ? "
                    "WHERE telegram_id=? AND mmk_balance >= ?",
                    (charged, telegram_id, charged))
                if cur.rowcount != 1:
                    bal_row = _fetchone(conn, "SELECT mmk_balance FROM users WHERE telegram_id=?", (telegram_id,))
                    bal = bal_row["mmk_balance"] if bal_row else 0
                    raise _InsufficientFunds(charged, bal)

                _execute(conn,
                    "UPDATE users SET total_spent = total_spent + ? WHERE telegram_id=?",
                    (charged, telegram_id))

                for stock_id, credential in claimed:
                    cur = conn.execute(
                        "INSERT INTO orders (telegram_id, username, product_id, product_name, amount_mmk, payment_method, credential) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (telegram_id, username, product_id, product_name, unit_price, "balance", credential))
                    orders.append((cur.lastrowid, credential))

                new_bal_row = _fetchone(conn, "SELECT mmk_balance FROM users WHERE telegram_id=?", (telegram_id,))
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
                "SELECT * FROM orders WHERE telegram_id=? ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (telegram_id, limit, offset))
        return _fetchall(conn,
            "SELECT * FROM orders ORDER BY created_at DESC LIMIT ? OFFSET ?", (limit, offset))

def get_order(order_id: int):
    with get_conn() as conn:
        return _fetchone(conn, "SELECT * FROM orders WHERE order_id=?", (order_id,))


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
            "UPDATE products SET fulfillment=?, manual_fields=? WHERE product_id=?",
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
        cur = conn.execute(
            "INSERT INTO orders (telegram_id, username, product_id, product_name, amount_mmk, "
            "payment_method, credential, status, fulfillment_mode) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (telegram_id, username, product_id, product_name, amount_mmk,
             payment_method, "", "awaiting_fulfillment", "preorder"))
        return cur.lastrowid

def create_preorder_atomic(telegram_id, username, product_id, product_name, total_mmk):
    with _lock:
        with get_conn() as conn:
            cur = _execute(conn,
                "UPDATE users SET mmk_balance = mmk_balance - ? "
                "WHERE telegram_id=? AND mmk_balance >= ?",
                (total_mmk, telegram_id, total_mmk))
            if cur.rowcount != 1:
                return None
            cur2 = conn.execute(
                "INSERT INTO orders (telegram_id, username, product_id, product_name, amount_mmk, "
                "payment_method, credential, status, fulfillment_mode) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (telegram_id, username, product_id, product_name, total_mmk,
                 "balance", "", "awaiting_fulfillment", "preorder"))
            order_id = cur2.lastrowid
            _execute(conn,
                "UPDATE users SET total_spent = total_spent + ? WHERE telegram_id=?",
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
            "UPDATE orders SET manual_info=?, status='awaiting_fulfillment' WHERE order_id=?",
            (json.dumps(info, ensure_ascii=False), order_id))

def update_manual_info(order_id: int, info: dict):
    with get_conn() as conn:
        _execute(conn, "UPDATE orders SET manual_info=? WHERE order_id=?",
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
        _execute(conn, "UPDATE orders SET status=? WHERE order_id=?", (status, order_id))

def set_order_credential(order_id: int, credential: str):
    with get_conn() as conn:
        _execute(conn, "UPDATE orders SET credential=? WHERE order_id=?", (credential, order_id))

def list_fulfillment_orders(limit: int = 50):
    with get_conn() as conn:
        return _fetchall(conn,
            "SELECT o.*, p.fulfillment AS mode FROM orders o "
            "LEFT JOIN products p ON p.product_id=o.product_id "
            "WHERE o.status IN ('awaiting_info','awaiting_fulfillment') "
            "ORDER BY o.created_at DESC LIMIT ?", (limit,))

def count_fulfillment_orders() -> int:
    with get_conn() as conn:
        row = _fetchone(conn,
            "SELECT COUNT(*) AS c FROM orders WHERE status IN ('awaiting_info','awaiting_fulfillment')")
        return row["c"]

def refund_order_atomic(order_id: int, only_statuses=None):
    with _lock:
        with get_conn() as conn:
            o = _fetchone(conn, "SELECT * FROM orders WHERE order_id=?", (order_id,))
            if not o or o["status"] == "refunded":
                return None
            if only_statuses and o["status"] not in only_statuses:
                return None
            if only_statuses:
                placeholders = ",".join("?" for _ in only_statuses)
                cur = _execute(conn,
                    f"UPDATE orders SET status='refunded' WHERE order_id=? AND status IN ({placeholders})",
                    (order_id, *only_statuses))
            else:
                cur = _execute(conn,
                    "UPDATE orders SET status='refunded' WHERE order_id=? AND status!='refunded'",
                    (order_id,))
            if cur.rowcount != 1:
                return None
            _execute(conn,
                "UPDATE users SET mmk_balance = mmk_balance + ? WHERE telegram_id=?",
                (o["amount_mmk"], o["telegram_id"]))
            _execute(conn,
                "UPDATE users SET total_spent=MAX(0,total_spent-?) WHERE telegram_id=?",
                (o["amount_mmk"], o["telegram_id"]))
            return o

def count_orders() -> int:
    with get_conn() as conn:
        row = _fetchone(conn, "SELECT COUNT(*) AS c FROM orders")
        return row["c"]

def count_orders_for_user(telegram_id: int) -> int:
    with get_conn() as conn:
        row = _fetchone(conn, "SELECT COUNT(*) AS c FROM orders WHERE telegram_id=?", (telegram_id,))
        return row["c"]

def count_deposits_for_user(telegram_id: int) -> int:
    with get_conn() as conn:
        row = _fetchone(conn, "SELECT COUNT(*) AS c FROM deposits WHERE telegram_id=?", (telegram_id,))
        return row["c"]

def search_user(query: str):
    with get_conn() as conn:
        if query.lstrip("-").isdigit():
            return _fetchone(conn, "SELECT * FROM users WHERE telegram_id=?", (int(query),))
        return _fetchone(conn, "SELECT * FROM users WHERE username LIKE ? LIMIT 1", (f"%{query}%",))


# ── order problem reports (warranty) ─────────────────────────────────────────

def create_order_report(order_id: int, telegram_id: int, username: str, message: str = None):
    with _lock:
        with get_conn() as conn:
            existing = _fetchone(conn,
                "SELECT report_id FROM order_reports WHERE order_id=? AND status='pending'",
                (order_id,))
            if existing:
                return None
            cur = conn.execute(
                "INSERT INTO order_reports (order_id, telegram_id, username, message) VALUES (?, ?, ?, ?)",
                (order_id, telegram_id, username or "", message))
            return cur.lastrowid

def get_order_report(report_id: int):
    with get_conn() as conn:
        return _fetchone(conn, "SELECT * FROM order_reports WHERE report_id=?", (report_id,))

def order_has_pending_report(order_id: int) -> bool:
    with get_conn() as conn:
        row = _fetchone(conn, "SELECT 1 FROM order_reports WHERE order_id=? AND status='pending'", (order_id,))
        return row is not None

def list_order_reports(status="pending", limit=10, offset=0):
    with get_conn() as conn:
        if status:
            return _fetchall(conn,
                "SELECT * FROM order_reports WHERE status=? ORDER BY created_at ASC LIMIT ? OFFSET ?",
                (status, limit, offset))
        return _fetchall(conn,
            "SELECT * FROM order_reports ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (limit, offset))

def count_order_reports(status="pending") -> int:
    with get_conn() as conn:
        if status:
            row = _fetchone(conn, "SELECT COUNT(*) AS c FROM order_reports WHERE status=?", (status,))
        else:
            row = _fetchone(conn, "SELECT COUNT(*) AS c FROM order_reports")
        return row["c"]

def resolve_order_report_atomic(report_id: int, status: str, handled_by: int):
    with _lock:
        with get_conn() as conn:
            r = _fetchone(conn, "SELECT * FROM order_reports WHERE report_id=?", (report_id,))
            if not r or r["status"] != "pending":
                return None
            cur = _execute(conn,
                "UPDATE order_reports SET status=?, handled_by=?, handled_at=datetime('now') "
                "WHERE report_id=? AND status='pending'",
                (status, handled_by, report_id))
            if cur.rowcount != 1:
                return None
            return r

def reopen_order_report(report_id: int):
    with get_conn() as conn:
        _execute(conn,
            "UPDATE order_reports SET status='pending', handled_by=NULL, handled_at=NULL WHERE report_id=?",
            (report_id,))


# ── deposits ──────────────────────────────────────────────────────────────────

def create_deposit(telegram_id, username, method, amount_mmk, screenshot_file_id=None, note=None):
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO deposits (telegram_id, username, method, amount_mmk, screenshot_file_id, note) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (telegram_id, username, method, amount_mmk, screenshot_file_id, note))
        return cur.lastrowid

def get_deposit(deposit_id: int):
    with get_conn() as conn:
        return _fetchone(conn, "SELECT * FROM deposits WHERE deposit_id=?", (deposit_id,))

def update_deposit(deposit_id: int, status: str, approved_by=None, note=None):
    with get_conn() as conn:
        _execute(conn,
            "UPDATE deposits SET status=?, approved_by=?, note=? WHERE deposit_id=?",
            (status, approved_by, note, deposit_id))

def approve_deposit_atomic(deposit_id: int, approved_by: int):
    with _lock:
        with get_conn() as conn:
            d = _fetchone(conn, "SELECT * FROM deposits WHERE deposit_id=?", (deposit_id,))
            if not d or d["status"] != "pending":
                return None
            cur = _execute(conn,
                "UPDATE deposits SET status='approved', approved_by=? WHERE deposit_id=? AND status='pending'",
                (approved_by, deposit_id))
            if cur.rowcount != 1:
                return None
            bonus = 0
            if cfg_get("deposit_bonus_enabled", "1") == "1":
                pct = get_deposit_bonus_percent(d["amount_mmk"])
                bonus = d["amount_mmk"] * pct // 100
            credit = _execute(conn,
                "UPDATE users SET mmk_balance = mmk_balance + ?, bonus_earned = bonus_earned + ? WHERE telegram_id=?",
                (d["amount_mmk"] + bonus, bonus, d["telegram_id"]))
            if credit.rowcount != 1:
                raise RuntimeError(f"approve_deposit: user row missing for {d['telegram_id']}")
            return d, bonus

def reject_deposit_atomic(deposit_id: int, rejected_by: int, note=None):
    with _lock:
        with get_conn() as conn:
            d = _fetchone(conn, "SELECT * FROM deposits WHERE deposit_id=?", (deposit_id,))
            if not d or d["status"] != "pending":
                return None
            cur = _execute(conn,
                "UPDATE deposits SET status='rejected', approved_by=?, note=? WHERE deposit_id=? AND status='pending'",
                (rejected_by, note, deposit_id))
            if cur.rowcount != 1:
                return None
            return d

def is_binance_txn_used(txn_id: str) -> bool:
    with get_conn() as conn:
        row = _fetchone(conn, "SELECT 1 FROM deposits WHERE binance_txn_id=?", (txn_id,))
        return row is not None

def create_binance_deposit_atomic(telegram_id, username, amount_mmk, txn_id, note=None):
    with _lock:
        with get_conn() as conn:
            existing = _fetchone(conn, "SELECT deposit_id FROM deposits WHERE binance_txn_id=?", (txn_id,))
            if existing:
                return None
            try:
                cur = conn.execute(
                    "INSERT INTO deposits (telegram_id, username, method, amount_mmk, status, note, binance_txn_id) "
                    "VALUES (?, ?, ?, ?, 'approved', ?, ?)",
                    (telegram_id, username, "binance", amount_mmk, note, txn_id))
                deposit_id = cur.lastrowid
            except sqlite3.IntegrityError:
                conn.rollback()
                return None
            bonus = 0
            if cfg_get("deposit_bonus_enabled", "1") == "1":
                pct = get_deposit_bonus_percent(amount_mmk)
                bonus = amount_mmk * pct // 100
            credit = _execute(conn,
                "UPDATE users SET mmk_balance = mmk_balance + ?, bonus_earned = bonus_earned + ? WHERE telegram_id=?",
                (amount_mmk + bonus, bonus, telegram_id))
            if credit.rowcount != 1:
                raise RuntimeError(f"binance credit: user row missing for {telegram_id}")
            return deposit_id, bonus

def list_deposits(status=None, limit=20, offset=0, telegram_id=None):
    with get_conn() as conn:
        if telegram_id and status:
            return _fetchall(conn,
                "SELECT * FROM deposits WHERE telegram_id=? AND status=? ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (telegram_id, status, limit, offset))
        if telegram_id:
            return _fetchall(conn,
                "SELECT * FROM deposits WHERE telegram_id=? ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (telegram_id, limit, offset))
        if status:
            return _fetchall(conn,
                "SELECT * FROM deposits WHERE status=? ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (status, limit, offset))
        return _fetchall(conn,
            "SELECT * FROM deposits ORDER BY created_at DESC LIMIT ? OFFSET ?", (limit, offset))

def count_deposits(status=None) -> int:
    with get_conn() as conn:
        if status:
            row = _fetchone(conn, "SELECT COUNT(*) AS c FROM deposits WHERE status=?", (status,))
        else:
            row = _fetchone(conn, "SELECT COUNT(*) AS c FROM deposits")
        return row["c"]


# ── analytics & dashboard ─────────────────────────────────────────────────────

def revenue_today() -> int:
    with get_conn() as conn:
        row = _fetchone(conn,
            "SELECT COALESCE(SUM(amount_mmk),0) AS s FROM orders "
            "WHERE created_at >= date('now') AND status!='refunded'")
        return row["s"]

def revenue_total() -> int:
    with get_conn() as conn:
        row = _fetchone(conn, "SELECT COALESCE(SUM(amount_mmk),0) AS s FROM orders WHERE status!='refunded'")
        return row["s"]

def revenue_last_n_days(n: int = 7):
    with get_conn() as conn:
        return _fetchall(conn,
            "SELECT date(created_at) as day, COALESCE(SUM(amount_mmk),0) as total "
            "FROM orders WHERE created_at >= datetime('now', ?) AND status!='refunded' "
            "GROUP BY day ORDER BY day ASC", (f'-{n} days',))

def orders_last_n_days(n: int = 7) -> int:
    with get_conn() as conn:
        row = _fetchone(conn,
            "SELECT COUNT(*) AS c FROM orders WHERE created_at >= datetime('now', ?) AND status!='refunded'",
            (f'-{n} days',))
        return row["c"]

def new_users_last_n_days(n: int = 7) -> int:
    with get_conn() as conn:
        row = _fetchone(conn,
            "SELECT COUNT(*) AS c FROM users WHERE registered_at >= datetime('now', ?)",
            (f'-{n} days',))
        return row["c"]

def top_products(limit: int = 5):
    with get_conn() as conn:
        return _fetchall(conn,
            "SELECT product_name, COUNT(*) as cnt, SUM(amount_mmk) as revenue "
            "FROM orders WHERE status!='refunded' GROUP BY product_name ORDER BY cnt DESC LIMIT ?",
            (limit,))

def products_low_stock():
    threshold = int(cfg_get("low_stock_threshold", "3"))
    with get_conn() as conn:
        rows = _fetchall(conn,
            "SELECT p.name, COUNT(s.stock_id) as cnt "
            "FROM products p JOIN stock s ON p.product_id=s.product_id "
            "WHERE p.active=1 AND s.reserved_for IS NULL "
            "GROUP BY p.product_id "
            "HAVING COUNT(s.stock_id) > 0 AND COUNT(s.stock_id) <= ?",
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
        row = _fetchone(conn, "SELECT state_json, updated_at FROM user_state WHERE telegram_id=?", (telegram_id,))
        if not row:
            return {}
        updated = row["updated_at"]
        if isinstance(updated, str):
            try:
                updated = datetime.fromisoformat(updated.replace("Z", "+00:00"))
                if updated.tzinfo is None:
                    updated = updated.replace(tzinfo=timezone.utc)
            except Exception:
                pass
        if isinstance(updated, datetime) and _utcnow() - updated > timedelta(minutes=30):
            _execute(conn, "DELETE FROM user_state WHERE telegram_id=?", (telegram_id,))
            return {}
        try:
            return json.loads(row["state_json"])
        except Exception:
            return {}

def state_set(telegram_id: int, data: dict):
    with get_conn() as conn:
        _execute(conn,
            "INSERT INTO user_state (telegram_id, state_json, updated_at) VALUES (?, ?, datetime('now')) "
            "ON CONFLICT(telegram_id) DO UPDATE SET state_json=excluded.state_json, updated_at=datetime('now')",
            (telegram_id, json.dumps(data)))

def state_clear(telegram_id: int):
    with get_conn() as conn:
        _execute(conn, "DELETE FROM user_state WHERE telegram_id=?", (telegram_id,))

def state_expire_all():
    with get_conn() as conn:
        _execute(conn,
            "DELETE FROM user_state WHERE updated_at < datetime('now', '-30 minutes')")


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
                cur = conn.execute(
                    "INSERT INTO giveaways (product_id, title, description, winner_slots, end_time, status, created_by) "
                    "VALUES (?, ?, ?, ?, ?, 'active', ?)",
                    (product_id, title, description, winner_slots, end_time, created_by))
                gid = cur.lastrowid
                prow = _fetchone(conn, "SELECT fulfillment FROM products WHERE product_id=?", (product_id,))
                mode = ((prow["fulfillment"] or "instant").strip().lower() if prow else "instant")
                if prow and mode == "instant" and winner_slots > 0:
                    rows = _fetchall(conn,
                        "SELECT stock_id FROM stock WHERE product_id=? AND reserved_for IS NULL LIMIT ?",
                        (product_id, winner_slots))
                    if len(rows) < winner_slots:
                        raise _GiveawayNoStock()
                    code = f"giveaway_{gid}"
                    _executemany(conn,
                        "UPDATE stock SET reserved_for=? WHERE stock_id=?",
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
        return _fetchone(conn, "SELECT * FROM giveaways WHERE giveaway_id=?", (giveaway_id,))

def list_giveaways(limit=50):
    with get_conn() as conn:
        return _fetchall(conn,
            "SELECT * FROM giveaways ORDER BY (status='active') DESC, created_at DESC LIMIT ?", (limit,))

def set_giveaway_status(giveaway_id: int, status: str, drawn_at=None, cancelled_at=None):
    with get_conn() as conn:
        _execute(conn,
            "UPDATE giveaways SET status=?, drawn_at=COALESCE(?, drawn_at), cancelled_at=COALESCE(?, cancelled_at) "
            "WHERE giveaway_id=?", (status, drawn_at, cancelled_at, giveaway_id))

def add_giveaway_entry(giveaway_id: int, telegram_id: int, username: str = None) -> bool:
    try:
        with get_conn() as conn:
            _execute(conn,
                "INSERT INTO giveaway_entries (giveaway_id, telegram_id, username) VALUES (?, ?, ?) "
                "ON CONFLICT DO NOTHING",
                (giveaway_id, telegram_id, username or ""))
            row = _fetchone(conn,
                "SELECT 1 FROM giveaway_entries WHERE giveaway_id=? AND telegram_id=?",
                (giveaway_id, telegram_id))
            return row is not None
    except sqlite3.IntegrityError:
        return False

def has_entered(giveaway_id: int, telegram_id: int) -> bool:
    with get_conn() as conn:
        row = _fetchone(conn,
            "SELECT 1 FROM giveaway_entries WHERE giveaway_id=? AND telegram_id=?",
            (giveaway_id, telegram_id))
        return row is not None

def count_entries(giveaway_id: int) -> int:
    with get_conn() as conn:
        row = _fetchone(conn, "SELECT COUNT(*) AS c FROM giveaway_entries WHERE giveaway_id=?", (giveaway_id,))
        return row["c"]

def list_entrants(giveaway_id: int):
    with get_conn() as conn:
        return _fetchall(conn,
            "SELECT telegram_id, username, entries FROM giveaway_entries WHERE giveaway_id=?",
            (giveaway_id,))

def get_giveaway_entry_count(giveaway_id: int, telegram_id: int) -> int:
    with get_conn() as conn:
        row = _fetchone(conn,
            "SELECT entries FROM giveaway_entries WHERE giveaway_id=? AND telegram_id=?",
            (giveaway_id, telegram_id))
        return row["entries"] if row else 0

def add_giveaway_bonus_entries(giveaway_id: int, telegram_id: int, n: int) -> int:
    with get_conn() as conn:
        cur = _execute(conn,
            "UPDATE giveaway_entries SET entries = entries + ? WHERE giveaway_id=? AND telegram_id=?",
            (n, giveaway_id, telegram_id))
        if cur.rowcount != 1:
            return 0
        row = _fetchone(conn,
            "SELECT entries FROM giveaway_entries WHERE giveaway_id=? AND telegram_id=?",
            (giveaway_id, telegram_id))
        return row["entries"]

def record_giveaway_invite(giveaway_id: int, inviter_id: int, invitee_id: int) -> bool:
    if inviter_id == invitee_id:
        return False
    try:
        with get_conn() as conn:
            _execute(conn,
                "INSERT INTO giveaway_invites (giveaway_id, inviter_id, invitee_id) VALUES (?, ?, ?) ON CONFLICT DO NOTHING",
                (giveaway_id, inviter_id, invitee_id))
            return True
    except Exception:
        return False

def get_giveaway_invite_leaderboard(giveaway_id: int, limit: int = 5):
    with get_conn() as conn:
        return _fetchall(conn,
            "SELECT inviter_id, COUNT(*) AS c FROM giveaway_invites WHERE giveaway_id=? GROUP BY inviter_id ORDER BY c DESC LIMIT ?",
            (giveaway_id, limit))

def count_giveaway_invites(giveaway_id: int, inviter_id: int) -> int:
    with get_conn() as conn:
        row = _fetchone(conn,
            "SELECT COUNT(*) AS c FROM giveaway_invites WHERE giveaway_id=? AND inviter_id=?",
            (giveaway_id, inviter_id))
        return row["c"]

def add_giveaway_winner(giveaway_id: int, telegram_id: int, username: str,
                        credential: str = None, delivered: int = 0, order_id: int = None) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO giveaway_winners (giveaway_id, telegram_id, username, credential, delivered, order_id) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (giveaway_id, telegram_id, username or "", credential, delivered, order_id))
        return cur.lastrowid

def list_winners(giveaway_id: int):
    with get_conn() as conn:
        return _fetchall(conn,
            "SELECT * FROM giveaway_winners WHERE giveaway_id=? ORDER BY winner_id ASC", (giveaway_id,))

def count_delivered_winners(giveaway_id: int) -> int:
    with get_conn() as conn:
        row = _fetchone(conn,
            "SELECT COUNT(*) AS c FROM giveaway_winners WHERE giveaway_id=? AND delivered=1",
            (giveaway_id,))
        return row["c"]

def list_giveaways_needing_end_reminder():
    with get_conn() as conn:
        return _fetchall(conn,
            "SELECT * FROM giveaways WHERE status='active' AND end_time IS NOT NULL "
            "AND end_time <= datetime('now') AND end_reminded=0")

def mark_giveaway_reminded(giveaway_id: int):
    with get_conn() as conn:
        _execute(conn, "UPDATE giveaways SET end_reminded=1 WHERE giveaway_id=?", (giveaway_id,))


# ── pending-deposit nudge ──────────────────────────────────────────────────────

def list_stale_pending_deposits(hours: int):
    with get_conn() as conn:
        return _fetchall(conn,
            "SELECT * FROM deposits WHERE status='pending' AND nudge_sent=0 "
            "AND created_at <= datetime('now', ?)", (f'-{hours} hours',))

def mark_deposit_nudged(deposit_id: int):
    with get_conn() as conn:
        _execute(conn, "UPDATE deposits SET nudge_sent=1 WHERE deposit_id=?", (deposit_id,))


# ── gifts ─────────────────────────────────────────────────────────────────────

def reserve_stock(product_id: int, gift_code: str):
    with _lock:
        with get_conn() as conn:
            while True:
                row = _fetchone(conn,
                    "SELECT stock_id FROM stock WHERE product_id=? AND reserved_for IS NULL LIMIT 1",
                    (product_id,))
                if not row:
                    return None
                cur = _execute(conn,
                    "UPDATE stock SET reserved_for=? WHERE stock_id=? AND reserved_for IS NULL",
                    (gift_code, row["stock_id"]))
                if cur.rowcount == 1:
                    return row["stock_id"]

def claim_reserved_stock(gift_code: str):
    with _lock:
        with get_conn() as conn:
            row = _fetchone(conn,
                "SELECT s.stock_id, s.product_id, s.credential, p.name AS pname "
                "FROM stock s LEFT JOIN products p ON p.product_id = s.product_id "
                "WHERE s.reserved_for=? LIMIT 1", (gift_code,))
            if not row:
                return None
            _execute(conn, "DELETE FROM stock WHERE stock_id=?", (row["stock_id"],))
        _mirror_sold(row["stock_id"], row["product_id"], row["pname"] or "", row["credential"],
                     None, _utcnow_iso())
        return row["stock_id"], row["credential"]

def release_stock_reservation(stock_id: int):
    with get_conn() as conn:
        _execute(conn, "UPDATE stock SET reserved_for=NULL WHERE stock_id=?", (stock_id,))


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
                cur = conn.execute(
                    "INSERT INTO ld_rounds (product_id, product_name, prize_mode, prize_value, "
                    "entry_price, total_slots, deadline, status, seed, seed_hash, created_by) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, 'open', ?, ?, ?)",
                    (product_id, product_name, prize_mode, prize_value,
                     entry_price, total_slots, deadline, seed, seed_hash, created_by))
                rid = cur.lastrowid
                if prize_mode == "instant":
                    row = _fetchone(conn,
                        "SELECT stock_id FROM stock WHERE product_id=? AND reserved_for IS NULL LIMIT 1",
                        (product_id,))
                    if not row:
                        raise _LdNoStock()
                    _execute(conn,
                        "UPDATE stock SET reserved_for=? WHERE stock_id=?",
                        (f"ld_{rid}", row["stock_id"]))
                return rid
        except _LdNoStock:
            return -1

def get_ld_round(round_id: int):
    with get_conn() as conn:
        return _fetchone(conn, "SELECT * FROM ld_rounds WHERE round_id=?", (round_id,))

def list_ld_rounds(status=None, limit=50):
    with get_conn() as conn:
        if status:
            return _fetchall(conn,
                "SELECT * FROM ld_rounds WHERE status=? ORDER BY round_id DESC LIMIT ?",
                (status, limit))
        return _fetchall(conn, "SELECT * FROM ld_rounds ORDER BY round_id DESC LIMIT ?", (limit,))

def ld_open_round_for_product(product_id: int):
    with get_conn() as conn:
        return _fetchone(conn,
            "SELECT * FROM ld_rounds WHERE product_id=? AND status='open' ORDER BY round_id LIMIT 1",
            (product_id,))

def ld_entries(round_id: int):
    with get_conn() as conn:
        return _fetchall(conn, "SELECT * FROM ld_entries WHERE round_id=? ORDER BY slot_no", (round_id,))

def ld_entry_count(round_id: int) -> int:
    with get_conn() as conn:
        row = _fetchone(conn, "SELECT COUNT(*) AS c FROM ld_entries WHERE round_id=?", (round_id,))
        return row["c"]

def ld_user_entries(round_id: int, telegram_id: int):
    with get_conn() as conn:
        return _fetchall(conn,
            "SELECT * FROM ld_entries WHERE round_id=? AND telegram_id=? ORDER BY slot_no",
            (round_id, telegram_id))

def ld_user_round_entries(telegram_id: int, limit=50):
    with get_conn() as conn:
        return _fetchall(conn,
            "SELECT e.*, r.product_name, r.prize_mode, r.prize_value, r.entry_price, "
            "r.total_slots, r.status AS round_status, r.winner_id "
            "FROM ld_entries e JOIN ld_rounds r ON r.round_id = e.round_id "
            "WHERE e.telegram_id=? ORDER BY e.entry_id DESC LIMIT ?",
            (telegram_id, limit))

def ld_buy_slot(round_id: int, telegram_id: int, username: str) -> dict:
    with _lock:
        with get_conn() as conn:
            r = _fetchone(conn, "SELECT * FROM ld_rounds WHERE round_id=?", (round_id,))
            if not r or r["status"] != "open":
                return {"error": "closed"}
            mine = _fetchone(conn,
                "SELECT COUNT(*) AS c FROM ld_entries WHERE round_id=? AND telegram_id=?",
                (round_id, telegram_id))
            if mine["c"] >= ld_cap_for(r["total_slots"]):
                return {"error": "cap"}
            total = _fetchone(conn, "SELECT COUNT(*) AS c FROM ld_entries WHERE round_id=?", (round_id,))
            if total["c"] >= r["total_slots"]:
                return {"error": "full"}
            cur = _execute(conn,
                "UPDATE users SET mmk_balance = mmk_balance - ? WHERE telegram_id=? AND mmk_balance >= ?",
                (r["entry_price"], telegram_id, r["entry_price"]))
            if cur.rowcount != 1:
                return {"error": "insufficient"}
            slot_no = total["c"] + 1
            conn.execute(
                "INSERT INTO ld_entries (round_id, telegram_id, username, slot_no) VALUES (?, ?, ?, ?)",
                (round_id, telegram_id, username, slot_no))
            filled = slot_no >= r["total_slots"]
            if filled:
                _execute(conn,
                    "UPDATE ld_rounds SET status='drawing' WHERE round_id=? AND status='open'",
                    (round_id,))
            bal = _fetchone(conn, "SELECT mmk_balance FROM users WHERE telegram_id=?", (telegram_id,))
            return {"ok": True, "slot_no": slot_no, "filled": filled, "balance": bal["mmk_balance"]}

def ld_finish_draw(round_id: int, winner_id: int, winner_slot: int) -> bool:
    with _lock:
        with get_conn() as conn:
            cur = _execute(conn,
                "UPDATE ld_rounds SET status='completed', winner_id=?, winner_slot=?, drawn_at=datetime('now') "
                "WHERE round_id=? AND status='drawing'",
                (winner_id, winner_slot, round_id))
            return cur.rowcount == 1

def ld_refund_round(round_id: int, to_status: str):
    with _lock:
        with get_conn() as conn:
            r = _fetchone(conn, "SELECT * FROM ld_rounds WHERE round_id=?", (round_id,))
            if not r or r["status"] not in ("open", "drawing"):
                return None
            cur = _execute(conn,
                "UPDATE ld_rounds SET status=? WHERE round_id=? AND status IN ('open','drawing')",
                (to_status, round_id))
            if cur.rowcount != 1:
                return None
            rows = _fetchall(conn,
                "SELECT telegram_id, COUNT(*) AS n FROM ld_entries WHERE round_id=? GROUP BY telegram_id",
                (round_id,))
            refunds = []
            for row in rows:
                amt = row["n"] * r["entry_price"]
                _execute(conn,
                    "UPDATE users SET mmk_balance = mmk_balance + ? WHERE telegram_id=?",
                    (amt, row["telegram_id"]))
                refunds.append((row["telegram_id"], amt, row["n"]))
            _execute(conn, "UPDATE stock SET reserved_for=NULL WHERE reserved_for=?", (f"ld_{round_id}",))
            return refunds

def release_reservations(code: str) -> int:
    with get_conn() as conn:
        cur = _execute(conn, "UPDATE stock SET reserved_for=NULL WHERE reserved_for=?", (code,))
        return cur.rowcount


# ── gifts helpers ─────────────────────────────────────────────────────────────

def _gift_code_exists(code: str) -> bool:
    with get_conn() as conn:
        row = _fetchone(conn, "SELECT 1 FROM gifts WHERE code=?", (code,))
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
            "VALUES (?, ?, ?, ?, ?, ?, ?, 'unclaimed', datetime('now', ?))",
            (code, product_id, product_name, stock_id, amount_mmk, sender_id, sender_name, f'+{int(expiry_days)} days'))

def add_total_spent(telegram_id: int, amount_mmk: int):
    with get_conn() as conn:
        _execute(conn, "UPDATE users SET total_spent = total_spent + ? WHERE telegram_id=?",
                 (amount_mmk, telegram_id))

def sub_total_spent(telegram_id: int, amount_mmk: int):
    with get_conn() as conn:
        _execute(conn, "UPDATE users SET total_spent = MAX(0, total_spent - ?) WHERE telegram_id=?",
                 (amount_mmk, telegram_id))

def get_gift_by_code(code: str):
    with get_conn() as conn:
        return _fetchone(conn, "SELECT * FROM gifts WHERE code=?", (code.strip().upper(),))

def claim_gift(code: str, recipient_id: int):
    with _lock:
        with get_conn() as conn:
            g = _fetchone(conn, "SELECT * FROM gifts WHERE code=?", (code.strip().upper(),))
            if not g or g["status"] != "unclaimed":
                return None
            if g["sender_id"] == recipient_id:
                return None
            cur = _execute(conn,
                "UPDATE gifts SET status='claimed', recipient_id=?, claimed_at=datetime('now') "
                "WHERE gift_id=? AND status='unclaimed'",
                (recipient_id, g["gift_id"]))
            if cur.rowcount != 1:
                return None
            return g

def release_expired_gifts() -> int:
    with get_conn() as conn:
        rows = _fetchall(conn,
            "SELECT gift_id, stock_id FROM gifts WHERE status='unclaimed' AND expires_at <= datetime('now')")
        for r in rows:
            _execute(conn, "UPDATE gifts SET status='expired' WHERE gift_id=?", (r["gift_id"],))
            if r["stock_id"]:
                _execute(conn, "UPDATE stock SET reserved_for=NULL WHERE stock_id=?", (r["stock_id"],))
        return len(rows)

def list_user_gifts(telegram_id: int, limit: int = 10):
    with get_conn() as conn:
        return _fetchall(conn,
            "SELECT * FROM gifts WHERE sender_id=? ORDER BY gift_id DESC LIMIT ?",
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
        row = _fetchone(conn, "SELECT total_spent FROM users WHERE telegram_id=?", (telegram_id,))
    spent = row["total_spent"] if row else 0
    return get_vip_tier(spent)


# ── CDK activations ───────────────────────────────────────────────────────────

def cdk_activation_create(user_id: int, product_id: str, product_name: str, cdk_key: str) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO cdk_activations (user_id, product_id, product_name, cdk_key, status) "
            "VALUES (?, ?, ?, ?, 'sending')",
            (user_id, product_id, product_name, cdk_key))
        return cur.lastrowid

def cdk_activation_update(act_id: int, status: str = None, provider_ref: str = None,
                          message: str = None, duration_ms: int = None):
    sets, vals = [], []
    if status is not None:
        sets.append("status=?")
        vals.append(status)
    if provider_ref is not None:
        sets.append("provider_ref=?")
        vals.append(provider_ref)
    if message is not None:
        sets.append("message=?")
        vals.append(message)
    if duration_ms is not None:
        sets.append("duration_ms=?")
        vals.append(duration_ms)
    if not sets:
        return
    sets.append("updated_at=datetime('now')")
    vals.append(act_id)
    with get_conn() as conn:
        _execute(conn, f"UPDATE cdk_activations SET {', '.join(sets)} WHERE id=?", vals)

def cdk_activation_list(user_id: int, limit: int = 10):
    with get_conn() as conn:
        return _fetchall(conn,
            "SELECT * FROM cdk_activations WHERE user_id=? ORDER BY id DESC LIMIT ?",
            (user_id, limit))

def cdk_activation_get(act_id: int, user_id: int):
    with get_conn() as conn:
        return _fetchone(conn,
            "SELECT * FROM cdk_activations WHERE id=? AND user_id=?", (act_id, user_id))

def cdk_activation_find_key(user_id: int, cdk_key: str):
    with get_conn() as conn:
        return _fetchone(conn,
            "SELECT * FROM cdk_activations WHERE user_id=? AND cdk_key=? ORDER BY id DESC LIMIT 1",
            (user_id, cdk_key.strip()))

def cdk_activation_reap():
    with get_conn() as conn:
        cur = _execute(conn,
            "UPDATE cdk_activations SET status='interrupted', updated_at=datetime('now') "
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
            "SELECT * FROM am_subscriptions WHERE telegram_id=? AND status='active' "
            "AND expires_at > datetime('now') ORDER BY expires_at DESC LIMIT 1", (telegram_id,))

def am_days_left(telegram_id: int) -> int:
    sub = am_get_subscription(telegram_id)
    if not sub:
        return 0
    with get_conn() as conn:
        row = _fetchone(conn,
            "SELECT CAST(CAST(julianday(expires_at) - julianday('now') AS INTEGER) AS INT) AS d "
            "FROM am_subscriptions WHERE subscription_id=?",
            (sub["subscription_id"],))
    return row["d"] if row and row["d"] > 0 else 0

def am_create_subscription(telegram_id: int, username: str, amount_paid: int, days: int = 30) -> int:
    with get_conn() as conn:
        existing = _fetchone(conn,
            "SELECT * FROM am_subscriptions WHERE telegram_id=? AND status='active' "
            "AND expires_at > datetime('now') ORDER BY expires_at DESC LIMIT 1", (telegram_id,))
        if existing:
            _execute(conn,
                "UPDATE am_subscriptions SET expires_at=datetime(expires_at, ?), amount_paid=amount_paid+? "
                "WHERE subscription_id=?", (f'+{days} days', amount_paid, existing["subscription_id"]))
            return existing["subscription_id"]
        cur = conn.execute(
            "INSERT INTO am_subscriptions (telegram_id, username, expires_at, amount_paid) "
            "VALUES (?, ?, datetime('now', ?), ?)",
            (telegram_id, username, f'+{days} days', amount_paid))
        return cur.lastrowid

def am_daily_used(telegram_id: int) -> int:
    with get_conn() as conn:
        row = _fetchone(conn,
            "SELECT count FROM am_usage WHERE telegram_id=? AND day=date('now')", (telegram_id,))
    return row["count"] if row else 0

def am_daily_remaining(telegram_id: int, cap: int = None) -> int:
    if cap is None:
        cap = int(cfg_get("am_daily_cap", "5") or 5)
    return max(0, cap - am_daily_used(telegram_id))

def am_bump_usage(telegram_id: int):
    with get_conn() as conn:
        _execute(conn,
            "INSERT INTO am_usage (telegram_id, day, count) VALUES (?, date('now'), 1) "
            "ON CONFLICT(telegram_id, day) DO UPDATE SET count=count+1",
            (telegram_id,))

def am_list_active(limit: int = 50):
    with get_conn() as conn:
        return _fetchall(conn,
            "SELECT * FROM am_subscriptions WHERE status='active' AND expires_at > datetime('now') "
            "ORDER BY expires_at LIMIT ?", (limit,))
