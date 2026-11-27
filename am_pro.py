"""Alight Motion Pro monthly subscription — user menus, subscribe flow,
upgrade flow, and admin tools (generate / upgrade / bulk).

User model (John's decision):
  - Subscribers: "Upgrade My Email" only, 5/day cap, 30-day subscription.
  - Admin: full trio — single generate, upgrade any email, bulk 5/15/25/50/100/custom.
All config lives in Settings (am_enabled / am_monthly_price / am_daily_cap).
"""

import re
import threading

import telebot
from telebot import types

import am_engine
import db
import emojis
import msg_templates as T
from utils import (
    admin_only, check_membership, clear_state, edit_or_send, fmt_mmk,
    get_state, is_admin, log_to_channel, mask_email, mask_name,
    membership_required_msg, set_state, silent_delete,
)

EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")

# ── helpers ──────────────────────────────────────────────────────────────────

def _am_on() -> bool:
    return db.cfg_get("am_enabled", "0") == "1"


def _price() -> int:
    try:
        return int(db.cfg_get("am_monthly_price", "0") or 0)
    except ValueError:
        return 0


def _cap() -> int:
    try:
        return int(db.cfg_get("am_daily_cap", "5") or 5)
    except ValueError:
        return 5


def _unavailable_text() -> str:
    return (
        f"{emojis.emoji('warning', '⚠️')} <b>Activate AlightMotion</b>\n\n"
        "This service is temporarily unavailable. Please check back later."
    )


def _render_menu(bot, uid, msg_id):
    """Route to pitch screen or subscriber menu based on subscription."""
    if not _am_on():
        edit_or_send(bot, uid, msg_id, _unavailable_text(),
                     reply_markup=_back_kb())
        return
    if is_admin(uid):
        _render_subscriber(bot, uid, msg_id, admin_mode=True)
        return
    sub = db.am_get_subscription(uid)
    if sub:
        _render_subscriber(bot, uid, msg_id)
    else:
        _render_pitch(bot, uid, msg_id)


def _back_kb(to="main_menu"):
    kb = types.InlineKeyboardMarkup()
    kb.add(emojis.button("Back", callback_data=to,
                         emoji_id="5255703720078879038", lead="⬅️"))
    return kb


def _render_pitch(bot, uid, msg_id):
    price = _price()
    text = (
        f"{emojis.emoji('am_menu')} <b>Activate AlightMotion</b>\n\n"
        "Get <b>Alight Motion Pro</b> on your own email — 1 full year, activated in minutes!\n\n"
        "How it works:\n"
        "1. Send us your email address\n"
        "2. We send an activation link to your inbox\n"
        "3. Paste the link back here — done, you're Pro!\n\n"
        f"One subscription covers you for <b>30 days</b> — upgrade up to "
        f"<b>{_cap()} emails per day</b>, every day.\n\n"
        f"{emojis.emoji('money', '💰')} Price: <b>{fmt_mmk(price)}/month</b>"
    )
    kb = types.InlineKeyboardMarkup()
    if price > 0:
        kb.add(emojis.button("Subscribe", callback_data="am_subscribe",
                             key="crown"))
    kb.add(emojis.button("Back", callback_data="main_menu",
                         emoji_id="5255703720078879038", lead="⬅️"))
    edit_or_send(bot, uid, msg_id, text, reply_markup=kb)


def _render_subscriber(bot, uid, msg_id, admin_mode=False):
    if admin_mode and not db.am_get_subscription(uid):
        status_line = f"{emojis.emoji('crown', '👑')} Admin access — no subscription needed"
    else:
        days = db.am_days_left(uid)
        left = db.am_daily_remaining(uid, _cap())
        status_line = (
            f"{emojis.emoji('stopwatch', '⏱')} Pro active — <b>{days} days left</b>, "
            f"<b>{left} upgrades</b> left today"
        )
    text = (
        f"{emojis.emoji('am_menu')} <b>Activate AlightMotion</b>\n\n"
        f"{status_line}"
    )
    kb = types.InlineKeyboardMarkup()
    kb.add(emojis.button("Upgrade My Email", callback_data="am_upgrade",
                         key="am_upgrade"))
    if is_admin(uid):
        kb.add(emojis.button("Generate Account", callback_data="am_admin_generate",
                             key="am_generate"),
               emojis.button("Bulk Generate", callback_data="am_admin_bulk",
                             key="am_bulk"))
    kb.add(emojis.button("Back", callback_data="main_menu",
                         emoji_id="5255703720078879038", lead="⬅️"))
    edit_or_send(bot, uid, msg_id, text, reply_markup=kb)


