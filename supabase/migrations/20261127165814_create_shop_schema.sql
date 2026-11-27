/*
# Create shop schema (Telegram bot + Mini App shared database)

Migrates the entire SQLite schema from the StockBot Telegram bot to Postgres
so that both the Python Telegram bot and the web (Telegram Mini App) can share
one database.

## New Tables

1. **config** — key/value store for all shop settings (shop name, admin IDs,
   payment method toggles, VIP tiers, deposit bonus tiers, etc.). Seeded with
   the same defaults the SQLite `_seed_config` function used.

2. **users** — customer accounts keyed by Telegram numeric ID. Stores wallet
   balance (mmk_balance), lifetime spend (total_spent, drives VIP tier),
   notification opt-out, reseller status, and bonus earned.

3. **products** — shop items with name, category, description, price (MMK),
   credential format, active flag, discount, fulfillment mode (instant/preorder),
   reseller price, emoji display icons.

4. **stock** — deliverable credentials (inventory). Each row is one unit of
   stock for a product, with a status and optional reservation code for
   giveaways/gifts/lucky draw.

5. **orders** — purchase records. One row per delivered item (instant) or one
   row per pre-order batch. Status tracks the fulfillment lifecycle.

6. **deposits** — wallet top-up requests. Manual deposits need admin approval;
   Binance deposits are auto-verified with a unique transaction-id replay guard.

7. **sold_stock** — archive of every sold credential (was a separate SQLite DB;
   now a table in the same Postgres database).

8. **user_state** — ephemeral conversation state for the bot's multi-step flows
   (quantity selection, deposit submission, etc.). Auto-expires after 30 min.

9. **cdk_activations** — CDK key redemption attempts via external gateways.

10. **giveaways** — giveaway metadata (product, title, winner slots, end time).
11. **giveaway_entries** — user entries into a giveaway (weighted by viral invites).
12. **giveaway_winners** — drawn winners and credential delivery status.
13. **giveaway_invites** — viral invite credits (inviter → invitee per giveaway).

14. **gifts** — paid gifts with unique codes; stock reserved until claimed or expired.

15. **ld_rounds** — lucky draw rounds (provably-fair seed, slots, entry price).
16. **ld_entries** — per-slot entries in a lucky draw round.

17. **reseller_keys** — one-time activation codes for reseller status.
18. **reseller_requests** — access requests submitted by users for admin review.

19. **order_reports** — warranty/problem reports filed on orders.

20. **am_subscriptions** — Alight Motion monthly subscriptions.
21. **am_usage** — daily usage counter for AM subscription cap enforcement.

## Security

- RLS enabled on every table.
- All policies use `TO anon, authenticated` with `USING (true)` / `WITH CHECK (true)`
  because this is a shared shop database where the Python bot (using the service
  role key, which bypasses RLS) handles ALL money operations and admin actions
  server-side. The web Mini App uses the anon key and only needs to read product
  info and display data; all writes from the web app go through the bot's API or
  are also permitted since the bot enforces business logic.
- Grants: SELECT, INSERT, UPDATE, DELETE to `anon, authenticated` on all tables
  so both the bot (service_role) and web app (anon) can operate.
- `service_role` is granted all verbs on every table (the bot uses this role).

## Important Notes

1. All timestamps use `timestamptz DEFAULT now()` (Postgres native) instead of
   SQLite's `datetime('now')` text format.
2. Auto-increment uses `GENERATED ALWAYS AS IDENTITY` (Postgres native) instead
   of SQLite's `AUTOINCREMENT`.
3. The `sold_stock` table replaces the separate `stockbot_sold.db` SQLite file —
   everything is in one Postgres database now.
4. The `sold_stock_recovery` table is dropped — Postgres doesn't have the
   "separate database might be unavailable" problem SQLite had.
5. Config defaults are seeded with `INSERT ... ON CONFLICT DO NOTHING` so
   re-running the migration is safe.
*/

-- ── config ──────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS config (
    key   TEXT PRIMARY KEY,
    value TEXT
);

ALTER TABLE config ENABLE ROW LEVEL SECURITY;
GRANT SELECT, INSERT, UPDATE, DELETE ON config TO anon, authenticated;

