"""Admin: orders view with detail and refund."""

import html
import telebot
from telebot import types

def _esc(s: str) -> str:
    return html.escape(str(s))

import db
import emojis
from utils import fmt_mmk, admin_only, back_kb, notify_admin, admin_report, fmt_money, user_link, fmt_date, format_credential, format_credential_block, log_to_channel, mask_name
from msg_templates import public_vouch_refund


def register(bot: telebot.TeleBot):

    @bot.callback_query_handler(func=lambda c: c.data == "adm_orders" or (c.data.startswith("adm_orders_") and c.data[len("adm_orders_"):].isdigit()))
    @admin_only
    def cb_adm_orders(call: types.CallbackQuery):
        parts  = call.data.split("_")
        offset = int(parts[2]) if len(parts) > 2 else 0
        orders = db.list_orders(limit=10, offset=offset)
        total  = db.count_orders()

        kb = types.InlineKeyboardMarkup(row_width=1)
        for o in orders:
            kb.add(emojis.button(
                f"#{o['order_id']} · @{o['username'] or o['telegram_id']} · {o['product_name']} · {fmt_mmk(o['amount_mmk'])}",
                callback_data=f"adm_order_{o['order_id']}",
            ))

        nav = []
        if offset > 0:
            nav.append(emojis.button("Prev", callback_data=f"adm_orders_{offset - 10}", emoji_id="5255703720078879038", lead="⬅️"))
        if offset + 10 < total:
            nav.append(emojis.button("Next", callback_data=f"adm_orders_{offset + 10}", emoji_id="5253767677670862169", lead="➡️"))
        if nav:
            kb.add(*nav)
        pending_reports = db.count_order_reports(status="pending")
        kb.add(emojis.button(f"Problem Reports ({pending_reports})", callback_data="adm_reports_0", emoji_id="5039665997506675838", lead="⚠️"))
        kb.add(
            emojis.button("Back", callback_data="admin_panel", emoji_id="5255703720078879038", lead="⬅️"),
            emojis.button("Main Menu",  callback_data="main_menu", emoji_id="5042022053356504092", lead="🏠"),
        )

        shown = offset + len(orders)
        bot.edit_message_text(
            f"<b>Orders</b> ({shown}/{total})\n\nTap an order for details:",
            call.from_user.id, call.message.message_id,
            reply_markup=kb,
        )
        bot.answer_callback_query(call.id)

    @bot.callback_query_handler(func=lambda c: c.data.startswith("adm_order_") and c.data[len("adm_order_"):].isdigit())
    @admin_only
    def cb_adm_order_detail(call: types.CallbackQuery):
        oid   = int(call.data[len("adm_order_"):])
        _show_order_detail(call.from_user.id, call.message.message_id, oid)
        bot.answer_callback_query(call.id)

    def _show_order_detail(admin_uid: int, msg_id: int, oid: int):
        order = db.get_order(oid)
        if not order:
            bot.send_message(admin_uid, "Order not found.")
            return
        prod      = db.get_product(order["product_id"])
        cred_fmt  = prod["credential_format"] if prod else ""
        # BLOCK formatter: a stored order credential can hold several
        # credentials (one per line, bulk deliveries). The singular
        # formatter splits the whole blob on '|' and mangles lines 2+.
        cred_text = format_credential_block(order["credential"], cred_fmt)

        refunded = order["status"] == "refunded"
        text = (
            f"<b>Order #{oid}</b>{' — REFUNDED' if refunded else ''}\n\n"
            f"User: {user_link(order['telegram_id'], order['username'])}\n"
            f"Product: {_esc(order['product_name'])}\n"
            f"Paid: <b>{fmt_money(order['amount_mmk'])}</b>\n"
            f"Date: {fmt_date(order['created_at'])}\n\n"
            f"{cred_text}"
        )
        kb = types.InlineKeyboardMarkup(row_width=2)
        if not refunded:
            kb.add(emojis.button("Refund", callback_data=f"adm_order_refund_{oid}", emoji_id="5226702984204797593", lead="♻️"))
        kb.add(
            emojis.button("Back", callback_data="adm_orders", emoji_id="5255703720078879038", lead="⬅️"),
            emojis.button("Main Menu",  callback_data="main_menu", emoji_id="5042022053356504092", lead="🏠"),
        )
        bot.edit_message_text(text, admin_uid, msg_id, reply_markup=kb)

    @bot.callback_query_handler(func=lambda c: c.data.startswith("adm_order_refund_") and c.data[len("adm_order_refund_"):].isdigit())
    @admin_only
    def cb_adm_order_refund(call: types.CallbackQuery):
        oid   = int(call.data[len("adm_order_refund_"):])
        order = db.get_order(oid)
        if not order:
            bot.answer_callback_query(call.id, "Order not found.")
            return
        if order["status"] == "refunded":
            bot.answer_callback_query(call.id, "Already refunded.", show_alert=True)
            return

        kb = types.InlineKeyboardMarkup(row_width=2)
        kb.add(
            emojis.button("Yes, Refund", callback_data=f"adm_order_refund_confirm_{oid}", emoji_id="5039844895779455925", lead="✅"),
            emojis.button("Cancel",       callback_data=f"adm_order_{oid}", emoji_id="5210952531676504517", lead="❌"),
        )
        bot.edit_message_text(
            f"<b>Confirm Refund</b>\n\n"
            f"Order #{oid} — {order['product_name']}\n"
            f"Refund amount: <b>{fmt_mmk(order['amount_mmk'])}</b> → @{order['username'] or order['telegram_id']}\n\n"
            f"This will credit the amount back to the user's balance.",
            call.from_user.id, call.message.message_id, reply_markup=kb,
        )
        bot.answer_callback_query(call.id)

    @bot.callback_query_handler(func=lambda c: c.data.startswith("adm_order_refund_confirm_"))
    @admin_only
    def cb_adm_order_refund_confirm(call: types.CallbackQuery):
        oid   = int(call.data[len("adm_order_refund_confirm_"):])
        order = db.refund_order_atomic(oid)
        if not order:
            bot.answer_callback_query(call.id, "Already refunded or not found.", show_alert=True)
            return

        bot.answer_callback_query(call.id, "Refunded!")

        admin_report(
            bot,
            "ORDER REFUNDED",
            [
                ("", "Order",  f"#{oid} — {_esc(order['product_name'])}"),
                ("", "User",   user_link(order['telegram_id'], order['username'])),
                ("", "Amount", fmt_money(order['amount_mmk'])),
                ("", "By",     user_link(call.from_user.id, call.from_user.username)),
            ],
            icon="",
        )

        # notify user
        try:
            kb_user = types.InlineKeyboardMarkup()
            kb_user.add(emojis.button("Dismiss", callback_data="dismiss_msg", emoji_id="5210952531676504517", lead="✖️"))
            bot.send_message(
                order["telegram_id"],
                f"<b>Refund Processed</b>\n\n"
                f"Order #{oid} ({order['product_name']}) has been refunded.\n"
                f"<b>{fmt_mmk(order['amount_mmk'])}</b> added back to your balance.",
                reply_markup=kb_user,
            )
        except Exception:
            pass

        _show_order_detail(call.from_user.id, call.message.message_id, oid)

    # ── problem reports (warranty) ────────────────────────────────────────────

    def _dismiss_kb():
        kb = types.InlineKeyboardMarkup()
        kb.add(emojis.button("Dismiss", callback_data="dismiss_msg", emoji_id="5210952531676504517", lead="✖️"))
        return kb

    def _clear_buttons(call):
        """Remove action buttons from the alert/list message after an action."""
        try:
            bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id,
                                          reply_markup=None)
        except Exception:
            pass

    @bot.callback_query_handler(func=lambda c: c.data.startswith("adm_reports_"))
    @admin_only
    def cb_adm_reports(call: types.CallbackQuery):
        offset  = int(call.data[len("adm_reports_"):] or 0)
        reports = db.list_order_reports(status="pending", limit=10, offset=offset)
        total   = db.count_order_reports(status="pending")
        kb = types.InlineKeyboardMarkup(row_width=1)
        for r in reports:
            kb.add(emojis.button(
                f"{emojis.emoji('pending', '⏳')} #{r['report_id']} · order {r['order_id']} · @{r['username'] or r['telegram_id']}",
                callback_data=f"adm_repv_{r['report_id']}",
            ))
        if not reports:
            kb.add(emojis.button("No pending reports", callback_data="noop", emoji_id="5039844895779455925", lead="✅"))
        nav = []
        if offset > 0:
            nav.append(emojis.button("Prev", callback_data=f"adm_reports_{offset - 10}", emoji_id="5255703720078879038", lead="⬅️"))
        if offset + 10 < total:
            nav.append(emojis.button("Next", callback_data=f"adm_reports_{offset + 10}", emoji_id="5253767677670862169", lead="➡️"))
        if nav:
            kb.add(*nav)
        kb.add(
            emojis.button("Back", callback_data="adm_orders", emoji_id="5255703720078879038", lead="⬅️"),
            emojis.button("Main Menu", callback_data="main_menu", emoji_id="5042022053356504092", lead="🏠"),
        )
        bot.edit_message_text(
            f"<b>Problem Reports</b> — {total} pending\n\nOldest first. Tap one to handle it:",
            call.from_user.id, call.message.message_id, reply_markup=kb,
        )
        bot.answer_callback_query(call.id)

    @bot.callback_query_handler(func=lambda c: c.data.startswith("adm_repv_"))
    @admin_only
    def cb_adm_report_view(call: types.CallbackQuery):
        rep_id = int(call.data[len("adm_repv_"):])
        r = db.get_order_report(rep_id)
        if not r:
            bot.answer_callback_query(call.id, "Report not found.")
            return
        order = db.get_order(r["order_id"])
        stock_left = db.stock_count(order["product_id"]) if order else 0
        text = (
            f"<b>Problem Report #{rep_id}</b> — {r['status'].capitalize()}\n\n"
            f"<blockquote>Order: #{r['order_id']} — {_esc(order['product_name']) if order else '?'}\n"
            f"User: {user_link(r['telegram_id'], r['username'])}\n"
            f"Paid: {fmt_money(order['amount_mmk']) if order else '—'}\n"
            f"Stock left for replacement: <b>{stock_left}</b>\n"
            f"Reported: {fmt_date(r['created_at'])}</blockquote>"
        )
        if r["message"]:
            text += f"\n\n<blockquote>{_esc(r['message'])}</blockquote>"
        if order:
            prod = db.get_product(order["product_id"])
            text += "\n\nDelivered item:\n" + format_credential(
                order["credential"], prod["credential_format"] if prod else "")
        kb = types.InlineKeyboardMarkup(row_width=3)
        if r["status"] == "pending":
            kb.add(
                emojis.button("Replace", callback_data=f"adm_rep_re_{rep_id}", emoji_id="5226702984204797593", lead="🔁"),
                emojis.button("Refund",  callback_data=f"adm_rep_rf_{rep_id}", emoji_id="5226702984204797593", lead="♻️"),
                emojis.button("Dismiss", callback_data=f"adm_rep_dm_{rep_id}", emoji_id="5210952531676504517", lead="✖️"),
            )
        kb.add(
            emojis.button("Back", callback_data="adm_reports_0", emoji_id="5255703720078879038", lead="⬅️"),
            emojis.button("Main Menu", callback_data="main_menu", emoji_id="5042022053356504092", lead="🏠"),
        )
        bot.edit_message_text(text, call.from_user.id, call.message.message_id, reply_markup=kb)
        bot.answer_callback_query(call.id)

    @bot.callback_query_handler(func=lambda c: c.data.startswith("adm_rep_re_"))
    @admin_only
    def cb_adm_report_replace(call: types.CallbackQuery):
        rep_id = int(call.data[len("adm_rep_re_"):])
        r = db.resolve_order_report_atomic(rep_id, "replaced", call.from_user.id)
        if not r:
            bot.answer_callback_query(call.id, "Already handled by another admin.", show_alert=True)
            return
        order = db.get_order(r["order_id"])
        if not order:
            db.reopen_order_report(rep_id)
            bot.answer_callback_query(call.id, "Order not found.", show_alert=True)
            return
        cred = db.claim_stock(order["product_id"], order["telegram_id"])
        if cred is None:
            db.reopen_order_report(rep_id)
            bot.answer_callback_query(
                call.id,
                "No stock left to replace with. Restock the product, then tap Replace again.",
                show_alert=True,
            )
            return
        new_oid = db.create_order(
            order["telegram_id"], order["username"], order["product_id"],
            f"{order['product_name']} (replacement)", 0, cred,
            payment_method="replacement",
        )
        prod = db.get_product(order["product_id"])
        cred_text = format_credential(cred, prod["credential_format"] if prod else "")
        try:
            bot.send_message(
                r["telegram_id"],
                f"<b>Replacement Delivered</b> — Order #{r['order_id']}\n\n"
                f"Sorry about the trouble! Here's a fresh <b>{_esc(order['product_name'])}</b> "
                f"(new order #{new_oid}, also saved in your My Orders):\n\n{cred_text}",
                reply_markup=_dismiss_kb(),
            )
        except Exception:
            pass
        _clear_buttons(call)
        bot.answer_callback_query(call.id, "Replaced and delivered!")
        admin_report(bot, "REPORT RESOLVED — REPLACED", [
            ("", "Report", f"#{rep_id} (order #{r['order_id']})"),
            ("", "Product", _esc(order["product_name"])),
            ("", "User", user_link(r["telegram_id"], r["username"])),
            ("", "New order", f"#{new_oid}"),
            ("", "By", user_link(call.from_user.id, call.from_user.username)),
        ])

    @bot.callback_query_handler(func=lambda c: c.data.startswith("adm_rep_rf_"))
    @admin_only
    def cb_adm_report_refund(call: types.CallbackQuery):
        rep_id = int(call.data[len("adm_rep_rf_"):])
        r = db.resolve_order_report_atomic(rep_id, "refunded", call.from_user.id)
        if not r:
            bot.answer_callback_query(call.id, "Already handled by another admin.", show_alert=True)
            return
        order = db.refund_order_atomic(r["order_id"])
        if not order:
            # order was already refunded earlier — report is resolved either way
            _clear_buttons(call)
            bot.answer_callback_query(call.id, "Order was already refunded — report closed.", show_alert=True)
            return
        try:
            bot.send_message(
                r["telegram_id"],
                f"<b>Refund Processed</b> — Order #{r['order_id']}\n\n"
                f"Sorry about the trouble! <b>{fmt_mmk(order['amount_mmk'])}</b> has been "
                f"added back to your balance.",
                reply_markup=_dismiss_kb(),
            )
        except Exception:
            pass
        _clear_buttons(call)
        bot.answer_callback_query(call.id, "Refunded!")
        admin_report(bot, "REPORT RESOLVED — REFUNDED", [
            ("", "Report", f"#{rep_id} (order #{r['order_id']})"),
            ("", "Product", _esc(order["product_name"])),
            ("", "User", user_link(r["telegram_id"], r["username"])),
            ("", "Amount", fmt_money(order["amount_mmk"])),
            ("", "By", user_link(call.from_user.id, call.from_user.username)),
        ])
        # public refund notice (masked buyer name) for the vouch channel
        try:
            log_to_channel(bot, public_vouch_refund(
                buyer=mask_name(r["username"]),
                amount=fmt_money(order["amount_mmk"]),
                order_id=r["order_id"],
            ))
        except Exception:
            pass

    @bot.callback_query_handler(func=lambda c: c.data.startswith("adm_rep_dm_"))
    @admin_only
    def cb_adm_report_dismiss(call: types.CallbackQuery):
        rep_id = int(call.data[len("adm_rep_dm_"):])
        r = db.resolve_order_report_atomic(rep_id, "dismissed", call.from_user.id)
        if not r:
            bot.answer_callback_query(call.id, "Already handled by another admin.", show_alert=True)
            return
        try:
            bot.send_message(
                r["telegram_id"],
                f"<b>Report Reviewed</b> — Order #{r['order_id']}\n\n"
                "We checked your report and the item appears to be working. Please "
                "try again carefully — if it still fails, contact Support directly.",
                reply_markup=_dismiss_kb(),
            )
        except Exception:
            pass
        _clear_buttons(call)
        bot.answer_callback_query(call.id, "Dismissed.")
        admin_report(bot, "REPORT DISMISSED", [
            ("", "Report", f"#{rep_id} (order #{r['order_id']})"),
            ("", "User", user_link(r["telegram_id"], r["username"])),
            ("", "By", user_link(call.from_user.id, call.from_user.username)),
        ])
