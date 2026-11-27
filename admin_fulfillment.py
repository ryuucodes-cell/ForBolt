"""Admin: fulfillment queue for pre-order orders."""

import html
import telebot
from telebot import types

def _esc(s: str) -> str:
    return html.escape(str(s))

import db
import emojis
from utils import (
    admin_only, back_kb, fmt_money, fmt_mmk, user_link, fmt_date,
    set_state, get_state, clear_state, is_admin,
    format_credential_block, silent_delete, edit_or_send,
)
from msg_templates import (
    preorder_delivered,
    admin_action_success,
)

_MODE_LABEL = {
    "preorder": "Pre-order",
}


def _mode_of(order) -> str:
    # Mode is frozen on the order at purchase; never inferred from the product's
    # *current* mode (which the admin may have changed since).
    return db.get_order_fulfillment(order)


def register(bot: telebot.TeleBot):

    def _show_queue(uid: int, msg_id: int):
        orders = db.list_fulfillment_orders(limit=30)
        kb = types.InlineKeyboardMarkup(row_width=1)
        for o in orders:
            mode = _mode_of(o)  # frozen on the order, not the product's current mode
            label = _MODE_LABEL.get(mode, mode)
            status = f"{emojis.emoji('pending', '⏳')} info needed" if o["status"] == "awaiting_info" else "🔧 working"
            kb.add(emojis.button(
                f"#{o['order_id']} · {label} · {o['product_name']} · {status}",
                callback_data=f"adm_fulfill_{o['order_id']}",
            ))
        kb.add(
            emojis.button("Back", callback_data="admin_panel", emoji_id="5255703720078879038", lead="⬅️"),
            emojis.button("Main Menu", callback_data="main_menu", emoji_id="5042022053356504092", lead="🏠"),
        )
        text = ("<b>Fulfillment Queue</b>\n\nTap an order to work on it:"
                if orders else
                f"<b>Fulfillment Queue</b>\n\nAll clear — nothing waiting. {emojis.emoji('party', '🎉')}")
        try:
            bot.edit_message_text(text, uid, msg_id, reply_markup=kb)
        except Exception:
            bot.send_message(uid, text, reply_markup=kb)

    def _show_detail(uid: int, msg_id: int, oid: int):
        order = db.get_order(oid)
        if not order:
            try:
                bot.edit_message_text("Order not found.", uid, msg_id,
                                      reply_markup=back_kb("adm_fulfill"))
            except Exception:
                pass
            return
        mode  = _mode_of(order)
        label = _MODE_LABEL.get(mode, mode)
        info  = db.get_manual_info(order)
        lines = [
            f"<b>{label} — Order #{oid}</b>\n",
            f"Product: <b>{_esc(order['product_name'])}</b>",
            f"Buyer: {user_link(order['telegram_id'], order['username'])}",
            f"Amount: <b>{fmt_money(order['amount_mmk'])}</b>",
            f"Status: <i>{order['status']}</i>",
            f"Date: {fmt_date(order['created_at'])}",
        ]
        if info:
            lines.append("\n<b>Customer info:</b>")
            for k, v in info.items():
                lines.append(f"• <b>{_esc(k)}:</b> <code>{_esc(v)}</code>")
        kb = types.InlineKeyboardMarkup(row_width=1)
        if order["status"] in ("awaiting_info", "awaiting_fulfillment"):
            kb.add(emojis.button("📤 Deliver Credential", callback_data=f"adm_fulfill_cred_{oid}",
                                 emoji_id="5445355530111437729", lead="📤"))
            kb.add(emojis.button("♻️ Refund", callback_data=f"adm_order_refund_{oid}",
                                 emoji_id="5226702984204797593", lead="♻️"))
        kb.add(
            emojis.button("Back", callback_data="adm_fulfill", emoji_id="5255703720078879038", lead="⬅️"),
            emojis.button("Main Menu", callback_data="main_menu", emoji_id="5042022053356504092", lead="🏠"),
        )
        try:
            bot.edit_message_text("\n".join(lines), uid, msg_id, reply_markup=kb)
        except Exception:
            bot.send_message(uid, "\n".join(lines), reply_markup=kb)

    @bot.callback_query_handler(func=lambda c: c.data == "adm_fulfill")
    @admin_only
    def cb_adm_fulfill(call: types.CallbackQuery):
        _show_queue(call.from_user.id, call.message.message_id)
        bot.answer_callback_query(call.id)

    @bot.callback_query_handler(
        func=lambda c: c.data.startswith("adm_fulfill_") and c.data[len("adm_fulfill_"):].isdigit())
    @admin_only
    def cb_adm_fulfill_detail(call: types.CallbackQuery):
        oid = int(call.data[len("adm_fulfill_"):])
        _show_detail(call.from_user.id, call.message.message_id, oid)
        bot.answer_callback_query(call.id)

    @bot.callback_query_handler(func=lambda c: c.data.startswith("adm_fulfill_cred_"))
    @admin_only
    def cb_adm_fulfill_cred(call: types.CallbackQuery):
        oid = int(call.data[len("adm_fulfill_cred_"):])
        order = db.get_order(oid)
        if not order or order["status"] not in ("awaiting_info", "awaiting_fulfillment"):
            bot.answer_callback_query(call.id, "This order is already handled.", show_alert=True)
            return
        set_state(call.from_user.id, step="fulfill_credential", order_id=oid)
        _p = db.get_product(order["product_id"])
        _fmt = (_p["credential_format"] or "").strip() if _p else ""
        _hint = (f"\nExpected format: <code>{_esc(_fmt)}</code> — e.g. <code>{_esc(_fmt.replace('|', ' | '))}</code>, one credential per line" if _fmt else "")
        prompt = bot.send_message(
            call.from_user.id,
            f"<b>Deliver Credential — Order #{oid}</b>{_hint}\n\n"
            f"Send the credential now (one per line for bulk orders).\n"
            f"Send /cancel to abort.",
        )
        # remember the prompt so we can clean up; best-effort
        try:
            st = get_state(call.from_user.id)
            set_state(call.from_user.id, step="fulfill_credential", order_id=oid,
                      prompt_msg_id=prompt.message_id)
        except Exception:
            pass
        bot.answer_callback_query(call.id)

    @bot.message_handler(func=lambda m: get_state(m.from_user.id).get("step") == "fulfill_credential")
    def msg_fulfill_credential(msg: types.Message):
        if not is_admin(msg.from_user.id):
            return
        uid = msg.from_user.id
        text = (msg.text or "").strip()
        if text == "/cancel":
            clear_state(uid)
            bot.send_message(uid, "Cancelled — no credential sent.")
            return
        st = get_state(uid)
        oid = st.get("order_id")
        prompt_id = st.get("prompt_msg_id")
        order = db.get_order(oid) if oid else None
        if not order or order["status"] not in ("awaiting_info", "awaiting_fulfillment"):
            clear_state(uid)
            bot.send_message(uid, "This order is already handled.")
            return
        if not text:
            bot.send_message(uid, "Please send the credential as a text message.")
            return
        # dynamic: remove the pasted credential from chat once read
        silent_delete(bot, uid, msg.message_id)
        p = db.get_product(order["product_id"])
        fmt = (p["credential_format"] or "").strip() if p else ""
        db.set_order_credential(oid, text)
        db.set_order_status(oid, "fulfilled")
        clear_state(uid)
        try:
            bot.send_message(order["telegram_id"],
                             preorder_delivered(_esc(order["product_name"]),
                                                format_credential_block(text, fmt)))
        except Exception:
            pass
        edit_or_send(bot, uid, prompt_id, admin_action_success(
            "Credential delivered", f"Order #{oid} — customer notified."))