# ── subscribe ────────────────────────────────────────────────────────────────

def _do_subscribe(bot, call):
    uid = call.from_user.id
    if not _am_on():
        bot.answer_callback_query(call.id, "Temporarily unavailable.")
        return
    if db.am_get_subscription(uid):
        _render_subscriber(bot, uid, call.message.message_id)
        bot.answer_callback_query(call.id)
        return
    price = _price()
    if price <= 0:
        bot.answer_callback_query(call.id, "Price not set yet.", show_alert=True)
        return
    user = db.get_user(uid)
    balance = (user["mmk_balance"] if user else 0) or 0
    if balance < price:
        bot.answer_callback_query(
            call.id,
            f"Not enough balance. Need {fmt_mmk(price)}, you have {fmt_mmk(balance)}.",
            show_alert=True,
        )
        return
    if not db.deduct_balance_atomic(uid, price):
        bot.answer_callback_query(call.id, "Payment failed, try again.", show_alert=True)
        return
    db.am_create_subscription(uid, call.from_user.username or "", price)
    db.add_total_spent(uid, price)  # counts toward VIP tiers
    try:
        log_to_channel(bot, T.log_am_subscribed(
            mask_name(call.from_user.username or ""), fmt_mmk(price)))
    except Exception:
        pass
    bot.answer_callback_query(call.id, "Subscribed! Welcome to AlightMotion Pro.")
    _render_subscriber(bot, uid, call.message.message_id)


# ── upgrade flow (subscriber) ────────────────────────────────────────────────

def _start_upgrade(bot, call, as_admin=False):
    uid = call.from_user.id
    if is_admin(uid):
        as_admin = True  # admins always use the unrestricted path
    if not _am_on() and not as_admin:
        bot.answer_callback_query(call.id, "Temporarily unavailable.")
        return
    if not as_admin:
        if not db.am_get_subscription(uid):
            _render_pitch(bot, uid, call.message.message_id)
            bot.answer_callback_query(call.id)
            return
        if db.am_daily_remaining(uid, _cap()) <= 0:
            bot.answer_callback_query(
                call.id,
                "You've used all your upgrades for today — come back tomorrow!",
                show_alert=True,
            )
            return
    bot.answer_callback_query(call.id)
    set_state(uid, step="am_await_email", am_admin=as_admin,
              prompt_msg_id=call.message.message_id)
    edit_or_send(
        bot, uid, call.message.message_id,
        f"{emojis.emoji('am_upgrade')} <b>Upgrade My Email</b>\n\n"
        "Send me the email address you want to upgrade to Pro.",
        reply_markup=_back_kb("am_menu"),
    )


