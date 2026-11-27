# Setup Guide — StockBot Telegram Bot + Mini App

This project has two frontends that share one database:

1. **Telegram Bot** (Python) — the chat-based shop with admin panel
2. **Telegram Mini App** (web app) — a mobile-friendly shop UI that opens inside Telegram

Both connect to the same **Supabase** (Postgres) database, so users see the same products, balance, orders, and deposits regardless of which one they use.

---

## Project Structure

```
project/
├── bot.py              # Bot entry point — starts polling + background threads
├── db.py               # Database layer (Postgres via psycopg2)
├── handlers.py         # Bot customer-facing handlers (shop, buy, wallet, etc.)
├── admin.py            # Admin panel entry
├── admin_*.py          # Admin sub-modules (products, deposits, orders, etc.)
├── topup.py            # Deposit / top-up flows
├── gifts.py            # Gift system
├── giveaway.py         # Giveaway system
├── lucky_draw.py       # Lucky draw system
├── cdk_redeem.py       # CDK key redemption
├── am_pro.py           # Alight Motion subscriptions
├── pricelist.py        # Price list
├── utils.py            # Shared helpers (formatting, keyboards, etc.)
├── emojis.py           # Emoji button helpers
├── msg_templates.py    # Message templates
├── binance_pay.py      # Binance Pay auto-verify
├── requirements.txt    # Python dependencies
├── Dockerfile          # Bot Docker image
├── docker-compose.yml  # Runs both bot + web app
├── .env                # Environment variables (secrets — never commit)
│
└── webapp/             # Telegram Mini App (web frontend)
    ├── package.json
    ├── vite.config.ts
    ├── tsconfig.json
    ├── index.html
    ├── Dockerfile       # Web app Docker image (nginx serving built files)
    └── src/
        ├── main.tsx
        ├── App.tsx       # Main app (shop, wallet, orders, deposits, giveaways, lucky draw)
        ├── supabase.ts   # Supabase client
        ├── types.ts      # TypeScript types
        └── index.css     # Styles
```

---

## Prerequisites

