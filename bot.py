"""
StockBot entry point.
Registers all handlers, starts Flask webhook server, starts background threads,
then runs the bot in polling mode.
"""

import logging
import os
import threading
import time

import html as _html
from datetime import datetime

import telebot
from telebot import apihelper
from dotenv import load_dotenv

apihelper.ENABLE_MIDDLEWARE = True

import db
import utils
import emojis
import msg_templates as T
import handlers
import topup
import admin
import giveaway
import lucky_draw
import cdk_redeem
import gifts
import am_pro
import pricelist

# ── logging setup ─────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("stockbot")

load_dotenv()

BOT_TOKEN    = os.getenv("TELEGRAM_BOT_TOKEN", "")
ADMIN_ID_ENV = os.getenv("ADMIN_CHAT_ID", "")

if not BOT_TOKEN:
    import sys
    sys.exit("ERROR: TELEGRAM_BOT_TOKEN is not set in .env — the bot cannot start without it.")

# ── init ──────────────────────────────────────────────────────────────────────

db.init_db()
if ADMIN_ID_ENV and ADMIN_ID_ENV.isdigit():
    db.cfg_set("admin_chat_id", int(ADMIN_ID_ENV))

# ── global exception handler: never let an error fail silently ─────────────────

# Substrings/class hints for transient network errors that are normal with long
# polling — the bot auto-reconnects, so these are logged but NOT alerted to admin.
_TRANSIENT_HINTS = (
    "connection aborted", "remotedisconnected", "remote end closed",
    "read timed out", "timed out", "connection reset", "connection refused",
    "max retries exceeded", "failed to establish", "connection broken",
    "temporarily unavailable", "bad gateway", "gateway time-out",
    "service unavailable", "connection error",
)

def _is_transient(exc) -> bool:
    name = type(exc).__name__.lower()
    if any(k in name for k in ("timeout", "connectionerror", "connectionreset",
                               "protocolerror", "readtimeout", "remotedisconnected")):
        return True
    s = str(exc).lower()
    return any(k in s for k in _TRANSIENT_HINTS)


class _BotExceptionHandler(telebot.ExceptionHandler):
    """Catches any unhandled error raised while processing an update.

    Keeps the bot running. Transient network errors (dropped polling connections,
    timeouts) are logged quietly. Genuine code errors are logged with a traceback
    and alerted to the primary admin (throttled) so real problems aren't invisible.
    """
    _last_alert = 0.0

    def handle(self, exception):
        if _is_transient(exception):
            log.warning("Transient network error (auto-recovering): %s", exception)
            return True

        log.exception("Unhandled error while processing update: %s", exception)
        now = time.time()
        if now - _BotExceptionHandler._last_alert > 60:
            _BotExceptionHandler._last_alert = now
            try:
                aid = utils.admin_id()
                if aid:
                    bot.send_message(
                        aid,
                        "<b>An Error Was Caught</b>\n"
                        "<blockquote>Something hit an error, but the bot is still running fine. "
                        "Please check the logs for details.</blockquote>\n"
                        f"<code>{_html.escape(str(exception))[:400]}</code>",
                    )
            except Exception:
                pass
        return True  # handled — polling continues


bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML", exception_handler=_BotExceptionHandler())


def setup_bot_commands(b: telebot.TeleBot):
    """Register the Telegram command menu (the Menu / "/" button in the
    chat input). John's call (2026-11-03): /start only — the shop is
    button-driven; /list stays unregistered and is typed manually."""
    b.set_my_commands([
        telebot.types.BotCommand("start", "Open the shop — main menu"),
    ])
utils.set_bot_instance(bot)

# ── request logging middleware ────────────────────────────────────────────────

@bot.middleware_handler(update_types=["message"])
def log_message(bot_instance, update):
    user = f"@{update.from_user.username}" if update.from_user.username else f"id:{update.from_user.id}"
    text = update.text or update.content_type
    log.info(f"MSG  {user} → {text!r}")

@bot.middleware_handler(update_types=["callback_query"])
def log_callback(bot_instance, update):
    user = f"@{update.from_user.username}" if update.from_user.username else f"id:{update.from_user.id}"
    log.info(f"BTN  {user} → {update.data!r}")

