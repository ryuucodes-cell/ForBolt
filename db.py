import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import re


def _utcnow() -> datetime:
    """UTC-aware 'now' that matches SQLite's datetime('now')."""
    return datetime.now(timezone.utc)


def _utcnow_iso() -> str:
    """UTC ISO timestamp matching SQLite's datetime('now') format."""
    return _utcnow().strftime("%Y-%m-%d %H:%M:%S")

_DATA_DIR    = os.environ.get("DATA_DIR", os.path.join(os.path.dirname(__file__), "data"))
os.makedirs(_DATA_DIR, exist_ok=True)

DB_PATH      = os.path.join(_DATA_DIR, "stockbot.db")
DB_SOLD_PATH = os.path.join(_DATA_DIR, "stockbot_sold.db")
_lock = threading.Lock()

@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
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
    conn = sqlite3.connect(DB_SOLD_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

def init_db():
    with get_conn() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS config (
            key   TEXT PRIMARY KEY,
            value TEXT
        );

        CREATE TABLE IF NOT EXISTS users (
            telegram_id   INTEGER PRIMARY KEY,
            username      TEXT,
            mmk_balance   INTEGER NOT NULL DEFAULT 0,
            total_spent   INTEGER NOT NULL DEFAULT 0,
            notify_opt_out INTEGER NOT NULL DEFAULT 0,
            registered_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS products (
            product_id         INTEGER PRIMARY KEY AUTOINCREMENT,
            name               TEXT NOT NULL,
            category           TEXT NOT NULL DEFAULT '',
            description        TEXT NOT NULL DEFAULT '',
            price_mmk          INTEGER NOT NULL,
            credential_format  TEXT NOT NULL DEFAULT '',
            active             INTEGER NOT NULL DEFAULT 1
        );

        CREATE TABLE IF NOT EXISTS stock (
            stock_id    INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id  INTEGER NOT NULL REFERENCES products(product_id),
            credential  TEXT NOT NULL,
            status      TEXT NOT NULL DEFAULT 'available',
            sold_to     INTEGER,
            sold_at     TEXT
        );

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

        CREATE TABLE IF NOT EXISTS orders (
            order_id       INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id    INTEGER NOT NULL,
            username       TEXT,
            product_id     INTEGER NOT NULL,
            product_name   TEXT NOT NULL,
            amount_mmk     INTEGER NOT NULL,
            payment_method TEXT NOT NULL DEFAULT 'balance',
            credential     TEXT NOT NULL,
            status         TEXT NOT NULL DEFAULT 'completed',
            created_at     TEXT NOT NULL DEFAULT (datetime('now'))
        );

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
            nudge_sent       INTEGER NOT NULL DEFAULT 0,
            created_at       TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS user_state (
            telegram_id  INTEGER PRIMARY KEY,
            state_json   TEXT NOT NULL DEFAULT '{}',
            updated_at   TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS sold_stock_recovery (
            stock_id    INTEGER PRIMARY KEY,
            product_id  INTEGER NOT NULL,
            credential  TEXT NOT NULL,
            sold_to     INTEGER,
            sold_at     TEXT
        );
        """)
        _seed_config(conn)
        _migrate(conn)
    init_sold_db()

def init_sold_db():
    """Separate DB that stores only sold stock items."""
    with get_sold_conn() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS sold_stock (
            stock_id    INTEGER PRIMARY KEY,
            product_id  INTEGER NOT NULL,
            product_name TEXT NOT NULL DEFAULT '',
            credential  TEXT NOT NULL,
            sold_to     INTEGER,
            sold_at     TEXT
        );
        """)

def _migrate(conn):
    existing_products = [r[1] for r in conn.execute("PRAGMA table_info(products)").fetchall()]
    if "discount_pct" not in existing_products:
        conn.execute("ALTER TABLE products ADD COLUMN discount_pct INTEGER NOT NULL DEFAULT 0")
    if "discount_until" not in existing_products:
        conn.execute("ALTER TABLE products ADD COLUMN discount_until TEXT")
    # photo_file_id column is no longer used (product photos removed) — leave any
    # existing column in place to preserve backward-compatibility, but don't
    # create it on fresh installs.
    existing_orders = [r[1] for r in conn.execute("PRAGMA table_info(orders)").fetchall()]
    if "status" not in existing_orders:
        conn.execute("ALTER TABLE orders ADD COLUMN status TEXT NOT NULL DEFAULT 'completed'")
    existing_users = [r[1] for r in conn.execute("PRAGMA table_info(users)").fetchall()]
    if "notify_opt_out" not in existing_users:
        conn.execute("ALTER TABLE users ADD COLUMN notify_opt_out INTEGER NOT NULL DEFAULT 0")
    # Binance auto-verify: unique transaction id claimed per deposit (replay guard)
    existing_deposits = [r[1] for r in conn.execute("PRAGMA table_info(deposits)").fetchall()]
    if "binance_txn_id" not in existing_deposits:
        conn.execute("ALTER TABLE deposits ADD COLUMN binance_txn_id TEXT")
    # Pending-deposit nudge: remind admins once about stale unreviewed deposits
    if "nudge_sent" not in existing_deposits:
        conn.execute("ALTER TABLE deposits ADD COLUMN nudge_sent INTEGER NOT NULL DEFAULT 0")
    # Fulfillment v2: per-product fulfillment mode ('instant' | 'manual_activation' | 'preorder')
    # and JSON-encoded info-field labels for manual activation.
    if "fulfillment" not in existing_products:
        conn.execute("ALTER TABLE products ADD COLUMN fulfillment TEXT NOT NULL DEFAULT 'instant'")
    if "manual_fields" not in existing_products:
        conn.execute("ALTER TABLE products ADD COLUMN manual_fields TEXT NOT NULL DEFAULT ''")
    if "manual_info" not in existing_orders:
        conn.execute("ALTER TABLE orders ADD COLUMN manual_info TEXT NOT NULL DEFAULT ''")
    if "fulfillment_mode" not in existing_orders:
        conn.execute("ALTER TABLE orders ADD COLUMN fulfillment_mode TEXT NOT NULL DEFAULT ''")
    # UNIQUE index (SQLite treats multiple NULLs as distinct, so non-binance rows are fine)
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_deposits_binance_txn ON deposits(binance_txn_id)")
    # Giveaway tables (created idempotently on existing installs)
    conn.executescript("""
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

        CREATE TABLE IF NOT EXISTS giveaway_entries (
            entry_id     INTEGER PRIMARY KEY AUTOINCREMENT,
            giveaway_id  INTEGER NOT NULL,
            telegram_id  INTEGER NOT NULL,
            username     TEXT,
            entered_at   TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(giveaway_id, telegram_id)
        );

        CREATE TABLE IF NOT EXISTS giveaway_winners (
            winner_id    INTEGER PRIMARY KEY AUTOINCREMENT,
            giveaway_id  INTEGER NOT NULL,
            telegram_id  INTEGER NOT NULL,
            username     TEXT,
            credential   TEXT,
            delivered    INTEGER NOT NULL DEFAULT 0,
            order_id     INTEGER,
            won_at       TEXT NOT NULL DEFAULT (datetime('now'))
        );
    """)

    # Indexes
    conn.execute("CREATE INDEX IF NOT EXISTS idx_deposits_uid_status ON deposits(telegram_id, status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_orders_uid ON orders(telegram_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_stock_pid ON stock(product_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_user_state_updated ON user_state(updated_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_giveaways_status ON giveaways(status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_gw_entries_gid ON giveaway_entries(giveaway_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_gw_winners_gid ON giveaway_winners(giveaway_id)")
    # add end_reminded to giveaways for existing installs created before this column
    gw_cols = [r[1] for r in conn.execute("PRAGMA table_info(giveaways)").fetchall()]
    if "end_reminded" not in gw_cols:
        conn.execute("ALTER TABLE giveaways ADD COLUMN end_reminded INTEGER NOT NULL DEFAULT 0")
    # Growth: viral giveaway entries — weighted entry counts
    existing_gentries = [r[1] for r in conn.execute("PRAGMA table_info(giveaway_entries)").fetchall()]
    if "entries" not in existing_gentries:
        conn.execute("ALTER TABLE giveaway_entries ADD COLUMN entries INTEGER NOT NULL DEFAULT 1")
    # Growth: gift stock reservation
    existing_stock = [r[1] for r in conn.execute("PRAGMA table_info(stock)").fetchall()]
    if "reserved_for" not in existing_stock:
        conn.execute("ALTER TABLE stock ADD COLUMN reserved_for TEXT")
    # Growth: lifetime deposit bonus earned (display only)
    _existing_users_growth = [r[1] for r in conn.execute("PRAGMA table_info(users)").fetchall()]
    if "bonus_earned" not in _existing_users_growth:
        conn.execute("ALTER TABLE users ADD COLUMN bonus_earned INTEGER NOT NULL DEFAULT 0")
    # Growth: new tables (idempotent on existing installs)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS giveaway_invites (
            invite_id    INTEGER PRIMARY KEY AUTOINCREMENT,
            giveaway_id  INTEGER NOT NULL,
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
            round_id    INTEGER NOT NULL,
            telegram_id INTEGER NOT NULL,
            username    TEXT NOT NULL DEFAULT '',
            slot_no     INTEGER NOT NULL,
            created_at  TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(round_id, slot_no)
        );
        CREATE INDEX IF NOT EXISTS idx_ld_entries_round ON ld_entries(round_id);
        CREATE INDEX IF NOT EXISTS idx_ld_entries_user ON ld_entries(telegram_id);
    """)

    # Reseller system columns (added idempotently on existing installs)
    existing_users = [r[1] for r in conn.execute("PRAGMA table_info(users)").fetchall()]
    if "is_reseller" not in existing_users:
        conn.execute("ALTER TABLE users ADD COLUMN is_reseller INTEGER NOT NULL DEFAULT 0")
    if "reseller_since" not in existing_users:
        conn.execute("ALTER TABLE users ADD COLUMN reseller_since TEXT")
    if "last_reseller_purchase" not in existing_users:
        conn.execute("ALTER TABLE users ADD COLUMN last_reseller_purchase TEXT")

    existing_products = [r[1] for r in conn.execute("PRAGMA table_info(products)").fetchall()]
    if "reseller_price_mmk" not in existing_products:
        # nullable — NULL means "no reseller price, fall back to retail price"
        conn.execute("ALTER TABLE products ADD COLUMN reseller_price_mmk INTEGER")
    if "emoji" not in existing_products:
        # per-product display icon (fallback Unicode char) shown in the shop list
        conn.execute("ALTER TABLE products ADD COLUMN emoji TEXT NOT NULL DEFAULT ''")
    if "emoji_id" not in existing_products:
        # optional premium/custom emoji id; the `emoji` column is its plain fallback
        conn.execute("ALTER TABLE products ADD COLUMN emoji_id TEXT NOT NULL DEFAULT ''")

    conn.executescript("""
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
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_reseller_keys_code ON reseller_keys(code)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_reseller_reqs_status ON reseller_requests(status)")

    # Warranty / problem reports on orders (created idempotently)
    conn.executescript("""
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
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_order_reports_status ON order_reports(status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_order_reports_order ON order_reports(order_id)")

    # AM Pro subscriptions (Alight Motion monthly)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS am_subscriptions (
            subscription_id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id  INTEGER NOT NULL,
            username     TEXT,
            started_at   TEXT NOT NULL DEFAULT (datetime('now')),
            expires_at   TEXT NOT NULL,
            status       TEXT NOT NULL DEFAULT 'active',
            amount_paid  INTEGER NOT NULL DEFAULT 0
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_am_subs_user ON am_subscriptions(telegram_id, status)")
    # AM daily usage (5/day cap enforcement)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS am_usage (
            telegram_id INTEGER NOT NULL,
            day         TEXT NOT NULL,
            count       INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (telegram_id, day)
        )
    """)

    # One-time currency migration: convert stored money back to native MMK.
    _migrate_currency_to_mmk(conn)

    # Warm & simple UI: swap old DEFAULT taglines for the friendly ones.
    # Only exact old defaults are touched — custom admin text is preserved.
    _tagline_swaps = {
        "shop_tagline": (
            ["💎 Premium Digital Products with Instant Delivery 💎",
             "Premium Digital Products with Instant Delivery"],
            "Digital products, delivered instantly",
        ),
        "shop_subtagline": (
            ["⚡ Fast · 🔒 Secure · 🤖 Automated",
             "Fast · Secure · Automated"],
            "Fast · Safe · Simple",
        ),
    }
    for key, (olds, new) in _tagline_swaps.items():
        for old in olds:
            conn.execute("UPDATE config SET value=? WHERE key=? AND value=?", (new, key, old))


def _migrate_currency_to_mmk(conn):
    """Convert USD-cents amounts back to native MMK integers, exactly once.

    This reverses the earlier USD-cents migration. Guarded by 'currency_mode':
      - 'mmk_native'  -> already done, skip
      - 'usd_cents'   -> convert cents -> mmk using the configured rate
      - unset/other   -> fresh or pre-USD legacy install; amounts are already MMK
                         (or all zero), so just stamp the flag without touching them
    Conversion: mmk = round(cents / 100 * rate). If the rate is unchanged since
    the USD migration, balances round-trip to the exact original MMK value.
    """
    row  = conn.execute("SELECT value FROM config WHERE key='currency_mode'").fetchone()
    mode = row["value"] if row else None
    if mode == "mmk_native":
        return  # already migrated

    if mode == "usd_cents":
        rate_row = conn.execute("SELECT value FROM config WHERE key='usd_to_mmk_rate'").fetchone()
        try:
            rate = float(rate_row["value"]) if rate_row and rate_row["value"] else 4800.0
        except (ValueError, TypeError):
            rate = 4800.0
        if rate <= 0:
            rate = 4800.0

        def to_mmk(cents):
            return int(round((cents or 0) / 100 * rate))

        for r in conn.execute("SELECT telegram_id, mmk_balance, total_spent FROM users").fetchall():
            conn.execute(
                "UPDATE users SET mmk_balance=?, total_spent=? WHERE telegram_id=?",
                (to_mmk(r["mmk_balance"]), to_mmk(r["total_spent"]), r["telegram_id"]),
            )
        for r in conn.execute("SELECT product_id, price_mmk, reseller_price_mmk FROM products").fetchall():
            conn.execute(
                "UPDATE products SET price_mmk=?, reseller_price_mmk=? WHERE product_id=?",
                (to_mmk(r["price_mmk"]),
                 (to_mmk(r["reseller_price_mmk"]) if r["reseller_price_mmk"] is not None else None),
                 r["product_id"]),
            )
        for r in conn.execute("SELECT order_id, amount_mmk FROM orders").fetchall():
            conn.execute(
                "UPDATE orders SET amount_mmk=? WHERE order_id=?",
                (to_mmk(r["amount_mmk"]), r["order_id"]),
            )
        for r in conn.execute("SELECT deposit_id, amount_mmk FROM deposits").fetchall():
            conn.execute(
                "UPDATE deposits SET amount_mmk=? WHERE deposit_id=?",
                (to_mmk(r["amount_mmk"]), r["deposit_id"]),
            )

    conn.execute("INSERT OR REPLACE INTO config(key,value) VALUES('currency_mode','mmk_native')")

def _seed_config(conn):
    defaults = {
        "shop_name": "StockBot Shop",
        "welcome_message": "Welcome! 👋 Have a look around — everything you buy is delivered instantly.",
        "admin_chat_id": "",
        "admin_ids": "",
        "support_url": "",
        "admin_username": "",
        "usd_to_mmk_rate": "4800",
        "low_stock_threshold": "3",
        "kbzpay_enabled": "0",
        "kbzpay_phone": "",
        "kbzpay_name": "",
        "wavepay_enabled": "0",
        "wavepay_phone": "",
        "wavepay_name": "",
        "cbpay_enabled": "0",
        "cbpay_phone": "",
        "cbpay_name": "",
        "uabpay_enabled": "0",
        "uabpay_phone": "",
        "uabpay_name": "",
        "ayapay_enabled": "0",
        "ayapay_phone": "",
        "ayapay_name": "",
        "maintenance_mode": "0",
        "channel_url": "",
        "channel_username": "",
        "channel_url2": "",
        "channel_username2": "",
        "channel_gate_enabled": "0",
        "log_channel_id": "",
        "reseller_inactivity_days": "30",
        "deposit_nudge_hours": "6",
        "shop_tagline": "Digital products, delivered instantly",
        "shop_subtagline": "Fast · Safe · Simple",
        "fulfillment_eta": "usually within 24 hours",
        # Growth: viral giveaway invites
        "giveaway_invite_enabled": "1",
        "giveaway_invite_entries": "1",
        # Growth: deposit bonus tiers — JSON list of [min_amount_mmk, percent]
        "deposit_bonus_enabled": "1",
        "deposit_bonus_tiers": "[[0, 3], [50000, 5], [200000, 8]]",
        # Growth: VIP tiers — JSON list of {name, min lifetime spend, discount %}
        "vip_enabled": "1",
        "vip_tiers": "[{\"name\": \"Silver\", \"min\": 50000, \"discount\": 0}, {\"name\": \"Gold\", \"min\": 200000, \"discount\": 3}, {\"name\": \"Diamond\", \"min\": 500000, \"discount\": 5}]",
        # Growth: gifts expire after N days (stock returns to sale)
        "gift_expiry_days": "30",
        # AM Pro (Alight Motion monthly subscription)
        "am_enabled": "0",
        "am_monthly_price": "0",
        "am_daily_cap": "5",
    }
    for k, v in defaults.items():
        conn.execute("INSERT OR IGNORE INTO config(key,value) VALUES(?,?)", (k, v))

# ── config ───────────────────────────────────────────────────────────────────

def cfg_get(key: str, default=None):
    with get_conn() as conn:
        row = conn.execute("SELECT value FROM config WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

def cfg_set(key: str, value):
    with get_conn() as conn:
        conn.execute("INSERT OR REPLACE INTO config(key,value) VALUES(?,?)", (key, str(value)))

def cfg_all() -> dict:
    with get_conn() as conn:
        rows = conn.execute("SELECT key,value FROM config").fetchall()
        return {r["key"]: r["value"] for r in rows}

# ── users ────────────────────────────────────────────────────────────────────

def ensure_user(telegram_id: int, username: str = None):
    with get_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO users(telegram_id,username) VALUES(?,?)",
            (telegram_id, username or ""),
        )
        if username:
            conn.execute(
                "UPDATE users SET username=? WHERE telegram_id=?",
                (username, telegram_id),
            )

def get_user(telegram_id: int):
    with get_conn() as conn:
        return conn.execute("SELECT * FROM users WHERE telegram_id=?", (telegram_id,)).fetchone()

def get_balance(telegram_id: int) -> int:
    u = get_user(telegram_id)
    return u["mmk_balance"] if u else 0

def adjust_balance(telegram_id: int, delta: int):
    with get_conn() as conn:
        conn.execute(
            "UPDATE users SET mmk_balance = mmk_balance + ? WHERE telegram_id=?",
            (delta, telegram_id),
        )

def adjust_balance_clamped(telegram_id: int, delta: int) -> int:
    """Apply delta but never let the balance drop below 0; return the new balance.

    Use for admin balance edits so a large 'remove' can't drive a user negative.
    Refund/credit paths keep using adjust_balance (unconditional) since they only
    ever add. Runs under _lock so the read-modify isn't racy.
    """
    with _lock:
        with get_conn() as conn:
            conn.execute(
                "UPDATE users SET mmk_balance = MAX(0, mmk_balance + ?) WHERE telegram_id=?",
                (delta, telegram_id),
            )
            row = conn.execute(
                "SELECT mmk_balance FROM users WHERE telegram_id=?", (telegram_id,)
            ).fetchone()
            return row["mmk_balance"] if row else 0

def deduct_balance_atomic(telegram_id: int, amount: int) -> bool:
    """Deduct amount only if sufficient balance exists. Returns True on success."""
    with _lock:
        with get_conn() as conn:
            cur = conn.execute(
                "UPDATE users SET mmk_balance = mmk_balance - ? "
                "WHERE telegram_id=? AND mmk_balance >= ?",
                (amount, telegram_id, amount),
            )
            return cur.rowcount == 1

def list_users(offset=0, limit=20):
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM users ORDER BY registered_at DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()

def set_notify_opt_out(telegram_id: int, value: bool):
    with get_conn() as conn:
        conn.execute("UPDATE users SET notify_opt_out=? WHERE telegram_id=?", (1 if value else 0, telegram_id))

def get_notify_opt_out(telegram_id: int) -> bool:
    with get_conn() as conn:
        row = conn.execute("SELECT notify_opt_out FROM users WHERE telegram_id=?", (telegram_id,)).fetchone()
        return bool(row["notify_opt_out"]) if row else False

def count_users() -> int:
    with get_conn() as conn:
        return conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]

