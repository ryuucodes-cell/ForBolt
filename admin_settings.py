"""Admin: settings — organized as a hub with focused sub-pages.

Structure:
    ⚙️ Settings (hub)
      ├── 🏪 Shop Info        shop name, welcome, support URL, support @user
      ├── 💱 Pricing & Stock  MMK-per-USDT rate, low-stock threshold
      ├── 💳 Payment Methods  → per-method page (toggle + phone/name or Pay ID)
      ├── 📢 Channel & Gate   join URL, username, membership gate
      ├── 🧾 Logging          private log channel ID
      ├── 👮 Manage Admins    primary + extra admins
      └── 🔧 Maintenance      toggle
"""

import re
import telebot
from telebot import types

import db
import emojis
from utils import admin_only, set_state, get_state, clear_state, back_kb, silent_delete

# Fields where the admin's raw input is captured as Telegram HTML (via
# emojis.capture_html) so any Premium emoji or formatting they type is
# preserved exactly. Stored value is ready-to-send HTML, not plain text.
_RICH_TEXT_KEYS = {"shop_name", "shop_tagline", "shop_subtagline", "welcome_message"}


def _strip_tags(s: str) -> str:
    """Plain-text preview of a rich HTML field, safe to slice/wrap in <code>
    without risking a truncated or nested tag breaking Telegram's parser."""
    return re.sub(r"<[^>]+>", "", s or "")


# ── which sub-page each editable config key belongs to (for post-edit return) ──
_KEY_PARENT = {
    "shop_name":           "adm_set_shop",
    "shop_tagline":        "adm_set_shop",
    "shop_subtagline":     "adm_set_shop",
    "welcome_message":     "adm_set_shop",
    "support_url":         "adm_set_shop",
    "admin_username":      "adm_set_shop",
    "fulfillment_eta":     "adm_set_shop",
    "low_stock_threshold": "adm_set_pricing",
    "usd_to_mmk_rate":     "adm_set_pricing",
    "kbzpay_phone":        "adm_pm_kbzpay",
    "kbzpay_name":         "adm_pm_kbzpay",
    "wavepay_phone":       "adm_pm_wavepay",
    "wavepay_name":        "adm_pm_wavepay",
    "cbpay_phone":         "adm_pm_cbpay",
    "cbpay_name":          "adm_pm_cbpay",
    "uabpay_phone":        "adm_pm_uabpay",
    "uabpay_name":         "adm_pm_uabpay",
    "ayapay_phone":        "adm_pm_ayapay",
    "ayapay_name":         "adm_pm_ayapay",
    "binance_pay_id":      "adm_pm_binance",
    "channel_url":         "adm_set_channel",
    "channel_username":    "adm_set_channel",
    "channel_url2":        "adm_set_channel",
    "channel_username2":   "adm_set_channel",
    "log_channel_id":      "adm_set_logging",
    "admin_chat_id":       "adm_admins",
    "giveaway_invite_entries": "adm_set_growth",
    "gift_expiry_days":    "adm_set_growth",
    "am_monthly_price":    "adm_set_am",
    "am_daily_cap":        "adm_set_am",
}

_EDITABLE_KEYS = set(_KEY_PARENT.keys())

_KEY_LABELS = {
    "shop_name":           "Shop Name",
    "shop_tagline":        "Header Tagline",
    "shop_subtagline":     "Header Sub-tagline",
    "welcome_message":     "Welcome Message",
    "support_url":         "Support URL",
    "admin_username":      "Support Username",
    "fulfillment_eta":     "Fulfillment ETA",
    "admin_chat_id":       "Primary Admin Chat ID",
    "low_stock_threshold": "Low Stock Threshold",
    "usd_to_mmk_rate":     "MMK per 1 USDT",
    "kbzpay_phone":        "KBZPay Phone Number",
    "kbzpay_name":         "KBZPay Account Name",
    "wavepay_phone":       "WavePay Phone Number",
    "wavepay_name":        "WavePay Account Name",
    "cbpay_phone":         "CB Pay Phone Number",
    "cbpay_name":          "CB Pay Account Name",
    "uabpay_phone":        "UAB Pay Phone Number",
    "uabpay_name":         "UAB Pay Account Name",
    "ayapay_phone":        "AYA Pay Phone Number",
    "ayapay_name":         "AYA Pay Account Name",
    "binance_pay_id":      "Binance Pay ID",
    "channel_url":         "Join Channel URL",
    "channel_username":    "Join Channel Username",
    "channel_url2":        "Join Channel 2 URL",
    "channel_username2":   "Join Channel 2 Username",
    "log_channel_id":      "Private Log Channel ID",
    "giveaway_invite_entries": "Entries per Giveaway Invite",
    "gift_expiry_days":    "Gift Expiry (days)",
    "am_monthly_price":    "AM Monthly Price (MMK)",
    "am_daily_cap":        "AM Daily Upgrades per User",
}

