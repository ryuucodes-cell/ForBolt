"""Admin: deposit list, approve, reject."""

import telebot
from telebot import types

import db
import emojis
from utils import fmt_mmk, admin_only, back_kb, notify_admin, set_state, get_state, clear_state, silent_delete, admin_report, fmt_money, user_link, fmt_datetime, log_topup
from msg_templates import deposit_approved, deposit_rejected, admin_deposit_approved_report, admin_deposit_rejected_report

_METHOD_LABELS = {
    "kbzpay":   "KBZPay",
    "wavepay":  "WavePay",
    "cbpay":    "CB Pay",
    "uabpay":   "UAB Pay",
    "ayapay":   "AYA Pay",
    "binance":  "Binance Pay",
}

def _method_label(method: str) -> str:
    return _METHOD_LABELS.get(method.lower(), method.upper())


def register(bot: telebot.TeleBot):

    @bot.callback_query_handler(func=lambda c: c.data == "adm_deposits" or c.data.startswith("adm_deposits_"))
    @admin_only
    def cb_adm_deposits(call: types.CallbackQuery):
        parts  = call.data.split("_")
        # adm_deposits or adm_deposits_{filter}_{offset}
        # filter: pending | all
        if len(parts) >= 4:
            filt   = parts[2]
            offset = int(parts[3])
        elif len(parts) == 3:
            filt   = parts[2]
            offset = 0
        else:
            filt   = "pending"
            offset = 0

        PAGE   = 10
        status = None if filt == "all" else "pending"
        deps   = db.list_deposits(status=status, limit=PAGE, offset=offset)
        total  = db.count_deposits(status=status)

        kb = types.InlineKeyboardMarkup(row_width=1)
        status_icon = {"pending": f"{emojis.emoji('pending', '⏳')}", "approved": f"{emojis.emoji('success', '✅')}", "rejected": f"{emojis.emoji('rejected', '❌')}"}
        if deps:
            for d in deps:
                icon = status_icon.get(d["status"], f"{emojis.emoji('question', '❓')}")
                kb.add(emojis.button(
                    f"{icon} #{d['deposit_id']} @{d['username'] or d['telegram_id']} — {fmt_mmk(d['amount_mmk'])} · {_method_label(d['method'])}",
                    callback_data=f"adm_dep_{d['deposit_id']}",
                ))
        else:
            kb.add(emojis.button("✅ No deposits here" if filt == "all" else "No pending deposits",
                callback_data="noop",
            ))

        # pagination row
        nav = []
        if offset > 0:
            nav.append(emojis.button("Prev", callback_data=f"adm_deposits_{filt}_{offset - PAGE}", emoji_id="5255703720078879038", lead="⬅️"))
        if offset + PAGE < total:
            nav.append(emojis.button("Next", callback_data=f"adm_deposits_{filt}_{offset + PAGE}", emoji_id="5253767677670862169", lead="➡️"))
        if nav:
            kb.add(*nav)

        # toggle between pending / all
        toggle_label = "Show All" if filt == "pending" else "Pending Only"
        toggle_cb    = f"adm_deposits_all_0" if filt == "pending" else f"adm_deposits_pending_0"
        kb.add(emojis.button(toggle_label, callback_data=toggle_cb, emoji_id="5039649904264217620"))
        kb.add(
            emojis.button("Back", callback_data="admin_panel", emoji_id="5255703720078879038", lead="⬅️"),
            emojis.button("Main Menu",  callback_data="main_menu", emoji_id="5042022053356504092", lead="🏠"),
        )

        shown = offset + len(deps)
        if filt == "pending":
            title = f"<b>Pending Deposits</b> — {total} waiting" if total else "<b>Pending Deposits</b> — all clear"
        else:
            title = f"<b>All Deposits</b> ({shown}/{total})"

        bot.edit_message_text(title, call.from_user.id, call.message.message_id, reply_markup=kb)
        bot.answer_callback_query(call.id)

    @bot.callback_query_handler(func=lambda c: c.data.startswith("adm_dep_") and len(c.data.split("_")) == 3)
    @admin_only
    def cb_adm_dep_detail(call: types.CallbackQuery):
        dep_id = int(call.data.split("_")[2])
        d      = db.get_deposit(dep_id)
        if not d:
            bot.answer_callback_query(call.id, "Not found.")
            return
        text = (
            f"<b>Deposit #{dep_id}</b>\n\n"
            f"User: {user_link(d['telegram_id'], d['username'])}\n"
            f"Method: {_method_label(d['method'])}\n"
            f"Amount: <b>{fmt_money(d['amount_mmk'])}</b>\n"
            f"Status: {d['status'].capitalize()}\n"
            f"Date: {fmt_datetime(d['created_at'])}"
        )
        kb = types.InlineKeyboardMarkup(row_width=2)
        if d["status"] == "pending":
            kb.add(
                emojis.button("Approve", callback_data=f"adm_dep_ok_{dep_id}", emoji_id="5039844895779455925", lead="✅"),
                emojis.button("Reject",  callback_data=f"adm_dep_no_{dep_id}", emoji_id="5210952531676504517", lead="❌"),
            )
        kb.add(
            emojis.button("Back", callback_data="adm_deposits", emoji_id="5255703720078879038", lead="⬅️"),
            emojis.button("Main Menu",  callback_data="main_menu", emoji_id="5042022053356504092", lead="🏠"),
        )

        if d["screenshot_file_id"]:
            try:
                bot.send_photo(
                    call.from_user.id,
                    d["screenshot_file_id"],
                    caption=text,
                    reply_markup=kb,
                )
                bot.delete_message(call.from_user.id, call.message.message_id)
            except Exception:
                bot.edit_message_text(text, call.from_user.id, call.message.message_id, reply_markup=kb)
        else:
            bot.edit_message_text(text, call.from_user.id, call.message.message_id, reply_markup=kb)
        bot.answer_callback_query(call.id)

    @bot.callback_query_handler(func=lambda c: c.data.startswith("adm_dep_ok_"))
    @admin_only
    def cb_adm_dep_approve(call: types.CallbackQuery):
        dep_id = int(call.data[len("adm_dep_ok_"):])
        approved = db.approve_deposit_atomic(dep_id, approved_by=call.from_user.id)
        if not approved:
            existing = db.get_deposit(dep_id)
            bot.answer_callback_query(call.id, f"Already {existing['status'] if existing else 'not found'}.")
            return
        d, bonus = approved
        try:
            bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)
        except Exception:
            pass
        bot.answer_callback_query(call.id, "Approved.")
        kb_user = types.InlineKeyboardMarkup()
        kb_user.add(emojis.button("Dismiss", callback_data="dismiss_msg", emoji_id="5210952531676504517", lead="✖️"))
        new_bal = db.get_balance(d["telegram_id"])
        try:
            bot.send_message(
                d["telegram_id"],
                deposit_approved(fmt_money(d['amount_mmk']), fmt_money(new_bal),
                                 fmt_money(bonus) if bonus else None),
                parse_mode='HTML',
                reply_markup=kb_user,
            )
        except Exception:
            pass  # user may have blocked the bot — balance is already credited
        notify_admin(
            bot,
            admin_deposit_approved_report(
                user_link=user_link(d['telegram_id'], d['username']),
                method=_method_label(d['method']),
                amount=fmt_money(d['amount_mmk']),
                deposit_id=dep_id,
                approved_by=user_link(call.from_user.id, call.from_user.username)
            )
        )
        # public top-up notice (masked name) for the vouch channel
        log_topup(bot, d["amount_mmk"], _method_label(d["method"]), approved=True, username=d["username"])

    @bot.callback_query_handler(func=lambda c: c.data.startswith("adm_dep_no_"))
    @admin_only
    def cb_adm_dep_reject(call: types.CallbackQuery):
        dep_id = int(call.data[len("adm_dep_no_"):])
        d      = db.get_deposit(dep_id)
        if not d or d["status"] != "pending":
            bot.answer_callback_query(call.id, f"Already {d['status'] if d else 'not found'}.")
            return
        reason_prompt = (
            f"Declining deposit #{dep_id} — {fmt_mmk(d['amount_mmk'])}\n\n"
            f"Enter a reason for the customer, or send <code>-</code> to skip:"
        )
        try:
            # works when the alert is a plain text message (deposit list detail)
            prompt = bot.edit_message_text(
                reason_prompt,
                call.from_user.id, call.message.message_id,
                reply_markup=back_kb("adm_deposits"),
            )
            prompt_id = prompt.message_id
        except Exception:
            # alert is a photo (screenshot notification) — can't edit text; drop buttons and send new
            try:
                bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)
            except Exception:
                pass
            prompt = bot.send_message(call.from_user.id, reason_prompt, reply_markup=back_kb("adm_deposits"))
            prompt_id = prompt.message_id
        set_state(call.from_user.id, step="adm_dep_reject_reason", adm_reject_dep_id=dep_id, prompt_msg_id=prompt_id)
        bot.answer_callback_query(call.id)

    @bot.message_handler(func=lambda m: get_state(m.from_user.id).get("step") == "adm_dep_reject_reason")
    @admin_only
    def msg_adm_dep_reject_reason(msg: types.Message):
        uid       = msg.from_user.id
        st        = get_state(uid)
        dep_id    = st.get("adm_reject_dep_id")
        prompt_id = st.get("prompt_msg_id")
        reason    = msg.text.strip()
        clear_state(uid)
        silent_delete(bot, uid, msg.message_id)

        note = None if reason == "-" else reason
        d = db.reject_deposit_atomic(dep_id, rejected_by=uid, note=note)
        if not d:
            try:
                bot.edit_message_text("Deposit no longer pending.", uid, prompt_id, reply_markup=back_kb("adm_deposits"))
            except Exception:
                pass
            return

        support_url = db.cfg_get("support_url", "")
        kb_user = types.InlineKeyboardMarkup(row_width=1)
        kb_user.add(emojis.button("Try Again", callback_data="topup_menu", emoji_id="5445353829304387411", lead="💳"))
        if support_url:
            kb_user.add(emojis.button("Contact Support", url=support_url, emoji_id="5039532814865794117", lead="💬"))
        kb_user.add(emojis.button("Dismiss", callback_data="dismiss_msg", emoji_id="5210952531676504517", lead="✖️"))
        try:
            bot.send_message(
                d["telegram_id"],
                deposit_rejected(
                    method=_method_label(d['method']),
                    amount_mmk=fmt_mmk(d['amount_mmk']),
                    deposit_id=dep_id,
                    reason=note
                ),
                parse_mode='HTML',
                reply_markup=kb_user
            )
        except Exception:
            pass

        notify_admin(
            bot,
            admin_deposit_rejected_report(
                user_link=user_link(d['telegram_id'], d['username']),
                method=_method_label(d['method']),
                amount=fmt_money(d['amount_mmk']),
                deposit_id=dep_id,
                rejected_by=user_link(uid, msg.from_user.username),
                reason=note
            )
        )
        # public rejection notice (masked name, no reason shown publicly)
        log_topup(bot, d["amount_mmk"], _method_label(d["method"]), approved=False, username=d["username"])
        try:
            bot.edit_message_text(
                f"Deposit #{dep_id} rejected." + (f"\nReason sent: {note}" if note else ""),
                uid, prompt_id,
                reply_markup=back_kb("adm_deposits"),
            )
        except Exception:
            bot.send_message(uid, f"Deposit #{dep_id} rejected.", reply_markup=back_kb("adm_deposits"))
