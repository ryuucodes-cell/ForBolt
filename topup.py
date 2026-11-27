"""Top-up flow: manual deposits (KBZPay / WavePay / CB Pay / Binance Pay)."""

import html

def _esc(s: str) -> str:
    return html.escape(str(s))

import telebot
from telebot import types

import db
import emojis
import binance_pay
import msg_templates
from utils import fmt_mmk, parse_mmk, usdt_to_send, fmt_usdt, mmk_from_usdt, _rate, mmk_str, back_kb, set_state, get_state, clear_state, notify_admin, silent_delete, check_rate_limit, admin_ids, fmt_money, ts, log_topup


def register(bot: telebot.TeleBot):

    # ── top-up menu ───────────────────────────────────────────────────────────

    @bot.callback_query_handler(func=lambda c: c.data == "topup_menu")
    def cb_topup_menu(call: types.CallbackQuery):
        uid = call.from_user.id
        # clear any in-progress deposit state when user returns to menu
        clear_state(uid)
        kb = types.InlineKeyboardMarkup(row_width=1)

        # (ekey, lead, label): premium icon on the BUTTON only — message text stays plain
        manual_methods = [
            ("kbzpay",   "pm_kbzpay",  "🏦", "KBZPay"),
            ("wavepay",  "pm_wavepay", "🌊", "WavePay"),
            ("cbpay",    "pm_cbpay",   "🏛", "CB Pay"),
            ("ayapay",   "pm_ayapay",  "💳", "AYA Pay"),
            ("uabpay",   "pm_uabpay",  "🏧", "UAB Pay"),
            ("binance",  "pm_binance", "🟡", "Binance Pay (USDT)"),
        ]
        for key, ekey, lead, label in manual_methods:
            if db.cfg_get(f"{key}_enabled", "0") == "1":
                kb.add(emojis.button(label, callback_data=f"topup_{key}",
                                     emoji_id=emojis.MESSAGE_EMOJI_IDS.get(ekey),
                                     lead=lead))

        if kb.keyboard:
            kb.add(emojis.button("🏠 Main Menu", callback_data="main_menu", emoji_id="5042022053356504092", lead="🏠"))
            bal = db.get_balance(uid)
            bot.edit_message_text(
                f"<b>Top Up Your Balance</b>\n\n"
                f"Current balance: <b>{fmt_money(bal)}</b>\n\n"
                f"Please choose a payment method below.\n"
                f"<i>Manual payments are added after a quick check by our team.</i>",
                uid, call.message.message_id,
                reply_markup=kb,
            )
        else:
            support_url = db.cfg_get("support_url", "")
            kb2 = types.InlineKeyboardMarkup(row_width=1)
            if support_url:
                kb2.add(emojis.button("💬 Contact Support", url=support_url, emoji_id="5039532814865794117", lead="💬"))
            kb2.add(emojis.button("🏠 Main Menu", callback_data="main_menu", emoji_id="5042022053356504092", lead="🏠"))
            bot.edit_message_text(
                "<b>No Payment Methods Available</b>\n\n"
                "Please check back shortly or contact our support team.",
                uid, call.message.message_id,
                reply_markup=kb2,
            )
        bot.answer_callback_query(call.id)

    # ── manual deposit: KBZPay ────────────────────────────────────────────────

    @bot.callback_query_handler(func=lambda c: c.data == "topup_kbzpay")
    def cb_topup_kbzpay(call: types.CallbackQuery):
        _show_manual_instructions(bot, call, "kbzpay", "KBZPay")

    @bot.callback_query_handler(func=lambda c: c.data == "topup_wavepay")
    def cb_topup_wavepay(call: types.CallbackQuery):
        _show_manual_instructions(bot, call, "wavepay", "WavePay")

    @bot.callback_query_handler(func=lambda c: c.data == "topup_cbpay")
    def cb_topup_cbpay(call: types.CallbackQuery):
        _show_manual_instructions(bot, call, "cbpay", "CB Pay")

    @bot.callback_query_handler(func=lambda c: c.data == "topup_uabpay")
    def cb_topup_uabpay(call: types.CallbackQuery):
        _show_manual_instructions(bot, call, "uabpay", "UAB Pay")

    @bot.callback_query_handler(func=lambda c: c.data == "topup_ayapay")
    def cb_topup_ayapay(call: types.CallbackQuery):
        _show_manual_instructions(bot, call, "ayapay", "AYA Pay")

    @bot.callback_query_handler(func=lambda c: c.data == "topup_binance")
    def cb_topup_binance(call: types.CallbackQuery):
        _show_binance_instructions(bot, call)

    def _show_manual_instructions(bot, call: types.CallbackQuery, key: str, label: str):
        uid   = call.from_user.id

        # block if a pending deposit already exists
        pending = db.list_deposits(status="pending", telegram_id=uid, limit=1)
        if pending:
            p = pending[0]
            kb = types.InlineKeyboardMarkup()
            kb.add(emojis.button("📋 View My Deposits", callback_data="my_deposits", emoji_id="5444856076954520455", lead="📋"))
            kb.add(
                emojis.button("⬅️ Back", callback_data="topup_menu", emoji_id="5255703720078879038", lead="⬅️"),
                emojis.button("🏠 Main Menu",  callback_data="main_menu", emoji_id="5042022053356504092", lead="🏠"),
            )
            bot.edit_message_text(
                msg_templates.deposit_pending_exists(
                    p["deposit_id"], fmt_mmk(p["amount_mmk"]), p["method"].upper()
                ),
                uid, call.message.message_id,
                reply_markup=kb,
            )
            bot.answer_callback_query(call.id)
            return

        phone = db.cfg_get(f"{key}_phone", "N/A")
        name  = db.cfg_get(f"{key}_name", "N/A")
        kb = types.InlineKeyboardMarkup(row_width=2)
        kb.add(
            emojis.button("⬅️ Back", callback_data="topup_menu", emoji_id="5255703720078879038", lead="⬅️"),
            emojis.button("🏠 Main Menu",  callback_data="main_menu", emoji_id="5042022053356504092", lead="🏠"),
        )
        prompt = bot.edit_message_text(
            f"<b>{label}</b>\n\n"
            f"How much would you like to top up?\n"
            f"Please enter the amount in <b>MMK</b> (e.g. <code>10000</code> or <code>25000</code>).\n\n"
            f"<blockquote>Please send this exact amount to "
            f"<code>{phone}</code> ({name}).</blockquote>",
            uid, call.message.message_id,
            reply_markup=kb,
        )
        set_state(uid, step=f"manual_amount_{key}", method_key=key, method_label=label, prompt_msg_id=prompt.message_id)
        bot.answer_callback_query(call.id)

    # ── Binance Pay deposit flow ──────────────────────────────────────────────

    def _show_binance_instructions(bot, call: types.CallbackQuery):
        uid = call.from_user.id
        kb = types.InlineKeyboardMarkup(row_width=2)
        kb.add(
            emojis.button("⬅️ Back", callback_data="topup_menu", emoji_id="5255703720078879038", lead="⬅️"),
            emojis.button("🏠 Main Menu",  callback_data="main_menu", emoji_id="5042022053356504092", lead="🏠"),
        )
        prompt = bot.edit_message_text(
            f"<b>Top Up with Binance Pay</b>\n\n"
            f"How much would you like to top up?\n"
            f"Please enter the amount in <b>MMK</b> (e.g. <code>10000</code> or <code>25000</code>).\n\n"
            f"<blockquote>We'll then show you the exact <b>USDT</b> amount to send, "
            f"based on today's rate.</blockquote>",
            uid, call.message.message_id, reply_markup=kb,
        )
        set_state(uid, step="binance_amount", prompt_msg_id=prompt.message_id)
        bot.answer_callback_query(call.id)

    @bot.message_handler(func=lambda m: get_state(m.from_user.id).get("step") == "binance_amount")
    def msg_binance_amount(msg: types.Message):
        uid       = msg.from_user.id
        st        = get_state(uid)
        prompt_id = st.get("prompt_msg_id")
        silent_delete(bot, uid, msg.message_id)

        mmk = parse_mmk(msg.text)
        if mmk is None:
            try:
                bot.edit_message_text(
                    "<b>Top Up with Binance Pay</b>\n\n"
                    "Please enter a valid MMK amount, e.g. <code>10000</code>:",
                    uid, prompt_id, reply_markup=back_kb("topup_menu"),
                )
            except Exception:
                pass
            return

        pay_id = db.cfg_get("binance_pay_id", "N/A")
        usdt   = usdt_to_send(mmk)
        set_state(uid, step="binance_amount_done", binance_mmk=mmk, binance_usdt=usdt,
                  prompt_msg_id=prompt_id)
        kb = types.InlineKeyboardMarkup(row_width=2)
        kb.add(emojis.button("✅ Payment Done", callback_data="binance_paid"))
        kb.add(
            emojis.button("⬅️ Back", callback_data="topup_binance", emoji_id="5255703720078879038", lead="⬅️"),
            emojis.button("🏠 Main Menu",  callback_data="main_menu", emoji_id="5042022053356504092", lead="🏠"),
        )
        pay_text = (
            f"<b>Top Up with Binance Pay</b>\n\n"
            f"To top up <b>{fmt_mmk(mmk)}</b>, please send exactly:\n"
            f"<b>{fmt_usdt(usdt)}</b>\n\n"
            f"<b>Our Binance Pay ID</b>\n"
            f"<code>{pay_id}</code>  <i>(tap to copy)</i>\n\n"
            f"<b>How to pay:</b>\n"
            f"<blockquote>"
            f"1. Open <b>Binance</b> → <b>Assets</b> → <b>Send</b>\n"
            f"2. Choose <b>Send to Binance user</b>\n"
            f"3. Paste our Pay ID above → <b>Continue</b>\n"
            f"4. Enter <b>{fmt_usdt(usdt)}</b> → <b>Continue</b> → confirm\n"
            f"5. Return here and select <b>Payment Done</b>"
            f"</blockquote>\n\n"
            f"<b>USDT only</b> — other currencies cannot be credited.\n"
            f"You'll be credited the <b>exact amount received</b>, converted to MMK."
        )
        try:
            bot.edit_message_text(pay_text, uid, prompt_id, reply_markup=kb)
        except Exception:
            bot.send_message(uid, pay_text, reply_markup=kb)

    @bot.callback_query_handler(func=lambda c: c.data == "binance_paid")
    def cb_binance_paid(call: types.CallbackQuery):
        uid = call.from_user.id
        kb = types.InlineKeyboardMarkup(row_width=2)
        kb.add(
            emojis.button("⬅️ Back", callback_data="topup_binance", emoji_id="5255703720078879038", lead="⬅️"),
            emojis.button("🏠 Main Menu",  callback_data="main_menu", emoji_id="5042022053356504092", lead="🏠"),
        )
        prompt = bot.edit_message_text(
            f"<b>Verify Your Payment</b>\n\n"
            f"After paying, Binance displays a <b>payment slip</b> bearing an "
            f"<b>Order ID</b>.\n\n"
            f"<blockquote>"
            f"1. On that slip, find the <b>Order ID</b>\n"
            f"2. Tap it to <b>copy</b> (it's a long number)\n"
            f"3. Paste it here"
            f"</blockquote>\n\n"
            f"Example: <code>441667223824539648</code>\n\n"
            f"<i>Slip already closed? Open the transfer under Assets → "
            f"Transaction History and copy the Order ID there.</i>",
            uid, call.message.message_id, reply_markup=kb,
        )
        set_state(uid, step="binance_verify_order", prompt_msg_id=prompt.message_id)
        bot.answer_callback_query(call.id)

    # ── Binance verify helpers ────────────────────────────────────────────────

    def _bnc_edit(uid, msg_id, text, kb):
        try:
            bot.edit_message_text(text, uid, msg_id, reply_markup=kb)
        except Exception:
            bot.send_message(uid, text, reply_markup=kb)

    def _bnc_done_kb():
        kb = types.InlineKeyboardMarkup(row_width=2)
        kb.add(
            emojis.button("💰 My Balance", callback_data="wallet_menu"),
            emojis.button("🛍 Shop",       callback_data="shop_categories"),
        )
        kb.add(emojis.button("🏠 Main Menu", callback_data="main_menu", emoji_id="5042022053356504092", lead="🏠"))
        return kb

    def _bnc_retry_kb():
        kb = types.InlineKeyboardMarkup(row_width=2)
        kb.add(emojis.button("🔁 Try Again", callback_data="binance_paid"))
        support_url = db.cfg_get("support_url", "")
        if support_url:
            kb.add(emojis.button("💬 Support", url=support_url, emoji_id="5039532814865794117", lead="💬"))
        kb.add(
            emojis.button("⬅️ Back", callback_data="topup_binance", emoji_id="5255703720078879038", lead="⬅️"),
            emojis.button("🏠 Main Menu",  callback_data="main_menu", emoji_id="5042022053356504092", lead="🏠"),
        )
        return kb

    def _bnc_manual_fallback(uid, uname, order_id, msg_id, reason):
        """When auto-verify can't run (keys missing / API down): file for manual review."""
        notify_admin(
            bot,
            f"<b>Binance deposit — manual review needed</b>\n\n"
            f"User: @{_esc(uname)} (<code>{uid}</code>)\n"
            f"Order ID: <code>{_esc(order_id)}</code>\n"
            f"Reason: {_esc(reason)}\n\n"
            f"<blockquote>Verify in Binance and add balance from the user's profile if valid.</blockquote>",
        )
        _bnc_edit(
            uid, msg_id,
            "<b>Submitted for Review</b>\n\n"
            "We couldn't verify automatically just now — our team will check your Order ID and "
            "credit your balance shortly. Thank you for your patience.",
            _bnc_done_kb(),
        )

    @bot.message_handler(func=lambda m: get_state(m.from_user.id).get("step") == "binance_verify_order")
    def msg_binance_verify_order(msg: types.Message):
        uid       = msg.from_user.id
        uname     = msg.from_user.username or ""
        st        = get_state(uid)
        prompt_id = st.get("prompt_msg_id")
        order_id  = (msg.text or "").strip()
        silent_delete(bot, uid, msg.message_id)

        if not order_id:
            return

        if not check_rate_limit(uid, "binance_verify", min_seconds=10):
            _bnc_edit(uid, prompt_id, "Please wait a few seconds before trying again.", _bnc_retry_kb())
            return

        _bnc_edit(uid, prompt_id, "<b>Verifying with Binance...</b>\n\nThis takes a few seconds.", None)

        if not binance_pay.is_configured():
            clear_state(uid)
            _bnc_manual_fallback(uid, uname, order_id, prompt_id, "Auto-verification offline (no API keys)")
            return

        result = binance_pay.find_deposit_by_order_id(order_id)
        status = result.get("status")

        if status == "matched":
            clear_state(uid)
            amount_usdt = result["amount_usdt"]
            mmk         = mmk_from_usdt(amount_usdt)   # credit USDT received × rate as MMK (rate-guarded)
            txn_id      = result["transaction_id"]
            note        = f"Binance auto | Order {result['order_id']} | {amount_usdt} {result['currency']} @ {int(_rate()):,}"
            # The user row must exist BEFORE the atomic credit (V1.5): the
            # credit is checked inside the transaction now, and a missing
            # row rolls the whole deposit back instead of recording an
            # 'approved' deposit against a 0 balance.
            db.ensure_user(uid, uname)
            try:
                result_dep = db.create_binance_deposit_atomic(uid, uname, mmk, txn_id, note=note)
            except Exception:
                _bnc_edit(
                    uid, prompt_id,
                    "<b>Something went wrong</b>\n\n"
                    "We couldn't credit your deposit just now. Please send the same Order ID again in a moment.",
                    _bnc_retry_kb(),
                )
                return
            if result_dep is None:
                _bnc_edit(
                    uid, prompt_id,
                    "<b>Already Credited</b>\n\nThis Order ID has already been used for a deposit.",
                    _bnc_done_kb(),
                )
                return
            dep_id, bonus = result_dep
            new_bal = db.get_balance(uid)
            bonus_line = f"\nBonus: <b>{fmt_mmk(bonus)}</b>" if bonus else ""
            _bnc_edit(
                uid, prompt_id,
                f"<b>Deposit Verified</b>\n\n"
                f"Received: <b>{fmt_usdt(amount_usdt)}</b>\n"
                f"Credited: <b>{fmt_mmk(mmk)}</b>{bonus_line}\n"
                f"New balance: <b>{fmt_mmk(new_bal)}</b>\n"
                f"{ts()}",
                _bnc_done_kb(),
            )
            notify_admin(
                bot,
                f"<b>Binance Auto-Deposit</b>\n\n"
                f"User: @{_esc(uname)} (<code>{uid}</code>)\n"
                f"Amount: <b>{fmt_usdt(amount_usdt)}</b> → <b>{fmt_mmk(mmk)}</b>\n"
                f"Order ID: <code>{_esc(result['order_id'])}</code>\n"
                f"Txn: <code>{_esc(txn_id)}</code>\n"
                f"Payer: {_esc(result.get('payer_name') or '—')} (<code>{result.get('payer_id') or '—'}</code>)\n"
                f"Deposit #{dep_id}",
            )
            try:
                log_topup(bot, mmk, "Binance Pay", approved=True, username=uname)
            except Exception:
                pass
            return

        # ── failure cases — keep state so "Try Again" works ──
        if status == "not_found":
            _bnc_edit(
                uid, prompt_id,
                "<b>Order ID Not Found</b>\n\n"
                "We couldn't locate that transfer yet — Binance can take a moment to update.\n"
                "Please double-check the Order ID and try again, or contact support.",
                _bnc_retry_kb(),
            )
        elif status == "not_incoming":
            _bnc_edit(
                uid, prompt_id,
                "<b>Transfer Not Received</b>\n\n"
                "Please ensure you sent to our Binance Pay ID and copied the correct Order ID.",
                _bnc_retry_kb(),
            )
        elif status == "wrong_currency":
            _bnc_edit(
                uid, prompt_id,
                f"<b>Incorrect Currency</b>\n\n"
                f"We accept <b>USDT</b> only. That transfer was in {_esc(result.get('currency', '?'))}.",
                _bnc_retry_kb(),
            )
        elif status == "too_old":
            _bnc_edit(
                uid, prompt_id,
                "<b>Transfer Too Old for Auto-Verification</b>\n\nPlease contact support for a manual review.",
                _bnc_retry_kb(),
            )
        elif status == "api_error":
            clear_state(uid)
            _bnc_manual_fallback(uid, uname, order_id, prompt_id, result.get("error") or "API error")
        else:
            _bnc_edit(uid, prompt_id, "Verification unsuccessful. Please try again or contact support.", _bnc_retry_kb())

    # ── manual deposit: collect amount ────────────────────────────────────────

    @bot.message_handler(func=lambda m: get_state(m.from_user.id).get("step", "").startswith("manual_amount_"))
    def msg_manual_amount(msg: types.Message):
        uid       = msg.from_user.id
        st        = get_state(uid)
        prompt_id = st.get("prompt_msg_id")
        key       = st.get("method_key", "")
        label     = st.get("method_label", "")
        silent_delete(bot, uid, msg.message_id)

        mmk = parse_mmk(msg.text)
        if mmk is None:
            try:
                bot.edit_message_text(
                    f"<b>{label}</b>\n\n"
                    f"Please enter a valid MMK amount, e.g. <code>10000</code>:",
                    uid, prompt_id, reply_markup=back_kb("topup_menu"),
                )
            except Exception:
                pass
            return

        phone = db.cfg_get(f"{key}_phone", "N/A")
        name  = db.cfg_get(f"{key}_name", "N/A")
        set_state(uid, step="manual_screenshot", manual_amount=mmk)
        pay_text = (
            f"<b>{label}</b> — Top up {fmt_mmk(mmk)}\n\n"
            f"Please send exactly <b>{mmk_str(mmk)}</b> to:\n"
            f"<code>{phone}</code> ({name})\n\n"
            f"After paying, please send a <b>screenshot</b> of the confirmation screen here."
        )
        try:
            bot.edit_message_text(pay_text, uid, prompt_id, reply_markup=back_kb("topup_menu"))
        except Exception:
            bot.send_message(uid, pay_text, reply_markup=back_kb("topup_menu"))

    # ── manual deposit: receive screenshot (photo or document) ───────────────

    def _process_screenshot(bot, msg: types.Message, file_id: str):
        """Shared logic for photo and document screenshot submissions."""
        uid    = msg.from_user.id

        if not check_rate_limit(uid, "deposit", min_seconds=30):
            bot.send_message(uid, "Please wait before submitting another deposit.")
            return

        st     = get_state(uid)
        amount = st.get("manual_amount", 0)
        key    = st.get("method_key", "manual")
        label  = st.get("method_label", "Manual")
        uname  = msg.from_user.username or ""

        clear_state(uid)
        dep_id = db.create_deposit(uid, uname, key, amount, file_id)

        kb_user = types.InlineKeyboardMarkup(row_width=1)
        kb_user.add(emojis.button("📋 Check Deposit Status", callback_data="my_deposits", emoji_id="5444856076954520455", lead="📋"))
        kb_user.add(emojis.button("🏠 Main Menu", callback_data="main_menu", emoji_id="5042022053356504092", lead="🏠"))

        bot.send_message(
            uid,
            f"<b>Deposit Submitted</b>\n\n"
            f"Method: {label}\n"
            f"Amount: <b>{fmt_money(amount)}</b>\n"
            f"Deposit ID: <b>#{dep_id}</b>\n"
            f"{ts()}\n\n"
            f"Awaiting approval — you'll be notified once processed.\n\n"
            f"<b>Please do not submit again</b> — track progress with the button below.",
            reply_markup=kb_user,
        )

        kb_admin = types.InlineKeyboardMarkup()
        kb_admin.add(
            emojis.button("✅ Approve", callback_data=f"adm_dep_ok_{dep_id}"),
            emojis.button("❌ Reject",  callback_data=f"adm_dep_no_{dep_id}"),
        )
        caption = (
            f"<b>New Deposit</b>\n\n"
            f"User: @{uname} (ID: <code>{uid}</code>)\n"
            f"Method: {label}\n"
            f"Amount: <b>{fmt_mmk(amount)}</b>\n"
            f"Deposit ID: #{dep_id}"
        )
        # notify every admin so any of them can approve/reject
        for aid in admin_ids():
            try:
                bot.send_photo(aid, file_id, caption=caption, reply_markup=kb_admin)
            except Exception:
                try:
                    bot.send_message(
                        aid,
                        caption + "\n\nCould not send screenshot.",
                        reply_markup=kb_admin,
                    )
                except Exception:
                    pass

    @bot.message_handler(
        content_types=["photo"],
        func=lambda m: get_state(m.from_user.id).get("step") == "manual_screenshot",
    )
    def msg_manual_screenshot(msg: types.Message):
        silent_delete(bot, msg.from_user.id, msg.message_id)
        _process_screenshot(bot, msg, msg.photo[-1].file_id)

    @bot.message_handler(
        content_types=["document"],
        func=lambda m: get_state(m.from_user.id).get("step") == "manual_screenshot",
    )
    def msg_manual_screenshot_doc(msg: types.Message):
        silent_delete(bot, msg.from_user.id, msg.message_id)
        _process_screenshot(bot, msg, msg.document.file_id)

    # Deposit approve/reject handlers live in admin_deposits.py (adm_dep_ok_ / adm_dep_no_).