# ── resellers ────────────────────────────────────────────────────────────────

def is_reseller(telegram_id: int) -> bool:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT is_reseller FROM users WHERE telegram_id=?", (telegram_id,)
        ).fetchone()
        return bool(row["is_reseller"]) if row else False

def set_reseller(telegram_id: int, value: bool):
    """Grant or revoke reseller status. On grant, stamp reseller_since and reset
    the activity clock so a freshly-granted reseller isn't instantly eligible for
    inactivity removal."""
    now = _utcnow_iso()
    with get_conn() as conn:
        if value:
            conn.execute(
                "UPDATE users SET is_reseller=1, reseller_since=?, last_reseller_purchase=? "
                "WHERE telegram_id=?",
                (now, now, telegram_id),
            )
        else:
            conn.execute(
                "UPDATE users SET is_reseller=0, reseller_since=NULL, last_reseller_purchase=NULL "
                "WHERE telegram_id=?",
                (telegram_id,),
            )

def touch_reseller_activity(telegram_id: int):
    """Record that a reseller just made a purchase — resets their inactivity clock."""
    with get_conn() as conn:
        conn.execute(
            "UPDATE users SET last_reseller_purchase=? WHERE telegram_id=? AND is_reseller=1",
            (_utcnow_iso(), telegram_id),
        )

def list_resellers():
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM users WHERE is_reseller=1 ORDER BY reseller_since DESC"
        ).fetchall()

def count_resellers() -> int:
    with get_conn() as conn:
        return conn.execute("SELECT COUNT(*) FROM users WHERE is_reseller=1").fetchone()[0]

