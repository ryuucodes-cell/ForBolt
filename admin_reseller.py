"""Admin: reseller management — keys, access requests, and the reseller roster.

Flow:
  • Admin generates one-time keys OR approves a user's access request (which
    auto-generates a key and DMs it to them).
  • User redeems the key to unlock the Reseller Hub (see handlers.py).
  • A background loop (bot.py) revokes resellers who haven't purchased within
    the configured inactivity window; revoked users can request access again.
"""

import html
import random
import string

import telebot
from telebot import types

import db
import emojis
from utils import (
    admin_only, back_kb, set_state, get_state, clear_state,
    silent_delete, admin_report, user_link, fmt_date, fmt_datetime, fmt_money,
)
from msg_templates import reseller_approved, reseller_rejected


def _esc(s) -> str:
    return html.escape(str(s))


# Ambiguous characters (0/O, 1/I/L) removed so codes are easy to read and type.
_KEY_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"


def _gen_key_code() -> str:
    """A grouped 12-char code, e.g. 'A7K9-XPQ2-M4RT'."""
    raw = "".join(random.choice(_KEY_ALPHABET) for _ in range(12))
    return f"{raw[:4]}-{raw[4:8]}-{raw[8:]}"


def _make_unique_key(created_by: int, note: str = None) -> str:
    """Generate a code guaranteed unique in the DB (retries on the rare clash)."""
    for _ in range(10):
        code = _gen_key_code()
        try:
            db.create_reseller_key(code, created_by, note=note)
            return code
        except Exception:
            continue
    # extreme fallback — extend length
    code = _gen_key_code() + "-" + "".join(random.choice(_KEY_ALPHABET) for _ in range(4))
    db.create_reseller_key(code, created_by, note=note)
    return code