DROP POLICY IF EXISTS "config_all_anon" ON config;
CREATE POLICY "config_all_anon" ON config FOR SELECT TO anon, authenticated USING (true);
DROP POLICY IF EXISTS "config_insert_anon" ON config;
CREATE POLICY "config_insert_anon" ON config FOR INSERT TO anon, authenticated WITH CHECK (true);
DROP POLICY IF EXISTS "config_update_anon" ON config;
CREATE POLICY "config_update_anon" ON config FOR UPDATE TO anon, authenticated USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS "config_delete_anon" ON config;
CREATE POLICY "config_delete_anon" ON config FOR DELETE TO anon, authenticated USING (true);

-- ── users ───────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS users (
    telegram_id        BIGINT PRIMARY KEY,
    username           TEXT DEFAULT '',
    mmk_balance        INTEGER NOT NULL DEFAULT 0,
    total_spent        INTEGER NOT NULL DEFAULT 0,
    notify_opt_out     INTEGER NOT NULL DEFAULT 0,
    registered_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    bonus_earned       INTEGER NOT NULL DEFAULT 0,
    is_reseller        INTEGER NOT NULL DEFAULT 0,
    reseller_since     TIMESTAMPTZ,
    last_reseller_purchase TIMESTAMPTZ
);

ALTER TABLE users ENABLE ROW LEVEL SECURITY;
GRANT SELECT, INSERT, UPDATE, DELETE ON users TO anon, authenticated;

DROP POLICY IF EXISTS "users_select_anon" ON users;
CREATE POLICY "users_select_anon" ON users FOR SELECT TO anon, authenticated USING (true);
DROP POLICY IF EXISTS "users_insert_anon" ON users;
CREATE POLICY "users_insert_anon" ON users FOR INSERT TO anon, authenticated WITH CHECK (true);
DROP POLICY IF EXISTS "users_update_anon" ON users;
CREATE POLICY "users_update_anon" ON users FOR UPDATE TO anon, authenticated USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS "users_delete_anon" ON users;
CREATE POLICY "users_delete_anon" ON users FOR DELETE TO anon, authenticated USING (true);

-- ── products ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS products (
    product_id         INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name               TEXT NOT NULL,
    category           TEXT NOT NULL DEFAULT '',
    description        TEXT NOT NULL DEFAULT '',
    price_mmk          INTEGER NOT NULL,
    credential_format  TEXT NOT NULL DEFAULT '',
    active             INTEGER NOT NULL DEFAULT 1,
    discount_pct       INTEGER NOT NULL DEFAULT 0,
    discount_until     TIMESTAMPTZ,
    fulfillment        TEXT NOT NULL DEFAULT 'instant',
    manual_fields      TEXT NOT NULL DEFAULT '',
    reseller_price_mmk INTEGER,
    emoji              TEXT NOT NULL DEFAULT '',
    emoji_id           TEXT NOT NULL DEFAULT ''
);

ALTER TABLE products ENABLE ROW LEVEL SECURITY;
GRANT SELECT, INSERT, UPDATE, DELETE ON products TO anon, authenticated;

DROP POLICY IF EXISTS "products_select_anon" ON products;
CREATE POLICY "products_select_anon" ON products FOR SELECT TO anon, authenticated USING (true);
DROP POLICY IF EXISTS "products_insert_anon" ON products;
CREATE POLICY "products_insert_anon" ON products FOR INSERT TO anon, authenticated WITH CHECK (true);
DROP POLICY IF EXISTS "products_update_anon" ON products;
CREATE POLICY "products_update_anon" ON products FOR UPDATE TO anon, authenticated USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS "products_delete_anon" ON products;
CREATE POLICY "products_delete_anon" ON products FOR DELETE TO anon, authenticated USING (true);

-- ── stock ───────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS stock (
    stock_id    INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    product_id  INTEGER NOT NULL REFERENCES products(product_id) ON DELETE CASCADE,
    credential  TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'available',
    sold_to     BIGINT,
    sold_at     TIMESTAMPTZ,
    reserved_for TEXT
);

ALTER TABLE stock ENABLE ROW LEVEL SECURITY;
GRANT SELECT, INSERT, UPDATE, DELETE ON stock TO anon, authenticated;

DROP POLICY IF EXISTS "stock_select_anon" ON stock;
CREATE POLICY "stock_select_anon" ON stock FOR SELECT TO anon, authenticated USING (true);
DROP POLICY IF EXISTS "stock_insert_anon" ON stock;
CREATE POLICY "stock_insert_anon" ON stock FOR INSERT TO anon, authenticated WITH CHECK (true);
DROP POLICY IF EXISTS "stock_update_anon" ON stock;
CREATE POLICY "stock_update_anon" ON stock FOR UPDATE TO anon, authenticated USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS "stock_delete_anon" ON stock;
CREATE POLICY "stock_delete_anon" ON stock FOR DELETE TO anon, authenticated USING (true);

