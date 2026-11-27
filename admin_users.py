"""Admin: user list, user profile, balance adjustment, user orders/deposits."""

import telebot
from telebot import types

import db
import emojis
from utils import fmt_mmk, parse_mmk, admin_only, set_state, get_state, clear_state, back_kb, silent_delete, admin_report, fmt_money, user_link, fmt_date, log_balance_change, notify_admin, is_admin
from msg_templates import balance_added_user, balance_removed_user, admin_balance_change_report


def register(bot: telebot.TeleBot):

    PER_PAGE = 10

    @bot.callback_query_handler(func=lambda c: c.data.startswith("adm_users_"))
    @admin_only
    def cb_adm_users(call: types.CallbackQuery):
        page  = int(call.data[len("adm_users_"):])
        total = db.count_users()
        users = db.list_users(offset=page * PER_PAGE, limit=PER_PAGE)
        kb    = types.InlineKeyboardMarkup(row_width=1)
        for u in users:
            kb.add(emojis.button(
                f"@{u['username'] or u['telegram_id']}  {fmt_mmk(u['mmk_balance'])}",
                callback_data=f"adm_user_{u['telegram_id']}_{page}",
            ))
        nav = []
        if page > 0:
            nav.append(emojis.button("⬅️ Prev", callback_data=f"adm_users_{page-1}", emoji_id="5255703720078879038"))
        if (page + 1) * PER_PAGE < total:
            nav.append(emojis.button("Next ➡️", callback_data=f"adm_users_{page+1}", emoji_id="5253767677670862169"))
        if nav:
            kb.row(*nav)
        kb.add(
            emojis.button("🔍 Search", callback_data="adm_user_search", emoji_id="5039649904264217620", lead="🔍"),
            emojis.button("⬅️ Back",   callback_data="admin_panel", emoji_id="5255703720078879038", lead="⬅️"),
        )
        kb.add(emojis.button("🏠 Main Menu", callback_data="main_menu", emoji_id="5042022053356504092", lead="🏠"))
        bot.edit_message_text(
            f"<b>Users</b> — {total} total | Page {page+1}",
            call.from_user.id, call.message.message_id, reply_markup=kb,
        )
        bot.answer_callback_query(call.id)

    @bot.callback_query_handler(func=lambda c: c.data.startswith("adm_user_") and len(c.data.split("_")) >= 3 and not c.data.startswith("adm_users_") and not c.data.startswith("adm_user_search") and not c.data.startswith("adm_user_orders_") and not c.data.startswith("adm_user_deps_") and not c.data.startswith("adm_addbal_") and not c.data.startswith("adm_rembal_"))
    @admin_only
    def cb_adm_user_profile(call: types.CallbackQuery):
        parts      = call.data.split("_")
        uid_target = int(parts[2])
        back_page  = int(parts[3]) if len(parts) > 3 else 0
        _show_user_profile(call.from_user.id, call.message.message_id, uid_target, back_page)
        bot.answer_callback_query(call.id)

    def _show_user_profile(admin_uid: int, msg_id: int, uid_target: int, back_page: int = 0):
        u = db.get_user(uid_target)
        if not u:
            bot.send_message(admin_uid, "User not found.")
            return
        order_count   = db.count_orders_for_user(uid_target)
        deposit_count = db.count_deposits_for_user(uid_target)
        reseller      = bool(u["is_reseller"]) if "is_reseller" in u.keys() else False
        reseller_line = ""
        if reseller:
            reseller_line = (
                f"\n<b>Reseller</b> · since {fmt_date(u['reseller_since'])} · "
                f"last buy {fmt_date(u['last_reseller_purchase'])}"
            )
        text = (
            f"<b>@{u['username'] or 'N/A'}</b>\n"
            f"ID: <code>{u['telegram_id']}</code>\n"
            f"Balance: <b>{fmt_money(u['mmk_balance'])}</b>\n"
            f"Total Spent: {fmt_money(u['total_spent'])}\n"
            f"Orders: {order_count}  |  Deposits: {deposit_count}\n"
            f"Registered: {fmt_date(u['registered_at'])}"
            f"{reseller_line}"
        )
        kb = types.InlineKeyboardMarkup(row_width=2)
        kb.add(
            emojis.button("➕ Add Balance",    callback_data=f"adm_addbal_{uid_target}", emoji_id="5039891861246838069", lead="➕"),
            emojis.button("➖ Remove Balance", callback_data=f"adm_rembal_{uid_target}", emoji_id="5231265631042548053", lead="➖"),
        )
        kb.add(
            emojis.button(f"📦 Orders ({order_count})",   callback_data=f"adm_user_orders_{uid_target}_0", emoji_id="5444856076954520455", lead="📦"),
            emojis.button(f"💳 Deposits ({deposit_count})", callback_data=f"adm_user_deps_{uid_target}_0", emoji_id="5444856076954520455", lead="💳"),
        )
        if reseller:
            kb.add(emojis.button("🏪❌ Revoke Reseller", callback_data=f"adm_ures_off_{uid_target}"))
        else:
            kb.add(emojis.button("🏪 Make Reseller", callback_data=f"adm_ures_on_{uid_target}", emoji_id="5039816072253932764", lead="🏪"))
        kb.add(
            emojis.button("⬅️ Back", callback_data=f"adm_users_{back_page}", emoji_id="5255703720078879038", lead="⬅️"),
            emojis.button("🏠 Main Menu",  callback_data="main_menu", emoji_id="5042022053356504092", lead="🏠"),
        )
        bot.edit_message_text(text, admin_uid, msg_id, reply_markup=kb)

    # ── reseller grant / revoke (from profile) ────────────────────────────────

    @bot.callback_query_handler(func=lambda c: c.data.startswith("adm_ures_on_"))
    @admin_only
    def cb_adm_ures_on(call: types.CallbackQuery):
        target = int(call.data[len("adm_ures_on_"):])
        db.ensure_user(target)
        db.set_reseller(target, True)
        bot.answer_callback_query(call.id, "Now a reseller.")
        tu = db.get_user(target)
        admin_report(
            bot, "RESELLER GRANTED (manual)",
            [
                ("", "User", user_link(target, tu["username"] if tu else None)),
                ("", "By",   user_link(call.from_user.id, call.from_user.username)),
            ],
            icon="",
        )
        try:
            kb_user = types.InlineKeyboardMarkup(row_width=1)
            kb_user.add(emojis.button("🏪 Open Reseller Hub", callback_data="reseller_hub", emoji_id="5039834781131474002", lead="🏪"))
            kb_user.add(emojis.button("✖️ Dismiss", callback_data="dismiss_msg"))
            days = db.cfg_get("reseller_inactivity_days", "30")
            bot.send_message(
                target,
                "<b>You're now a Reseller!</b>\n\n"
                "An admin granted you wholesale pricing. Open the Reseller Hub to start.\n\n"
                f"<i>Buy at least once every {days} days to keep access.</i>",
                reply_markup=kb_user,
            )
        except Exception:
            pass
        _show_user_profile(call.from_user.id, call.message.message_id, target)

    @bot.callback_query_handler(func=lambda c: c.data.startswith("adm_ures_off_"))
    @admin_only
    def cb_adm_ures_off(call: types.CallbackQuery):
        target = int(call.data[len("adm_ures_off_"):])
        db.set_reseller(target, False)
        bot.answer_callback_query(call.id, "Reseller status revoked.")
        tu = db.get_user(target)
        admin_report(
            bot, "RESELLER REVOKED (manual)",
            [
                ("", "User", user_link(target, tu["username"] if tu else None)),
                ("", "By", user_link(call.from_user.id, call.from_user.username)),
            ],
            icon="",
        )
        try:
            kb_user = types.InlineKeyboardMarkup()
            kb_user.add(emojis.button("✖️ Dismiss", callback_data="dismiss_msg"))
            bot.send_message(
                target,
                "<b>Reseller access removed</b>\n\n"
                "Your reseller status was removed by an admin. You can request access again anytime.",
                reply_markup=kb_user,
            )
        except Exception:
            pass
        _show_user_profile(call.from_user.id, call.message.message_id, target)

    # ── user orders (from profile) ────────────────────────────────────────────

    @bot.callback_query_handler(func=lambda c: c.data.startswith("adm_user_orders_"))
    @admin_only
    def cb_adm_user_orders(call: types.CallbackQuery):
        parts      = call.data.split("_")
        uid_target = int(parts[3])
        offset     = int(parts[4])
        orders     = db.list_orders(limit=10, offset=offset, telegram_id=uid_target)
        total      = db.count_orders_for_user(uid_target)
        kb         = types.InlineKeyboardMarkup(row_width=1)
        for o in orders:
            kb.add(emojis.button(
                f"#{o['order_id']} · {o['product_name']} · {fmt_mmk(o['amount_mmk'])} · {o['created_at'][:10]}",
                callback_data=f"adm_order_{o['order_id']}",
            ))
        nav = []
        if offset > 0:
            nav.append(emojis.button("⬅️ Prev", callback_data=f"adm_user_orders_{uid_target}_{offset-10}", emoji_id="5255703720078879038"))
        if offset + 10 < total:
            nav.append(emojis.button("Next ➡️", callback_data=f"adm_user_orders_{uid_target}_{offset+10}", emoji_id="5253767677670862169"))
        if nav:
            kb.add(*nav)
        kb.add(
            emojis.button("⬅️ Back", callback_data=f"adm_user_{uid_target}", emoji_id="5255703720078879038", lead="⬅️"),
            emojis.button("🏠 Main Menu",  callback_data="main_menu", emoji_id="5042022053356504092", lead="🏠"),
        )
        shown = offset + len(orders)
        u = db.get_user(uid_target)
        uname = (u["username"] if u else "") or str(uid_target)
        bot.edit_message_text(
            f"<b>Orders for @{uname}</b> ({shown}/{total})",
            call.from_user.id, call.message.message_id, reply_markup=kb,
        )
        bot.answer_callback_query(call.id)

    # ── user deposits (from profile) ──────────────────────────────────────────

    @bot.callback_query_handler(func=lambda c: c.data.startswith("adm_user_deps_"))
    @admin_only
    def cb_adm_user_deps(call: types.CallbackQuery):
        parts      = call.data.split("_")
        uid_target = int(parts[3])
        offset     = int(parts[4])
        deps       = db.list_deposits(limit=10, offset=offset, telegram_id=uid_target)
        total      = db.count_deposits_for_user(uid_target)
        status_icon = {"pending": f"{emojis.emoji('pending', '⏳')}", "approved": f"{emojis.emoji('success', '✅')}", "rejected": f"{emojis.emoji('rejected', '❌')}"}
        kb          = types.InlineKeyboardMarkup(row_width=1)
        for d in deps:
            icon = status_icon.get(d["status"], f"{emojis.emoji('question', '❓')}")
            kb.add(emojis.button(
                f"{icon} #{d['deposit_id']} · {d['method'].upper()} · {fmt_mmk(d['amount_mmk'])} · {d['created_at'][:10]}",
                callback_data=f"adm_dep_{d['deposit_id']}",
            ))
        nav = []
        if offset > 0:
            nav.append(emojis.button("⬅️ Prev", callback_data=f"adm_user_deps_{uid_target}_{offset-10}", emoji_id="5255703720078879038"))
        if offset + 10 < total:
            nav.append(emojis.button("Next ➡️", callback_data=f"adm_user_deps_{uid_target}_{offset+10}", emoji_id="5253767677670862169"))
        if nav:
            kb.add(*nav)
        kb.add(
            emojis.button("⬅️ Back", callback_data=f"adm_user_{uid_target}", emoji_id="5255703720078879038", lead="⬅️"),
            emojis.button("🏠 Main Menu",  callback_data="main_menu", emoji_id="5042022053356504092", lead="🏠"),
        )
        shown = offset + len(deps)
        u2 = db.get_user(uid_target)
        uname2 = (u2["username"] if u2 else "") or str(uid_target)
        bot.edit_message_text(
            f"<b>Deposits for @{uname2}</b> ({shown}/{total})",
            call.from_user.id, call.message.message_id, reply_markup=kb,
        )
        bot.answer_callback_query(call.id)

    # ── user search ───────────────────────────────────────────────────────────

    @bot.callback_query_handler(func=lambda c: c.data == "adm_user_search")
    @admin_only
    def cb_adm_user_search(call: types.CallbackQuery):
        prompt = bot.edit_message_text(
            "<b>User Search</b>\n\n"
            "Send a username (without @) or numeric Telegram ID:",
            call.from_user.id, call.message.message_id,
            reply_markup=back_kb("adm_users_0"),
        )
        set_state(call.from_user.id, step="adm_user_search", prompt_msg_id=prompt.message_id)
        bot.answer_callback_query(call.id)

    @bot.message_handler(func=lambda m: get_state(m.from_user.id).get("step") == "adm_user_search")
    @admin_only
    def msg_adm_user_search(msg: types.Message):
        uid       = msg.from_user.id
        prompt_id = get_state(uid).get("prompt_msg_id")
        query     = msg.text.strip().lstrip("@")
        clear_state(uid)
        silent_delete(bot, uid, msg.message_id)

        user = db.search_user(query)
        if not user:
            try:
                bot.edit_message_text(
                    f"No user found for <b>{query}</b>.",
                    uid, prompt_id, reply_markup=back_kb("adm_users_0"),
                )
            except Exception:
                pass
            return
        _show_user_profile(uid, prompt_id, user["telegram_id"])

    @bot.callback_query_handler(func=lambda c: c.data.startswith("adm_addbal_"))
    @admin_only
    def cb_adm_addbal(call: types.CallbackQuery):
        uid_target = int(call.data[len("adm_addbal_"):])
        prompt = bot.edit_message_text(
            f"Enter amount to <b>add</b> to user {uid_target} (MMK, e.g. 12000):",
            call.from_user.id, call.message.message_id,
            reply_markup=back_kb(f"adm_user_{uid_target}"),
        )
        set_state(call.from_user.id, step="adj_balance", adj_uid=uid_target, adj_dir=1, prompt_msg_id=prompt.message_id)
        bot.answer_callback_query(call.id)

    @bot.callback_query_handler(func=lambda c: c.data.startswith("adm_rembal_"))
    @admin_only
    def cb_adm_rembal(call: types.CallbackQuery):
        uid_target = int(call.data[len("adm_rembal_"):])
        prompt = bot.edit_message_text(
            f"Enter amount to <b>remove</b> from user {uid_target} (MMK, e.g. 12000):",
            call.from_user.id, call.message.message_id,
            reply_markup=back_kb(f"adm_user_{uid_target}"),
        )
        set_state(call.from_user.id, step="adj_balance", adj_uid=uid_target, adj_dir=-1, prompt_msg_id=prompt.message_id)
        bot.answer_callback_query(call.id)

    @bot.message_handler(func=lambda m: get_state(m.from_user.id).get("step") == "adj_balance")
    @admin_only
    def msg_adj_balance(msg: types.Message):
        uid       = msg.from_user.id
        st        = get_state(uid)
        target    = st.get("adj_uid")
        dirn      = st.get("adj_dir")
        prompt_id = st.get("prompt_msg_id")
        # state may have expired (30 min) between the filter check and here
        if target is None or dirn is None:
            clear_state(uid)
            bot.send_message(uid, "That balance-adjust session expired. Please open the user again and retry.")
            return
        mmk = parse_mmk(msg.text)
        if mmk is None:
            bot.send_message(uid, "Please enter a valid MMK amount, e.g. 12000.")
            return
        clear_state(uid)
        prev_bal = db.get_balance(target)
        if dirn > 0:
            db.adjust_balance(target, mmk)
            new_bal = db.get_balance(target)
        else:
            # clamped so a large 'remove' can't drive the balance negative
            new_bal = db.adjust_balance_clamped(target, -mmk)
        # actual change applied (a clamped remove may take less than requested)
        amount = new_bal - prev_bal
        silent_delete(bot, uid, msg.message_id)

        target_user = db.get_user(target)
        uname = (target_user["username"] if target_user else "") or str(target)
        
        notify_admin(
            bot,
            admin_balance_change_report(
                user_link=user_link(target, uname),
                amount=fmt_money(abs(amount)),
                new_balance=fmt_money(new_bal),
                adjusted_by=user_link(uid, msg.from_user.username),
                action="added" if dirn > 0 else "removed"
            )
        )
        # public balance-change notice (masked name) for the vouch channel
        log_balance_change(bot, abs(amount), added=(dirn > 0), username=(target_user["username"] if target_user else None))
        
        # Only send user notification if target is NOT an admin
        # (admins already get the admin report, so skip duplicate)
        if not is_admin(target):
            try:
                kb_user = types.InlineKeyboardMarkup()
                kb_user.add(emojis.button("Dismiss", callback_data="dismiss_msg", emoji_id="5210952531676504517", lead="✖️"))
                
                user_message = balance_added_user(fmt_mmk(abs(amount)), fmt_mmk(new_bal)) if dirn > 0 else balance_removed_user(fmt_mmk(abs(amount)), fmt_mmk(new_bal))
                
                bot.send_message(
                    target,
                    user_message,
                    parse_mode='HTML',
                    reply_markup=kb_user,
                )
            except Exception:
                pass
        try:
            _show_user_profile(uid, prompt_id, target)
        except Exception:
            bot.send_message(uid, f"Done. New balance: {fmt_mmk(new_bal)}.", reply_markup=back_kb(f"adm_user_{target}"))
