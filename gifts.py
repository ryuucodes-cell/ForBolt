"""
Ryuu — Gift mode.

Buy any product as a gift: pay normally, get a gift link, send it to a
friend. The friend opens the link, claims the gift, and the product is
delivered like a normal purchase. Stock is reserved at gift creation so it
can't be sold twice; unclaimed gifts expire after gift_expiry_days and the
stock returns to sale.
"""

import html
import logging

import telebot
from telebot import types

import db
import emojis
import msg_templates as T
from utils import (
    silent_delete, edit_or_send, check_rate_limit,
    fmt_money, fmt_usd,
)

log = logging.getLogger("gifts")

_bot_username = None


def _username(bot: telebot.TeleBot) -> str:
    global _bot_username
    if not _bot_username:
        try:
            _bot_username = bot.get_me().username or ""
        except Exception:
            _bot_username = ""
    return _bot_username


def gift_link(bot: telebot.TeleBot, code: str) -> str:
    un = _username(bot)
    return f"https://t.me/{un}?start=gift_{code}" if un else f"gift_{code}"


def _esc(s) -> str:
    return html.escape(str(s or ""), quote=False)


def register(bot: telebot.TeleBot):

    # ── entry: "Gift" button on the product page ──

    @bot.callback_query_handler(func=lambda c: c.data.startswith("giftbuy_"))
    def cb_gift_buy(call: types.CallbackQuery):
        uid = call.from_user.id
        pid = int(call.data[len("giftbuy_"):])
        p = db.get_product(pid)
        if not p or not p["active"]:
            bot.answer_callback_query(call.id, "Product not available.")
            return
        if db.get_fulfillment(p) != "instant":
            bot.answer_callback_query(call.id, "Gifts are only available for instant-delivery products.", show_alert=True)
            return
        if db.stock_count(pid) < 1:
            bot.answer_callback_query(call.id, "Out of stock right now.", show_alert=True)
            return
        price = db.get_price_for_user(p, uid)
        bal = db.get_balance(uid)
        kb = types.InlineKeyboardMarkup(row_width=2)
        kb.add(
            emojis.button(f"Send Gift — {fmt_usd(price)}", callback_data=f"giftconfirm_{pid}_{price}",
                          emoji_id="5039823300683891773", lead="🎁"),
            emojis.button("Cancel", callback_data=f"prod_{pid}",
                          emoji_id="5210952531676504517", lead="❌"),
        )
        text = (
            f"{emojis.emoji('gift', '🎁')} <b>Send as Gift</b>\n\n"
            f"Product: <b>{_esc(p['name'])}</b>\n"
            f"Price: <b>{fmt_usd(price)}</b>\n"
            f"Your balance: <b>{fmt_usd(bal)}</b>\n\n"
            f"You'll get a gift link to send to your friend. "
            f"The gift expires in {db.cfg_get('gift_expiry_days', '30')} days if unclaimed."
        )
        edit_or_send(bot, uid, call.message.message_id, text, reply_markup=kb)
        bot.answer_callback_query(call.id)

    @bot.callback_query_handler(func=lambda c: c.data.startswith("giftconfirm_"))
    def cb_gift_confirm(call: types.CallbackQuery):
        parts = call.data.split("_")
        pid = int(parts[1])
        quoted = int(parts[2]) if len(parts) > 2 else None
        uid = call.from_user.id
        uname = call.from_user.username or ""

        if not check_rate_limit(uid, "gift", min_seconds=5):
            bot.answer_callback_query(call.id, "Please wait a moment.", show_alert=True)
            return
        p = db.get_product(pid)
        if not p or not p["active"]:
            bot.answer_callback_query(call.id, "Product not available.")
            return
        if db.get_fulfillment(p) != "instant":
            bot.answer_callback_query(call.id, "Gifts are only available for instant-delivery products.", show_alert=True)
            return
        price = db.get_price_for_user_by_id(pid, uid)
        if quoted is not None and price > quoted:
            bot.answer_callback_query(call.id, "Price changed — please review again.", show_alert=True)
            return
        if db.stock_count(pid) < 1:
            bot.answer_callback_query(call.id, "Just sold out.", show_alert=True)
            return

        # Mint code + reserve atomically first so we never charge without stock.
        try:
            gift_code = db.mint_gift_code()
        except Exception:
            log.exception("gift code mint failed")
            bot.answer_callback_query(call.id, "Something went wrong.", show_alert=True)
            return
        stock_id = db.reserve_stock(pid, gift_code)
        if not stock_id:
            bot.answer_callback_query(call.id, "Just sold out.", show_alert=True)
            return
        if not db.deduct_balance_atomic(uid, price):
            db.release_stock_reservation(stock_id)
            bot.answer_callback_query(call.id, "Insufficient balance.", show_alert=True)
            return
        try:
            expiry_days = int(db.cfg_get("gift_expiry_days", "30") or 30)
            db.create_gift(gift_code, pid, p["name"], stock_id, price, uid, uname, expiry_days)
        except Exception:
            log.exception("gift create failed")
            db.release_stock_reservation(stock_id)
            try:
                db.adjust_balance(uid, price)  # refund — best effort
            except Exception:
                pass
            bot.answer_callback_query(call.id, "Something went wrong — you were not charged.", show_alert=True)
            return

        # Sender's lifetime spend counts the gift purchase (VIP progress).
        try:
            db.add_total_spent(uid, price)
        except Exception:
            pass
        link = gift_link(bot, gift_code)
        kb = types.InlineKeyboardMarkup(row_width=1)
        kb.add(types.InlineKeyboardButton("📤 Share Gift Link", url=f"https://t.me/share/url?url={link}&text=Here's a gift for you!"))
        kb.add(emojis.button("My Gifts", callback_data="my_gifts",
                             emoji_id="5039823300683891773", lead="🎁"))
        kb.add(emojis.button("Main Menu", callback_data="main_menu",
                             emoji_id="5042022053356504092", lead="🏠"))
        edit_or_send(bot, uid, call.message.message_id,
                     T.gift_created(_esc(p["name"]), link, expiry_days),
                     reply_markup=kb)
        bot.answer_callback_query(call.id, "Gift ready!")

    # ── claim via deep link: /start gift_<CODE> ──

    @bot.callback_query_handler(func=lambda c: c.data.startswith("giftclaim_"))
    def cb_gift_claim(call: types.CallbackQuery):
        uid = call.from_user.id
        uname = call.from_user.username or ""
        code = call.data[len("giftclaim_"):]
        _do_claim(bot, uid, uname, code, call.message.message_id, call.id)

    @bot.callback_query_handler(func=lambda c: c.data == "my_gifts")
    def cb_my_gifts(call: types.CallbackQuery):
        uid = call.from_user.id
        gifts = db.list_user_gifts(uid, 10)
        if not gifts:
            text = f"{emojis.emoji('gift', '🎁')} <b>My Gifts</b>\n\nYou haven't sent any gifts yet."
        else:
            lines = []
            for g in gifts:
                status = {"unclaimed": f"{emojis.emoji('pending', '⏳')} Unclaimed", "claimed": f"{emojis.emoji('success', '✅')} Claimed", "expired": f"{emojis.emoji('hourglass_done', '⌛')} Expired"}.get(g["status"], g["status"])
                lines.append(f"{emojis.emoji('gift', '🎁')} <b>{_esc(g['product_name'])}</b> — {status}\n<code>{g['code']}</code>")
            text = f"{emojis.emoji('gift', '🎁')} <b>My Gifts</b>\n\n" + "\n\n".join(lines)
        kb = types.InlineKeyboardMarkup(row_width=1)
        kb.add(emojis.button("Main Menu", callback_data="main_menu",
                             emoji_id="5042022053356504092", lead="🏠"))
        edit_or_send(bot, uid, call.message.message_id, text, reply_markup=kb)
        bot.answer_callback_query(call.id)