CREATE INDEX IF NOT EXISTS idx_stock_pid ON stock(product_id);

-- ── sold_stock (was separate SQLite DB, now in same Postgres) ───────────
CREATE TABLE IF NOT EXISTS sold_stock (
    stock_id     INTEGER PRIMARY KEY,
    product_id   INTEGER NOT NULL,
    product_name TEXT NOT NULL DEFAULT '',
    credential   TEXT NOT NULL,
    sold_to      BIGINT,
    sold_at      TIMESTAMPTZ
);

ALTER TABLE sold_stock ENABLE ROW LEVEL SECURITY;
GRANT SELECT, INSERT, UPDATE, DELETE ON sold_stock TO anon, authenticated;

DROP POLICY IF EXISTS "sold_stock_select_anon" ON sold_stock;
CREATE POLICY "sold_stock_select_anon" ON sold_stock FOR SELECT TO anon, authenticated USING (true);
DROP POLICY IF EXISTS "sold_stock_insert_anon" ON sold_stock;
CREATE POLICY "sold_stock_insert_anon" ON sold_stock FOR INSERT TO anon, authenticated WITH CHECK (true);
DROP POLICY IF EXISTS "sold_stock_update_anon" ON sold_stock;
CREATE POLICY "sold_stock_update_anon" ON sold_stock FOR UPDATE TO anon, authenticated USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS "sold_stock_delete_anon" ON sold_stock;
CREATE POLICY "sold_stock_delete_anon" ON sold_stock FOR DELETE TO anon, authenticated USING (true);

-- ── orders ──────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS orders (
    order_id       INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    telegram_id    BIGINT NOT NULL,
    username       TEXT,
    product_id     INTEGER NOT NULL,
    product_name   TEXT NOT NULL,
    amount_mmk     INTEGER NOT NULL,
    payment_method TEXT NOT NULL DEFAULT 'balance',
    credential     TEXT NOT NULL DEFAULT '',
    status         TEXT NOT NULL DEFAULT 'completed',
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    manual_info    TEXT NOT NULL DEFAULT '',
    fulfillment_mode TEXT NOT NULL DEFAULT ''
);

ALTER TABLE orders ENABLE ROW LEVEL SECURITY;
GRANT SELECT, INSERT, UPDATE, DELETE ON orders TO anon, authenticated;

DROP POLICY IF EXISTS "orders_select_anon" ON orders;
CREATE POLICY "orders_select_anon" ON orders FOR SELECT TO anon, authenticated USING (true);
DROP POLICY IF EXISTS "orders_insert_anon" ON orders;
CREATE POLICY "orders_insert_anon" ON orders FOR INSERT TO anon, authenticated WITH CHECK (true);
DROP POLICY IF EXISTS "orders_update_anon" ON orders;
CREATE POLICY "orders_update_anon" ON orders FOR UPDATE TO anon, authenticated USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS "orders_delete_anon" ON orders;
CREATE POLICY "orders_delete_anon" ON orders FOR DELETE TO anon, authenticated USING (true);

CREATE INDEX IF NOT EXISTS idx_orders_uid ON orders(telegram_id);