# ── register handlers ─────────────────────────────────────────────────────────

handlers.register(bot)
topup.register(bot)
admin.register(bot)
giveaway.register(bot)
lucky_draw.register(bot)
cdk_redeem.register(bot)
gifts.register(bot)
am_pro.register(bot)
pricelist.register(bot)

# ── background threads ────────────────────────────────────────────────────────

def _auto_backup_loop():
    """Supabase handles backups automatically — this loop is now a no-op
    that just logs a heartbeat once a day so we know the background thread
    is alive. The daily SQLite VACUUM+send was removed when we moved to Postgres."""
    while True:
        time.sleep(3600)
        try:
            now = datetime.now()
            if now.hour == 0:
                log.info("Daily heartbeat: Supabase manages database backups automatically")
        except Exception as e:
            log.error(f"Backup loop error: {e}")

def _discount_expiry_loop():
    """Every 60 seconds: clear expired discounts/stale states, send giveaway
    end reminders, release expired gifts. Each job runs in its OWN try with
    logging (V1.5): previously all four shared one try with a silent
    'except: pass', so one persistently failing job starved the rest —
    including gift expiry — forever, with nothing in the logs."""
    _jobs = (
        ("expire_discounts", lambda: db.expire_discounts()),
        ("state_expire_all", lambda: db.state_expire_all()),
        ("giveaway_end_reminders", lambda: giveaway.check_end_reminders(bot)),
        ("release_expired_gifts", lambda: db.release_expired_gifts()),
        ("lucky_draw_sweep", lambda: lucky_draw.sweep(bot)),
    )
    while True:
        time.sleep(60)
        for _name, _job in _jobs:
            try:
                _job()
            except Exception as e:
                log.error(f"Housekeeping job {_name} failed: {e}")

def _reseller_inactivity_loop():
    """Every 6 hours: revoke resellers who haven't purchased within the configured
    inactivity window, and DM each of them so they know they can re-apply."""
    while True:
        time.sleep(6 * 3600)
        try:
            days = int(db.cfg_get("reseller_inactivity_days", "30") or 30)
            if days <= 0:
                continue
            revoked = db.revoke_inactive_resellers(days)
            if not revoked:
                continue
            import telebot as _tb
            kb = _tb.types.InlineKeyboardMarkup(row_width=1)
            kb.add(_tb.types.InlineKeyboardButton("Request Access Again", callback_data="reseller_request"))
            kb.add(_tb.types.InlineKeyboardButton("Dismiss", callback_data="dismiss_msg"))
            for uid, uname in revoked:
                try:
                    bot.send_message(
                        uid,
                        "<b>Reseller Access Expired</b>\n\n"
                        f"No purchase was made in the last {days} days, so your "
                        "reseller access has been removed automatically.\n\n"
                        "You're welcome to request access again anytime.",
                        reply_markup=kb,
                    )
                except Exception:
                    pass
            try:
                names = ", ".join(f"@{u or i}" for i, u in revoked[:10])
                extra = f" (+{len(revoked) - 10} more)" if len(revoked) > 10 else ""
                utils.notify_admin(
                    bot,
                    f"<b>Reseller cleanup</b>\n\n"
                    f"Removed <b>{len(revoked)}</b> inactive reseller(s): {names}{extra}",
                )
            except Exception:
                pass
        except Exception as e:
            log.error(f"Reseller inactivity loop error: {e}")