- **Docker** and **Docker Compose** installed on your server
- A **Telegram Bot Token** (from [@BotFather](https://t.me/BotFather))
- Your **Telegram user ID** (numeric — message [@userinfobot](https://t.me/userinfobot) to get it)
- Your **Supabase database password** (set when the project was provisioned)

---

## Step 1: Configure Environment Variables

Open `.env` and fill in the missing values:

### Required for the bot:

```
TELEGRAM_BOT_TOKEN=123456789:ABCdefGhiJklMnoPqrStuVwxYz
ADMIN_CHAT_ID=123456789
```

### Required: Database connection string

Find your Supabase database URL:
1. Go to your Supabase Dashboard → **Settings** → **Database**
2. Look for the **Connection string** section
3. Copy the **URI** format (it looks like `postgresql://postgres.[project-ref]:[password]@aws-0-[region].pooler.supabase.com:6543/postgres`)
4. Replace `YOUR_DB_PASSWORD` in `.env`:

```
SUPABASE_DB_URL=postgresql://postgres.kbnomuvdtgzoxcsqupqm:YOUR_ACTUAL_PASSWORD@aws-0-ap-southeast-1.pooler.supabase.com:6543/postgres
```

### Optional: Binance Pay (for auto-verified USDT deposits)

```
BINANCE_API_KEY=your_binance_api_key
BINANCE_API_SECRET=your_binance_api_secret
```

### Already configured (don't change):

The `VITE_SUPABASE_URL` and `VITE_SUPABASE_ANON_KEY` values are pre-filled and used by the web app. The `SUPABASE_URL` and `SUPABASE_ANON_KEY` values are used by the bot.

---

## Step 2: Start Everything with Docker

From the project root directory:

```bash
docker compose up --build -d
```

This starts two containers:

| Container | What it does | Port |
|-----------|-------------|------|
| `stockbot` | Telegram bot (long polling) | — |
| `stockbot-webapp` | Web app served by nginx | 8080 |

Check they're running:

```bash
docker compose ps
```

View bot logs:

```bash
docker logs -f stockbot
```

View web app logs:

```bash
docker logs -f stockbot-webapp
```

---

## Step 3: Set Up the Telegram Mini App

The web app runs on your server at `http://YOUR_SERVER_IP:8080`. To connect it to Telegram:

### Option A: Use a domain with HTTPS (recommended)

Telegram Mini Apps require HTTPS. If you have a domain:

1. Point a subdomain (e.g. `shop.yourdomain.com`) to your server
2. Set up a reverse proxy (nginx, Caddy, etc.) with SSL certificates
3. Update `docker-compose.yml` to expose the web app behind your proxy

### Option B: Use Cloudflare Tunnel (free, no domain needed)

```bash
# Install cloudflared
curl -L https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64 -o /usr/local/bin/cloudflared
chmod +x /usr/local/bin/cloudflared

# Create a tunnel
cloudflared tunnel --url http://localhost:8080
```

This gives you a free HTTPS URL like `https://random-words-xyz.trycloudflare.com`.

### Configure the Mini App button in Telegram

Once you have an HTTPS URL for the web app:

1. Message **@BotFather** on Telegram
2. Send `/newapp` or select your bot → **Bot Settings** → **Menu Button** → **Configure Mini App**
3. Set the URL to your HTTPS web app URL (e.g. `https://shop.yourdomain.com`)
4. Give it a name (e.g. "Shop")

Now users will see a "Shop" button in the bot's menu that opens the web app inside Telegram.

---

## Step 4: Configure the Shop

Once the bot is running, message it on Telegram and send `/start`.

As the admin (your `ADMIN_CHAT_ID`), you'll see an **Admin Panel** button. From there you can:

- **Add products** — name, category, price, description, fulfillment mode
- **Add stock** — paste credentials for instant-delivery products
- **Configure payment methods** — KBZPay, WavePay, CB Pay, Binance
- **Set up channels** — require users to join your channel before shopping
- **Create giveaways** and **lucky draws**
- **Generate reseller keys**
- **Adjust user balances**
- **Broadcast messages** to all users
- **Configure settings** — shop name, taglines, VIP tiers, deposit bonuses, etc.

---

## Running Locally (without Docker)

### Bot

```bash
pip install -r requirements.txt
python bot.py
```

### Web App

```bash
cd webapp
npm install
npm run dev
```

The web app runs at `http://localhost:5173`. Open it in your browser to preview.

---

## How the Two Frontends Share Data

Both the bot and the web app connect to the same Supabase Postgres database:

- The **bot** uses a direct Postgres connection (via `psycopg2`) with the `SUPABASE_DB_URL` connection string. This gives it full access to all tables for money operations, stock management, and admin actions.
- The **web app** uses the Supabase JavaScript client with the anon key. It reads products, displays orders/deposits, and lets users browse the shop. Money operations (purchases, deposits) go through the same database tables.

When a user buys something through the bot, it instantly appears in the web app's order history. When a deposit is approved by the admin, the wallet balance updates in both places.

---

## Database

The database is hosted on **Supabase** and includes automatic daily backups and point-in-time recovery. You can manage backups from the Supabase Dashboard → Database → Backups.

All 21 tables (users, products, stock, orders, deposits, giveaways, gifts, lucky draw rounds, reseller keys, CDK activations, AM subscriptions, etc.) were created automatically when the project was provisioned.

---

## Troubleshooting

### Bot won't start

- Check `TELEGRAM_BOT_TOKEN` is set correctly in `.env`
- Check `SUPABASE_DB_URL` has the correct password
- Run `docker logs stockbot` to see the error

### Web app shows blank page

- Make sure `VITE_SUPABASE_URL` and `VITE_SUPABASE_ANON_KEY` are set in `.env`
- The web app reads these at build time — rebuild with `docker compose up --build -d`

### Web app can't load products

- This usually means the Supabase anon key doesn't have access. The database tables have RLS policies that allow the anon key to read all data — verify these are in place via Supabase Dashboard → Authentication → Policies.

### Mini App button doesn't appear in Telegram

- You need to configure the Menu Button URL via @BotFather (see Step 3)
- The URL must be HTTPS

### Balance is different between bot and web app

- Both read from the same database table (`users.mmk_balance`). If they differ, try refreshing the web app — the bot always has the authoritative value since it handles all money operations.

---

## Stopping / Restarting

```bash
# Stop both
docker compose down

# Restart both
docker compose restart

# Rebuild after code changes
docker compose up --build -d
```