def revoke_inactive_resellers(days: int):
    """Revoke reseller status for anyone whose last reseller purchase is older than
    `days`. Returns a list of (telegram_id, username) that were revoked so the
    caller can notify them."""
    cutoff = (_utcnow() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    with _lock:
        with get_conn() as conn:
            rows = conn.execute(
                "SELECT telegram_id, username FROM users "
                "WHERE is_reseller=1 AND (last_reseller_purchase IS NULL OR last_reseller_purchase < ?)",
                (cutoff,),
            ).fetchall()
            revoked = [(r["telegram_id"], r["username"]) for r in rows]
            if revoked:
                conn.execute(
                    "UPDATE users SET is_reseller=0, reseller_since=NULL, last_reseller_purchase=NULL "
                    "WHERE is_reseller=1 AND (last_reseller_purchase IS NULL OR last_reseller_purchase < ?)",
                    (cutoff,),
                )
            return revoked

# ── reseller keys ──────────────────────────────────────────────────────────────

def create_reseller_key(code: str, created_by: int, note: str = None) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO reseller_keys(code, created_by, note) VALUES(?,?,?)",
            (code, created_by, note),
        )
        return cur.lastrowid

def redeem_reseller_key(code: str, telegram_id: int) -> str:
    """Atomically redeem a key and grant reseller status. Returns one of:
    'ok' | 'not_found' | 'used' | 'revoked' | 'already_reseller'."""
    with _lock:
        with get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM reseller_keys WHERE code=?", (code,)
            ).fetchone()
            if not row:
                return "not_found"
            if row["revoked"]:
                return "revoked"
            if row["used_by"]:
                return "used"
            already = conn.execute(
                "SELECT is_reseller FROM users WHERE telegram_id=?", (telegram_id,)
            ).fetchone()
            if already and already["is_reseller"]:
                return "already_reseller"
            now = _utcnow_iso()
            conn.execute(
                "UPDATE reseller_keys SET used_by=?, used_at=? WHERE key_id=? AND used_by IS NULL",
                (telegram_id, now, row["key_id"]),
            )
            conn.execute(
                "UPDATE users SET is_reseller=1, reseller_since=?, last_reseller_purchase=? "
                "WHERE telegram_id=?",
                (now, now, telegram_id),
            )
            return "ok"

def list_reseller_keys(only_unused=False, limit=50):
    with get_conn() as conn:
        q = "SELECT * FROM reseller_keys"
        if only_unused:
            q += " WHERE used_by IS NULL AND revoked=0"
        q += " ORDER BY created_at DESC LIMIT ?"
        return conn.execute(q, (limit,)).fetchall()

def revoke_reseller_key(key_id: int) -> bool:
    """Revoke an unused key. Returns False if it was already used or not found."""
    with get_conn() as conn:
        cur = conn.execute(
            "UPDATE reseller_keys SET revoked=1 WHERE key_id=? AND used_by IS NULL AND revoked=0",
            (key_id,),
        )
        return cur.rowcount == 1

def count_unused_reseller_keys() -> int:
    with get_conn() as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM reseller_keys WHERE used_by IS NULL AND revoked=0"
        ).fetchone()[0]

# ── reseller requests ────────────────────────────────────────────────────────

def create_reseller_request(telegram_id: int, username: str, message: str = None) -> int | None:
    """Create a pending access request. Returns None if the user already has one pending."""
    with _lock:
        with get_conn() as conn:
            existing = conn.execute(
                "SELECT request_id FROM reseller_requests WHERE telegram_id=? AND status='pending'",
                (telegram_id,),
            ).fetchone()
            if existing:
                return None
            cur = conn.execute(
                "INSERT INTO reseller_requests(telegram_id, username, message) VALUES(?,?,?)",
                (telegram_id, username or "", message),
            )
            return cur.lastrowid

def get_reseller_request(request_id: int):
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM reseller_requests WHERE request_id=?", (request_id,)
        ).fetchone()

def list_reseller_requests(status="pending", limit=20, offset=0):
    with get_conn() as conn:
        if status:
            return conn.execute(
                "SELECT * FROM reseller_requests WHERE status=? ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (status, limit, offset),
            ).fetchall()
        return conn.execute(
            "SELECT * FROM reseller_requests ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()

def count_reseller_requests(status="pending") -> int:
    with get_conn() as conn:
        if status:
            return conn.execute(
                "SELECT COUNT(*) FROM reseller_requests WHERE status=?", (status,)
            ).fetchone()[0]
        return conn.execute("SELECT COUNT(*) FROM reseller_requests").fetchone()[0]

def resolve_reseller_request_atomic(request_id: int, status: str, handled_by: int):
    """Mark a pending request approved/rejected. Guards on status='pending'.
    Returns the request row on success, or None if already handled/not found."""
    with _lock:
        with get_conn() as conn:
            r = conn.execute(
                "SELECT * FROM reseller_requests WHERE request_id=?", (request_id,)
            ).fetchone()
            if not r or r["status"] != "pending":
                return None
            cur = conn.execute(
                "UPDATE reseller_requests SET status=?, handled_by=?, handled_at=? "
                "WHERE request_id=? AND status='pending'",
                (status, handled_by, _utcnow_iso(), request_id),
            )
            if cur.rowcount != 1:
                return None
            return r

# ── products ─────────────────────────────────────────────────────────────────

def add_product(name, category, description, price_mmk, credential_format):
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO products(name,category,description,price_mmk,credential_format) VALUES(?,?,?,?,?)",
            (name, category, description, price_mmk, credential_format),
        )
        return cur.lastrowid

def get_product(product_id: int):
    with get_conn() as conn:
        return conn.execute("SELECT * FROM products WHERE product_id=?", (product_id,)).fetchone()

def list_products(active_only=True):
    with get_conn() as conn:
        q = "SELECT * FROM products" + (" WHERE active=1" if active_only else "") + " ORDER BY name COLLATE NOCASE ASC"
        rows = conn.execute(q).fetchall()

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
        q = "SELECT DISTINCT category FROM products" + (" WHERE active=1" if active_only else "")
        rows = conn.execute(q).fetchall()
        return [r["category"] for r in rows]

def update_product(product_id: int, **fields):
    allowed = {"name", "category", "description", "price_mmk", "credential_format", "active", "discount_pct", "discount_until", "reseller_price_mmk", "emoji", "emoji_id"}
    fields = {k: v for k, v in fields.items() if k in allowed}
    if not fields:
        return
    sets = ", ".join(f"{k}=?" for k in fields)
    vals = list(fields.values()) + [product_id]
    with get_conn() as conn:
        conn.execute(f"UPDATE products SET {sets} WHERE product_id=?", vals)

def set_discount(product_id: int, pct: int, until_iso: str):
    with get_conn() as conn:
        conn.execute(
            "UPDATE products SET discount_pct=?, discount_until=? WHERE product_id=?",
            (pct, until_iso, product_id),
        )

def clear_discount(product_id: int):
    with get_conn() as conn:
        conn.execute(
            "UPDATE products SET discount_pct=0, discount_until=NULL WHERE product_id=?",
            (product_id,),
        )

def expire_discounts():
    """Clear discounts whose until time has passed. Called by background thread."""
    now = _utcnow_iso()
    with get_conn() as conn:
        conn.execute(
            "UPDATE products SET discount_pct=0, discount_until=NULL "
            "WHERE discount_pct > 0 AND discount_until IS NOT NULL AND discount_until <= ?",
            (now,),
        )

def get_effective_price(product) -> int:
    """Return discounted price if an active discount exists, else original price."""
    pct   = product["discount_pct"] or 0
    until = product["discount_until"]
    if pct > 0 and until and datetime.fromisoformat(until) > _utcnow().replace(tzinfo=None):
        return max(1, int(product["price_mmk"] * (100 - pct) / 100))
    return product["price_mmk"]

def get_effective_price_for_id(product_id: int) -> int:
    """Thread-safe: fetch product and compute effective price in one call."""
    p = get_product(product_id)
    if not p:
        return 0
    return get_effective_price(p)

def product_emoji(product, default="📦") -> str:
    """Plain-fallback display icon (for BUTTONS — custom emoji can't render there).
    Safe on rows from installs before the column existed."""
    try:
        e = product["emoji"]
    except (IndexError, KeyError):
        return default
    e = (e or "").strip()
    return e if e else default

def product_emoji_id(product) -> str:
    """The product's premium custom-emoji id, or '' if none set."""
    try:
        eid = product["emoji_id"]
    except (IndexError, KeyError):
        return ""
    return (eid or "").strip()

def product_emoji_html(product, default="📦") -> str:
    """Rich display icon for MESSAGE TEXT/captions: a premium custom-emoji span when
    an id is set, otherwise the plain fallback. Requires parse_mode=HTML."""
    from utils import tg_emoji
    fallback = product_emoji(product, default=default)
    eid = product_emoji_id(product)
    return tg_emoji(fallback, eid) if eid else fallback

def _reseller_price(product):
    """Return the product's reseller price if set (>0), else None.

    Uses .keys() guard so it's safe on rows from installs before the column existed.
    """
    try:
        rp = product["reseller_price_mmk"]
    except (IndexError, KeyError):
        return None
    if rp is None or rp <= 0:
        return None
    return rp

def get_reseller_price(product) -> int:
    """Price a reseller pays. Reseller price ignores time-limited discounts —
    resellers already get their standing rate. Falls back to the retail effective
    price (discounts included) when no reseller price is configured."""
    rp = _reseller_price(product)
    if rp is not None:
        return rp
    return get_effective_price(product)

def get_price_for_user(product, telegram_id: int) -> int:
    """Effective price for a specific user: reseller rate if they're a reseller,
    otherwise the normal retail (discount-aware) price with their VIP tier
    discount applied on top."""
    if is_reseller(telegram_id):
        return get_reseller_price(product)
    price = get_effective_price(product)
    _tier_name, disc = get_user_vip_tier(telegram_id)
    if disc > 0:
        price = price * (100 - disc) // 100
    return price

def get_price_for_user_by_id(product_id: int, telegram_id: int) -> int:
    """Thread-safe: fetch product and compute the user-specific price in one call.
    Used at charge time so the reseller rate is authoritative."""
    p = get_product(product_id)
    if not p:
        return 0
    return get_price_for_user(p, telegram_id)

def is_discount_active(product) -> bool:
    pct   = product["discount_pct"] or 0
    until = product["discount_until"]
    return pct > 0 and bool(until) and datetime.fromisoformat(until) > _utcnow().replace(tzinfo=None)

def delete_product(product_id: int) -> bool:
    with get_conn() as conn:
        cnt = conn.execute(
            "SELECT COUNT(*) FROM stock WHERE product_id=?", (product_id,)
        ).fetchone()[0]
        if cnt > 0:
            return False
        conn.execute("DELETE FROM products WHERE product_id=?", (product_id,))
        return True

def stock_count(product_id: int) -> int:
    """Available (unreserved) stock for sale."""
    with get_conn() as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM stock WHERE product_id=? AND reserved_for IS NULL", (product_id,)
        ).fetchone()[0]

# ── stock ─────────────────────────────────────────────────────────────────────