def handle_deep_link(bot: telebot.TeleBot, uid: int, uname: str, payload: str) -> bool:
    """Handle /start gift_<CODE>. Returns True if the payload was consumed."""
    if not payload.startswith("gift_"):
        return False
    code = payload[len("gift_"):]
    g = db.get_gift_by_code(code)
    if not g:
        bot.send_message(uid, T.gift_claim_invalid("This gift link is invalid."))
        return True
    if g["status"] == "claimed":
        bot.send_message(uid, T.gift_claim_invalid("This gift was already claimed."))
        return True
    if _gift_expired(g):
        bot.send_message(uid, T.gift_claim_invalid("This gift has expired."))
        return True
    if g["sender_id"] == uid:
        bot.send_message(uid, T.gift_claim_invalid("This is your own gift — send the link to your friend!"))
        return True
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(emojis.button("Claim Gift", callback_data=f"giftclaim_{g['code']}",
                         emoji_id="5039823300683891773", lead="🎁"))
    bot.send_message(uid, T.gift_received(_esc(g["product_name"]), "@" + g["sender_name"] if g["sender_name"] else "a friend"),
                     parse_mode="HTML", reply_markup=kb)
    return True


def _gift_expired(g) -> bool:
    """True if the gift has expired. The stored expires_at is a SQLite UTC
    string ('YYYY-MM-DD HH:MM:SS'); parse it and compare as real datetimes.
    The old code string-compared it against a local-time ISO string with a
    'T' separator, which sorted wrong and read gifts as expired hours
    early (V1.5 fix)."""
    if g["status"] == "expired":
        return True
    from datetime import datetime, timezone
    try:
        exp = datetime.strptime(g["expires_at"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return False
    return exp <= datetime.now(timezone.utc)


def _do_claim(bot: telebot.TeleBot, uid: int, uname: str, code: str, msg_id: int, call_id: str):
    from utils import format_credential
    g = db.get_gift_by_code(code)
    if not g or g["status"] != "unclaimed" or g["sender_id"] == uid:
        bot.answer_callback_query(call_id, "This gift can't be claimed.", show_alert=True)
        return
    claimed = db.claim_gift(code, uid)
    if not claimed:
        bot.answer_callback_query(call_id, "This gift can't be claimed.", show_alert=True)
        return
    taken = db.claim_reserved_stock(g["code"])
    if not taken:
        bot.answer_callback_query(call_id, "Something went wrong — contact support.", show_alert=True)
        return
    _stock_id, credential = taken
    p = db.get_product(g["product_id"])
    cred_fmt = p["credential_format"] if p and p["credential_format"] else None
    try:
        order_id = db.create_order(uid, uname, g["product_id"], g["product_name"], 0, credential, payment_method="gift")
    except Exception:
        order_id = None
    try:
        db.ensure_user(uid, uname)
    except Exception:
        pass
    text = (
        f"{emojis.emoji('gift', '🎁')} <b>Gift Claimed!</b>\n\n"
        f"<blockquote>{emojis.emoji('package', '📦')} <b>{_esc(g['product_name'])}</b>\n{format_credential(credential, cred_fmt)}</blockquote>\n\n"
        f"Enjoy!"
    )
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(emojis.button("Main Menu", callback_data="main_menu",
                         emoji_id="5042022053356504092", lead="🏠"))
    edit_or_send(bot, uid, msg_id, text, reply_markup=kb)
    bot.answer_callback_query(call_id, "Enjoy your gift!")
    # tell the sender
    try:
        bot.send_message(g["sender_id"], T.gift_claimed_notice(
            _esc(g["product_name"]), "@" + uname if uname else f"user {uid}"), parse_mode="HTML")
    except Exception:
        pass