def register(bot: telebot.TeleBot):

    # ═══ HOME ══════════════════════════════════════════════════════════════════

    def _render_home(uid, msg_id):
        pending_reqs = db.count_reseller_requests(status="pending")
        total_resell = db.count_resellers()
        unused_keys  = db.count_unused_reseller_keys()
        days         = db.cfg_get("reseller_inactivity_days", "30")
        text = (
            f"<b>Reseller Management</b> {emojis.emoji('key', '🔑')}\n\n"
            f"<blockquote>Active resellers: <b>{total_resell}</b>\n"
            f"Pending requests: <b>{pending_reqs}</b>\n"
            f"Unused keys: <b>{unused_keys}</b>\n"
            f"Auto-remove after: <b>{days} days</b> of no purchase</blockquote>\n\n"
            "<blockquote>Resellers who don't buy within the window are removed "
            "automatically and can request access again.</blockquote>"
        )
        kb = types.InlineKeyboardMarkup(row_width=2)
        req_label = f"Requests ({pending_reqs})" if pending_reqs else "Requests"
        kb.add(
            emojis.button(req_label, callback_data="adm_res_reqs_0", emoji_id="5039827436737397847"),
            emojis.button("🏪 Resellers", callback_data="adm_res_list_0", emoji_id="5039834781131474002", lead="🏪"),
        )
        kb.add(
            emojis.button("🎟 Generate Key", callback_data="adm_res_genkey", emoji_id="5042167377869932162", lead="🎟"),
            emojis.button("📋 Keys", callback_data="adm_res_keys_0", emoji_id="5296369303661067030", lead="📋"),
        )
        kb.add(emojis.button("⏳ Inactivity Window", callback_data="adm_res_setdays", emoji_id="5040045238823945201", lead="⏳"))
        kb.add(
            emojis.button("⬅️ Back", callback_data="admin_panel", emoji_id="5255703720078879038", lead="⬅️"),
            emojis.button("🏠 Main Menu", callback_data="main_menu", emoji_id="5042022053356504092", lead="🏠"),
        )
        try:
            bot.edit_message_text(text, uid, msg_id, reply_markup=kb)
        except Exception:
            bot.send_message(uid, text, reply_markup=kb)

    @bot.callback_query_handler(func=lambda c: c.data == "adm_reseller")
    @admin_only
    def cb_adm_reseller(call: types.CallbackQuery):
        _render_home(call.from_user.id, call.message.message_id)
        bot.answer_callback_query(call.id)

    # ═══ REQUESTS ══════════════════════════════════════════════════════════════

    @bot.callback_query_handler(func=lambda c: c.data.startswith("adm_res_reqs_"))
    @admin_only
    def cb_adm_res_reqs(call: types.CallbackQuery):
        offset = int(call.data[len("adm_res_reqs_"):])
        PAGE   = 10
        reqs   = db.list_reseller_requests(status="pending", limit=PAGE, offset=offset)
        total  = db.count_reseller_requests(status="pending")
        kb = types.InlineKeyboardMarkup(row_width=1)
        if reqs:
            for r in reqs:
                kb.add(emojis.button(f"📥 @{r['username'] or r['telegram_id']} · {fmt_date(r['created_at'])}",
                    callback_data=f"adm_res_req_{r['request_id']}",
                ))
        else:
            kb.add(emojis.button("✅ No pending requests", callback_data="noop"))
        nav = []
        if offset > 0:
            nav.append(emojis.button("⬅️ Prev", callback_data=f"adm_res_reqs_{offset - PAGE}", emoji_id="5255703720078879038"))
        if offset + PAGE < total:
            nav.append(emojis.button("Next ➡️", callback_data=f"adm_res_reqs_{offset + PAGE}", emoji_id="5253767677670862169"))
        if nav:
            kb.add(*nav)
        kb.add(
            emojis.button("⬅️ Back", callback_data="adm_reseller", emoji_id="5255703720078879038", lead="⬅️"),
            emojis.button("🏠 Main Menu", callback_data="main_menu", emoji_id="5042022053356504092", lead="🏠"),
        )
        bot.edit_message_text(
            f"<b>Reseller Requests</b> — {total} pending",
            call.from_user.id, call.message.message_id, reply_markup=kb,
        )
        bot.answer_callback_query(call.id)

    @bot.callback_query_handler(func=lambda c: c.data.startswith("adm_res_req_") and c.data[len("adm_res_req_"):].isdigit())
    @admin_only
    def cb_adm_res_req_detail(call: types.CallbackQuery):
        rid = int(call.data[len("adm_res_req_"):])
        r   = db.get_reseller_request(rid)
        if not r:
            bot.answer_callback_query(call.id, "Request not found.")
            return
        u = db.get_user(r["telegram_id"])
        orders  = db.count_orders_for_user(r["telegram_id"])
        spent   = fmt_money(u["total_spent"]) if u else "—"
        bal     = fmt_money(u["mmk_balance"]) if u else "—"
        joined  = fmt_date(u["registered_at"]) if u else "—"
        text = (
            f"<b>Reseller Request #{rid}</b>\n\n"
            f"<blockquote>User: {user_link(r['telegram_id'], r['username'])}\n"
            f"Orders: <b>{orders}</b>\n"
            f"Total spent: <b>{spent}</b>\n"
            f"Balance: <b>{bal}</b>\n"
            f"Joined: {joined}\n"
            f"Requested: {fmt_datetime(r['created_at'])}</blockquote>"
        )
        if r["message"]:
            text += f"\n\n<blockquote>{_esc(r['message'])}</blockquote>"
        if r["status"] != "pending":
            text += f"\n\nStatus: <b>{r['status'].capitalize()}</b>"
        kb = types.InlineKeyboardMarkup(row_width=2)
        if r["status"] == "pending":
            kb.add(
                emojis.button("✅ Approve & Send Key", callback_data=f"adm_res_reqok_{rid}"),
                emojis.button("❌ Reject", callback_data=f"adm_res_reqno_{rid}"),
            )
        kb.add(
            emojis.button("⬅️ Back", callback_data="adm_res_reqs_0", emoji_id="5255703720078879038", lead="⬅️"),
            emojis.button("🏠 Main Menu", callback_data="main_menu", emoji_id="5042022053356504092", lead="🏠"),
        )
        bot.edit_message_text(text, call.from_user.id, call.message.message_id, reply_markup=kb)
        bot.answer_callback_query(call.id)

    @bot.callback_query_handler(func=lambda c: c.data.startswith("adm_res_reqok_"))
    @admin_only
    def cb_adm_res_req_approve(call: types.CallbackQuery):
        rid = int(call.data[len("adm_res_reqok_"):])
        r   = db.resolve_reseller_request_atomic(rid, "approved", handled_by=call.from_user.id)
        if not r:
            existing = db.get_reseller_request(rid)
            bot.answer_callback_query(call.id, f"Already {existing['status'] if existing else 'gone'}.")
            return
        bot.answer_callback_query(call.id, "Approved — generating key…")
        # generate a one-time key and DM it to the requester
        code = _make_unique_key(call.from_user.id, note=f"approved req #{rid} for @{r['username'] or r['telegram_id']}")
        try:
            kb_user = types.InlineKeyboardMarkup(row_width=1)
            kb_user.add(emojis.button("Activate Now", callback_data="reseller_activate", emoji_id="5307843983102204243", lead="🔑"))
            kb_user.add(emojis.button("Dismiss", callback_data="dismiss_msg", emoji_id="5210952531676504517", lead="✖️"))
            bot.send_message(
                r["telegram_id"],
                reseller_approved(code),
                parse_mode='HTML',
                reply_markup=kb_user,
            )
        except Exception:
            pass
        admin_report(
            bot, "RESELLER REQUEST APPROVED",
            [
                ("", "User", user_link(r["telegram_id"], r["username"])),
                ("", "Key", f"<code>{code}</code>"),
                ("", "By", user_link(call.from_user.id, call.from_user.username)),
            ],
            icon="",
        )
        _render_home(call.from_user.id, call.message.message_id)

    @bot.callback_query_handler(func=lambda c: c.data.startswith("adm_res_reqno_"))
    @admin_only
    def cb_adm_res_req_reject(call: types.CallbackQuery):
        rid = int(call.data[len("adm_res_reqno_"):])
        r   = db.resolve_reseller_request_atomic(rid, "rejected", handled_by=call.from_user.id)
        if not r:
            existing = db.get_reseller_request(rid)
            bot.answer_callback_query(call.id, f"Already {existing['status'] if existing else 'gone'}.")
            return
        bot.answer_callback_query(call.id, "Rejected.")
        try:
            kb_user = types.InlineKeyboardMarkup(row_width=1)
            kb_user.add(emojis.button("Back to Shop", callback_data="main_menu", emoji_id="5406683434124859552", lead="🛍"))
            bot.send_message(
                r["telegram_id"],
                reseller_rejected(),
                parse_mode='HTML',
                reply_markup=kb_user,
            )
        except Exception:
            pass
        admin_report(
            bot, "RESELLER REQUEST REJECTED",
            [
                ("", "User", user_link(r["telegram_id"], r["username"])),
                ("", "By", user_link(call.from_user.id, call.from_user.username)),
            ],
            icon="",
        )
        _render_home(call.from_user.id, call.message.message_id)

    # ═══ KEYS ══════════════════════════════════════════════════════════════════

    @bot.callback_query_handler(func=lambda c: c.data == "adm_res_genkey")
    @admin_only
    def cb_adm_res_genkey(call: types.CallbackQuery):
        code = _make_unique_key(call.from_user.id, note="manual generate")
        kb = types.InlineKeyboardMarkup(row_width=2)
        kb.add(emojis.button("🎟 Generate Another", callback_data="adm_res_genkey", emoji_id="5042167377869932162", lead="🎟"))
        kb.add(
            emojis.button("📋 All Keys", callback_data="adm_res_keys_0", emoji_id="5296369303661067030", lead="📋"),
            emojis.button("⬅️ Back", callback_data="adm_reseller", emoji_id="5255703720078879038", lead="⬅️"),
        )
        bot.edit_message_text(
            "<b>New Reseller Key</b>\n\n"
            f"<code>{code}</code>  <i>(tap to copy)</i>\n\n"
            "Send this to the person you want to make a reseller. It works once. "
            "They activate it via <b>Become a Reseller → Activate Key</b>.",
            call.from_user.id, call.message.message_id, reply_markup=kb,
        )
        bot.answer_callback_query(call.id, "Key generated.")

    @bot.callback_query_handler(func=lambda c: c.data.startswith("adm_res_keys_"))
    @admin_only
    def cb_adm_res_keys(call: types.CallbackQuery):
        offset = int(call.data[len("adm_res_keys_"):])
        PAGE   = 10
        keys   = db.list_reseller_keys(limit=PAGE + offset)[offset:offset + PAGE]
        kb = types.InlineKeyboardMarkup(row_width=1)
        if keys:
            for k in keys:
                if k["revoked"]:
                    status = ""
                elif k["used_by"]:
                    status = ""
                else:
                    status = ""
                label = f"{status} {k['code']}"
                if k["used_by"]:
                    label += f" · @{k['used_by']}"
                kb.add(emojis.button(label, callback_data=f"adm_res_key_{k['key_id']}"))
        else:
            kb.add(emojis.button("No keys yet", callback_data="noop"))
        nav = []
        if offset > 0:
            nav.append(emojis.button("⬅️ Prev", callback_data=f"adm_res_keys_{offset - PAGE}", emoji_id="5255703720078879038"))
        if len(db.list_reseller_keys(limit=offset + PAGE + 1)) > offset + PAGE:
            nav.append(emojis.button("Next ➡️", callback_data=f"adm_res_keys_{offset + PAGE}", emoji_id="5253767677670862169"))
        if nav:
            kb.add(*nav)
        kb.add(
            emojis.button("🎟 Generate Key", callback_data="adm_res_genkey", emoji_id="5042167377869932162", lead="🎟"),
            emojis.button("⬅️ Back", callback_data="adm_reseller", emoji_id="5255703720078879038", lead="⬅️"),
        )
        bot.edit_message_text(
            "<b>Reseller Keys</b>\nunused · used · revoked",
            call.from_user.id, call.message.message_id, reply_markup=kb,
        )
        bot.answer_callback_query(call.id)

    @bot.callback_query_handler(func=lambda c: c.data.startswith("adm_res_key_") and c.data[len("adm_res_key_"):].isdigit())
    @admin_only
    def cb_adm_res_key_detail(call: types.CallbackQuery):
        kid  = int(call.data[len("adm_res_key_"):])
        keys = db.list_reseller_keys(limit=100000)
        k    = next((x for x in keys if x["key_id"] == kid), None)
        if not k:
            bot.answer_callback_query(call.id, "Key not found.")
            return
        if k["revoked"]:
            status = "Revoked"
        elif k["used_by"]:
            status = f"Used by {user_link(k['used_by'], None)}"
        else:
            status = "Unused"
        text = (
            f"<b>Key #{kid}</b>\n\n"
            f"<code>{k['code']}</code>\n\n"
            f"Status: {status}\n"
            f"Created: {fmt_datetime(k['created_at'])}"
        )
        if k["used_at"]:
            text += f"\nUsed: {fmt_datetime(k['used_at'])}"
        if k["note"]:
            text += f"\nNote: {_esc(k['note'])}"
        kb = types.InlineKeyboardMarkup(row_width=1)
        if not k["used_by"] and not k["revoked"]:
            kb.add(emojis.button("🚫 Revoke Key", callback_data=f"adm_res_keyrev_{kid}"))
        kb.add(
            emojis.button("⬅️ Back", callback_data="adm_res_keys_0", emoji_id="5255703720078879038", lead="⬅️"),
            emojis.button("🏠 Main Menu", callback_data="main_menu", emoji_id="5042022053356504092", lead="🏠"),
        )
        bot.edit_message_text(text, call.from_user.id, call.message.message_id, reply_markup=kb)
        bot.answer_callback_query(call.id)

    @bot.callback_query_handler(func=lambda c: c.data.startswith("adm_res_keyrev_"))
    @admin_only
    def cb_adm_res_key_revoke(call: types.CallbackQuery):
        kid = int(call.data[len("adm_res_keyrev_"):])
        ok  = db.revoke_reseller_key(kid)
        bot.answer_callback_query(call.id, "Revoked." if ok else "Can't revoke — already used.", show_alert=not ok)
        call.data = "adm_res_keys_0"
        cb_adm_res_keys(call)

    # ═══ INACTIVITY WINDOW ═════════════════════════════════════════════════════

    @bot.callback_query_handler(func=lambda c: c.data == "adm_res_setdays")
    @admin_only
    def cb_adm_res_setdays(call: types.CallbackQuery):
        current = db.cfg_get("reseller_inactivity_days", "30")
        kb = types.InlineKeyboardMarkup(row_width=4)
        presets = [7, 14, 30, 45, 60, 90]
        btns = [emojis.button(f"{'✅ ' if str(d) == current else ''}{d}d",
                                           callback_data=f"adm_res_days_{d}") for d in presets]
        kb.add(*btns)
        kb.add(
            emojis.button("⬅️ Back", callback_data="adm_reseller", emoji_id="5255703720078879038", lead="⬅️"),
            emojis.button("🏠 Main Menu", callback_data="main_menu", emoji_id="5042022053356504092", lead="🏠"),
        )
        bot.edit_message_text(
            f"<b>Inactivity Window</b>\n\n"
            f"Current: <b>{current} days</b>\n\n"
            "<blockquote>A reseller who makes no purchase within this many days is "
            "automatically removed from the reseller panel. They can request access "
            "again afterward.</blockquote>",
            call.from_user.id, call.message.message_id, reply_markup=kb,
        )
        bot.answer_callback_query(call.id)

    @bot.callback_query_handler(func=lambda c: c.data.startswith("adm_res_days_"))
    @admin_only
    def cb_adm_res_days(call: types.CallbackQuery):
        days = call.data[len("adm_res_days_"):]
        db.cfg_set("reseller_inactivity_days", days)
        bot.answer_callback_query(call.id, f"Set to {days} days.")
        _render_home(call.from_user.id, call.message.message_id)

    # ═══ RESELLER ROSTER ═══════════════════════════════════════════════════════

    @bot.callback_query_handler(func=lambda c: c.data.startswith("adm_res_list_"))
    @admin_only
    def cb_adm_res_list(call: types.CallbackQuery):
        offset = int(call.data[len("adm_res_list_"):])
        PAGE   = 10
        all_r  = db.list_resellers()
        total  = len(all_r)
        chunk  = all_r[offset:offset + PAGE]
        kb = types.InlineKeyboardMarkup(row_width=1)
        if chunk:
            for u in chunk:
                kb.add(emojis.button(f"🏪 @{u['username'] or u['telegram_id']} · last buy {fmt_date(u['last_reseller_purchase'])}",
                    callback_data=f"adm_res_view_{u['telegram_id']}",
                ))
        else:
            kb.add(emojis.button("No active resellers", callback_data="noop"))
        nav = []
        if offset > 0:
            nav.append(emojis.button("⬅️ Prev", callback_data=f"adm_res_list_{offset - PAGE}", emoji_id="5255703720078879038"))
        if offset + PAGE < total:
            nav.append(emojis.button("Next ➡️", callback_data=f"adm_res_list_{offset + PAGE}", emoji_id="5253767677670862169"))
        if nav:
            kb.add(*nav)
        kb.add(
            emojis.button("⬅️ Back", callback_data="adm_reseller", emoji_id="5255703720078879038", lead="⬅️"),
            emojis.button("🏠 Main Menu", callback_data="main_menu", emoji_id="5042022053356504092", lead="🏠"),
        )
        bot.edit_message_text(
            f"<b>Active Resellers</b> — {total} total",
            call.from_user.id, call.message.message_id, reply_markup=kb,
        )
        bot.answer_callback_query(call.id)

    @bot.callback_query_handler(func=lambda c: c.data.startswith("adm_res_view_"))
    @admin_only
    def cb_adm_res_view(call: types.CallbackQuery):
        target = int(call.data[len("adm_res_view_"):])
        _render_reseller_profile(call.from_user.id, call.message.message_id, target)
        bot.answer_callback_query(call.id)

    def _render_reseller_profile(admin_uid, msg_id, target):
        u = db.get_user(target)
        if not u:
            bot.send_message(admin_uid, "User not found.")
            return
        orders = db.count_orders_for_user(target)
        text = (
            f"<b>Reseller — @{u['username'] or target}</b>\n\n"
            f"<blockquote>{user_link(target, u['username'])}\n"
            f"Balance: <b>{fmt_money(u['mmk_balance'])}</b>\n"
            f"Total spent: <b>{fmt_money(u['total_spent'])}</b>\n"
            f"Orders: <b>{orders}</b>\n"
            f"Reseller since: {fmt_date(u['reseller_since'])}\n"
            f"Last purchase: {fmt_date(u['last_reseller_purchase'])}</blockquote>"
        )
        kb = types.InlineKeyboardMarkup(row_width=1)
        kb.add(emojis.button("🚫 Revoke Reseller Status", callback_data=f"adm_res_revoke_{target}"))
        kb.add(
            emojis.button("⬅️ Back", callback_data="adm_res_list_0", emoji_id="5255703720078879038", lead="⬅️"),
            emojis.button("🏠 Main Menu", callback_data="main_menu", emoji_id="5042022053356504092", lead="🏠"),
        )
        try:
            bot.edit_message_text(text, admin_uid, msg_id, reply_markup=kb)
        except Exception:
            bot.send_message(admin_uid, text, reply_markup=kb)

    @bot.callback_query_handler(func=lambda c: c.data.startswith("adm_res_revoke_"))
    @admin_only
    def cb_adm_res_revoke(call: types.CallbackQuery):
        target = int(call.data[len("adm_res_revoke_"):])
        db.set_reseller(target, False)
        bot.answer_callback_query(call.id, "Reseller status revoked.")
        u = db.get_user(target)
        try:
            kb_user = types.InlineKeyboardMarkup()
            kb_user.add(emojis.button("✖️ Dismiss", callback_data="dismiss_msg"))
            bot.send_message(
                target,
                "<b>Reseller access removed</b>\n\n"
                "Your reseller status has been removed by an admin. "
                "You can request access again anytime.",
                reply_markup=kb_user,
            )
        except Exception:
            pass
        admin_report(
            bot, "RESELLER REVOKED (manual)",
            [
                ("", "User", user_link(target, u["username"] if u else None)),
                ("", "By", user_link(call.from_user.id, call.from_user.username)),
            ],
            icon="",
        )
        _render_home(call.from_user.id, call.message.message_id)