_KEY_HINTS = {
    "shop_name": (
        "The name shown at the top of every menu.\n"
        "Example: <code>MyShop</code>\n"
        "You can include Telegram Premium (animated) emoji directly in this "
        "message — they'll be preserved exactly as you type them."
    ),
    "shop_tagline": (
        "First line under the shop name on the main menu. Use emoji freely — "
        "including Telegram Premium emoji, which are preserved exactly as typed.\n"
        "Example: <code>Premium Digital Products with Instant Delivery</code>\n"
        "Send <code>-</code> to leave it blank."
    ),
    "shop_subtagline": (
        "Second line under the tagline. Good for a short feature list. Premium "
        "emoji you type here are preserved exactly.\n"
        "Example: <code>Fast · Secure · Automated</code>\n"
        "Send <code>-</code> to leave it blank."
    ),
    "welcome_message": (
        "Shown on the main menu under the header. Premium emoji you type here "
        "are preserved exactly.\n"
        "Example: <code>Browse our latest products below!</code>"
    ),
    "support_url": (
        "A Telegram link or URL for your support contact. Shown when users tap Support.\n"
        "Example: <code>https://t.me/yoursupport</code>"
    ),
    "admin_username": (
        "Fallback support contact shown when no Support URL is set. Enter the username WITHOUT @.\n"
        "Example: if your support is @myshophelp, enter <code>myshophelp</code>"
    ),
    "fulfillment_eta": (
        "Delivery-time promise shown on pre-order product pages.\n"
        "Example: <code>usually within 24 hours</code>"
    ),
    "admin_chat_id": (
        "Your personal Telegram numeric ID. The bot sends admin alerts and auto-backups here.\n"
        "To find it: message @userinfobot — it replies with your ID.\n"
        "Example: <code>123456789</code>"
    ),
    "low_stock_threshold": (
        "When a product's remaining stock drops to this number or below, you get an alert.\n"
        "Example: <code>3</code> means alert when 3 or fewer items left."
    ),
    "kbzpay_phone": (
        "The KBZPay phone number users send money to.\n"
        "Example: <code>09123456789</code>"
    ),
    "kbzpay_name": (
        "The account holder name shown to users so they can verify before sending.\n"
        "Example: <code>Ko Aung</code>"
    ),
    "wavepay_phone": (
        "The WavePay phone number users send money to.\n"
        "Example: <code>09123456789</code>"
    ),
    "wavepay_name": (
        "The WavePay account holder name shown to users.\n"
        "Example: <code>Ko Aung</code>"
    ),
    "cbpay_phone": (
        "The CB Pay phone number users send money to.\n"
        "Example: <code>09123456789</code>"
    ),
    "cbpay_name": (
        "The CB Pay account holder name shown to users.\n"
        "Example: <code>Ko Aung</code>"
    ),
    "binance_pay_id": (
        "Your Binance Pay ID — users send USDT to this ID.\n"
        "Find it in Binance app → Profile → Pay → My Pay ID.\n"
        "Example: <code>123456789</code>"
    ),
    "channel_url": (
        "The public invite link to your channel. Shown to users who haven't joined yet.\n"
        "Example: <code>https://t.me/yourchannel</code>"
    ),
    "channel_username": (
        "Your channel's username WITHOUT the @ symbol. Used to verify membership.\n"
        "Example: if your channel is @myshopnews, enter <code>myshopnews</code>"
    ),
    "log_channel_id": (
        "The numeric ID of your PRIVATE admin-only channel. Purchase and deposit logs go here.\n"
        "Add the bot as an admin in that channel first, then get the ID by forwarding any "
        "message from it to @userinfobot.\n"
        "Example: <code>-1001234567890</code> (channel IDs start with -100)"
    ),
}

# payment method meta: key -> (label, [(field_suffix, human_label), ...])
_PM_META = {
    "kbzpay":  ("KBZPay",      [("phone", "phone number"), ("name", "account name")]),
    "wavepay": ("WavePay",     [("phone", "phone number"), ("name", "account name")]),
    "cbpay":   ("CB Pay",      [("phone", "phone number"), ("name", "account name")]),
    "uabpay":  ("UAB Pay",     [("phone", "phone number"), ("name", "account name")]),
    "ayapay":  ("AYA Pay",     [("phone", "phone number"), ("name", "account name")]),
    "binance": ("Binance Pay", [("pay_id", "Pay ID")]),
}