def _on_email_received(bot, msg):
    uid = msg.from_user.id
    st = get_state(uid)
    if st.get("step") != "am_await_email":
        return False
    prompt_id = st.get("prompt_msg_id")
    # Delete the user's email immediately — keep the chat clean.
    silent_delete(bot, uid, msg.message_id)
    email = msg.text.strip().lower()
    if not EMAIL_RE.match(email):
        edit_or_send(
            bot, uid, prompt_id,
            f"{emojis.emoji('rejected', '❌')} That doesn't look like a valid email. Try again:",
            reply_markup=_back_kb("am_menu"),
        )
        return True
    as_admin = st.get("am_admin", False)
    # Fire the magic link, then wait for the user to paste it back.
    # All updates edit the same prompt message in place.
    edit_or_send(
        bot, uid, prompt_id,
        f"{emojis.emoji('processing', '⏳')} Sending activation link to <code>{email}</code>...",
    )
    res = am_engine.send_link(email)
    if not res["success"]:
        msg_text = res.get("message", "")
        quota = "QUOTA_EXCEEDED" in msg_text or "quota" in msg_text.lower()
        edit_or_send(
            bot, uid, prompt_id,
            f"{emojis.emoji('rejected', '❌')} Could not send the link"
            + (" — daily activation limit reached, try again later." if quota else f": {msg_text[:120]}"),
            reply_markup=_back_kb("am_menu"),
        )
        clear_state(uid)
        return True
    set_state(uid, step="am_await_link", am_email=email, am_admin=as_admin,
              prompt_msg_id=prompt_id)
    edit_or_send(
        bot, uid, prompt_id,
        "We sent an activation link to your inbox! Open your inbox, "
        "press and hold on the sign in text, then copy the link, and paste it here.",
    )
    return True


def _on_link_received(bot, msg):
    uid = msg.from_user.id
    st = get_state(uid)
    if st.get("step") != "am_await_link":
        return False
    prompt_id = st.get("prompt_msg_id")
    # Delete the user's link immediately — it contains a sensitive code.
    silent_delete(bot, uid, msg.message_id)
    email = st.get("am_email", "")
    as_admin = st.get("am_admin", False)
    oob = am_engine.extract_oob_code(msg.text.strip())
    if not oob:
        edit_or_send(
            bot, uid, prompt_id,
            f"{emojis.emoji('rejected', '❌')} I couldn't find a sign-in link in that message. "
            "Paste the full link from your email:",
        )
        return True
    clear_state(uid)
    masked_buyer = mask_name(msg.from_user.username or "")
    edit_or_send(
        bot, uid, prompt_id,
        f"{emojis.emoji('processing', '⏳')} Activating Pro on <code>{email}</code> — this takes a few seconds...",
    )

    def _run():
        try:
            signed = am_engine.sign_in(email, oob)
            if not signed["success"]:
                raise RuntimeError(signed.get("message", "sign-in failed"))
            act = am_engine.activate(signed["id_token"])
            if not act["success"]:
                raise RuntimeError(act.get("message", "activation failed"))
            if not as_admin:
                db.am_bump_usage(uid)
            try:
                log_to_channel(bot, T.log_am_activated(
                    masked_buyer, mask_email(email)))
            except Exception:
                pass
            left = db.am_daily_remaining(uid, _cap()) if not as_admin else "∞"
            kb = types.InlineKeyboardMarkup()
            kb.add(emojis.button("Back to AlightMotion", callback_data="am_menu",
                                 emoji_id="5255703720078879038", lead="⬅️"))
            edit_or_send(
                bot, uid, prompt_id,
                f"{emojis.emoji('success', '✅')} <b>Done!</b> <code>{email}</code> "
                f"is now <b>Alight Motion Pro</b> for 1 year. Enjoy!"
                + (f"\n\n{left} upgrades left today." if not as_admin else ""),
                reply_markup=kb,
            )
        except Exception as e:
            err = str(e)[:160]
            kb = types.InlineKeyboardMarkup()
            kb.add(emojis.button("Try Again", callback_data="am_upgrade",
                                 key="am_upgrade"),
                   emojis.button("Back", callback_data="am_menu",
                                 emoji_id="5255703720078879038", lead="⬅️"))
            edit_or_send(
                bot, uid, prompt_id,
                f"{emojis.emoji('rejected', '❌')} Activation failed: {err}\n\n"
                "The link may have expired — try again with a fresh link.",
                reply_markup=kb,
            )

    threading.Thread(target=_run, daemon=True).start()
    return True


# ── admin: single generate ───────────────────────────────────────────────────