def add_stock_bulk(product_id: int, credentials: list) -> int:
    # store raw — escape at display time, not insert time
    clean = [c.strip() for c in credentials if c.strip()]
    rows  = [(product_id, c) for c in clean]
    with get_conn() as conn:
        conn.executemany(
            "INSERT INTO stock(product_id,credential) VALUES(?,?)", rows
        )
    return len(rows)

def claim_stock(product_id: int, telegram_id: int) -> str | None:
    """Atomically claim one available stock item. Returns credential or None."""
    with _lock:
        # Phase 1: claim from main DB. Loop so that if a row is snatched by another
        # process between our SELECT and DELETE, we retry the next available row
        # instead of returning a credential we didn't actually claim. The guarded
        # DELETE + rowcount check is the authoritative claim — only the process
        # whose DELETE affects 1 row owns that credential (matches the atomic
        # pattern used by deduct_balance_atomic / approve_deposit_atomic).
        with get_conn() as conn:
            credential = stock_id = None
            pname = ""
            sold_at = _utcnow_iso()
            while True:
                row = conn.execute(
                    "SELECT * FROM stock WHERE product_id=? AND reserved_for IS NULL LIMIT 1",
                    (product_id,),
                ).fetchone()
                if not row:
                    return None
                cur = conn.execute(
                    "DELETE FROM stock WHERE stock_id=? AND reserved_for IS NULL", (row["stock_id"],)
                )
                if cur.rowcount == 1:
                    credential = row["credential"]
                    stock_id   = row["stock_id"]
                    p = conn.execute("SELECT name FROM products WHERE product_id=?", (product_id,)).fetchone()
                    pname = p["name"] if p else ""
                    break
                # else: another claimer won this row — try the next one

        # Phase 2: mirror to the sold DB (best-effort, with a recovery fallback).
        _mirror_sold(stock_id, product_id, pname, credential, telegram_id, sold_at)
        return credential


def _mirror_sold(stock_id, product_id, pname, credential, telegram_id, sold_at):
    """Write a claimed item into the separate sold DB. Best-effort: if the sold DB
    is unavailable the item is already gone from available stock, so we fall back
    to a recovery table in the main DB. Never raises."""
    try:
        with get_sold_conn() as sconn:
            sconn.execute(
                "INSERT OR IGNORE INTO sold_stock(stock_id,product_id,product_name,credential,sold_to,sold_at) VALUES(?,?,?,?,?,?)",
                (stock_id, product_id, pname, credential, telegram_id, sold_at),
            )
    except Exception:
        try:
            with get_conn() as conn:
                conn.execute(
                    "INSERT OR IGNORE INTO sold_stock_recovery(stock_id,product_id,credential,sold_to,sold_at) VALUES(?,?,?,?,?)",
                    (stock_id, product_id, credential, telegram_id, sold_at),
                )
        except Exception:
            pass

def list_stock_items(product_id: int, limit=50):
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM stock WHERE product_id=? ORDER BY stock_id DESC LIMIT ?",
            (product_id, limit),
        ).fetchall()

def list_sold_items(product_id: int = None, limit=50):
    with get_sold_conn() as conn:
        if product_id:
            return conn.execute(
                "SELECT * FROM sold_stock WHERE product_id=? ORDER BY sold_at DESC LIMIT ?",
                (product_id, limit),
            ).fetchall()
        return conn.execute(
            "SELECT * FROM sold_stock ORDER BY sold_at DESC LIMIT ?", (limit,)
        ).fetchall()

def delete_stock_item(stock_id: int):
    with get_conn() as conn:
        conn.execute("DELETE FROM stock WHERE stock_id=?", (stock_id,))

# ── orders ────────────────────────────────────────────────────────────────────

def create_order(telegram_id, username, product_id, product_name, amount_mmk, credential, payment_method="balance"):
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO orders(telegram_id,username,product_id,product_name,amount_mmk,payment_method,credential) VALUES(?,?,?,?,?,?,?)",
            (telegram_id, username, product_id, product_name, amount_mmk, payment_method, credential),
        )
        return cur.lastrowid

class _InsufficientFunds(Exception):
    """Internal: raised inside purchase_atomic's transaction to force a rollback of
    already-claimed stock when the balance check fails. Never escapes the module."""
    def __init__(self, need, balance):
        self.need = need
        self.balance = balance
        super().__init__("insufficient funds")


def purchase_atomic(product_id, telegram_id, username, unit_price, qty, product_name):
    """Claim up to `qty` stock items AND deduct balance for exactly what was claimed,
    in ONE main-DB transaction. Closes the crash window where balance is deducted but
    stock is lost (or vice-versa): stock, balance, and order rows all commit together
    or roll back together.

    Returns a dict:
      {"ok": True, "orders": [(order_id, credential), ...], "charged": int,
       "claimed": int, "new_balance": int}
      {"ok": False, "reason": "out_of_stock"}      — no stock at all
      {"ok": False, "reason": "insufficient", "need": int, "balance": int}

    Pricing is authoritative here (caller passes the price it already resolved for
    this user). The sold-DB mirror is done AFTER commit, best-effort, since it lives
    in a separate database and must never roll back a paid, delivered order.
    """
    from datetime import datetime
    with _lock:
        claimed = []      # (stock_id, credential) — populated inside the txn
        orders = []       # (order_id, credential)
        charged = 0
        new_balance = 0
        try:
            with get_conn() as conn:
                # 1) claim up to qty rows via guarded DELETEs (skip rows lost to a racer)
                attempts = 0
                while len(claimed) < qty:
                    row = conn.execute(
                        "SELECT stock_id, credential FROM stock WHERE product_id=? AND reserved_for IS NULL LIMIT 1",
                        (product_id,),
                    ).fetchone()
                    if not row:
                        break
                    cur = conn.execute("DELETE FROM stock WHERE stock_id=?", (row["stock_id"],))
                    if cur.rowcount == 1:
                        claimed.append((row["stock_id"], row["credential"]))
                    attempts += 1
                    if attempts > qty + 50:      # safety valve against a pathological loop
                        break

                if not claimed:
                    return {"ok": False, "reason": "out_of_stock"}

                actual_qty = len(claimed)
                charged    = unit_price * actual_qty

                # 2) deduct balance for exactly what we claimed — guarded so we never overdraw
                cur = conn.execute(
                    "UPDATE users SET mmk_balance = mmk_balance - ? "
                    "WHERE telegram_id=? AND mmk_balance >= ?",
                    (charged, telegram_id, charged),
                )
                if cur.rowcount != 1:
                    bal_row = conn.execute(
                        "SELECT mmk_balance FROM users WHERE telegram_id=?", (telegram_id,)
                    ).fetchone()
                    bal = bal_row["mmk_balance"] if bal_row else 0
                    # raise to roll back the stock DELETEs, then report insufficient funds
                    raise _InsufficientFunds(charged, bal)

                conn.execute(
                    "UPDATE users SET total_spent = total_spent + ? WHERE telegram_id=?",
                    (charged, telegram_id),
                )

                # 3) record one order row per claimed item (same transaction)
                for stock_id, credential in claimed:
                    oc = conn.execute(
                        "INSERT INTO orders(telegram_id,username,product_id,product_name,amount_mmk,payment_method,credential) "
                        "VALUES(?,?,?,?,?,?,?)",
                        (telegram_id, username, product_id, product_name, unit_price, "balance", credential),
                    )
                    orders.append((oc.lastrowid, credential))

                new_bal_row = conn.execute(
                    "SELECT mmk_balance FROM users WHERE telegram_id=?", (telegram_id,)
                ).fetchone()
                new_balance = new_bal_row["mmk_balance"] if new_bal_row else 0
        except _InsufficientFunds as e:
            # transaction rolled back — no stock claimed, no balance moved
            return {"ok": False, "reason": "insufficient", "need": e.need, "balance": e.balance}

        # committed. mirror to sold DB best-effort (never rolls back the sale).
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
            return conn.execute(
                "SELECT * FROM orders WHERE telegram_id=? ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (telegram_id, limit, offset),
            ).fetchall()
        return conn.execute(
            "SELECT * FROM orders ORDER BY created_at DESC LIMIT ? OFFSET ?", (limit, offset)
        ).fetchall()

def get_order(order_id: int):
    with get_conn() as conn:
        return conn.execute("SELECT * FROM orders WHERE order_id=?", (order_id,)).fetchone()


# ── fulfillment v2 (manual activation / pre-order) ────────────────────────────

FULFILLMENT_MODES = ("instant", "preorder")

def get_fulfillment(product) -> str:
    """Fulfillment mode for a product row (defaults to 'instant').

    Legacy 'manual_activation' products (the feature was removed in V1.5)
    are treated as pre-orders everywhere."""
    try:
        mode = (product["fulfillment"] or "instant").strip().lower()
    except (KeyError, TypeError, AttributeError):
        return "instant"
    if mode == "manual_activation":
        return "preorder"
    return mode if mode in FULFILLMENT_MODES else "instant"


def set_product_fulfillment(product_id: int, fulfillment: str, manual_fields: str = ""):
    """Set a product's fulfillment mode + JSON info-field labels."""
    if fulfillment == "manual_activation":
        fulfillment = "preorder"
    if fulfillment not in FULFILLMENT_MODES:
        fulfillment = "instant"
    with get_conn() as conn:
        conn.execute(
            "UPDATE products SET fulfillment=?, manual_fields=? WHERE product_id=?",
            (fulfillment, manual_fields or "", product_id),
        )


def get_manual_fields(product) -> list:
    """Parse a product's manual_fields JSON into a list of labels."""
    import json
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
    """Create a pre-order (fulfillment) order: no credential yet, status
    'awaiting_fulfillment', mode frozen on the order so later product edits
    can't confuse the admin queue. (Name kept for historical reasons —
    manual activation was removed in V1.5; any legacy mode value coerces
    to 'preorder'.)"""
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO orders(telegram_id,username,product_id,product_name,amount_mmk,"
            "payment_method,credential,status,fulfillment_mode) VALUES(?,?,?,?,?,?,?,?,?)",
            (telegram_id, username, product_id, product_name, amount_mmk,
             payment_method, "", "awaiting_fulfillment", "preorder"),
        )
        return cur.lastrowid


def create_preorder_atomic(telegram_id, username, product_id, product_name, total_mmk):
    """Charge the wallet AND create the pre-order in ONE transaction.

    Mirrors purchase_atomic's guarantee for the pre-order path: balance
    deduction and order insert commit together or roll back together, so
    a crash can never leave a customer charged with no order (the V1.5
    fix for the old deduct-then-create two-step). Returns the order_id,
    or None if the balance is insufficient."""
    with _lock:
        with get_conn() as conn:
            cur = conn.execute(
                "UPDATE users SET mmk_balance = mmk_balance - ? "
                "WHERE telegram_id=? AND mmk_balance >= ?",
                (total_mmk, telegram_id, total_mmk),
            )
            if cur.rowcount != 1:
                return None
            cur = conn.execute(
                "INSERT INTO orders(telegram_id,username,product_id,product_name,amount_mmk,"
                "payment_method,credential,status,fulfillment_mode) VALUES(?,?,?,?,?,?,?,?,?)",
                (telegram_id, username, product_id, product_name, total_mmk,
                 "balance", "", "awaiting_fulfillment", "preorder"),
            )
            conn.execute(
                "UPDATE users SET total_spent = total_spent + ? WHERE telegram_id=?",
                (total_mmk, telegram_id),
            )
            return cur.lastrowid