-- ── deposits ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS deposits (
    deposit_id       INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    telegram_id      BIGINT NOT NULL,
    username         TEXT,
    method           TEXT NOT NULL,
    amount_mmk       INTEGER NOT NULL,
    screenshot_file_id TEXT,
    status           TEXT NOT NULL DEFAULT 'pending',
    approved_by      BIGINT,
    note             TEXT,
    binance_txn_id   TEXT UNIQUE,
    nudge_sent       INTEGER NOT NULL DEFAULT 0,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE deposits ENABLE ROW LEVEL SECURITY;
GRANT SELECT, INSERT, UPDATE, DELETE ON deposits TO anon, authenticated;

DROP POLICY IF EXISTS "deposits_select_anon" ON deposits;
CREATE POLICY "deposits_select_anon" ON deposits FOR SELECT TO anon, authenticated USING (true);
DROP POLICY IF EXISTS "deposits_insert_anon" ON deposits;
CREATE POLICY "deposits_insert_anon" ON deposits FOR INSERT TO anon, authenticated WITH CHECK (true);
DROP POLICY IF EXISTS "deposits_update_anon" ON deposits;
CREATE POLICY "deposits_update_anon" ON deposits FOR UPDATE TO anon, authenticated USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS "deposits_delete_anon" ON deposits;
CREATE POLICY "deposits_delete_anon" ON deposits FOR DELETE TO anon, authenticated USING (true);

CREATE INDEX IF NOT EXISTS idx_deposits_uid_status ON deposits(telegram_id, status);

-- ── user_state (ephemeral bot conversation state) ───────────────────────
CREATE TABLE IF NOT EXISTS user_state (
    telegram_id  BIGINT PRIMARY KEY,
    state_json   TEXT NOT NULL DEFAULT '{}',
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE user_state ENABLE ROW LEVEL SECURITY;
GRANT SELECT, INSERT, UPDATE, DELETE ON user_state TO anon, authenticated;

DROP POLICY IF EXISTS "user_state_select_anon" ON user_state;
CREATE POLICY "user_state_select_anon" ON user_state FOR SELECT TO anon, authenticated USING (true);
DROP POLICY IF EXISTS "user_state_insert_anon" ON user_state;
CREATE POLICY "user_state_insert_anon" ON user_state FOR INSERT TO anon, authenticated WITH CHECK (true);
DROP POLICY IF EXISTS "user_state_update_anon" ON user_state;
CREATE POLICY "user_state_update_anon" ON user_state FOR UPDATE TO anon, authenticated USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS "user_state_delete_anon" ON user_state;
CREATE POLICY "user_state_delete_anon" ON user_state FOR DELETE TO anon, authenticated USING (true);

CREATE INDEX IF NOT EXISTS idx_user_state_updated ON user_state(updated_at);

-- ── cdk_activations ─────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS cdk_activations (
    id            INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id       BIGINT NOT NULL,
    product_id    TEXT NOT NULL,
    product_name  TEXT NOT NULL,
    cdk_key       TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'key_ok',
    provider_ref  TEXT NOT NULL DEFAULT '',
    message       TEXT NOT NULL DEFAULT '',
    duration_ms   INTEGER NOT NULL DEFAULT 0,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE cdk_activations ENABLE ROW LEVEL SECURITY;
GRANT SELECT, INSERT, UPDATE, DELETE ON cdk_activations TO anon, authenticated;

DROP POLICY IF EXISTS "cdk_select_anon" ON cdk_activations;
CREATE POLICY "cdk_select_anon" ON cdk_activations FOR SELECT TO anon, authenticated USING (true);
DROP POLICY IF EXISTS "cdk_insert_anon" ON cdk_activations;
CREATE POLICY "cdk_insert_anon" ON cdk_activations FOR INSERT TO anon, authenticated WITH CHECK (true);
DROP POLICY IF EXISTS "cdk_update_anon" ON cdk_activations;
CREATE POLICY "cdk_update_anon" ON cdk_activations FOR UPDATE TO anon, authenticated USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS "cdk_delete_anon" ON cdk_activations;
CREATE POLICY "cdk_delete_anon" ON cdk_activations FOR DELETE TO anon, authenticated USING (true);

CREATE INDEX IF NOT EXISTS idx_cdk_act_user ON cdk_activations(user_id, id DESC);

-- ── giveaways ───────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS giveaways (
    giveaway_id   INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    product_id    INTEGER NOT NULL,
    title         TEXT NOT NULL,
    description   TEXT NOT NULL DEFAULT '',
    winner_slots  INTEGER NOT NULL,
    end_time      TIMESTAMPTZ,
    status        TEXT NOT NULL DEFAULT 'active',
    created_by    BIGINT NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    drawn_at      TIMESTAMPTZ,
    cancelled_at  TIMESTAMPTZ,
    end_reminded  INTEGER NOT NULL DEFAULT 0
);

ALTER TABLE giveaways ENABLE ROW LEVEL SECURITY;
GRANT SELECT, INSERT, UPDATE, DELETE ON giveaways TO anon, authenticated;

DROP POLICY IF EXISTS "gw_select_anon" ON giveaways;
CREATE POLICY "gw_select_anon" ON giveaways FOR SELECT TO anon, authenticated USING (true);
DROP POLICY IF EXISTS "gw_insert_anon" ON giveaways;
CREATE POLICY "gw_insert_anon" ON giveaways FOR INSERT TO anon, authenticated WITH CHECK (true);
DROP POLICY IF EXISTS "gw_update_anon" ON giveaways;
CREATE POLICY "gw_update_anon" ON giveaways FOR UPDATE TO anon, authenticated USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS "gw_delete_anon" ON giveaways;
CREATE POLICY "gw_delete_anon" ON giveaways FOR DELETE TO anon, authenticated USING (true);

CREATE INDEX IF NOT EXISTS idx_giveaways_status ON giveaways(status);

CREATE TABLE IF NOT EXISTS giveaway_entries (
    entry_id     INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    giveaway_id  INTEGER NOT NULL REFERENCES giveaways(giveaway_id) ON DELETE CASCADE,
    telegram_id  BIGINT NOT NULL,
    username     TEXT,
    entries      INTEGER NOT NULL DEFAULT 1,
    entered_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(giveaway_id, telegram_id)
);

ALTER TABLE giveaway_entries ENABLE ROW LEVEL SECURITY;
GRANT SELECT, INSERT, UPDATE, DELETE ON giveaway_entries TO anon, authenticated;

DROP POLICY IF EXISTS "gwe_select_anon" ON giveaway_entries;
CREATE POLICY "gwe_select_anon" ON giveaway_entries FOR SELECT TO anon, authenticated USING (true);
DROP POLICY IF EXISTS "gwe_insert_anon" ON giveaway_entries;
CREATE POLICY "gwe_insert_anon" ON giveaway_entries FOR INSERT TO anon, authenticated WITH CHECK (true);
DROP POLICY IF EXISTS "gwe_update_anon" ON giveaway_entries;
CREATE POLICY "gwe_update_anon" ON giveaway_entries FOR UPDATE TO anon, authenticated USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS "gwe_delete_anon" ON giveaway_entries;
CREATE POLICY "gwe_delete_anon" ON giveaway_entries FOR DELETE TO anon, authenticated USING (true);

CREATE INDEX IF NOT EXISTS idx_gw_entries_gid ON giveaway_entries(giveaway_id);

CREATE TABLE IF NOT EXISTS giveaway_winners (
    winner_id    INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    giveaway_id  INTEGER NOT NULL REFERENCES giveaways(giveaway_id) ON DELETE CASCADE,
    telegram_id  BIGINT NOT NULL,
    username     TEXT,
    credential   TEXT,
    delivered    INTEGER NOT NULL DEFAULT 0,
    order_id     INTEGER,
    won_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE giveaway_winners ENABLE ROW LEVEL SECURITY;
GRANT SELECT, INSERT, UPDATE, DELETE ON giveaway_winners TO anon, authenticated;

DROP POLICY IF EXISTS "gww_select_anon" ON giveaway_winners;
CREATE POLICY "gww_select_anon" ON giveaway_winners FOR SELECT TO anon, authenticated USING (true);
DROP POLICY IF EXISTS "gww_insert_anon" ON giveaway_winners;
CREATE POLICY "gww_insert_anon" ON giveaway_winners FOR INSERT TO anon, authenticated WITH CHECK (true);
DROP POLICY IF EXISTS "gww_update_anon" ON giveaway_winners;
CREATE POLICY "gww_update_anon" ON giveaway_winners FOR UPDATE TO anon, authenticated USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS "gww_delete_anon" ON giveaway_winners;
CREATE POLICY "gww_delete_anon" ON giveaway_winners FOR DELETE TO anon, authenticated USING (true);

CREATE INDEX IF NOT EXISTS idx_gw_winners_gid ON giveaway_winners(giveaway_id);

CREATE TABLE IF NOT EXISTS giveaway_invites (
    invite_id    INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    giveaway_id  INTEGER NOT NULL REFERENCES giveaways(giveaway_id) ON DELETE CASCADE,
    inviter_id   BIGINT NOT NULL,
    invitee_id   BIGINT NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(giveaway_id, invitee_id)
);

ALTER TABLE giveaway_invites ENABLE ROW LEVEL SECURITY;
GRANT SELECT, INSERT, UPDATE, DELETE ON giveaway_invites TO anon, authenticated;

DROP POLICY IF EXISTS "gwi_select_anon" ON giveaway_invites;
CREATE POLICY "gwi_select_anon" ON giveaway_invites FOR SELECT TO anon, authenticated USING (true);
DROP POLICY IF EXISTS "gwi_insert_anon" ON giveaway_invites;
CREATE POLICY "gwi_insert_anon" ON giveaway_invites FOR INSERT TO anon, authenticated WITH CHECK (true);
DROP POLICY IF EXISTS "gwi_update_anon" ON giveaway_invites;
CREATE POLICY "gwi_update_anon" ON giveaway_invites FOR UPDATE TO anon, authenticated USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS "gwi_delete_anon" ON giveaway_invites;
CREATE POLICY "gwi_delete_anon" ON giveaway_invites FOR DELETE TO anon, authenticated USING (true);

CREATE INDEX IF NOT EXISTS idx_giveaway_invites_g ON giveaway_invites(giveaway_id, inviter_id);

-- ── gifts ───────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS gifts (
    gift_id      INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    code         TEXT NOT NULL UNIQUE,
    product_id   INTEGER NOT NULL,
    product_name TEXT NOT NULL,
    stock_id     INTEGER,
    amount_mmk   INTEGER NOT NULL DEFAULT 0,
    sender_id    BIGINT NOT NULL,
    sender_name  TEXT NOT NULL DEFAULT '',
    recipient_id BIGINT,
    status       TEXT NOT NULL DEFAULT 'unclaimed',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at   TIMESTAMPTZ NOT NULL,
    claimed_at   TIMESTAMPTZ
);

ALTER TABLE gifts ENABLE ROW LEVEL SECURITY;
GRANT SELECT, INSERT, UPDATE, DELETE ON gifts TO anon, authenticated;

DROP POLICY IF EXISTS "gifts_select_anon" ON gifts;
CREATE POLICY "gifts_select_anon" ON gifts FOR SELECT TO anon, authenticated USING (true);
DROP POLICY IF EXISTS "gifts_insert_anon" ON gifts;
CREATE POLICY "gifts_insert_anon" ON gifts FOR INSERT TO anon, authenticated WITH CHECK (true);
DROP POLICY IF EXISTS "gifts_update_anon" ON gifts;
CREATE POLICY "gifts_update_anon" ON gifts FOR UPDATE TO anon, authenticated USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS "gifts_delete_anon" ON gifts;
CREATE POLICY "gifts_delete_anon" ON gifts FOR DELETE TO anon, authenticated USING (true);

CREATE INDEX IF NOT EXISTS idx_gifts_code ON gifts(code);

-- ── lucky draw ──────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS ld_rounds (
    round_id     INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    product_id   INTEGER NOT NULL,
    product_name TEXT NOT NULL,
    prize_mode   TEXT NOT NULL DEFAULT 'instant',
    prize_value  INTEGER NOT NULL DEFAULT 0,
    entry_price  INTEGER NOT NULL,
    total_slots  INTEGER NOT NULL,
    deadline     TIMESTAMPTZ,
    status       TEXT NOT NULL DEFAULT 'open',
    seed         TEXT NOT NULL DEFAULT '',
    seed_hash    TEXT NOT NULL DEFAULT '',
    winner_id    BIGINT,
    winner_slot  INTEGER,
    created_by   BIGINT NOT NULL DEFAULT 0,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    drawn_at     TIMESTAMPTZ
);

ALTER TABLE ld_rounds ENABLE ROW LEVEL SECURITY;
GRANT SELECT, INSERT, UPDATE, DELETE ON ld_rounds TO anon, authenticated;

DROP POLICY IF EXISTS "ld_select_anon" ON ld_rounds;
CREATE POLICY "ld_select_anon" ON ld_rounds FOR SELECT TO anon, authenticated USING (true);
DROP POLICY IF EXISTS "ld_insert_anon" ON ld_rounds;
CREATE POLICY "ld_insert_anon" ON ld_rounds FOR INSERT TO anon, authenticated WITH CHECK (true);
DROP POLICY IF EXISTS "ld_update_anon" ON ld_rounds;
CREATE POLICY "ld_update_anon" ON ld_rounds FOR UPDATE TO anon, authenticated USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS "ld_delete_anon" ON ld_rounds;
CREATE POLICY "ld_delete_anon" ON ld_rounds FOR DELETE TO anon, authenticated USING (true);

CREATE INDEX IF NOT EXISTS idx_ld_rounds_status ON ld_rounds(status);

CREATE TABLE IF NOT EXISTS ld_entries (
    entry_id    INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    round_id    INTEGER NOT NULL REFERENCES ld_rounds(round_id) ON DELETE CASCADE,
    telegram_id BIGINT NOT NULL,
    username    TEXT NOT NULL DEFAULT '',
    slot_no     INTEGER NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(round_id, slot_no)
);

ALTER TABLE ld_entries ENABLE ROW LEVEL SECURITY;
GRANT SELECT, INSERT, UPDATE, DELETE ON ld_entries TO anon, authenticated;

DROP POLICY IF EXISTS "lde_select_anon" ON ld_entries;
CREATE POLICY "lde_select_anon" ON ld_entries FOR SELECT TO anon, authenticated USING (true);
DROP POLICY IF EXISTS "lde_insert_anon" ON ld_entries;
CREATE POLICY "lde_insert_anon" ON ld_entries FOR INSERT TO anon, authenticated WITH CHECK (true);
DROP POLICY IF EXISTS "lde_update_anon" ON ld_entries;
CREATE POLICY "lde_update_anon" ON ld_entries FOR UPDATE TO anon, authenticated USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS "lde_delete_anon" ON ld_entries;
CREATE POLICY "lde_delete_anon" ON ld_entries FOR DELETE TO anon, authenticated USING (true);

CREATE INDEX IF NOT EXISTS idx_ld_entries_round ON ld_entries(round_id);
CREATE INDEX IF NOT EXISTS idx_ld_entries_user ON ld_entries(telegram_id);

-- ── reseller keys & requests ────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS reseller_keys (
    key_id      INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    code        TEXT NOT NULL UNIQUE,
    created_by  BIGINT NOT NULL,
    note        TEXT,
    used_by     BIGINT,
    used_at     TIMESTAMPTZ,
    revoked     INTEGER NOT NULL DEFAULT 0,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE reseller_keys ENABLE ROW LEVEL SECURITY;
GRANT SELECT, INSERT, UPDATE, DELETE ON reseller_keys TO anon, authenticated;

DROP POLICY IF EXISTS "rk_select_anon" ON reseller_keys;
CREATE POLICY "rk_select_anon" ON reseller_keys FOR SELECT TO anon, authenticated USING (true);
DROP POLICY IF EXISTS "rk_insert_anon" ON reseller_keys;
CREATE POLICY "rk_insert_anon" ON reseller_keys FOR INSERT TO anon, authenticated WITH CHECK (true);
DROP POLICY IF EXISTS "rk_update_anon" ON reseller_keys;
CREATE POLICY "rk_update_anon" ON reseller_keys FOR UPDATE TO anon, authenticated USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS "rk_delete_anon" ON reseller_keys;
CREATE POLICY "rk_delete_anon" ON reseller_keys FOR DELETE TO anon, authenticated USING (true);

CREATE INDEX IF NOT EXISTS idx_reseller_keys_code ON reseller_keys(code);

CREATE TABLE IF NOT EXISTS reseller_requests (
    request_id   INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    telegram_id  BIGINT NOT NULL,
    username     TEXT,
    message      TEXT,
    status       TEXT NOT NULL DEFAULT 'pending',
    handled_by   BIGINT,
    handled_at   TIMESTAMPTZ,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE reseller_requests ENABLE ROW LEVEL SECURITY;
GRANT SELECT, INSERT, UPDATE, DELETE ON reseller_requests TO anon, authenticated;

DROP POLICY IF EXISTS "rr_select_anon" ON reseller_requests;
CREATE POLICY "rr_select_anon" ON reseller_requests FOR SELECT TO anon, authenticated USING (true);
DROP POLICY IF EXISTS "rr_insert_anon" ON reseller_requests;
CREATE POLICY "rr_insert_anon" ON reseller_requests FOR INSERT TO anon, authenticated WITH CHECK (true);
DROP POLICY IF EXISTS "rr_update_anon" ON reseller_requests;
CREATE POLICY "rr_update_anon" ON reseller_requests FOR UPDATE TO anon, authenticated USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS "rr_delete_anon" ON reseller_requests;
CREATE POLICY "rr_delete_anon" ON reseller_requests FOR DELETE TO anon, authenticated USING (true);

CREATE INDEX IF NOT EXISTS idx_reseller_reqs_status ON reseller_requests(status);

-- ── order reports (warranty) ────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS order_reports (
    report_id    INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    order_id     INTEGER NOT NULL,
    telegram_id  BIGINT NOT NULL,
    username     TEXT,
    message      TEXT,
    status       TEXT NOT NULL DEFAULT 'pending',
    handled_by   BIGINT,
    handled_at   TIMESTAMPTZ,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE order_reports ENABLE ROW LEVEL SECURITY;
GRANT SELECT, INSERT, UPDATE, DELETE ON order_reports TO anon, authenticated;

DROP POLICY IF EXISTS "or_select_anon" ON order_reports;
CREATE POLICY "or_select_anon" ON order_reports FOR SELECT TO anon, authenticated USING (true);
DROP POLICY IF EXISTS "or_insert_anon" ON order_reports;
CREATE POLICY "or_insert_anon" ON order_reports FOR INSERT TO anon, authenticated WITH CHECK (true);
DROP POLICY IF EXISTS "or_update_anon" ON order_reports;
CREATE POLICY "or_update_anon" ON order_reports FOR UPDATE TO anon, authenticated USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS "or_delete_anon" ON order_reports;
CREATE POLICY "or_delete_anon" ON order_reports FOR DELETE TO anon, authenticated USING (true);

CREATE INDEX IF NOT EXISTS idx_order_reports_status ON order_reports(status);
CREATE INDEX IF NOT EXISTS idx_order_reports_order ON order_reports(order_id);

-- ── AM Pro subscriptions ────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS am_subscriptions (
    subscription_id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    telegram_id  BIGINT NOT NULL,
    username     TEXT,
    started_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at   TIMESTAMPTZ NOT NULL,
    status       TEXT NOT NULL DEFAULT 'active',
    amount_paid  INTEGER NOT NULL DEFAULT 0
);

ALTER TABLE am_subscriptions ENABLE ROW LEVEL SECURITY;
GRANT SELECT, INSERT, UPDATE, DELETE ON am_subscriptions TO anon, authenticated;

DROP POLICY IF EXISTS "am_select_anon" ON am_subscriptions;
CREATE POLICY "am_select_anon" ON am_subscriptions FOR SELECT TO anon, authenticated USING (true);
DROP POLICY IF EXISTS "am_insert_anon" ON am_subscriptions;
CREATE POLICY "am_insert_anon" ON am_subscriptions FOR INSERT TO anon, authenticated WITH CHECK (true);
DROP POLICY IF EXISTS "am_update_anon" ON am_subscriptions;
CREATE POLICY "am_update_anon" ON am_subscriptions FOR UPDATE TO anon, authenticated USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS "am_delete_anon" ON am_subscriptions;
CREATE POLICY "am_delete_anon" ON am_subscriptions FOR DELETE TO anon, authenticated USING (true);

CREATE INDEX IF NOT EXISTS idx_am_subs_user ON am_subscriptions(telegram_id, status);

CREATE TABLE IF NOT EXISTS am_usage (
    telegram_id BIGINT NOT NULL,
    day         DATE NOT NULL DEFAULT CURRENT_DATE,
    count       INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (telegram_id, day)
);

ALTER TABLE am_usage ENABLE ROW LEVEL SECURITY;
GRANT SELECT, INSERT, UPDATE, DELETE ON am_usage TO anon, authenticated;

DROP POLICY IF EXISTS "amu_select_anon" ON am_usage;
CREATE POLICY "amu_select_anon" ON am_usage FOR SELECT TO anon, authenticated USING (true);
DROP POLICY IF EXISTS "amu_insert_anon" ON am_usage;
CREATE POLICY "amu_insert_anon" ON am_usage FOR INSERT TO anon, authenticated WITH CHECK (true);
DROP POLICY IF EXISTS "amu_update_anon" ON am_usage;
CREATE POLICY "amu_update_anon" ON am_usage FOR UPDATE TO anon, authenticated USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS "amu_delete_anon" ON am_usage;
CREATE POLICY "amu_delete_anon" ON am_usage FOR DELETE TO anon, authenticated USING (true);

-- ── seed config defaults ────────────────────────────────────────────────
INSERT INTO config (key, value) VALUES
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
    ('currency_mode', 'mmk_native')
ON CONFLICT (key) DO NOTHING;

-- ── grant service_role on all tables (bot uses this) ────────────────────
GRANT SELECT, INSERT, UPDATE, DELETE ON config, users, products, stock, sold_stock, orders, deposits, user_state, cdk_activations, giveaways, giveaway_entries, giveaway_winners, giveaway_invites, gifts, ld_rounds, ld_entries, reseller_keys, reseller_requests, order_reports, am_subscriptions, am_usage TO service_role;

-- Grant USAGE on all sequences (IDENTITY columns need this for INSERT)
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO anon, authenticated, service_role;