def _admin_generate(bot, call):
    uid = call.from_user.id
    msg_id = call.message.message_id
    bot.answer_callback_query(call.id, "Generating account...")
    edit_or_send(
        bot, uid, msg_id,
        f"{emojis.emoji('processing', '⏳')} Generating Alight Motion Pro account...",
    )

    def _run():
        try:
            res = am_engine.generate_account()
            if not res["success"]:
                raise RuntimeError(res.get("message", "generation failed"))
            kb = types.InlineKeyboardMarkup()
            kb.add(types.InlineKeyboardButton("Open Login Link", url=res["login_link"]))
            kb.add(emojis.button("Dismiss", callback_data="am_dismiss",
                                 emoji_id="5039614900280754969", lead="🗑"))
            edit_or_send(
                bot, uid, msg_id,
                f"{emojis.emoji('success', '✅')} <b>Account ready!</b>\n\n"
                f"Email: <code>{res['email']}</code>\n"
                "Tap the button below to log in (or copy the link):\n"
                f"<code>{res['login_link']}</code>",
                reply_markup=kb,
            )
        except Exception as e:
            kb = types.InlineKeyboardMarkup()
            kb.add(emojis.button("Try Again", callback_data="am_admin_generate",
                                 key="am_generate"),
                   emojis.button("Back", callback_data="am_menu",
                                 emoji_id="5255703720078879038", lead="⬅️"))
            edit_or_send(
                bot, uid, msg_id,
                f"{emojis.emoji('rejected', '❌')} Generation failed: {str(e)[:160]}",
                reply_markup=kb,
            )

    threading.Thread(target=_run, daemon=True).start()


# ── admin: bulk generate ─────────────────────────────────────────────────────

_BULK_QTYS = [5, 15, 25, 50, 100]

def _admin_bulk_menu(bot, uid, msg_id):
    kb = types.InlineKeyboardMarkup(row_width=2)
    for q in _BULK_QTYS:
        kb.add(emojis.button(str(q), callback_data=f"am_bulk_{q}", key="dart"))
    kb.add(emojis.button("Custom", callback_data="am_bulk_custom", key="pencil"))
    kb.add(emojis.button("Back", callback_data="am_menu",
                         emoji_id="5255703720078879038", lead="⬅️"))
    edit_or_send(
        bot, uid, msg_id,
        f"{emojis.emoji('am_bulk')} <b>Bulk Generate</b>\n\nHow many accounts?",
        reply_markup=kb,
    )


def _admin_bulk_run(bot, uid, msg_id, count):
    edit_or_send(
        bot, uid, msg_id,
        f"{emojis.emoji('processing', '⏳')} Generating <b>0/{count}</b>...",
    )
    results = []

    def _run():
        ok = 0
        for i in range(1, count + 1):
            try:
                res = am_engine.generate_account()
                if res["success"]:
                    ok += 1
                    results.append((res["email"], res["login_link"]))
            except Exception:
                pass
            try:
                edit_or_send(
                    bot, uid, msg_id,
                    f"{emojis.emoji('processing', '⏳')} Generating <b>{i}/{count}</b>... ({ok} ok)",
                )
            except Exception:
                pass
        if not results:
            kb = types.InlineKeyboardMarkup()
            kb.add(emojis.button("Back", callback_data="am_menu",
                                 emoji_id="5255703720078879038", lead="⬅️"))
            edit_or_send(
                bot, uid, msg_id,
                f"{emojis.emoji('rejected', '❌')} Bulk generation failed — no accounts created.",
                reply_markup=kb,
            )
            return
        from datetime import datetime
        lines = [f"Alight Motion Pro accounts ({ok}/{count})",
                 f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}"]
        for n, (email, link) in enumerate(results, 1):
            lines += [f"", f"[{n}] {email}", link]
        buf = "\n".join(lines).encode("utf-8")
        kb = types.InlineKeyboardMarkup()
        kb.add(emojis.button("Back to AlightMotion", callback_data="am_menu",
                             emoji_id="5255703720078879038", lead="⬅️"))
        edit_or_send(
            bot, uid, msg_id,
            f"{emojis.emoji('success', '✅')} <b>{ok}/{count}</b> accounts ready! Sending the file...",
            reply_markup=kb,
        )
        bot.send_document(
            uid, ("alight_motion_accounts.txt", buf),
            caption=f"{emojis.emoji('success', '✅')} <b>{ok}/{count}</b> Alight Motion Pro accounts",
        )

    threading.Thread(target=_run, daemon=True).start()