def get_order_fulfillment(order) -> str:
    """Fulfillment mode frozen on the order; falls back to inference for old rows.
    Instant orders (no fulfillment markers) correctly resolve to 'instant'.
    Legacy manual-activation orders resolve to 'preorder' (feature removed
    in V1.5 — they are worked from the same admin queue)."""
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
    """Store the customer's submitted info (JSON) and move to 'awaiting_fulfillment'."""
    import json
    with get_conn() as conn:
        conn.execute(
            "UPDATE orders SET manual_info=?, status='awaiting_fulfillment' WHERE order_id=?",
            (json.dumps(info, ensure_ascii=False), order_id),
        )


def update_manual_info(order_id: int, info: dict):
    """Save a draft of the customer's info WITHOUT changing order status.
    Called after every answer so nothing is lost if the user stops mid-flow."""
    import json
    with get_conn() as conn:
        conn.execute(
            "UPDATE orders SET manual_info=? WHERE order_id=?",
            (json.dumps(info, ensure_ascii=False), order_id),
        )


def get_manual_info(order) -> dict:
    """Parse an order's manual_info JSON into a dict."""
    import json
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
        conn.execute("UPDATE orders SET status=? WHERE order_id=?", (status, order_id))


def set_order_credential(order_id: int, credential: str):
    """Store the admin-supplied credential (pre-order delivery)."""
    with get_conn() as conn:
        conn.execute("UPDATE orders SET credential=? WHERE order_id=?", (credential, order_id))


def list_fulfillment_orders(limit: int = 50):
    """Pending non-instant orders for the admin fulfillment queue."""
    with get_conn() as conn:
        return conn.execute(
            "SELECT o.*, p.fulfillment AS mode FROM orders o "
            "LEFT JOIN products p ON p.product_id=o.product_id "
            "WHERE o.status IN ('awaiting_info','awaiting_fulfillment') "
            "ORDER BY o.created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()


def count_fulfillment_orders() -> int:
    with get_conn() as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM orders WHERE status IN ('awaiting_info','awaiting_fulfillment')"
        ).fetchone()[0]

# NOTE: a status-only refund_order() used to live here. It was removed because it
# marked an order 'refunded' WITHOUT crediting the user back — a money-loss footgun
# if ever called. Always use refund_order_atomic(), which does both atomically.

def refund_order_atomic(order_id: int, only_statuses=None):
    """Mark an order refunded and credit the user back in one atomic transaction.

    Guards on the current status so two concurrent confirms can't double-credit.
    Returns the order row on success, or None if the order was already refunded
    or not found. When only_statuses is given, the refund only lands if the
    order is currently in one of those statuses (the customer cancel path
    passes the two awaiting statuses, so a last-second admin fulfillment
    can never be refunded out from under the shop)."""
    with _lock:
        with get_conn() as conn:
            o = conn.execute("SELECT * FROM orders WHERE order_id=?", (order_id,)).fetchone()
            if not o or o["status"] == "refunded":
                return None
            if only_statuses and o["status"] not in only_statuses:
                return None
            if only_statuses:
                placeholders = ",".join("?" for _ in only_statuses)
                cur = conn.execute(
                    f"UPDATE orders SET status='refunded' WHERE order_id=? AND status IN ({placeholders})",
                    (order_id, *only_statuses),
                )
            else:
                cur = conn.execute(
                    "UPDATE orders SET status='refunded' WHERE order_id=? AND status!='refunded'",
                    (order_id,),
                )
            if cur.rowcount != 1:
                return None
            conn.execute(
                "UPDATE users SET mmk_balance = mmk_balance + ? WHERE telegram_id=?",
                (o["amount_mmk"], o["telegram_id"]),
            )
            conn.execute(
                "UPDATE users SET total_spent=MAX(0,total_spent-?) WHERE telegram_id=?",
                (o["amount_mmk"], o["telegram_id"]),
            )
            return o

def count_orders() -> int:
    with get_conn() as conn:
        return conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0]

def count_orders_for_user(telegram_id: int) -> int:
    with get_conn() as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM orders WHERE telegram_id=?", (telegram_id,)
        ).fetchone()[0]

def count_deposits_for_user(telegram_id: int) -> int:
    with get_conn() as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM deposits WHERE telegram_id=?", (telegram_id,)
        ).fetchone()[0]

def search_user(query: str):
    """Search by username (partial) or exact telegram_id."""
    with get_conn() as conn:
        if query.isdigit():
            return conn.execute(
                "SELECT * FROM users WHERE telegram_id=?", (int(query),)
            ).fetchone()
        return conn.execute(
            "SELECT * FROM users WHERE username LIKE ? LIMIT 1", (f"%{query}%",)
        ).fetchone()

# ── order problem reports (warranty) ─────────────────────────────────────────

def create_order_report(order_id: int, telegram_id: int, username: str, message: str = None):
    """File a problem report for an order. Returns report_id, or None if a
    pending report already exists for this order (one open case at a time)."""
    with _lock:
        with get_conn() as conn:
            existing = conn.execute(
                "SELECT report_id FROM order_reports WHERE order_id=? AND status='pending'",
                (order_id,),
            ).fetchone()
            if existing:
                return None
            cur = conn.execute(
                "INSERT INTO order_reports(order_id, telegram_id, username, message) VALUES(?,?,?,?)",
                (order_id, telegram_id, username or "", message),
            )
            return cur.lastrowid

def get_order_report(report_id: int):
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM order_reports WHERE report_id=?", (report_id,)
        ).fetchone()

def order_has_pending_report(order_id: int) -> bool:
    with get_conn() as conn:
        return conn.execute(
            "SELECT 1 FROM order_reports WHERE order_id=? AND status='pending'",
            (order_id,),
        ).fetchone() is not None

def list_order_reports(status="pending", limit=10, offset=0):
    with get_conn() as conn:
        if status:
            return conn.execute(
                "SELECT * FROM order_reports WHERE status=? ORDER BY created_at ASC LIMIT ? OFFSET ?",
                (status, limit, offset),
            ).fetchall()
        return conn.execute(
            "SELECT * FROM order_reports ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()

def count_order_reports(status="pending") -> int:
    with get_conn() as conn:
        if status:
            return conn.execute(
                "SELECT COUNT(*) FROM order_reports WHERE status=?", (status,)
            ).fetchone()[0]
        return conn.execute("SELECT COUNT(*) FROM order_reports").fetchone()[0]

def resolve_order_report_atomic(report_id: int, status: str, handled_by: int):
    """Mark a pending report replaced/refunded/dismissed. Guards on 'pending' so
    two admins can't resolve the same case twice. Returns the report row on
    success, or None if already handled / not found."""
    with _lock:
        with get_conn() as conn:
            r = conn.execute(
                "SELECT * FROM order_reports WHERE report_id=?", (report_id,)
            ).fetchone()
            if not r or r["status"] != "pending":
                return None
            cur = conn.execute(
                "UPDATE order_reports SET status=?, handled_by=?, handled_at=? "
                "WHERE report_id=? AND status='pending'",
                (status, handled_by, _utcnow_iso(), report_id),
            )
            if cur.rowcount != 1:
                return None
            return r

def reopen_order_report(report_id: int):
    """Set a report back to pending (e.g. replacement failed for lack of stock)."""
    with get_conn() as conn:
        conn.execute(
            "UPDATE order_reports SET status='pending', handled_by=NULL, handled_at=NULL "
            "WHERE report_id=?",
            (report_id,),
        )

# ── deposits ──────────────────────────────────────────────────────────────────

def create_deposit(telegram_id, username, method, amount_mmk, screenshot_file_id=None, note=None):
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO deposits(telegram_id,username,method,amount_mmk,screenshot_file_id,note) VALUES(?,?,?,?,?,?)",
            (telegram_id, username, method, amount_mmk, screenshot_file_id, note),
        )
        return cur.lastrowid

def get_deposit(deposit_id: int):
    with get_conn() as conn:
        return conn.execute("SELECT * FROM deposits WHERE deposit_id=?", (deposit_id,)).fetchone()

def update_deposit(deposit_id: int, status: str, approved_by=None, note=None):
    with get_conn() as conn:
        conn.execute(
            "UPDATE deposits SET status=?, approved_by=?, note=? WHERE deposit_id=?",
            (status, approved_by, note, deposit_id),
        )

def approve_deposit_atomic(deposit_id: int, approved_by: int):
    """Approve a pending deposit and credit the user in one atomic transaction.

    Guards on status='pending' so two concurrent approvals can't double-credit.
    Applies the configured deposit bonus tier on top of the deposited amount.
    Returns (deposit row, bonus_mmk) on success, or None if already handled/not found.
    """
    with _lock:
        with get_conn() as conn:
            d = conn.execute("SELECT * FROM deposits WHERE deposit_id=?", (deposit_id,)).fetchone()
            if not d or d["status"] != "pending":
                return None
            cur = conn.execute(
                "UPDATE deposits SET status='approved', approved_by=? WHERE deposit_id=? AND status='pending'",
                (approved_by, deposit_id),
            )
            if cur.rowcount != 1:
                return None
            bonus = 0
            if cfg_get("deposit_bonus_enabled", "1") == "1":
                pct = get_deposit_bonus_percent(d["amount_mmk"])
                bonus = d["amount_mmk"] * pct // 100
            credit = conn.execute(
                "UPDATE users SET mmk_balance = mmk_balance + ?, bonus_earned = bonus_earned + ? WHERE telegram_id=?",
                (d["amount_mmk"] + bonus, bonus, d["telegram_id"]),
            )
            if credit.rowcount != 1:
                # No user row to credit — roll everything back (deposit
                # stays pending) instead of approving into the void.
                raise RuntimeError(f"approve_deposit: user row missing for {d['telegram_id']}")
            return d, bonus

def reject_deposit_atomic(deposit_id: int, rejected_by: int, note=None):
    """Reject a pending deposit in one atomic transaction, guarding on status='pending'.

    Returns the deposit row on success, or None if it was already handled or not found.
    """
    with _lock:
        with get_conn() as conn:
            d = conn.execute("SELECT * FROM deposits WHERE deposit_id=?", (deposit_id,)).fetchone()
            if not d or d["status"] != "pending":
                return None
            cur = conn.execute(
                "UPDATE deposits SET status='rejected', approved_by=?, note=? WHERE deposit_id=? AND status='pending'",
                (rejected_by, note, deposit_id),
            )
            if cur.rowcount != 1:
                return None
            return d