def _deposit_nudge_loop():
    """Every 30 minutes: remind admins about deposits that have been pending
    longer than the configured nudge window (default 6h, 0 = disabled).
    Each deposit is nudged once (nudge_sent flag survives restarts)."""
    while True:
        time.sleep(30 * 60)
        try:
            hours = int(db.cfg_get("deposit_nudge_hours", "6") or 6)
            if hours <= 0:
                continue
            stale = db.list_stale_pending_deposits(hours)
            if not stale:
                continue
            import telebot as _tb
            for d in stale:
                kb = _tb.types.InlineKeyboardMarkup()
                kb.add(_tb.types.InlineKeyboardButton(
                    f"Review Deposit #{d['deposit_id']}",
                    callback_data=f"adm_dep_{d['deposit_id']}"))
                uname = d["username"] or str(d["telegram_id"])
                try:
                    utils.notify_admin(
                        bot,
                        f"{emojis.emoji('pending', '⏳')} <b>Deposit Still Waiting</b>\n\n"
                        f"<blockquote>Deposit: <b>#{d['deposit_id']}</b>\n"
                        f"User: @{uname} (<code>{d['telegram_id']}</code>)\n"
                        f"Method: {d['method'].upper()}\n"
                        f"Amount: <b>{d['amount_mmk']:,d} MMK</b></blockquote>\n\n"
                        f"Waiting for over {hours}h — tap below to review it.",
                        reply_markup=kb,
                    )
                    db.mark_deposit_nudged(d["deposit_id"])
                except Exception as e:
                    # Never swallow silently — a dead nudge loop with no
                    # log line is how this feature stayed broken (V1.5).
                    log.error(f"Deposit nudge failed for #{d['deposit_id']}: {e}")
        except Exception as e:
            log.error(f"Deposit nudge loop error: {e}")


def _cdk_gateway_health_loop():
    """Every 15 minutes: lightweight liveness probe per CDK gateway.
    Alerts the log channel when one goes down (2 consecutive failures)
    and when it recovers."""
    time.sleep(60)  # let the bot settle first
    fails = {}
    while True:
        time.sleep(15 * 60)
        try:
            for p in cdk_redeem.CDK_PRODUCTS:
                pid = p["id"]
                try:
                    alive = cdk_redeem.gateway_health_check(p)
                except Exception:
                    log.exception(f"CDK health probe crashed for {pid}")
                    alive = False
                if alive:
                    if fails.get(pid, 0) >= 2:
                        try:
                            utils.log_to_channel(bot, T.cdk_gateway_recovered(p["name"]))
                        except Exception:
                            pass
                    fails[pid] = 0
                else:
                    fails[pid] = fails.get(pid, 0) + 1
                    if fails[pid] == 2:
                        log.warning(f"CDK gateway down: {p['name']}")
                        try:
                            utils.log_to_channel(bot, T.cdk_gateway_down(p["name"]))
                        except Exception:
                            pass
        except Exception as e:
            log.error(f"CDK gateway health loop error: {e}")


# ── main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    log.info("Starting StockBot...")

    # Reap CDK activations left in-progress by a previous run (restart/deploy).
    try:
        n = db.cdk_activation_reap()
        if n:
            log.info(f"CDK reaper: marked {n} stale activation(s) interrupted")
    except Exception as e:
        log.error(f"CDK reaper failed: {e}")

    # Re-arm CDK pending watchdogs: activations still awaiting a provider
    # verdict get settled (confirmed, failed, or timed out at the deadline)
    # instead of hanging on 'pending' forever across restarts.
    try:
        n = cdk_redeem.sweep_pending_activations(bot)
        if n:
            log.info(f"CDK sweep: re-armed watchdog for {n} pending activation(s)")
    except Exception as e:
        log.error(f"CDK pending sweep failed: {e}")

    t_discount = threading.Thread(target=_discount_expiry_loop, daemon=True)
    t_discount.start()

    t_backup = threading.Thread(target=_auto_backup_loop, daemon=True)
    t_backup.start()

    t_reseller = threading.Thread(target=_reseller_inactivity_loop, daemon=True)
    t_reseller.start()

    t_nudge = threading.Thread(target=_deposit_nudge_loop, daemon=True)
    t_nudge.start()

    t_cdkhealth = threading.Thread(target=_cdk_gateway_health_loop, daemon=True)
    t_cdkhealth.start()

    # Telegram command menu (Menu / "/" button): /start only.
    try:
        setup_bot_commands(bot)
        log.info("Bot commands registered: /start")
    except Exception as e:
        log.error(f"Bot command setup failed: {e}")

    log.info("StockBot running — polling for updates")
    print("StockBot running...")
    bot.infinity_polling(timeout=30, long_polling_timeout=20)
