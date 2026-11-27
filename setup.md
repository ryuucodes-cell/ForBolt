# Setup Guide — StockBot Telegram Bot + Mini App

This project has two frontends that share one database:

1. **Telegram Bot** (Python) — the chat-based shop with admin panel
2. **Telegram Mini App** (web app) — a mobile-friendly shop UI that opens inside Telegram

Both use the same **SQLite database file** — no external database server, no subscription, completely free. The database file lives in a Docker volume so it persists across restarts.

---

## Project Structure

```
project/
├── bot.py              # Bot entry point — starts polling + API server + background threads
├── api.py              # Flask API server (serves web app data from the same SQLite DB)
├── db.py               # Database layer (SQLite — file-based, no server needed)
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
        ├── api.ts        # API client (fetches from the bot's Flask server)
        ├── types.ts      # TypeScript types
        └── index.css     # Styles
```

---

## Prerequisites

- **Docker** and **Docker Compose** installed on your server
- A **Telegram Bot Token** (from [@BotFather](https://t.me/BotFather))
- Your **Telegram user ID** (numeric — message [@userinfobot](https://t.me/userinfobot) to get it)

That's it — no database server, no external services, no subscriptions.

---

## Step 1: Configure Environment Variables

Open `.env` and fill in:

```
TELEGRAM_BOT_TOKEN=123456789:ABCdefGhiJklMnoPqrStuVwxYz
ADMIN_CHAT_ID=123456789
```

### Optional: Binance Pay (for auto-verified USDT deposits)

```
BINANCE_API_KEY=your_binance_api_key
BINANCE_API_SECRET=your_binance_api_secret
```

No database configuration is needed — SQLite creates the database file automatically on first run.

---

## Step 2: Start Everything with Docker

From the project root directory:

```bash
docker compose up --build -d
```

This starts two containers:

| Container | What it does | Port |
|-----------|-------------|------|
| `stockbot` | Telegram bot (polling) + Flask API server | 5000 |
| `stockbot-webapp` | Web app served by nginx (proxies /api to bot) | 8080 |

The SQLite database file is stored in a Docker volume (`shop-data`) at `/app/data/shop.db` inside the bot container. It persists across restarts and updates.

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

This starts the bot and the API server (port 5000) together.

### Web App

```bash
cd webapp
npm install
npm run dev
```

The web app runs at `http://localhost:5173`. It will fetch data from `http://localhost:5000/api/...` — make sure the bot is running so the API is available.

---

## How the Two Frontends Share Data

Both the bot and the web app use the same SQLite database file:

- The **bot** reads and writes directly to the SQLite file for all operations (purchases, deposits, stock management, admin actions).
- The **web app** makes HTTP requests to the bot's built-in Flask API server (port 5000), which reads from the same SQLite file. nginx proxies `/api/` requests from port 8080 to the bot's port 5000.

When a user buys something through the bot, it instantly appears in the web app's order history. When a deposit is approved by the admin, the wallet balance updates in both places.

---

## Database

The database is a **SQLite file** at `/app/data/shop.db` (inside the Docker container). It's stored in a Docker volume so it survives container restarts and updates.

- **Backups**: Copy the `shop.db` file from the Docker volume. A daily VACUUM runs automatically to keep the file compact.
- **Restore**: Replace the `shop.db` file and restart the container (`docker restart stockbot`).

All 21 tables (users, products, stock, orders, deposits, giveaways, gifts, lucky draw rounds, reseller keys, CDK activations, AM subscriptions, etc.) are created automatically on first run.

---

## Troubleshooting

### Bot won't start

- Check `TELEGRAM_BOT_TOKEN` is set correctly in `.env`
- Run `docker logs stockbot` to see the error

### Web app shows blank page

- Make sure the bot container is running (it serves the API)
- Run `docker logs stockbot-webapp` to check nginx errors
- Rebuild with `docker compose up --build -d`

### Web app can't load products

- The API server runs inside the bot container on port 5000 — check it's running
- Run `docker logs stockbot` and look for "API server started on port 5000"
- Try `curl http://localhost:5000/api/products` to test the API directly

### Mini App button doesn't appear in Telegram

- You need to configure the Menu Button URL via @BotFather (see Step 3)
- The URL must be HTTPS

### Balance is different between bot and web app

- Both read from the same database file. If they differ, try refreshing the web app — the bot always has the authoritative value since it handles all money operations.

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

The database file is preserved in the `shop-data` Docker volume when you stop or restart.