def is_binance_txn_used(txn_id: str) -> bool:
    """True if this Binance transaction id has already been credited."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM deposits WHERE binance_txn_id=?", (txn_id,)
        ).fetchone()
        return row is not None

def create_binance_deposit_atomic(telegram_id, username, amount_mmk, txn_id, note=None):
    """Create an already-approved Binance deposit and credit the user atomically.

    The UNIQUE index on binance_txn_id is the replay guard: if the same
    transaction id is submitted twice (even concurrently), the second insert
    fails and no double-credit happens.

    Returns (deposit_id, bonus_mmk) on success, or None if this transaction was
    already claimed.
    """
    with _lock:
        with get_conn() as conn:
            existing = conn.execute(
                "SELECT deposit_id FROM deposits WHERE binance_txn_id=?", (txn_id,)
            ).fetchone()
            if existing:
                return None
            try:
                ins = conn.execute(
                    "INSERT INTO deposits(telegram_id,username,method,amount_mmk,status,note,binance_txn_id) "
                    "VALUES(?,?,?,?,'approved',?,?)",
                    (telegram_id, username, "binance", amount_mmk, note, txn_id),
                )
            except sqlite3.IntegrityError:
                return None
            bonus = 0
            if cfg_get("deposit_bonus_enabled", "1") == "1":
                pct = get_deposit_bonus_percent(amount_mmk)
                bonus = amount_mmk * pct // 100
            credit = conn.execute(
                "UPDATE users SET mmk_balance = mmk_balance + ?, bonus_earned = bonus_earned + ? WHERE telegram_id=?",
                (amount_mmk + bonus, bonus, telegram_id),
            )
            if credit.rowcount != 1:
                # No user row to credit — raise so the whole transaction
                # (including the deposit row) rolls back. The caller can
                # retry once the row exists; nothing is half-recorded.
                raise RuntimeError(f"binance credit: user row missing for {telegram_id}")
            return ins.lastrowid, bonus

def list_deposits(status=None, limit=20, offset=0, telegram_id=None):
    with get_conn() as conn:
        if telegram_id and status:
            return conn.execute(
                "SELECT * FROM deposits WHERE telegram_id=? AND status=? ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (telegram_id, status, limit, offset),
            ).fetchall()
        if telegram_id:
            return conn.execute(
                "SELECT * FROM deposits WHERE telegram_id=? ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (telegram_id, limit, offset),
            ).fetchall()
        if status:
            return conn.execute(
                "SELECT * FROM deposits WHERE status=? ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (status, limit, offset),
            ).fetchall()
        return conn.execute(
            "SELECT * FROM deposits ORDER BY created_at DESC LIMIT ? OFFSET ?", (limit, offset)
        ).fetchall()

def count_deposits(status=None) -> int:
    with get_conn() as conn:
        if status:
            return conn.execute(
                "SELECT COUNT(*) FROM deposits WHERE status=?", (status,)
            ).fetchone()[0]
        return conn.execute("SELECT COUNT(*) FROM deposits").fetchone()[0]

# ── analytics & dashboard ─────────────────────────────────────────────────────

def revenue_today() -> int:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(amount_mmk),0) FROM orders WHERE DATE(created_at)=DATE('now') AND status!='refunded'"
        ).fetchone()
        return row[0] if row else 0

def revenue_total() -> int:
    with get_conn() as conn:
        row = conn.execute("SELECT COALESCE(SUM(amount_mmk),0) FROM orders WHERE status!='refunded'").fetchone()
        return row[0] if row else 0

def revenue_last_n_days(n: int = 7):
    """Returns list of (date, total) for the last n days."""
    with get_conn() as conn:
        return conn.execute(
            "SELECT DATE(created_at) as day, COALESCE(SUM(amount_mmk),0) as total "
            "FROM orders "
            "WHERE created_at >= DATE('now', ?) AND status!='refunded' "
            "GROUP BY day ORDER BY day ASC",
            (f"-{n} days",),
        ).fetchall()

def orders_last_n_days(n: int = 7) -> int:
    with get_conn() as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM orders WHERE created_at >= DATE('now', ?) AND status!='refunded'",
            (f"-{n} days",),
        ).fetchone()[0]

def new_users_last_n_days(n: int = 7) -> int:
    with get_conn() as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM users WHERE registered_at >= DATE('now', ?)",
            (f"-{n} days",),
        ).fetchone()[0]

def top_products(limit: int = 5):
    with get_conn() as conn:
        return conn.execute(
            "SELECT product_name, COUNT(*) as cnt, SUM(amount_mmk) as revenue "
            "FROM orders WHERE status!='refunded' GROUP BY product_name ORDER BY cnt DESC LIMIT ?",
            (limit,),
        ).fetchall()

def products_low_stock():
    """Products with stock above 0 but at or below the threshold."""
    threshold = int(cfg_get("low_stock_threshold", "3"))
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT p.name, COUNT(s.stock_id) as cnt "
            "FROM products p JOIN stock s ON p.product_id=s.product_id "
            "WHERE p.active=1 "
            "GROUP BY p.product_id "
            "HAVING cnt > 0 AND cnt <= ?",
            (threshold,),
        ).fetchall()
        return [{"name": r["name"], "cnt": r["cnt"]} for r in rows]

def products_out_of_stock():
    """Active products with zero stock."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT p.name FROM products p "
            "WHERE p.active=1 AND "
            "(SELECT COUNT(*) FROM stock s WHERE s.product_id=p.product_id) = 0"
        ).fetchall()
        return [{"name": r["name"]} for r in rows]

def state_get(telegram_id: int) -> dict:
    import json
    with get_conn() as conn:
        row = conn.execute(
            "SELECT state_json, updated_at FROM user_state WHERE telegram_id=?",
            (telegram_id,),
        ).fetchone()
        if not row:
            return {}
        # auto-expire states older than 30 minutes
        updated = datetime.fromisoformat(row["updated_at"])
        if _utcnow().replace(tzinfo=None) - updated > timedelta(minutes=30):
            conn.execute("DELETE FROM user_state WHERE telegram_id=?", (telegram_id,))
            return {}
        try:
            return json.loads(row["state_json"])
        except Exception:
            return {}

def state_set(telegram_id: int, data: dict):
    import json
    with get_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO user_state(telegram_id, state_json, updated_at) VALUES(?,?,?)",
            (telegram_id, json.dumps(data), _utcnow_iso()),
        )

def state_clear(telegram_id: int):
    with get_conn() as conn:
        conn.execute("DELETE FROM user_state WHERE telegram_id=?", (telegram_id,))

def state_expire_all():
    """Clear all states older than 30 minutes. Called by background thread."""
    cutoff = (_utcnow() - timedelta(minutes=30)).strftime("%Y-%m-%d %H:%M:%S")
    with get_conn() as conn:
        conn.execute("DELETE FROM user_state WHERE updated_at < ?", (cutoff,))


# ── giveaways ─────────────────────────────────────────────────────────────────

class _GiveawayNoStock(Exception):
    """Raised inside create_giveaway's transaction to force a rollback when
    an instant prize doesn't have enough unreserved stock to protect."""


def create_giveaway(product_id, title, description, winner_slots, end_time, created_by):
    """Create a new active giveaway. Returns giveaway_id, None if one is
    already active, or -1 if the prize is an instant product without
    enough unreserved stock to cover the winner slots.

    For instant prizes the winner stock is RESERVED at creation time
    (reserved_for='giveaway_<id>') in the same transaction, so regular
    buyers can never buy the prizes out from under the draw (V1.5).
    Reservations are claimed at draw time and released on cancel or
    after the draw (leftovers when there are fewer entrants than slots)."""
    with _lock:
        try:
            with get_conn() as conn:
                existing = conn.execute(
                    "SELECT giveaway_id FROM giveaways WHERE status='active' LIMIT 1"
                ).fetchone()
                if existing:
                    return None
                cur = conn.execute(
                    "INSERT INTO giveaways(product_id,title,description,winner_slots,end_time,status,created_by) "
                    "VALUES(?,?,?,?,?,'active',?)",
                    (product_id, title, description, winner_slots, end_time, created_by),
                )
                gid = cur.lastrowid
                prow = conn.execute(
                    "SELECT fulfillment FROM products WHERE product_id=?", (product_id,)
                ).fetchone()
                mode = ((prow["fulfillment"] or "instant").strip().lower()
                        if prow else "instant")
                if prow and mode == "instant" and winner_slots > 0:
                    rows = conn.execute(
                        "SELECT stock_id FROM stock WHERE product_id=? AND reserved_for IS NULL LIMIT ?",
                        (product_id, winner_slots),
                    ).fetchall()
                    if len(rows) < winner_slots:
                        raise _GiveawayNoStock()
                    code = f"giveaway_{gid}"
                    conn.executemany(
                        "UPDATE stock SET reserved_for=? WHERE stock_id=?",
                        [(code, r["stock_id"]) for r in rows],
                    )
                return gid
        except _GiveawayNoStock:
            return -1

def get_active_giveaway():
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM giveaways WHERE status='active' ORDER BY created_at DESC LIMIT 1"
        ).fetchone()

def get_giveaway(giveaway_id: int):
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM giveaways WHERE giveaway_id=?", (giveaway_id,)
        ).fetchone()

def list_giveaways(limit=50):
    """Active giveaways first, then most recent others."""
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM giveaways "
            "ORDER BY (status='active') DESC, created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()

def set_giveaway_status(giveaway_id: int, status: str, drawn_at: str = None, cancelled_at: str = None):
    with get_conn() as conn:
        conn.execute(
            "UPDATE giveaways SET status=?, "
            "drawn_at=COALESCE(?, drawn_at), cancelled_at=COALESCE(?, cancelled_at) "
            "WHERE giveaway_id=?",
            (status, drawn_at, cancelled_at, giveaway_id),
        )

def add_giveaway_entry(giveaway_id: int, telegram_id: int, username: str = None) -> bool:
    """Record one entry. Returns False if the user already entered (UNIQUE violation)."""
    try:
        with get_conn() as conn:
            conn.execute(
                "INSERT INTO giveaway_entries(giveaway_id,telegram_id,username) VALUES(?,?,?)",
                (giveaway_id, telegram_id, username or ""),
            )
        return True
    except sqlite3.IntegrityError:
        return False

def has_entered(giveaway_id: int, telegram_id: int) -> bool:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM giveaway_entries WHERE giveaway_id=? AND telegram_id=?",
            (giveaway_id, telegram_id),
        ).fetchone()
        return row is not None

def count_entries(giveaway_id: int) -> int:
    with get_conn() as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM giveaway_entries WHERE giveaway_id=?", (giveaway_id,)
        ).fetchone()[0]

def list_entrants(giveaway_id: int):
    with get_conn() as conn:
        return conn.execute(
            "SELECT telegram_id, username, entries FROM giveaway_entries WHERE giveaway_id=?",
            (giveaway_id,),
        ).fetchall()


def get_giveaway_entry_count(giveaway_id: int, telegram_id: int) -> int:
    """Total entries (base + bonus) for a user in a giveaway."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT entries FROM giveaway_entries WHERE giveaway_id=? AND telegram_id=?",
            (giveaway_id, telegram_id),
        ).fetchone()
        return row["entries"] if row else 0


def add_giveaway_bonus_entries(giveaway_id: int, telegram_id: int, n: int) -> int:
    """Add bonus entries (viral invites). Inviter must have entered already.
    Returns the new total, or 0 if they haven't entered."""
    with get_conn() as conn:
        cur = conn.execute(
            "UPDATE giveaway_entries SET entries = entries + ? WHERE giveaway_id=? AND telegram_id=?",
            (n, giveaway_id, telegram_id),
        )
        if cur.rowcount != 1:
            return 0
        row = conn.execute(
            "SELECT entries FROM giveaway_entries WHERE giveaway_id=? AND telegram_id=?",
            (giveaway_id, telegram_id),
        ).fetchone()
        return row["entries"]