def register(bot: telebot.TeleBot):

    # ── shared nav helper ─────────────────────────────────────────────────────

    def _nav(kb, back_cb):
        kb.add(
            emojis.button("⬅️ Back", callback_data=back_cb, emoji_id="5255703720078879038", lead="⬅️"),
            emojis.button("🏠 Main Menu", callback_data="main_menu", emoji_id="5042022053356504092", lead="🏠"),
        )

    def _edit(uid, msg_id, text, kb):
        try:
            bot.edit_message_text(text, uid, msg_id, reply_markup=kb)
        except Exception:
            bot.send_message(uid, text, reply_markup=kb)

    # ── hub ────────────────────────────────────────────────────────────────────

    def _render_hub(uid, msg_id):
        cfg = db.cfg_all()
        mm  = "ON" if cfg.get("maintenance_mode") == "1" else "OFF"
        pay = " ".join(
            f"{label.split()[0]}: {'ON' if cfg.get(f'{k}_enabled') == '1' else 'OFF'}"
            for k, (label, _) in _PM_META.items()
        )
        text = (
            "<b>Settings</b>\n\n"
            "Choose a section to configure.\n\n"
            f"Shop: <b>{cfg.get('shop_name', 'N/A')}</b>\n"
            f"Rate: <b>{cfg.get('usd_to_mmk_rate', '4800')} MMK/USDT</b>\n"
            f"Payments: {pay}\n"
            f"Maintenance: {mm}"
        )
        kb = types.InlineKeyboardMarkup(row_width=2)
        kb.add(
            emojis.button("🏪 Shop Info",       callback_data="adm_set_shop", emoji_id="5278702045883292456", lead="🏪"),
            emojis.button("💱 Pricing & Stock", callback_data="adm_set_pricing", emoji_id="5040025580758631490", lead="💱"),
        )
        kb.add(emojis.button("💳 Payment Methods", callback_data="adm_set_payments", emoji_id="5229064374403998351", lead="💳"))
        kb.add(
            emojis.button("📢 Channel & Gate", callback_data="adm_set_channel", emoji_id="5461151367559141950", lead="📢"),
            emojis.button("🧾 Logging",        callback_data="adm_set_logging", emoji_id="5039579582764680065", lead="🧾"),
        )
        kb.add(emojis.button("🚀 Growth", callback_data="adm_set_growth", emoji_id="5312361253610475399", lead="🚀"))
        kb.add(emojis.button("Activate AlightMotion", callback_data="adm_set_am", emoji_id="5041866884778034602", lead="🎬"))
        kb.add(emojis.button("👮 Manage Admins", callback_data="adm_admins", emoji_id="5042295196096662988", lead="👮"))
        if mm == "ON":
            mm_label = "Maintenance: ON"
            mm_eid   = "5039844895779455925"
        else:
            mm_label = "Maintenance: OFF"
            mm_eid   = "5040042498634810056"
        kb.add(emojis.button(mm_label, callback_data="adm_toggle_maintenance", emoji_id=mm_eid))
        _nav(kb, "admin_panel")
        _edit(uid, msg_id, text, kb)

    @bot.callback_query_handler(func=lambda c: c.data == "adm_settings")
    @admin_only
    def cb_adm_settings(call: types.CallbackQuery):
        _render_hub(call.from_user.id, call.message.message_id)
        bot.answer_callback_query(call.id)

    # ── shop info ──────────────────────────────────────────────────────────────

    def _render_shop(uid, msg_id):
        cfg = db.cfg_all()
        # These 4 fields may contain Telegram HTML (premium emoji tags) — show
        # a plain-text preview here (tags stripped) so slicing never breaks
        # HTML parsing. The full rich version still renders correctly on the
        # actual main menu in handlers.py.
        name_prev = _strip_tags(cfg.get('shop_name') or '') or '—'
        tag_prev  = _strip_tags(cfg.get('shop_tagline') or '')[:60] or '—'
        sub_prev  = _strip_tags(cfg.get('shop_subtagline') or '')[:60] or '—'
        wel_prev  = _strip_tags(cfg.get('welcome_message') or '')[:80] or '—'
        text = (
            "<b>Shop Info</b>\n\n"
            f"Name: <b>{name_prev}</b>\n"
            f"Tagline: <i>{tag_prev}</i>\n"
            f"Sub-tagline: <i>{sub_prev}</i>\n"
            f"Welcome: <i>{wel_prev}</i>\n"
            f"Support URL: <code>{cfg.get('support_url') or '—'}</code>\n"
            f"Support @user: <code>{cfg.get('admin_username') or '—'}</code>\n"
            f"Fulfillment ETA: <code>{cfg.get('fulfillment_eta') or '—'}</code>\n\n"
            "<blockquote>Previews above show plain text only. Premium emoji you've "
            "added still render fully for real users on the actual menu.</blockquote>"
        )
        kb = types.InlineKeyboardMarkup(row_width=2)
        kb.add(
            emojis.button("✏️ Shop Name",     callback_data="adm_set_shop_name"),
            emojis.button("✏️ Welcome Msg",   callback_data="adm_set_welcome_message"),
        )
        kb.add(
            emojis.button("✏️ Tagline",       callback_data="adm_set_shop_tagline"),
            emojis.button("✏️ Sub-tagline",   callback_data="adm_set_shop_subtagline"),
        )
        kb.add(
            emojis.button("✏️ Support URL",   callback_data="adm_set_support_url"),
            emojis.button("✏️ Support @user", callback_data="adm_set_admin_username"),
        )
        kb.add(
            emojis.button("⏱️ Fulfillment ETA", callback_data="adm_set_fulfillment_eta",
                          emoji_id="5386367538735104399", lead="⏱️"),
        )
        _nav(kb, "adm_settings")
        _edit(uid, msg_id, text, kb)

    @bot.callback_query_handler(func=lambda c: c.data == "adm_set_shop")
    @admin_only
    def cb_adm_set_shop(call: types.CallbackQuery):
        _render_shop(call.from_user.id, call.message.message_id)
        bot.answer_callback_query(call.id)

    # ── pricing & stock ──────────────────────────────────────────────────────

    def _render_pricing(uid, msg_id):
        cfg = db.cfg_all()
        text = (
            "<b>Pricing & Stock</b>\n\n"
            f"USDT→MMK rate: <b>{cfg.get('usd_to_mmk_rate', '4800')} MMK per 1 USDT</b>\n"
            f"Low-stock alert at: <b>{cfg.get('low_stock_threshold', '3')}</b> or fewer"
        )
        kb = types.InlineKeyboardMarkup(row_width=1)
        kb.add(emojis.button("💱 USDT→MMK Rate",       callback_data="adm_set_rate_menu", emoji_id="5039789890133296083", lead="💱"))
        kb.add(emojis.button("📦 Low Stock Threshold", callback_data="adm_set_low_stock_threshold", emoji_id="5042042652019655612", lead="📦"))
        _nav(kb, "adm_settings")
        _edit(uid, msg_id, text, kb)

    @bot.callback_query_handler(func=lambda c: c.data == "adm_set_pricing")
    @admin_only
    def cb_adm_set_pricing(call: types.CallbackQuery):
        _render_pricing(call.from_user.id, call.message.message_id)
        bot.answer_callback_query(call.id)

    # ── payment methods list ───────────────────────────────────────────────────

    def _render_payments(uid, msg_id):
        cfg = db.cfg_all()
        text = "<b>Payment Methods</b>\n\nTap a method to configure it."
        kb = types.InlineKeyboardMarkup(row_width=1)
        for key, (label, _) in _PM_META.items():
            on = cfg.get(f"{key}_enabled") == "1"
            kb.add(emojis.button(
                label, callback_data=f"adm_pm_{key}",
                emoji_id=emojis.MESSAGE_EMOJI_IDS["success"] if on else emojis.MESSAGE_EMOJI_IDS["rejected"],
                lead="✅" if on else "❌",
            ))
        _nav(kb, "adm_settings")
        _edit(uid, msg_id, text, kb)

    @bot.callback_query_handler(func=lambda c: c.data == "adm_set_payments")
    @admin_only
    def cb_adm_set_payments(call: types.CallbackQuery):
        _render_payments(call.from_user.id, call.message.message_id)
        bot.answer_callback_query(call.id)

    # ── single payment method page ─────────────────────────────────────────────

    def _render_method(uid, msg_id, method):
        if method not in _PM_META:
            _render_payments(uid, msg_id)
            return
        label, fields = _PM_META[method]
        cfg     = db.cfg_all()
        enabled = cfg.get(f"{method}_enabled") == "1"
        status  = "Enabled" if enabled else "Disabled"

        lines = [f"{label}", "", f"Status: <b>{status}</b>"]
        for suffix, human in fields:
            val = cfg.get(f"{method}_{suffix}") or "—"
            lines.append(f"{human.capitalize()}: <code>{val}</code>")
        text = "\n".join(lines)

        kb = types.InlineKeyboardMarkup(row_width=2)
        toggle_label = "Turn OFF" if enabled else "Turn ON"
        kb.add(emojis.button(toggle_label, callback_data=f"adm_pmtoggle_{method}"))
        edit_btns = [
            emojis.button(f"✏️ {human.capitalize()}", callback_data=f"adm_set_{method}_{suffix}")
            for suffix, human in fields
        ]
        kb.add(*edit_btns)
        if method == "binance":
            kb.add(emojis.button("🔌 Test Connection", callback_data="adm_binance_test", emoji_id="5039675231686361986", lead="🔌"))
        _nav(kb, "adm_set_payments")
        _edit(uid, msg_id, text, kb)

    @bot.callback_query_handler(func=lambda c: c.data.startswith("adm_pm_"))
    @admin_only
    def cb_adm_pm(call: types.CallbackQuery):
        method = call.data[len("adm_pm_"):]
        _render_method(call.from_user.id, call.message.message_id, method)
        bot.answer_callback_query(call.id)

    # fields required before a payment method can be enabled
    _PM_REQUIRED = {
        "kbzpay":   [("kbzpay_phone",  "KBZPay phone number"),  ("kbzpay_name",   "KBZPay account name")],
        "wavepay":  [("wavepay_phone", "WavePay phone number"), ("wavepay_name",  "WavePay account name")],
        "cbpay":    [("cbpay_phone",   "CB Pay phone number"),  ("cbpay_name",    "CB Pay account name")],
        "uabpay":   [("uabpay_phone",  "UAB Pay phone number"), ("uabpay_name",   "UAB Pay account name")],
        "ayapay":   [("ayapay_phone",  "AYA Pay phone number"), ("ayapay_name",   "AYA Pay account name")],
        "binance":  [("binance_pay_id", "Binance Pay ID")],
    }

    @bot.callback_query_handler(func=lambda c: c.data.startswith("adm_pmtoggle_"))
    @admin_only
    def cb_adm_pmtoggle(call: types.CallbackQuery):
        method  = call.data[len("adm_pmtoggle_"):]
        key     = method + "_enabled"
        current = db.cfg_get(key, "0")
        turning_on = current != "1"
        if turning_on and method in _PM_REQUIRED:
            missing = [label for field, label in _PM_REQUIRED[method] if not db.cfg_get(field, "").strip()]
            if missing:
                bot.answer_callback_query(call.id, f"Set these first: {', '.join(missing)}", show_alert=True)
                return
        db.cfg_set(key, "0" if current == "1" else "1")
        bot.answer_callback_query(call.id, "Enabled." if turning_on else "Disabled.")
        _render_method(call.from_user.id, call.message.message_id, method)

    # ── channel & gate ─────────────────────────────────────────────────────────

    def _render_channel(uid, msg_id):
        cfg  = db.cfg_all()
        gate = cfg.get("channel_gate_enabled") == "1"
        text = (
            "<b>Channel & Gate</b>\n\n"
            f"Channel 1 URL: <code>{cfg.get('channel_url') or '—'}</code>\n"
            f"Channel 1 username: <code>{cfg.get('channel_username') or '—'}</code>\n"
            f"Channel 2 URL: <code>{cfg.get('channel_url2') or '—'}</code>\n"
            f"Channel 2 username: <code>{cfg.get('channel_username2') or '—'}</code>\n"
            f"Membership gate: <b>{'ON' if gate else 'OFF'}</b>\n\n"
            "<blockquote>When the gate is ON, users must join ALL configured channels before using the shop.</blockquote>"
        )
        kb = types.InlineKeyboardMarkup(row_width=2)
        kb.add(
            emojis.button("✏️ Channel URL",      callback_data="adm_set_channel_url"),
            emojis.button("✏️ Channel Username", callback_data="adm_set_channel_username"),
        )
        kb.add(
            emojis.button("✏️ Channel 2 URL",      callback_data="adm_set_channel_url2"),
            emojis.button("✏️ Channel 2 Username", callback_data="adm_set_channel_username2"),
        )
        kb.add(emojis.button("🔒 Gate: ON (tap to disable)" if gate else "Gate: OFF (tap to enable)",
            callback_data="adm_toggle_channel_gate",
        ))
        _nav(kb, "adm_settings")
        _edit(uid, msg_id, text, kb)

    @bot.callback_query_handler(func=lambda c: c.data == "adm_set_channel")
    @admin_only
    def cb_adm_set_channel(call: types.CallbackQuery):
        _render_channel(call.from_user.id, call.message.message_id)
        bot.answer_callback_query(call.id)

    @bot.callback_query_handler(func=lambda c: c.data == "adm_toggle_channel_gate")
    @admin_only
    def cb_adm_toggle_channel_gate(call: types.CallbackQuery):
        current = db.cfg_get("channel_gate_enabled", "0")
        db.cfg_set("channel_gate_enabled", "0" if current == "1" else "1")
        bot.answer_callback_query(call.id, "Gate enabled." if current != "1" else "Gate disabled.")
        _render_channel(call.from_user.id, call.message.message_id)

    # ── logging ──────────────────────────────────────────────────────────────

    def _render_logging(uid, msg_id):
        cfg = db.cfg_all()
        text = (
            "<b>Logging</b>\n\n"
            f"Private log channel: <code>{cfg.get('log_channel_id') or '—'}</code>\n\n"
            "<blockquote>Sales, deposits and balance changes are posted here (masked names). "
            "Add the bot as an admin of the channel first.</blockquote>"
        )
        kb = types.InlineKeyboardMarkup(row_width=1)
        kb.add(emojis.button("✏️ Log Channel ID", callback_data="adm_set_log_channel_id"))
        _nav(kb, "adm_settings")
        _edit(uid, msg_id, text, kb)

    @bot.callback_query_handler(func=lambda c: c.data == "adm_set_logging")
    @admin_only
    def cb_adm_set_logging(call: types.CallbackQuery):
        _render_logging(call.from_user.id, call.message.message_id)
        bot.answer_callback_query(call.id)

    # ── maintenance toggle (from hub) ──────────────────────────────────────────

    @bot.callback_query_handler(func=lambda c: c.data == "adm_toggle_maintenance")
    @admin_only
    def cb_adm_toggle_maintenance(call: types.CallbackQuery):
        current = db.cfg_get("maintenance_mode", "0")
        db.cfg_set("maintenance_mode", "0" if current == "1" else "1")
        bot.answer_callback_query(call.id, "Maintenance ON." if current != "1" else "Maintenance OFF.")
        _render_hub(call.from_user.id, call.message.message_id)

    # ── growth ────────────────────────────────────────────────────────────────

    # (config key, label, emoji registry key, unicode fallback) — labels go on
    # buttons, so the premium icon is passed via emoji_id (never an HTML tag).
    _GROWTH_TOGGLES = [
        ("giveaway_invite_enabled", "Giveaway Invites", "dart", "🎯"),
        ("deposit_bonus_enabled",   "Deposit Bonus",   "money", "💰"),
        ("vip_enabled",             "VIP Tiers",       "crown", "👑"),
    ]

    def _render_growth(uid, msg_id):
        import json as _json
        lines = []
        try:
            tiers = _json.loads(db.cfg_get("deposit_bonus_tiers", "[]") or "[]")
            lines.append("Deposit bonus: " + ", ".join(f"{p}% on {int(m)//1000}k+" for m, p in tiers))
        except Exception:
            pass
        try:
            vips = _json.loads(db.cfg_get("vip_tiers", "[]") or "[]")
            lines.append("VIP: " + ", ".join(
                f"{t.get('name')} {int(t.get('min', 0))//1000}k ({t.get('discount', 0)}% off)"
                for t in vips))
        except Exception:
            pass
        cfg = db.cfg_all()
        text = "<b>🚀 Growth</b>\n\n"
        if lines:
            text += "<blockquote>" + "\n".join(lines) + "</blockquote>\n\n"
        text += (
            f"Giveaway invites: <b>{'ON' if cfg.get('giveaway_invite_enabled') == '1' else 'OFF'}</b> "
            f"({cfg.get('giveaway_invite_entries', '1')} entries per invite)\n"
            f"Deposit bonus: <b>{'ON' if cfg.get('deposit_bonus_enabled') == '1' else 'OFF'}</b>\n"
            f"VIP tiers: <b>{'ON' if cfg.get('vip_enabled') == '1' else 'OFF'}</b>\n"
            f"Gift expiry: <b>{cfg.get('gift_expiry_days', '30')} days</b>"
        )
        kb = types.InlineKeyboardMarkup(row_width=2)
        for key, label, ekey, lead in _GROWTH_TOGGLES:
            on = cfg.get(key) == "1"
            kb.add(emojis.button(
                f"{label}: {'ON' if on else 'OFF'}",
                callback_data=f"adm_growth_toggle_{key}",
                emoji_id=emojis.MESSAGE_EMOJI_IDS.get(ekey),
                lead=lead,
            ))
        kb.add(
            emojis.button("Entries / Invite", callback_data="adm_set_giveaway_invite_entries",
                          emoji_id="5226702984204797593", lead="🔢"),
            emojis.button("Gift Expiry", callback_data="adm_set_gift_expiry_days",
                          emoji_id="5226702984204797593", lead="⏳"),
        )
        _nav(kb, "adm_settings")
        _edit(uid, msg_id, text, kb)

    @bot.callback_query_handler(func=lambda c: c.data == "adm_set_growth")
    @admin_only
    def cb_adm_set_growth(call: types.CallbackQuery):
        _render_growth(call.from_user.id, call.message.message_id)
        bot.answer_callback_query(call.id)

    @bot.callback_query_handler(func=lambda c: c.data.startswith("adm_growth_toggle_"))
    @admin_only
    def cb_adm_growth_toggle(call: types.CallbackQuery):
        key = call.data[len("adm_growth_toggle_"):]
        if key not in [k for k, *_ in _GROWTH_TOGGLES]:
            bot.answer_callback_query(call.id, "Unknown setting.")
            return
        current = db.cfg_get(key, "1")
        db.cfg_set(key, "0" if current == "1" else "1")
        bot.answer_callback_query(call.id, "Updated.")
        _render_growth(call.from_user.id, call.message.message_id)

    # ── Alight Motion Pro subscription settings ───────────────────────────────

    def _render_am(uid, msg_id):
        cfg = db.cfg_all()
        on = cfg.get("am_enabled") == "1"
        price = cfg.get("am_monthly_price", "0")
        cap = cfg.get("am_daily_cap", "5")
        try:
            n_subs = len(db.am_list_active(limit=10000))
        except Exception:
            n_subs = "?"
        text = (
            f"{emojis.emoji('am_menu')} <b>Activate AlightMotion</b>\n\n"
            f"Status: <b>{'ON' if on else 'OFF'}</b>\n"
            f"Monthly price: <b>{price} Ks</b>\n"
            f"Daily upgrades per user: <b>{cap}</b>\n"
            f"Active subscriptions: <b>{n_subs}</b>"
        )
        kb = types.InlineKeyboardMarkup(row_width=2)
        kb.add(emojis.button(
            f"Turn {'OFF' if on else 'ON'}", callback_data="adm_am_toggle",
            emoji_id=emojis.MESSAGE_EMOJI_IDS["success"] if not on else emojis.MESSAGE_EMOJI_IDS["rejected"],
            lead="✅" if not on else "❌",
        ))
        kb.add(
            emojis.button("Monthly Price", callback_data="adm_set_am_monthly_price",
                          emoji_id="5226702984204797593", lead="💰"),
            emojis.button("Daily Cap", callback_data="adm_set_am_daily_cap",
                          emoji_id="5226702984204797593", lead="🔢"),
        )
        _nav(kb, "adm_settings")
        _edit(uid, msg_id, text, kb)

    @bot.callback_query_handler(func=lambda c: c.data == "adm_set_am")
    @admin_only
    def cb_adm_set_am(call: types.CallbackQuery):
        _render_am(call.from_user.id, call.message.message_id)
        bot.answer_callback_query(call.id)

    @bot.callback_query_handler(func=lambda c: c.data == "adm_am_toggle")
    @admin_only
    def cb_adm_am_toggle(call: types.CallbackQuery):
        current = db.cfg_get("am_enabled", "0")
        db.cfg_set("am_enabled", "0" if current == "1" else "1")
        bot.answer_callback_query(call.id, "Updated.")
        _render_am(call.from_user.id, call.message.message_id)

    # ── USD/MMK rate quick-select ─────────────────────────────────────────────

    _RATES = [4500, 4700, 4800, 4900, 5000, 5100, 5200, 5300, 5500]

    @bot.callback_query_handler(func=lambda c: c.data == "adm_set_rate_menu")
    @admin_only
    def cb_adm_rate_menu(call: types.CallbackQuery):
        current = db.cfg_get("usd_to_mmk_rate", "4800")
        kb      = types.InlineKeyboardMarkup(row_width=3)
        btns    = [emojis.button(f"{'✅ ' if str(r) == current else ''}{r}",
                                              callback_data=f"adm_setrate_{r}") for r in _RATES]
        kb.add(*btns)
        kb.add(emojis.button("✏️ Enter Custom Rate", callback_data="adm_setrate_custom"))
        _nav(kb, "adm_set_pricing")
        _edit(call.from_user.id, call.message.message_id,
              f"Current rate: <b>{current} MMK per 1 USDT</b>\n\nSelect a new rate or enter a custom value:", kb)
        bot.answer_callback_query(call.id)

    @bot.callback_query_handler(func=lambda c: c.data == "adm_setrate_custom")
    @admin_only
    def cb_adm_setrate_custom(call: types.CallbackQuery):
        prompt = bot.edit_message_text(
            "<b>Custom Rate (MMK per 1 USDT)</b>\n\nEnter the rate as a plain number:\nExample: <code>4750</code>",
            call.from_user.id, call.message.message_id,
            reply_markup=back_kb("adm_set_rate_menu"),
        )
        set_state(call.from_user.id, step="edit_cfg_field", cfg_key="usd_to_mmk_rate", prompt_msg_id=prompt.message_id)
        bot.answer_callback_query(call.id)

    @bot.callback_query_handler(func=lambda c: c.data.startswith("adm_setrate_"))
    @admin_only
    def cb_adm_setrate(call: types.CallbackQuery):
        rate = call.data[len("adm_setrate_"):]
        db.cfg_set("usd_to_mmk_rate", rate)
        bot.answer_callback_query(call.id, f"Rate set to {rate}.")
        _render_pricing(call.from_user.id, call.message.message_id)

    # ── generic text-field editor ─────────────────────────────────────────────

    @bot.callback_query_handler(func=lambda c: c.data.startswith("adm_set_") and c.data[len("adm_set_"):] in _EDITABLE_KEYS)
    @admin_only
    def cb_adm_set_field(call: types.CallbackQuery):
        key     = call.data[len("adm_set_"):]
        current = db.cfg_get(key, "")
        hint    = _KEY_HINTS.get(key, "")
        hint_block = f"\n\n<blockquote>{hint}</blockquote>" if hint else ""
        parent  = _KEY_PARENT.get(key, "adm_settings")
        if key in _RICH_TEXT_KEYS:
            # plain-text preview only (tags stripped) — avoids a broken <code>
            # wrapper if the stored value contains premium-emoji HTML tags
            current_line = f"Current: <code>{_strip_tags(current) or '—'}</code> <i>(emoji shown as plain text here only)</i>"
        else:
            current_line = f"Current: <code>{current or '—'}</code>"
        prompt  = bot.edit_message_text(
            f"<b>{_KEY_LABELS.get(key, key)}</b>\n\n"
            f"{current_line}"
            f"{hint_block}\n\n"
            f"Enter the new value:",
            call.from_user.id, call.message.message_id,
            reply_markup=back_kb(parent),
        )
        set_state(call.from_user.id, step="edit_cfg_field", cfg_key=key, prompt_msg_id=prompt.message_id)
        bot.answer_callback_query(call.id)

    @bot.message_handler(func=lambda m: get_state(m.from_user.id).get("step") == "edit_cfg_field")
    @admin_only
    def msg_edit_cfg_field(msg: types.Message):
        uid       = msg.from_user.id
        st        = get_state(uid)
        key       = st.get("cfg_key")
        prompt_id = st.get("prompt_msg_id")
        plain     = (msg.text or "").strip()

        # light validation for numeric fields
        if key in ("usd_to_mmk_rate", "low_stock_threshold"):
            clean = plain.replace(",", "")
            if not clean.isdigit() or int(clean) <= 0:
                bot.send_message(uid, "Please enter a valid positive number.")
                return
            value = clean

        # optional text fields: "-" clears them (checked on plain text, before
        # any HTML capture, so a bare "-" always works regardless of key type)
        elif key in ("shop_tagline", "shop_subtagline") and plain == "-":
            value = ""

        elif key in _RICH_TEXT_KEYS:
            # Capture as Telegram HTML so any Premium (custom) emoji or
            # formatting the admin typed is preserved exactly — not just the
            # emoji's plain fallback character.
            value = emojis.capture_html(msg).strip()

        else:
            value = plain

        db.cfg_set(key, value)
        clear_state(uid)
        silent_delete(bot, uid, msg.message_id)
        _render_page(uid, prompt_id, _KEY_PARENT.get(key, "adm_settings"))

    # ── manage admins ─────────────────────────────────────────────────────────

    def _parse_admin_ids(raw: str) -> list:
        ids = []
        for part in (raw or "").replace(",", " ").split():
            p = part.strip()
            if p.lstrip("-").isdigit() and int(p) not in ids:
                ids.append(int(p))
        return ids

    def _admins_text_kb():
        from utils import admin_id
        primary = admin_id()
        extras  = _parse_admin_ids(db.cfg_get("admin_ids", ""))
        lines = [
            "<b>Manage Admins</b>\n",
            "Admins can open the admin panel and approve deposits.\n",
            f"Primary (Admin Chat ID): <code>{primary or '—'}</code>",
        ]
        if extras:
            lines.append("\n<b>Extra admins:</b>")
            for a in extras:
                lines.append(f"• <code>{a}</code>")
        else:
            lines.append("\nNo extra admins yet.")
        lines.append("\n<blockquote>The primary admin receives alerts and backups. Extra admins get full admin access.</blockquote>")

        kb = types.InlineKeyboardMarkup(row_width=1)
        kb.add(emojis.button("⭐ Set Primary Admin ID", callback_data="adm_set_admin_chat_id"))
        kb.add(emojis.button("➕ Add Admin", callback_data="adm_admin_add", emoji_id="5039891861246838069", lead="➕"))
        for a in extras:
            kb.add(emojis.button(f"➖ Remove {a}", callback_data=f"adm_admin_del_{a}", emoji_id="5231265631042548053", lead="➖"))
        _nav(kb, "adm_settings")
        return "\n".join(lines), kb

    @bot.callback_query_handler(func=lambda c: c.data == "adm_admins")
    @admin_only
    def cb_adm_admins(call: types.CallbackQuery):
        text, kb = _admins_text_kb()
        _edit(call.from_user.id, call.message.message_id, text, kb)
        bot.answer_callback_query(call.id)

    @bot.callback_query_handler(func=lambda c: c.data == "adm_admin_add")
    @admin_only
    def cb_adm_admin_add(call: types.CallbackQuery):
        prompt = bot.edit_message_text(
            "<b>Add Admin</b>\n\n"
            "<blockquote>Send the new admin's numeric Telegram ID.\n"
            "They can find it by messaging @userinfobot.\n"
            "Example: <code>123456789</code></blockquote>",
            call.from_user.id, call.message.message_id,
            reply_markup=back_kb("adm_admins"),
        )
        set_state(call.from_user.id, step="add_admin_id", prompt_msg_id=prompt.message_id)
        bot.answer_callback_query(call.id)

    @bot.message_handler(func=lambda m: get_state(m.from_user.id).get("step") == "add_admin_id")
    @admin_only
    def msg_add_admin_id(msg: types.Message):
        from utils import admin_id
        uid       = msg.from_user.id
        st        = get_state(uid)
        prompt_id = st.get("prompt_msg_id")
        raw       = msg.text.strip().replace(",", "")
        clear_state(uid)
        silent_delete(bot, uid, msg.message_id)

        if not raw.lstrip("-").isdigit():
            text, kb = _admins_text_kb()
            _edit(uid, prompt_id, "That's not a valid numeric ID.\n\n" + text, kb)
            return

        new_id = int(raw)
        extras = _parse_admin_ids(db.cfg_get("admin_ids", ""))
        if new_id == admin_id() or new_id in extras:
            note = "That user is already an admin."
        else:
            extras.append(new_id)
            db.cfg_set("admin_ids", ",".join(str(x) for x in extras))
            note = f"Added <code>{new_id}</code> as an admin."
        text, kb = _admins_text_kb()
        _edit(uid, prompt_id, note + "\n\n" + text, kb)

    @bot.callback_query_handler(func=lambda c: c.data.startswith("adm_admin_del_"))
    @admin_only
    def cb_adm_admin_del(call: types.CallbackQuery):
        target = call.data[len("adm_admin_del_"):]
        extras = _parse_admin_ids(db.cfg_get("admin_ids", ""))
        extras = [a for a in extras if str(a) != target]
        db.cfg_set("admin_ids", ",".join(str(x) for x in extras))
        text, kb = _admins_text_kb()
        _edit(call.from_user.id, call.message.message_id, f"Removed <code>{target}</code>.\n\n" + text, kb)
        bot.answer_callback_query(call.id, "Removed.")

    # ── page dispatcher (used after a field edit to return to the right page) ──

    def _render_page(uid, msg_id, cb):
        if cb == "adm_set_shop":
            _render_shop(uid, msg_id)
        elif cb == "adm_set_pricing":
            _render_pricing(uid, msg_id)
        elif cb == "adm_set_payments":
            _render_payments(uid, msg_id)
        elif cb == "adm_set_channel":
            _render_channel(uid, msg_id)
        elif cb == "adm_set_logging":
            _render_logging(uid, msg_id)
        elif cb == "adm_admins":
            text, kb = _admins_text_kb()
            _edit(uid, msg_id, text, kb)
        elif cb.startswith("adm_pm_"):
            _render_method(uid, msg_id, cb[len("adm_pm_"):])
        else:
            _render_hub(uid, msg_id)
