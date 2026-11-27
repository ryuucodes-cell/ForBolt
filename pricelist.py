"""/list - secret admin-only command.

Sends the admin a copy-ready PLAIN-TEXT price list of every active
product (grouped by category, live prices, discounts shown old -> new,
instant products at zero stock marked Sold Out) so it can be pasted
into other groups/channels as marketing. Non-admins get total silence -
the command's existence is never revealed.
"""
import re
from datetime import date

import telebot
from telebot import types

import db
from utils import is_admin, silent_delete

# Category keyword -> emoji for the section headers (word match).
_CATEGORY_EMOJI = [
    ("streaming", "🎬"), ("stream", "🎬"), ("movie", "🎬"),
    ("movies", "🎬"), ("video", "🎬"), ("tv", "🎬"),
    ("music", "🎧"), ("audio", "🎧"), ("song", "🎧"), ("songs", "🎧"),
    ("vpn", "🛡️"),
    ("game", "🎮"), ("games", "🎮"), ("gaming", "🎮"),
    ("ai", "🤖"), ("software", "💻"), ("app", "📱"), ("apps", "📱"),
    ("education", "📚"), ("learning", "📚"), ("course", "📚"),
    ("design", "🎨"), ("social", "📣"), ("boost", "📣"),
]
_DEFAULT_EMOJI = "📦"

_MAX_LEN = 3900  # stay under Telegram's 4096 char message limit

_me_cache = {}


def _cat_emoji(category: str) -> str:
    words = [w for w in re.split(r"[^a-z0-9]+", (category or "").lower()) if w]
    for kw, em in _CATEGORY_EMOJI:
        if kw in words:
            return em
    return _DEFAULT_EMOJI


def _fmt(amount) -> str:
    return f"{int(amount):,}"


def _product_line(p) -> str:
    base = int(p["price_mmk"])
    if db.is_discount_active(p):
        eff = int(db.get_effective_price(p))
        line = f"• {p['name']} — {_fmt(base)} → {_fmt(eff)} Ks"
    else:
        line = f"• {p['name']} — {_fmt(base)} Ks"
    # Sold Out only applies to instant products (pre-order
    # products are fulfilled by the admin and never "run out" this way).
    if db.get_fulfillment(p) == "instant" and db.stock_count(p["product_id"]) <= 0:
        line += " ❌ Sold Out"
    return line


def build_price_list(bot_username: str = "") -> list:
    """Return the price list as a list of message-ready text chunks."""
    shop = db.cfg_get("shop_name", "StockBot")
    groups = {}
    order = []
    for p in db.list_products(active_only=True):
        cat = (p["category"] or "").strip() or "Other"
        if cat not in groups:
            groups[cat] = []
            order.append(cat)
        groups[cat].append(p)

    lines = [f"🔥 {shop} — PRICE LIST 🔥",
             f"📅 {date.today():%d %b %Y}",
             ""]
    for cat in sorted(order, key=lambda c: c.lower()):
        lines.append(f"{_cat_emoji(cat)} {cat.upper()}")
        for p in groups[cat]:
            lines.append(_product_line(p))
        lines.append("")
    if bot_username:
        lines.append(f"🛒 Order here: @{bot_username}")
    lines.append("💬 အော်ဒါတင်ရန် bot ထဲဝင်ပြီး ကြိုက်တာရွေးဝယ်နိုင်ပါပြီ")

    # Pack lines into chunks under the Telegram limit (never split a line).
    chunks, cur = [], ""
    for ln in lines:
        if cur and len(cur) + len(ln) + 1 > _MAX_LEN:
            chunks.append(cur.rstrip("\n"))
            cur = ""
        cur += ln + "\n"
    if cur.strip():
        chunks.append(cur.rstrip("\n"))
    return chunks


def _bot_username(bot) -> str:
    if "u" not in _me_cache:
        try:
            _me_cache["u"] = bot.get_me().username or ""
        except Exception:
            _me_cache["u"] = ""
    return _me_cache["u"]


def register(bot: telebot.TeleBot):

    @bot.message_handler(commands=["list"])
    def cmd_list(msg: types.Message):
        uid = msg.from_user.id
        if not is_admin(uid):
            return  # secret command: total silence for non-admins
        silent_delete(bot, msg.chat.id, msg.message_id)
        chunks = build_price_list(_bot_username(bot))
        if not chunks:
            bot.send_message(uid, "No active products yet.", parse_mode=None)
            return
        # Always DM the admin, even if /list was typed in a group, so the
        # list (and the command) stay out of public view.
        for chunk in chunks:
            bot.send_message(uid, chunk, parse_mode=None,
                             disable_web_page_preview=True)