def record_giveaway_invite(giveaway_id: int, inviter_id: int, invitee_id: int) -> bool:
    """Record an invite credit. False if already credited / self-invite."""
    if inviter_id == invitee_id:
        return False
    with get_conn() as conn:
        try:
            conn.execute(
                "INSERT INTO giveaway_invites (giveaway_id, inviter_id, invitee_id) VALUES (?, ?, ?)",
                (giveaway_id, inviter_id, invitee_id),
            )
            return True
        except Exception:
            return False


def get_giveaway_invite_leaderboard(giveaway_id: int, limit: int = 5):
    """Top inviters: (inviter_id, invite_count)."""
    with get_conn() as conn:
        return conn.execute(
            "SELECT inviter_id, COUNT(*) AS c FROM giveaway_invites WHERE giveaway_id=? GROUP BY inviter_id ORDER BY c DESC LIMIT ?",
            (giveaway_id, limit),
        ).fetchall()


def count_giveaway_invites(giveaway_id: int, inviter_id: int) -> int:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM giveaway_invites WHERE giveaway_id=? AND inviter_id=?",
            (giveaway_id, inviter_id),
        ).fetchone()
        return row["c"]

def add_giveaway_winner(giveaway_id: int, telegram_id: int, username: str,
                        credential: str = None, delivered: int = 0, order_id: int = None) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO giveaway_winners(giveaway_id,telegram_id,username,credential,delivered,order_id) "
            "VALUES(?,?,?,?,?,?)",
            (giveaway_id, telegram_id, username or "", credential, delivered, order_id),
        )
        return cur.lastrowid

def list_winners(giveaway_id: int):
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM giveaway_winners WHERE giveaway_id=? ORDER BY winner_id ASC",
            (giveaway_id,),
        ).fetchall()

def count_delivered_winners(giveaway_id: int) -> int:
    with get_conn() as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM giveaway_winners WHERE giveaway_id=? AND delivered=1",
            (giveaway_id,),
        ).fetchone()[0]

def list_giveaways_needing_end_reminder():
    """Active giveaways whose end_time has passed and haven't been reminded yet."""
    now = _utcnow_iso()
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM giveaways WHERE status='active' AND end_time IS NOT NULL "
            "AND end_time <= ? AND end_reminded=0",
            (now,),
        ).fetchall()

def mark_giveaway_reminded(giveaway_id: int):
    with get_conn() as conn:
        conn.execute("UPDATE giveaways SET end_reminded=1 WHERE giveaway_id=?", (giveaway_id,))


# ── pending-deposit nudge ──────────────────────────────────────────────────────

def list_stale_pending_deposits(hours: int):
    """Pending deposits older than `hours` that haven't been nudged yet.

    created_at is stored in UTC (SQLite datetime('now')), so the cutoff is
    computed in UTC too — consistent regardless of container timezone.
    """
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM deposits WHERE status='pending' AND nudge_sent=0 "
            "AND created_at <= datetime('now', ?)",
            (f"-{int(hours)} hours",),
        ).fetchall()


def mark_deposit_nudged(deposit_id: int):
    with get_conn() as conn:
        conn.execute("UPDATE deposits SET nudge_sent=1 WHERE deposit_id=?", (deposit_id,))


# ── growth: gifts ─────────────────────────────────────────────────────────────

def reserve_stock(product_id: int, gift_code: str):
    """Atomically reserve one available stock item for a gift. Returns stock_id or None."""
    with _lock:
        with get_conn() as conn:
            while True:
                row = conn.execute(
                    "SELECT stock_id FROM stock WHERE product_id=? AND reserved_for IS NULL LIMIT 1",
                    (product_id,),
                ).fetchone()
                if not row:
                    return None
                cur = conn.execute(
                    "UPDATE stock SET reserved_for=? WHERE stock_id=? AND reserved_for IS NULL",
                    (gift_code, row["stock_id"]),
                )
                if cur.rowcount == 1:
                    return row["stock_id"]


def claim_reserved_stock(gift_code: str):
    """Take the reserved stock item for a gift claim. Returns (stock_id, credential) or None."""
    with _lock:
        with get_conn() as conn:
            row = conn.execute(
                "SELECT s.stock_id, s.product_id, s.credential, p.name AS pname"
                " FROM stock s LEFT JOIN products p ON p.product_id = s.product_id"
                " WHERE s.reserved_for=? LIMIT 1",
                (gift_code,),
            ).fetchone()
            if not row:
                return None
            conn.execute("DELETE FROM stock WHERE stock_id=?", (row["stock_id"],))
        _mirror_sold(row["stock_id"], row["product_id"], row["pname"] or "", row["credential"], None,
                     _utcnow_iso())
        return row["stock_id"], row["credential"]


def release_stock_reservation(stock_id: int):
    with get_conn() as conn:
        conn.execute("UPDATE stock SET reserved_for=NULL WHERE stock_id=?", (stock_id,))


# ── lucky draw ───────────────────────────────────────────────────────────────

class _LdNoStock(Exception):
    """Raised inside create_ld_round's transaction to force a rollback when
    an instant prize has no stock to reserve."""


def ld_cap_for(total_slots: int) -> int:
    """Max slots one user may hold in a round (locked rule): rounds with
    <= 10 slots are 1-per-person; bigger rounds allow up to 3."""
    return 1 if total_slots <= 10 else 3


def create_ld_round(product_id, product_name, prize_mode, prize_value,
                    entry_price, total_slots, deadline, created_by,
                    seed, seed_hash):
    """Create a lucky-draw round. For instant prizes, one stock item is
    RESERVED in the same transaction (reserved_for='ld_<id>') so the prize
    can't be sold while the round runs. Returns round_id, or -1 when an
    instant prize has no stock to reserve."""
    with _lock:
        try:
            with get_conn() as conn:
                cur = conn.execute(
                    "INSERT INTO ld_rounds(product_id,product_name,prize_mode,prize_value,"
                    "entry_price,total_slots,deadline,status,seed,seed_hash,created_by) "
                    "VALUES(?,?,?,?,?,?,?,'open',?,?,?)",
                    (product_id, product_name, prize_mode, prize_value,
                     entry_price, total_slots, deadline, seed, seed_hash, created_by),
                )
                rid = cur.lastrowid
                if prize_mode == "instant":
                    row = conn.execute(
                        "SELECT stock_id FROM stock WHERE product_id=? AND reserved_for IS NULL LIMIT 1",
                        (product_id,),
                    ).fetchone()
                    if not row:
                        raise _LdNoStock()
                    conn.execute(
                        "UPDATE stock SET reserved_for=? WHERE stock_id=?",
                        (f"ld_{rid}", row["stock_id"]),
                    )
                return rid
        except _LdNoStock:
            return -1


def get_ld_round(round_id: int):
    with get_conn() as conn:
        return conn.execute("SELECT * FROM ld_rounds WHERE round_id=?", (round_id,)).fetchone()


def list_ld_rounds(status=None, limit=50):
    with get_conn() as conn:
        if status:
            return conn.execute(
                "SELECT * FROM ld_rounds WHERE status=? ORDER BY round_id DESC LIMIT ?",
                (status, limit),
            ).fetchall()
        return conn.execute(
            "SELECT * FROM ld_rounds ORDER BY round_id DESC LIMIT ?", (limit,)
        ).fetchall()


def ld_open_round_for_product(product_id: int):
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM ld_rounds WHERE product_id=? AND status='open' ORDER BY round_id LIMIT 1",
            (product_id,),
        ).fetchone()


def ld_entries(round_id: int):
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM ld_entries WHERE round_id=? ORDER BY slot_no", (round_id,)
        ).fetchall()


def ld_entry_count(round_id: int) -> int:
    with get_conn() as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM ld_entries WHERE round_id=?", (round_id,)
        ).fetchone()[0]


def ld_user_entries(round_id: int, telegram_id: int):
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM ld_entries WHERE round_id=? AND telegram_id=? ORDER BY slot_no",
            (round_id, telegram_id),
        ).fetchall()


def ld_user_round_entries(telegram_id: int, limit=50):
    """A user's entries joined with their rounds (for the My Slots view)."""
    with get_conn() as conn:
        return conn.execute(
            "SELECT e.*, r.product_name, r.prize_mode, r.prize_value, r.entry_price,"
            " r.total_slots, r.status AS round_status, r.winner_id"
            " FROM ld_entries e JOIN ld_rounds r ON r.round_id = e.round_id"
            " WHERE e.telegram_id=? ORDER BY e.entry_id DESC LIMIT ?",
            (telegram_id, limit),
        ).fetchall()


def ld_buy_slot(round_id: int, telegram_id: int, username: str) -> dict:
    """Atomically charge the wallet and claim the next slot in one
    transaction (the V1.5 money pattern). When the last slot sells, the
    round flips to 'drawing' in the same commit.
    Returns {"ok": True, "slot_no", "filled", "balance"} or
    {"error": "closed"|"full"|"cap"|"insufficient"}."""
    with _lock:
        with get_conn() as conn:
            r = conn.execute("SELECT * FROM ld_rounds WHERE round_id=?", (round_id,)).fetchone()
            if not r or r["status"] != "open":
                return {"error": "closed"}
            mine = conn.execute(
                "SELECT COUNT(*) FROM ld_entries WHERE round_id=? AND telegram_id=?",
                (round_id, telegram_id),
            ).fetchone()[0]
            if mine >= ld_cap_for(r["total_slots"]):
                return {"error": "cap"}
            total = conn.execute(
                "SELECT COUNT(*) FROM ld_entries WHERE round_id=?", (round_id,)
            ).fetchone()[0]
            if total >= r["total_slots"]:
                return {"error": "full"}
            cur = conn.execute(
                "UPDATE users SET mmk_balance = mmk_balance - ? WHERE telegram_id=? AND mmk_balance >= ?",
                (r["entry_price"], telegram_id, r["entry_price"]),
            )
            if cur.rowcount != 1:
                return {"error": "insufficient"}
            slot_no = total + 1
            conn.execute(
                "INSERT INTO ld_entries(round_id,telegram_id,username,slot_no) VALUES(?,?,?,?)",
                (round_id, telegram_id, username, slot_no),
            )
            filled = slot_no >= r["total_slots"]
            if filled:
                conn.execute(
                    "UPDATE ld_rounds SET status='drawing' WHERE round_id=? AND status='open'",
                    (round_id,),
                )
            bal = conn.execute(
                "SELECT mmk_balance FROM users WHERE telegram_id=?", (telegram_id,)
            ).fetchone()[0]
            return {"ok": True, "slot_no": slot_no, "filled": filled, "balance": bal}


def ld_finish_draw(round_id: int, winner_id: int, winner_slot: int) -> bool:
    """Mark a drawing round completed with its winner. Guarded on
    status='drawing' so the result can only ever land once."""
    with _lock:
        with get_conn() as conn:
            cur = conn.execute(
                "UPDATE ld_rounds SET status='completed', winner_id=?, winner_slot=?,"
                " drawn_at=datetime('now') WHERE round_id=? AND status='drawing'",
                (winner_id, winner_slot, round_id),
            )
            return cur.rowcount == 1