# ── registration ─────────────────────────────────────────────────────────────

def register(bot: telebot.TeleBot):

    @bot.callback_query_handler(func=lambda c: c.data == "am_dismiss")
    def cb_am_dismiss(call: types.CallbackQuery):
        silent_delete(bot, call.message.chat.id, call.message.message_id)
        bot.answer_callback_query(call.id)

    @bot.callback_query_handler(func=lambda c: c.data == "am_menu")
    def cb_am_menu(call: types.CallbackQuery):
        uid = call.from_user.id
        if not is_admin(uid) and not check_membership(bot, uid):
            bot.answer_callback_query(call.id)
            membership_required_msg(bot, uid)
            return
        _render_menu(bot, uid, call.message.message_id)
        bot.answer_callback_query(call.id)

    @bot.callback_query_handler(func=lambda c: c.data == "am_subscribe")
    def cb_am_subscribe(call: types.CallbackQuery):
        _do_subscribe(bot, call)

    @bot.callback_query_handler(func=lambda c: c.data == "am_upgrade")
    def cb_am_upgrade(call: types.CallbackQuery):
        _start_upgrade(bot, call)

    @bot.callback_query_handler(func=lambda c: c.data == "am_admin_generate")
    @admin_only
    def cb_am_admin_generate(call: types.CallbackQuery):
        _admin_generate(bot, call)

    @bot.callback_query_handler(func=lambda c: c.data == "am_admin_bulk")
    @admin_only
    def cb_am_admin_bulk(call: types.CallbackQuery):
        bot.answer_callback_query(call.id)
        _admin_bulk_menu(bot, call.from_user.id, call.message.message_id)

    @bot.callback_query_handler(func=lambda c: c.data.startswith("am_bulk_"))
    @admin_only
    def cb_am_bulk_qty(call: types.CallbackQuery):
        uid = call.from_user.id
        arg = call.data[len("am_bulk_"):]
        if arg == "custom":
            set_state(uid, step="am_await_bulk_count",
                      prompt_msg_id=call.message.message_id)
            bot.answer_callback_query(call.id)
            edit_or_send(bot, uid, call.message.message_id,
                         "How many accounts? Send a number (1–200):",
                         reply_markup=_back_kb("am_menu"))
            return
        try:
            count = int(arg)
        except ValueError:
            bot.answer_callback_query(call.id, "Invalid.")
            return
        bot.answer_callback_query(call.id, f"Generating {count}...")
        _admin_bulk_run(bot, uid, call.message.message_id, count)

    @bot.message_handler(func=lambda m: (get_state(m.from_user.id).get("step") or "").startswith("am_"))
    def on_am_text(msg: types.Message):
        st = get_state(msg.from_user.id)
        step = st.get("step", "")
        if step == "am_await_email":
            _on_email_received(bot, msg)
        elif step == "am_await_link":
            _on_link_received(bot, msg)
        elif step == "am_await_bulk_count":
            try:
                count = int(msg.text.strip())
            except ValueError:
                count = 0
            if not 1 <= count <= 200:
                silent_delete(bot, msg.from_user.id, msg.message_id)
                edit_or_send(
                    bot, msg.from_user.id, st.get("prompt_msg_id"),
                    f"{emojis.emoji('rejected', '❌')} Send a number between 1 and 200:",
                    reply_markup=_back_kb("am_menu"),
                )
                return
            clear_state(msg.from_user.id)
            # Delete the typed number, then reuse the prompt message for progress.
            prompt_id = st.get("prompt_msg_id")
            silent_delete(bot, msg.from_user.id, msg.message_id)
            _admin_bulk_run(bot, msg.from_user.id, prompt_id, count)