def ld_refund_round(round_id: int, to_status: str):
    """Refund every entry of an open/drawing round and close it as
    to_status ('expired' or 'cancelled'), releasing the reserved prize.
    All in one transaction. Returns [(telegram_id, amount, slots), ...]
    or None when the round was already finished (double-refund-proof)."""
    with _lock:
        with get_conn() as conn:
            r = conn.execute("SELECT * FROM ld_rounds WHERE round_id=?", (round_id,)).fetchone()
            if not r or r["status"] not in ("open", "drawing"):
                return None
            cur = conn.execute(
                "UPDATE ld_rounds SET status=? WHERE round_id=? AND status IN ('open','drawing')",
                (to_status, round_id),
            )
            if cur.rowcount != 1:
                return None
            rows = conn.execute(
                "SELECT telegram_id, COUNT(*) AS n FROM ld_entries WHERE round_id=? GROUP BY telegram_id",
                (round_id,),
            ).fetchall()
            refunds = []
            for row in rows:
                amt = row["n"] * r["entry_price"]
                conn.execute(
                    "UPDATE users SET mmk_balance = mmk_balance + ? WHERE telegram_id=?",
                    (amt, row["telegram_id"]),
                )
                refunds.append((row["telegram_id"], amt, row["n"]))
            conn.execute(
                "UPDATE stock SET reserved_for=NULL WHERE reserved_for=?",
                (f"ld_{round_id}",),
            )
            return refunds


def release_reservations(code: str) -> int:
    """Un-reserve every stock row held under a reservation code (e.g.
    'giveaway_<id>' leftovers after a draw, or a cancelled giveaway).
    Returns how many rows were released."""
    with get_conn() as conn:
        cur = conn.execute("UPDATE stock SET reserved_for=NULL WHERE reserved_for=?", (code,))
        return cur.rowcount


def _gift_code_exists(code: str) -> bool:
    with get_conn() as conn:
        return conn.execute("SELECT 1 FROM gifts WHERE code=?", (code,)).fetchone() is not None


def mint_gift_code() -> str:
    """Mint a unique 8-char gift code."""
    import secrets
    import string
    for _ in range(10):
        code = "".join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(8))
        if not _gift_code_exists(code):
            return code
    raise RuntimeError("could not mint gift code")


def create_gift(code: str, product_id: int, product_name: str, stock_id: int, amount_mmk: int,
                sender_id: int, sender_name: str, expiry_days: int = 30):
    """Create a gift record with a pre-minted code."""
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO gifts (code, product_id, product_name, stock_id, amount_mmk, sender_id, sender_name, status, expires_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, 'unclaimed', datetime('now', ?))",
            (code, product_id, product_name, stock_id, amount_mmk, sender_id, sender_name,
             f"+{int(expiry_days)} days"),
        )


def add_total_spent(telegram_id: int, amount_mmk: int):
    """Bump lifetime spend (VIP progress) outside the normal purchase flow."""
    with get_conn() as conn:
        conn.execute(
            "UPDATE users SET total_spent = total_spent + ? WHERE telegram_id=?",
            (amount_mmk, telegram_id),
        )


def sub_total_spent(telegram_id: int, amount_mmk: int):
    """Drop lifetime spend (refunds) — never below zero."""
    with get_conn() as conn:
        conn.execute(
            "UPDATE users SET total_spent = MAX(0, total_spent - ?) WHERE telegram_id=?",
            (amount_mmk, telegram_id),
        )


def get_gift_by_code(code: str):
    with get_conn() as conn:
        return conn.execute("SELECT * FROM gifts WHERE code=?", (code.strip().upper(),)).fetchone()


def claim_gift(code: str, recipient_id: int):
    """Mark a gift claimed. Returns the gift row, or None if not claimable."""
    with _lock:
        with get_conn() as conn:
            g = conn.execute("SELECT * FROM gifts WHERE code=?", (code.strip().upper(),)).fetchone()
            if not g or g["status"] != "unclaimed":
                return None
            if g["sender_id"] == recipient_id:
                return None
            cur = conn.execute(
                "UPDATE gifts SET status='claimed', recipient_id=?, claimed_at=datetime('now') WHERE gift_id=? AND status='unclaimed'",
                (recipient_id, g["gift_id"]),
            )
            if cur.rowcount != 1:
                return None
            return g


def release_expired_gifts() -> int:
    """Expire old unclaimed gifts and return their stock to sale. Returns count."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT gift_id, stock_id FROM gifts WHERE status='unclaimed' AND expires_at <= datetime('now')"
        ).fetchall()
        for r in rows:
            conn.execute("UPDATE gifts SET status='expired' WHERE gift_id=?", (r["gift_id"],))
            if r["stock_id"]:
                conn.execute("UPDATE stock SET reserved_for=NULL WHERE stock_id=?", (r["stock_id"],))
        return len(rows)


def list_user_gifts(telegram_id: int, limit: int = 10):
    """Gifts sent by a user, newest first."""
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM gifts WHERE sender_id=? ORDER BY gift_id DESC LIMIT ?",
            (telegram_id, limit),
        ).fetchall()


# ── growth: deposit bonus ─────────────────────────────────────────────────────

def get_deposit_bonus_percent(amount_mmk: int) -> int:
    """Bonus % for a deposit amount from the configured tiers [[min, pct], ...]."""
    import json
    try:
        tiers = json.loads(cfg_get("deposit_bonus_tiers", "[[0, 3], [50000, 5], [200000, 8]]") or "[]")
    except Exception:
        tiers = []
    pct = 0
    for min_amt, p in tiers:
        if amount_mmk >= int(min_amt):
            pct = int(p)
    return pct


# ── growth: VIP tiers ─────────────────────────────────────────────────────────

def get_vip_tiers():
    """Configured tiers: list of {name, min, discount}, sorted by min ascending."""
    import json
    try:
        tiers = json.loads(cfg_get("vip_tiers", "[]") or "[]")
    except Exception:
        tiers = []
    return sorted(tiers, key=lambda t: int(t.get("min", 0)))


def get_vip_tier(total_spent: int):
    """Returns (tier_name or None, discount_pct) for a lifetime spend."""
    if cfg_get("vip_enabled", "1") != "1":
        return None, 0
    name, disc = None, 0
    for t in get_vip_tiers():
        if total_spent >= int(t.get("min", 0)):
            name, disc = t.get("name"), int(t.get("discount", 0))
    return name, disc


def get_user_vip_tier(telegram_id: int):
    """(tier_name or None, discount_pct) for a user based on total_spent."""
    with get_conn() as conn:
        row = conn.execute("SELECT total_spent FROM users WHERE telegram_id=?", (telegram_id,)).fetchone()
    spent = row["total_spent"] if row else 0
    return get_vip_tier(spent)


# ── CDK activations ───────────────────────────────────────────────────────────

def cdk_activation_create(user_id: int, product_id: str, product_name: str, cdk_key: str) -> int:
    """Record a new activation attempt. Returns the activation id."""
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO cdk_activations (user_id, product_id, product_name, cdk_key, status)"
            " VALUES (?, ?, ?, ?, 'sending')",
            (user_id, product_id, product_name, cdk_key),
        )
        return cur.lastrowid


def cdk_activation_update(act_id: int, status: str = None, provider_ref: str = None,
                          message: str = None, duration_ms: int = None):
    """Update an activation's status / ref / message. Only touched fields change."""
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
        conn.execute(f"UPDATE cdk_activations SET {', '.join(sets)} WHERE id=?", vals)


def cdk_activation_list(user_id: int, limit: int = 10):
    """Latest activation records for a user, newest first."""
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM cdk_activations WHERE user_id=? ORDER BY id DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()


def cdk_activation_get(act_id: int, user_id: int):
    """One activation record, only if it belongs to the user."""
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM cdk_activations WHERE id=? AND user_id=?",
            (act_id, user_id),
        ).fetchone()


def cdk_activation_find_key(user_id: int, cdk_key: str):
    """Latest record where this user activated the same key (for status re-checks)."""
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM cdk_activations WHERE user_id=? AND cdk_key=? ORDER BY id DESC LIMIT 1",
            (user_id, cdk_key.strip()),
        ).fetchone()


def cdk_activation_reap():
    """Mark non-terminal activations as interrupted (e.g. after a restart
    killed their worker thread). Returns the number of records touched."""
    with get_conn() as conn:
        cur = conn.execute(
            "UPDATE cdk_activations SET status='interrupted', updated_at=datetime('now')"
            " WHERE status NOT IN ('success','failed','pending','interrupted')"
        )
        return cur.rowcount


def cdk_activation_pending():
    """All activations still waiting on a provider verdict, oldest first.

    Used by the startup sweep: rows left 'pending' by a previous run get
    their watchdog re-armed so they settle instead of hanging forever."""
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM cdk_activations WHERE status='pending' ORDER BY id ASC"
        ).fetchall()


# ── AM Pro subscriptions (Alight Motion monthly) ─────────────────────────────

def am_get_subscription(telegram_id: int):
    """Return the active subscription row, or None."""
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM am_subscriptions WHERE telegram_id=? AND status='active'"
            " AND datetime(expires_at) > datetime('now') ORDER BY expires_at DESC LIMIT 1",
            (telegram_id,),
        ).fetchone()


def am_days_left(telegram_id: int) -> int:
    sub = am_get_subscription(telegram_id)
    if not sub:
        return 0
    with get_conn() as conn:
        row = conn.execute(
            "SELECT CAST(julianday(expires_at) - julianday('now') AS INTEGER) AS d"
            " FROM am_subscriptions WHERE subscription_id=?",
            (sub["subscription_id"],),
        ).fetchone()
    return max(0, row["d"] if row else 0)


def am_create_subscription(telegram_id: int, username: str, amount_paid: int, days: int = 30) -> int:
    """Create (or extend) a subscription. Returns subscription_id."""
    with get_conn() as conn:
        existing = conn.execute(
            "SELECT * FROM am_subscriptions WHERE telegram_id=? AND status='active'"
            " AND datetime(expires_at) > datetime('now') ORDER BY expires_at DESC LIMIT 1",
            (telegram_id,),
        ).fetchone()
        if existing:
            # Extend from current expiry (renewals stack, never lose paid days).
            cur = conn.execute(
                "UPDATE am_subscriptions SET expires_at=datetime(expires_at, ?), amount_paid=amount_paid+?"
                " WHERE subscription_id=?",
                (f"+{days} days", amount_paid, existing["subscription_id"]),
            )
            return existing["subscription_id"]
        cur = conn.execute(
            "INSERT INTO am_subscriptions(telegram_id, username, expires_at, amount_paid)"
            " VALUES(?, ?, datetime('now', ?), ?)",
            (telegram_id, username, f"+{days} days", amount_paid),
        )
        return cur.lastrowid


def am_daily_used(telegram_id: int) -> int:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT count FROM am_usage WHERE telegram_id=? AND day=date('now')",
            (telegram_id,),
        ).fetchone()
    return row["count"] if row else 0


def am_daily_remaining(telegram_id: int, cap: int | None = None) -> int:
    if cap is None:
        cap = int(cfg_get("am_daily_cap", "5") or 5)
    return max(0, cap - am_daily_used(telegram_id))


def am_bump_usage(telegram_id: int):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO am_usage(telegram_id, day, count) VALUES(?, date('now'), 1)"
            " ON CONFLICT(telegram_id, day) DO UPDATE SET count=count+1",
            (telegram_id,),
        )


def am_list_active(limit: int = 50):
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM am_subscriptions WHERE status='active'"
            " AND datetime(expires_at) > datetime('now') ORDER BY expires_at LIMIT ?",
            (limit,),
        ).fetchall()
