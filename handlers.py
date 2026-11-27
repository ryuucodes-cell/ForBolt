"""Customer-facing handlers: /start, shop browsing, buy, orders, balance, support."""

import telebot
from telebot import types

import db
import emojis
from utils import (
    fmt_mmk, fmt_usd, is_admin, is_reseller, maintenance,
    main_menu_kb, back_kb,
    set_state, get_state, clear_state,
    notify_admin, check_membership, membership_required_msg,
    check_rate_limit,
    log_vouch, fmt_money, fmt_date, ts,
    format_credential, format_credential_block, user_link, silent_delete, edit_or_send,
)
from msg_templates import (
    purchase_complete, purchase_insufficient_balance, purchase_out_of_stock,
    low_stock_alert, out_of_stock_alert, maintenance_mode,
    preorder_confirm_text,
    preorder_received, preorder_cancelled, preorder_delivered,
    fulfillment_status_line, admin_fulfill_new,
)


import html as _html
import re as _re

_TG_EMOJI_PAIR = _re.compile(
    r"&lt;tg-emoji emoji-id=&quot;(\d+)&quot;&gt;(.*?)&lt;/tg-emoji&gt;",
    _re.DOTALL)


def _esc(s: str) -> str:
    return _html.escape(str(s))


def _esc_desc(s: str) -> str:
    """Escape a product description for HTML parse mode WITHOUT breaking
    the premium-emoji <tg-emoji> tags descriptions intentionally carry
    (admin-set, e.g. <tg-emoji emoji-id="123">😐</tg-emoji>): escape
    everything first, then restore complete, valid tg-emoji pairs. A
    stray < or & in the text still can't break the product page."""
    out = _html.escape(str(s))
    return _TG_EMOJI_PAIR.sub(
        lambda m: '<tg-emoji emoji-id="%s">%s</tg-emoji>'
                  % (m.group(1), m.group(2)),
        out)


_METHOD_LABELS = {
    "kbzpay":   "KBZPay",
    "wavepay":  "WavePay",
    "cbpay":    "CB Pay",
    "binance":  "Binance Pay",
}

def _method_label(method: str) -> str:
    return _METHOD_LABELS.get(method.lower(), method.upper())


def register(bot: telebot.TeleBot):

    # ── /start ────────────────────────────────────────────────────────────────

    @bot.message_handler(commands=["start"])
    def cmd_start(msg: types.Message):
        uid   = msg.from_user.id
        uname = msg.from_user.username or ""
        # Deep-link payload: "/start <payload>" (gifts, giveaway invites)
        payload = ""
        try:
            parts = (msg.text or "").split(maxsplit=1)
            if len(parts) > 1:
                payload = parts[1].strip()
        except Exception:
            payload = ""
        db.ensure_user(uid, uname)
        silent_delete(bot, uid, msg.message_id)

        if not is_admin(uid) and not check_membership(bot, uid):
            membership_required_msg(bot, uid)
            return

        if maintenance() and not is_admin(uid):
            bot.send_message(uid, maintenance_mode())
            return

        # Route deep links before the normal menu
        if payload:
            try:
                import gifts as _gifts
                if _gifts.handle_deep_link(bot, uid, uname, payload):
                    return
            except Exception:
                pass
            try:
                import giveaway as _gw
                if _gw.handle_deep_link(bot, uid, uname, payload):
                    return
            except Exception:
                pass

        bot.send_message(uid, _main_menu_text(uid), reply_markup=main_menu_kb(uid))

    # ── membership re-check ───────────────────────────────────────────────────

    @bot.callback_query_handler(func=lambda c: c.data == "check_membership")
    def cb_check_membership(call: types.CallbackQuery):
        uid = call.from_user.id
        if is_admin(uid) or check_membership(bot, uid):
            bot.answer_callback_query(call.id, "Verified!")
            uname = call.from_user.username or ""
            db.ensure_user(uid, uname)
            bot.edit_message_text(
                _main_menu_text(uid),
                uid, call.message.message_id,
                reply_markup=main_menu_kb(uid),
            )
        else:
            bot.answer_callback_query(call.id, "Please join the channel first, then select I've Joined.", show_alert=True)

    def _reply(bot, call, text, reply_markup):
        """Edit the current message in place; fall back to sending only if edit fails."""
        uid = call.from_user.id
        try:
            bot.edit_message_text(text, uid, call.message.message_id, reply_markup=reply_markup)
        except Exception:
            bot.send_message(uid, text, reply_markup=reply_markup)

    # ── main menu (callback) ──────────────────────────────────────────────────

    def _main_menu_text(uid: int) -> str:
        # Not escaped: shop_name / tagline / subtagline / welcome_message are
        # captured as ready-to-send Telegram HTML (via emojis.capture_html in
        # admin_settings.py), so any Premium emoji or formatting the admin
        # typed is preserved and rendered here exactly as entered.
        shop     = db.cfg_get("shop_name", "StockBot")
        tagline  = db.cfg_get("shop_tagline", "")
        subtag   = db.cfg_get("shop_subtagline", "")
        wtext    = db.cfg_get("welcome_message", "Welcome!")
        bal      = db.get_balance(uid)
        reseller = is_reseller(uid)

        opted_out  = db.get_notify_opt_out(uid)
        notif_line = f"\n<i>Notifications are muted — you can re-enable them below.</i>" if opted_out else ""
        role_badge = " · <b>Reseller</b>" if reseller else ""

        header = f"<b>{shop}</b>"
        if tagline:
            header += f"\n{tagline}"
        if subtag:
            header += f"\n{subtag}"

        return (
            f"{header}\n\n"
            f"<blockquote>{wtext}\n\n"
            f"Balance: <b>{fmt_usd(bal)}</b>{role_badge}</blockquote>\n"
            f"<i>What would you like to do today?</i>"
            f"{notif_line}"
        )

    @bot.callback_query_handler(func=lambda c: c.data == "main_menu")
    def cb_main_menu(call: types.CallbackQuery):
        uid = call.from_user.id
        _reply(bot, call, _main_menu_text(uid), main_menu_kb(uid))
        bot.answer_callback_query(call.id)

    # ── shop categories ───────────────────────────────────────────────────────

    def _shop_groups():
        """Ordered category keys for active products + per-key counts + products.

        Keys are exact category strings; '' (uncategorized) groups under 'Other'
        and sorts last. The category menu only appears with 2+ groups.
        """
        prods  = db.list_products(active_only=True)
        counts = {}
        for p in prods:
            c = (p["category"] or "").strip()
            counts[c] = counts.get(c, 0) + 1
        groups = sorted([c for c in counts if c], key=str.lower)
        if "" in counts:
            groups.append("")
        return groups, counts, prods

    def _cat_label(cat: str) -> str:
        return cat if cat else "Other"

    def _render_product_list(call, prods, back_cb: str, title: str):
        """Shared product-button list (used by flat shop and per-category views)."""
        kb = types.InlineKeyboardMarkup(row_width=1)
        for p in prods:
            cnt = db.stock_count(p["product_id"])
            emoji = db.product_emoji(p)
            eid   = db.product_emoji_id(p)
            # List shows the product name only — price and stock live on the
            # detail page (one tap away). The green/red button color still
            # signals stock status at a glance.
            label = f"{emoji} {p['name']}"
            if db.get_fulfillment(p) != "instant":
                style = "success"  # manual/pre-order: always accept orders (decline later via refund)
            elif cnt > 0:
                style = "success"  # green button — in stock
            else:
                style = "danger"   # red button — out of stock
            # product's own premium logo becomes the button icon when set
            kb.add(emojis.button(label, callback_data=f"prod_{p['product_id']}", emoji_id=eid, lead=emoji, style=style))
        if back_cb:
            kb.add(emojis.button("Back", callback_data=back_cb, emoji_id="5255703720078879038", lead="⬅️"))
        kb.add(emojis.button("Main Menu", callback_data="main_menu", emoji_id="5042022053356504092", lead="🏠"))
        _reply(bot, call, title, kb)

    def _shop_gate(call) -> bool:
        """Common shop entry checks. Returns True when the user may browse."""
        uid = call.from_user.id
        if not is_admin(uid) and not check_membership(bot, uid):
            bot.answer_callback_query(call.id)
            membership_required_msg(bot, uid)
            return False
        if maintenance() and not is_admin(uid):
            bot.answer_callback_query(call.id, "We're under maintenance — please visit us again soon.", show_alert=True)
            return False
        return True

    @bot.callback_query_handler(func=lambda c: c.data == "shop_categories")
    def cb_shop_categories(call: types.CallbackQuery):
        uid = call.from_user.id
        if not _shop_gate(call):
            return
        groups, counts, prods = _shop_groups()
        if not prods:
            bot.answer_callback_query(call.id, "We're adding new products — check back soon!", show_alert=True)
            return
        reseller = is_reseller(uid)

        # single group (or no categories used) → flat list, exactly as before
        if len(groups) <= 1:
            title = ("<b>Reseller Products</b>\n\n<i>Wholesale pricing applied.</i>"
                     if reseller else "<b>Products</b>\n\n<i>Select a product to view details.</i>")
            _render_product_list(call, prods, back_cb="", title=title)
            bot.answer_callback_query(call.id)
            return

        kb = types.InlineKeyboardMarkup(row_width=1)
        for i, cat in enumerate(groups):
            kb.add(emojis.button(
                f"{emojis.emoji('folder', '📂')} {_cat_label(cat)} · {counts[cat]}",
                callback_data=f"shopcat_{i}",
            ))
        kb.add(emojis.button("Main Menu", callback_data="main_menu", emoji_id="5042022053356504092", lead="🏠"))
        title = ("<b>Reseller Products</b>\n\n<i>Wholesale pricing applied. Choose a category.</i>"
                 if reseller else "<b>Products</b>\n\n<i>Choose a category.</i>")
        _reply(bot, call, title, kb)
        bot.answer_callback_query(call.id)

    @bot.callback_query_handler(func=lambda c: c.data.startswith("shopcat_"))
    def cb_shopcat(call: types.CallbackQuery):
        if not _shop_gate(call):
            return
        groups, counts, prods = _shop_groups()
        try:
            idx = int(call.data[len("shopcat_"):])
            cat = groups[idx]
        except (ValueError, IndexError):
            # categories changed since this menu was shown — refresh the root
            cb_shop_categories(call)
            return
        in_cat = [p for p in prods if (p["category"] or "").strip() == cat]
        if not in_cat:
            cb_shop_categories(call)
            return
        _render_product_list(
            call, in_cat, back_cb="shop_categories",
            title=f"<b>{_esc(_cat_label(cat))}</b>\n\n<i>Select a product to view details.</i>",
        )
        bot.answer_callback_query(call.id)

    # ── product detail ────────────────────────────────────────────────────────

    @bot.callback_query_handler(func=lambda c: c.data.startswith("prod_"))
    def cb_product(call: types.CallbackQuery):
        pid = int(call.data[5:])
        p   = db.get_product(pid)
        if not p or not p["active"]:
            bot.answer_callback_query(call.id, "Product not available.")
            return
        cnt       = db.stock_count(pid)
        uid       = call.from_user.id
        bal       = db.get_balance(uid)
        reseller  = is_reseller(uid)

        if reseller:
            eff_price = db.get_reseller_price(p)
            # show retail crossed out only when the reseller genuinely saves
            if eff_price < p["price_mmk"]:
                price_line = f"Price: <s>{fmt_usd(p['price_mmk'])}</s> → <b>{fmt_usd(eff_price)}</b> (reseller)"
            else:
                price_line = f"Price: <b>{fmt_usd(eff_price)}</b> (reseller)"
        else:
            eff_price = db.get_price_for_user(p, uid)
            disc      = db.is_discount_active(p)
            if disc:
                price_line = (
                    f"Price: <s>{fmt_usd(p['price_mmk'])}</s> → <b>{fmt_usd(eff_price)}</b> ({p['discount_pct']}% OFF )"
                )
            else:
                price_line = f"Price: <b>{fmt_usd(eff_price)}</b>"
            tier_name, tier_disc = db.get_user_vip_tier(uid)
            if tier_name and tier_disc:
                price_line += f"  {emojis.emoji('crown', '👑')} {tier_name} −{tier_disc}%"

        mode = db.get_fulfillment(p)
        eta  = (db.cfg_get("fulfillment_eta", "") or "").strip() or "usually within 24 hours"
        if mode == "preorder":
            avail_line = f"Pre-order · {eta}\n"
        else:
            avail_line = f"Stock: <b>{cnt}</b>\n"
        emoji = db.product_emoji_html(p)   # premium emoji in text if set, else plain
        caption = (
            f"{emoji} <b>{_esc(p['name'])}</b>\n\n"
            f"<blockquote>{_esc_desc(p['description'])}</blockquote>\n\n"
            f"{price_line}\n"
            f"{avail_line}"
            f"Your balance: <b>{fmt_usd(bal)}</b>"
        )
        kb = types.InlineKeyboardMarkup(row_width=1)
        if mode == "preorder":
            kb.add(emojis.button("Pre-order Now", callback_data=f"preorder_{pid}", emoji_id="5312361253610475399", lead="🛒"))
        elif cnt > 0:
            kb.add(emojis.button("Buy", callback_data=f"qty_{pid}", emoji_id="5312361253610475399", lead="🛒"))
            kb.add(emojis.button("Gift to a Friend", callback_data=f"giftbuy_{pid}", emoji_id="5039823300683891773", lead="🎁"))
        else:
            kb.add(emojis.button("Out of Stock", callback_data="noop", emoji_id="5039671744172917707", lead="❌"))
        _ld_round = db.ld_open_round_for_product(pid)
        if _ld_round:
            kb.add(emojis.button("Win this in Lucky Draw", callback_data=f"ld_view_{_ld_round['round_id']}", emoji_id="5260547274957672345", lead="🎲"))
        # Back returns to this product's category when the shop is categorized
        groups, _counts, _prods = _shop_groups()
        cat = (p["category"] or "").strip()
        back_cb = (f"shopcat_{groups.index(cat)}"
                   if len(groups) > 1 and cat in groups else "shop_categories")
        kb.add(emojis.button("Back", callback_data=back_cb, emoji_id="5255703720078879038", lead="⬅️"))
        kb.add(emojis.button("Main Menu",  callback_data="main_menu", emoji_id="5042022053356504092", lead="🏠"))

        bot.answer_callback_query(call.id)
        _reply(bot, call, caption, kb)

    # ── buy: quantity selected → confirmation ─────────────────────────────────

    # ── buy: quantity selection screen ────────────────────────────────────────

    _QTY_PRESETS          = [1, 2, 3, 5, 10, 15, 20, 25]
    _RESELLER_QTY_PRESETS = [1, 5, 10, 25, 50, 100, 150, 200]

    @bot.callback_query_handler(func=lambda c: c.data.startswith("qty_"))
    def cb_qty(call: types.CallbackQuery):
        pid = int(call.data[len("qty_"):])
        p   = db.get_product(pid)
        if not p or not p["active"]:
            bot.answer_callback_query(call.id, "Product not available.")
            return
        cnt = db.stock_count(pid)
        if cnt <= 0:
            bot.answer_callback_query(call.id, "Out of stock.", show_alert=True)
            return
        uid     = call.from_user.id
        eff     = db.get_price_for_user(p, uid)
        base    = _RESELLER_QTY_PRESETS if is_reseller(uid) else _QTY_PRESETS
        presets = [q for q in base if q <= cnt] or [1]
        kb   = types.InlineKeyboardMarkup(row_width=4)
        btns = [emojis.button(str(q), callback_data=f"buyqty_{pid}_{q}") for q in presets]
        for i in range(0, len(btns), 4):
            kb.row(*btns[i:i + 4])
        kb.add(emojis.button("Custom Amount", callback_data=f"qtycustom_{pid}", emoji_id="5395444784611480792", lead="✏️"))
        kb.add(
            emojis.button("Back to Product", callback_data=f"prod_{pid}", emoji_id="5255703720078879038", lead="⬅️"),
            emojis.button("Main Menu",       callback_data="main_menu", emoji_id="5042022053356504092", lead="🏠"),
        )
        text = (
            f"<b>Select Quantity</b>\n\n"
            f"<b>{_esc(p['name'])}</b>\n"
            f"{fmt_usd(eff)} each\n"
            f"In stock: <b>{cnt}</b>\n\n"
            f"How many would you like?"
        )
        _reply(bot, call, text, kb)
        bot.answer_callback_query(call.id)

    @bot.callback_query_handler(func=lambda c: c.data.startswith("qtycustom_"))
    def cb_qtycustom(call: types.CallbackQuery):
        pid = int(call.data[len("qtycustom_"):])
        uid = call.from_user.id
        cnt = db.stock_count(pid)
        if cnt <= 0:
            bot.answer_callback_query(call.id, "Out of stock.", show_alert=True)
            return
        prompt = bot.edit_message_text(
            f"<b>Custom Quantity</b>\n\n"
            f"How many would you like? Enter a number between <b>1</b> and <b>{cnt}</b>:",
            uid, call.message.message_id, reply_markup=back_kb(f"qty_{pid}"),
        )
        set_state(uid, step="buy_custom_qty", buy_pid=pid, prompt_msg_id=prompt.message_id)
        bot.answer_callback_query(call.id)

    @bot.message_handler(func=lambda m: get_state(m.from_user.id).get("step") == "buy_custom_qty")
    def msg_buy_custom_qty(msg: types.Message):
        uid       = msg.from_user.id
        st        = get_state(uid)
        pid       = st.get("buy_pid")
        prompt_id = st.get("prompt_msg_id")
        silent_delete(bot, uid, msg.message_id)
        cnt  = db.stock_count(pid)
        text = (msg.text or "").strip().replace(",", "")
        if not text.isdigit() or int(text) < 1:
            try:
                bot.edit_message_text(
                    f"<b>Custom Quantity</b>\n\nPlease enter a valid number between 1 and {cnt}:",
                    uid, prompt_id, reply_markup=back_kb(f"qty_{pid}"),
                )
            except Exception:
                pass
            return
        qty = int(text)
        if qty > cnt:
            try:
                bot.edit_message_text(
                    f"<b>Custom Quantity</b>\n\nOnly <b>{cnt}</b> available. Please enter {cnt} or fewer:",
                    uid, prompt_id, reply_markup=back_kb(f"qty_{pid}"),
                )
            except Exception:
                pass
            return
        clear_state(uid)
        _show_buy_confirm(uid, prompt_id, pid, qty)

    # ── buy: confirmation screen (shared by presets + custom) ─────────────────

    def _show_buy_confirm(uid: int, msg_id: int, pid: int, qty: int):
        p = db.get_product(pid)
        if not p or not p["active"]:
            try:
                bot.edit_message_text("Product not available.", uid, msg_id, reply_markup=back_kb("shop_categories"))
            except Exception:
                pass
            return
        cnt = db.stock_count(pid)
        original_qty = qty
        qty = min(qty, cnt)
        if qty < 1:
            try:
                bot.edit_message_text("Out of stock.", uid, msg_id, reply_markup=back_kb("shop_categories"))
            except Exception:
                pass
            return
        clamped_note = ""
        if qty < original_qty:
            clamped_note = f"\n⚠️ Only {cnt} in stock — adjusted from {original_qty} to {qty}."
        reseller  = is_reseller(uid)
        eff_price = db.get_price_for_user(p, uid)
        total     = eff_price * qty
        bal       = db.get_balance(uid)
        if reseller:
            price_line = (
                f"<s>{fmt_usd(p['price_mmk'])}</s> → {fmt_usd(eff_price)} each (reseller)"
                if eff_price < p["price_mmk"] else f"{fmt_usd(eff_price)} each (reseller)"
            )
        else:
            disc = db.is_discount_active(p)
            price_line = (
                f"<s>{fmt_usd(p['price_mmk'])}</s> → {fmt_usd(eff_price)} each ({p['discount_pct']}% OFF)"
                if disc else f"{fmt_usd(eff_price)} each"
            )
            tier_name, tier_disc = db.get_user_vip_tier(uid)
            if tier_name and tier_disc:
                price_line += f"  {emojis.emoji('crown', '👑')} {tier_name} −{tier_disc}%"

        if bal < total:
            kb = types.InlineKeyboardMarkup(row_width=2)
            kb.add(emojis.button("Top Up", callback_data="topup_menu", emoji_id="5445353829304387411", lead="💳"))
            kb.add(
                emojis.button("Change Qty", callback_data=f"qty_{pid}", emoji_id="5226702984204797593", lead="🔢"),
                emojis.button("Main Menu",  callback_data="main_menu", emoji_id="5042022053356504092", lead="🏠"),
            )
            text = (
                f"<b>{_esc(p['name'])}</b> × {qty}\n"
                f"Price: {price_line}\n"
                f"Total: <b>{fmt_usd(total)}</b>\n\n"
                f"<b>Insufficient balance.</b>\n"
                f"Your balance is {fmt_usd(bal)} — <b>{fmt_usd(total - bal)}</b> short of the total.\n\n"
                f"Top up to complete your purchase."
            )
            try:
                bot.edit_message_text(text, uid, msg_id, reply_markup=kb)
            except Exception:
                bot.send_message(uid, text, reply_markup=kb)
            return

        kb = types.InlineKeyboardMarkup(row_width=2)
        kb.add(
            emojis.button(f"Confirm — pay {fmt_usd(total)}", callback_data=f"buyconfirm_{pid}_{qty}_{eff_price}", emoji_id="5039844895779455925", lead="✅"),
            emojis.button("Cancel", callback_data=f"prod_{pid}", emoji_id="5210952531676504517", lead="❌"),
        )
        confirm_text = (
            f"<b>Confirm Purchase</b>\n\n"
            f"Product: <b>{_esc(p['name'])}</b>\n"
            f"Quantity: <b>{qty}</b>\n"
            f"Price: {price_line}\n"
            f"Total: <b>{fmt_usd(total)}</b>\n"
            f"Balance after: <b>{fmt_usd(bal - total)}</b>"
            f"{clamped_note}"
        )
        try:
            bot.edit_message_text(confirm_text, uid, msg_id, reply_markup=kb)
        except Exception:
            bot.send_message(uid, confirm_text, reply_markup=kb)

    @bot.callback_query_handler(func=lambda c: c.data.startswith("buyqty_"))
    def cb_buyqty(call: types.CallbackQuery):
        _, pid_s, qty_s = call.data.split("_")
        _show_buy_confirm(call.from_user.id, call.message.message_id, int(pid_s), int(qty_s))
        bot.answer_callback_query(call.id)

    def _vip_tier_up_check(uid: int, amount_mmk: int):
        """Compare VIP tier before/after a purchase and celebrate an upgrade."""
        try:
            new_spent = (db.get_user(uid) or {"total_spent": 0})["total_spent"] or 0
            old_tier, _ = db.get_vip_tier(max(0, new_spent - amount_mmk))
            new_tier, new_disc = db.get_vip_tier(new_spent)
            if new_tier and new_tier != old_tier:
                from msg_templates import vip_tier_up
                try:
                    bot.send_message(uid, vip_tier_up(new_tier, new_disc), parse_mode="HTML")
                except Exception:
                    pass
        except Exception:
            pass

    # ── buy: confirmed ────────────────────────────────────────────────────────

    @bot.callback_query_handler(func=lambda c: c.data.startswith("buyconfirm_"))
    def cb_buyconfirm(call: types.CallbackQuery):
        # callback: buyconfirm_<pid>_<qty>_<quoted_price>. The quoted price is the
        # per-unit price the user actually saw and agreed to on the confirm screen.
        # (Older in-flight callbacks may omit it — treat quoted as unknown then.)
        parts = call.data.split("_")
        pid   = int(parts[1])
        qty   = int(parts[2])
        quoted = int(parts[3]) if len(parts) > 3 else None
        uid   = call.from_user.id
        uname = call.from_user.username or ""

        if not check_rate_limit(uid, "buy", min_seconds=5):
            bot.answer_callback_query(call.id, "Please wait a moment before purchasing again.", show_alert=True)
            return

        p = db.get_product(pid)
        if not p or not p["active"]:
            bot.answer_callback_query(call.id, "Product not available.")
            return

        # re-fetch price at charge time — reseller rate is authoritative here, and a
        # retail discount may have expired since the confirm screen was shown
        reseller  = is_reseller(uid)
        eff_price = db.get_price_for_user_by_id(pid, uid)

        # H1 guard: never charge MORE than the user was quoted. If the price rose
        # since the confirm screen (e.g. a discount expired between render and tap),
        # re-show the confirm screen with the new price instead of silently
        # overcharging. A lower/equal price is fine — the user just pays less.
        if quoted is not None and eff_price > quoted:
            bot.answer_callback_query(call.id, "The price has been updated — please review and confirm again.", show_alert=True)
            _show_buy_confirm(uid, call.message.message_id, pid, qty)
            return

        db.ensure_user(uid, uname)

        # C3: claim stock + deduct balance + record orders in ONE transaction, so a
        # crash can never leave the user charged with no stock (or stock gone with no
        # charge). purchase_atomic claims up to qty, charges only for what it claimed,
        # and rolls everything back if the balance is short.
        result = db.purchase_atomic(pid, uid, uname, eff_price, qty, p["name"])

        if not result["ok"]:
            if result["reason"] == "out_of_stock":
                bot.answer_callback_query(call.id, "This item just sold out — no charge was made.", show_alert=True)
                notify_admin(bot, out_of_stock_alert(p['name'], pid))
            else:  # insufficient
                need = result.get("need", eff_price * qty)
                bal  = result.get("balance", db.get_balance(uid))
                bot.answer_callback_query(
                    call.id,
                    purchase_insufficient_balance(fmt_mmk(need), fmt_mmk(bal), fmt_mmk(need - bal)),
                    show_alert=True,
                )
            return

        credentials  = [cred for _oid, cred in result["orders"]]
        order_ids    = [oid for oid, _cred in result["orders"]]
        actual_qty   = result["claimed"]
        actual_total = result["charged"]
        new_bal      = result["new_balance"]

        # reseller: reset inactivity clock so an active reseller keeps their access
        try:
            db.touch_reseller_activity(uid)
        except Exception:
            pass

        try:
            remaining  = db.stock_count(pid)
            threshold  = int(db.cfg_get("low_stock_threshold", "3") or 3)
            if remaining <= threshold:
                notify_admin(bot, low_stock_alert(p['name'], remaining))
        except Exception:
            pass  # low-stock alert is best-effort

        # build receipt
        new_bal = db.get_balance(uid)
        
        # Format credentials
        formatted_creds = []
        for i, cred in enumerate(credentials, 1):
            cred_text = format_credential(cred, p["credential_format"])
            oid_label = f"Order #{order_ids[i-1]}" if order_ids[i-1] else "Order"
            formatted_creds.append(f"<b>{oid_label}</b>\n{cred_text}")
        
        receipt = purchase_complete(
            product_name=_esc(p['name']),
            qty=actual_qty,
            unit_price=fmt_money(eff_price),
            total=fmt_money(actual_total),
            new_balance=fmt_mmk(new_bal),
            order_ids=", ".join([f"#{oid}" for oid in order_ids]),
            credentials="\n\n".join(formatted_creds)
        )

        kb = types.InlineKeyboardMarkup(row_width=2)
        kb.add(
            emojis.button("My Orders",  callback_data="my_orders", emoji_id="5444856076954520455", lead="📦"),
            emojis.button("Main Menu", callback_data="main_menu", emoji_id="5042022053356504092", lead="🏠"),
        )

        try:
            bot.edit_message_text(receipt, uid, call.message.message_id, parse_mode='HTML', reply_markup=kb)
        except Exception:
            bot.send_message(uid, receipt, parse_mode='HTML', reply_markup=kb)
        bot.answer_callback_query(call.id, "Purchase successful!")

        # public vouch — masked buyer name, no ID or balance.
        # Reseller purchases are logged too, but WITHOUT the amount: posting the
        # total would leak wholesale pricing to retail buyers watching the channel.
        log_vouch(bot, _esc(p["name"]), actual_qty,
                  None if reseller else actual_total, uname)
        _vip_tier_up_check(uid, actual_total)

    # ── fulfillment v2: manual activation / pre-order ─────────────────────────

    _FULFILL_MODE_LABEL = {
        "preorder": "Pre-order",
    }

    def _fulfill_notify_admin(oid: int):
        """Alert admins about a new/updated fulfillment order."""
        order = db.get_order(oid)
        if not order:
            return
        mode = db.get_order_fulfillment(order)
        label = _FULFILL_MODE_LABEL.get(mode, mode)
        buyer = user_link(order["telegram_id"], order["username"])
        info = { _esc(k): _esc(v) for k, v in db.get_manual_info(order).items() }
        text = admin_fulfill_new(_esc(order["product_name"]), label, buyer,
                                 oid, fmt_money(order["amount_mmk"]), info)
        kb = types.InlineKeyboardMarkup()
        kb.add(emojis.button("View order", callback_data=f"adm_fulfill_{oid}",
                             emoji_id="5444856076954520455", lead="📋"))
        notify_admin(bot, text, reply_markup=kb)

    def _refund_manual_order(uid: int, oid: int) -> bool:
        """Refund a pending pre-order. Returns True on success.

        The money move goes through refund_order_atomic (status-guarded,
        single transaction) — the old read-check-credit sequence here let
        a fast double-tap on 'Yes, cancel & refund' credit the customer
        twice (fixed in V1.5)."""
        order = db.get_order(oid)
        if not order or order["telegram_id"] != uid:
            return False
        if order["status"] not in ("awaiting_info", "awaiting_fulfillment"):
            return False
        done = db.refund_order_atomic(
            oid, only_statuses=("awaiting_info", "awaiting_fulfillment"))
        if not done:
            return False
        clear_state(uid)
        return True

    def _fulfill_insufficient_kb(pid: int, back_cb: str):
        kb = types.InlineKeyboardMarkup(row_width=2)
        kb.add(emojis.button("Top Up", callback_data="topup_menu", emoji_id="5445353829304387411", lead="💳"))
        kb.add(
            emojis.button("Back", callback_data=back_cb, emoji_id="5255703720078879038", lead="⬅️"),
            emojis.button("Main Menu", callback_data="main_menu", emoji_id="5042022053356504092", lead="🏠"),
        )
        return kb

    # ── pre-order: quantity → confirm → charge (bulk) ─────────────────────────

    _PREORDER_QTY_PRESETS = [1, 2, 3, 5, 10, 15, 20, 25]

    @bot.callback_query_handler(func=lambda c: c.data.startswith("preorder_"))
    def cb_preorder(call: types.CallbackQuery):
        pid = int(call.data[len("preorder_"):])
        p = db.get_product(pid)
        if not p or not p["active"] or db.get_fulfillment(p) != "preorder":
            bot.answer_callback_query(call.id, "Product not available.")
            return
        uid = call.from_user.id
        eff = db.get_price_for_user(p, uid)
        kb = types.InlineKeyboardMarkup(row_width=4)
        btns = [emojis.button(str(q), callback_data=f"preorderqty_{pid}_{q}")
                for q in _PREORDER_QTY_PRESETS]
        for i in range(0, len(btns), 4):
            kb.row(*btns[i:i + 4])
        kb.add(
            emojis.button("Back to Product", callback_data=f"prod_{pid}", emoji_id="5255703720078879038", lead="⬅️"),
            emojis.button("Main Menu", callback_data="main_menu", emoji_id="5042022053356504092", lead="🏠"),
        )
        text = (
            f"<b>Select Quantity</b>\n\n"
            f"<b>{_esc(p['name'])}</b>\n"
            f"{fmt_usd(eff)} each\n\n"
            f"How many would you like?"
        )
        _reply(bot, call, text, kb)
        bot.answer_callback_query(call.id)

    def _show_preorder_confirm(uid: int, msg_id: int, pid: int, qty: int) -> bool:
        """Render the pre-order confirm screen. False if unavailable."""
        p = db.get_product(pid)
        if not p or not p["active"] or db.get_fulfillment(p) != "preorder":
            return False
        eff = db.get_price_for_user(p, uid)
        total = eff * qty
        bal = db.get_balance(uid)
        if bal < total:
            text = (
                f"<b>{_esc(p['name'])}</b> × {qty}\n"
                f"Total: <b>{fmt_usd(total)}</b>\n\n"
                f"<b>Insufficient balance.</b>\n"
                f"Your balance is {fmt_usd(bal)} — <b>{fmt_usd(total - bal)}</b> short.\n\n"
                f"Top up to continue."
            )
            edit_or_send(bot, uid, msg_id, text, _fulfill_insufficient_kb(pid, f"preorder_{pid}"))
            return True
        kb = types.InlineKeyboardMarkup(row_width=2)
        kb.add(
            emojis.button(f"Confirm — pay {fmt_usd(total)}", callback_data=f"preorderconfirm_{pid}_{qty}_{eff}",
                          emoji_id="5039844895779455925", lead="✅"),
            emojis.button("Cancel", callback_data=f"prod_{pid}", emoji_id="5210952531676504517", lead="❌"),
        )
        edit_or_send(bot, uid, msg_id, preorder_confirm_text(_esc(p["name"]), qty, fmt_usd(total)), kb)
        return True

    @bot.callback_query_handler(func=lambda c: c.data.startswith("preorderqty_"))
    def cb_preorder_qty(call: types.CallbackQuery):
        parts = call.data[len("preorderqty_"):].split("_")
        pid, qty = int(parts[0]), max(1, int(parts[1]))
        if not _show_preorder_confirm(call.from_user.id, call.message.message_id, pid, qty):
            bot.answer_callback_query(call.id, "Product not available.")
            return
        bot.answer_callback_query(call.id)

    @bot.callback_query_handler(func=lambda c: c.data.startswith("preorderconfirm_"))
    def cb_preorder_confirm(call: types.CallbackQuery):
        # preorderconfirm_<pid>_<qty>_<quoted_price>
        parts  = call.data[len("preorderconfirm_"):].split("_")
        pid    = int(parts[0])
        qty    = max(1, int(parts[1]))
        quoted = int(parts[2]) if len(parts) > 2 else None
        uid    = call.from_user.id
        uname  = call.from_user.username or ""
        if not check_rate_limit(uid, "buy", min_seconds=5):
            bot.answer_callback_query(call.id, "Please wait a moment before purchasing again.", show_alert=True)
            return
        p = db.get_product(pid)
        if not p or not p["active"] or db.get_fulfillment(p) != "preorder":
            bot.answer_callback_query(call.id, "Product not available.")
            return
        eff_price = db.get_price_for_user_by_id(pid, uid)
        if quoted is not None and eff_price > quoted:
            bot.answer_callback_query(call.id, "The price has been updated — please review and confirm again.", show_alert=True)
            _show_preorder_confirm(uid, call.message.message_id, pid, qty)
            return
        total = eff_price * qty
        db.ensure_user(uid, uname)
        # Charge + create the order atomically: a crash between the two
        # can never leave the customer charged with no order (V1.5).
        oid = db.create_preorder_atomic(uid, uname, pid, p["name"], total)
        if not oid:
            bal = db.get_balance(uid)
            bot.answer_callback_query(
                call.id,
                purchase_insufficient_balance(fmt_mmk(total), fmt_mmk(bal), fmt_mmk(total - bal)),
                show_alert=True,
            )
            return
        try:
            db.touch_reseller_activity(uid)
        except Exception:
            pass
        _fulfill_notify_admin(oid)
        try:
            bot.edit_message_text(preorder_received(), uid, call.message.message_id,
                                  reply_markup=back_kb("my_orders"))
        except Exception:
            bot.send_message(uid, preorder_received(), reply_markup=back_kb("my_orders"))
        log_vouch(bot, _esc(p["name"]), qty, None if is_reseller(uid) else total, uname)
        # total_spent is now updated atomically inside create_preorder_atomic
        _vip_tier_up_check(uid, total)
        bot.answer_callback_query(call.id, "Pre-order placed!")

    # ── cancel a pending manual / pre-order ───────────────────────────────────

    @bot.callback_query_handler(func=lambda c: c.data.startswith("manual_cancel_yes_"))
    def cb_manual_cancel_yes(call: types.CallbackQuery):
        oid = int(call.data[len("manual_cancel_yes_"):])
        uid = call.from_user.id
        if _refund_manual_order(uid, oid):
            try:
                bot.edit_message_text(preorder_cancelled(), uid, call.message.message_id,
                                      reply_markup=back_kb("my_orders"))
            except Exception:
                bot.send_message(uid, preorder_cancelled(), reply_markup=back_kb("my_orders"))
            bot.answer_callback_query(call.id, "Order cancelled — refunded.")
        else:
            bot.answer_callback_query(call.id, "This order can't be cancelled anymore.", show_alert=True)

    @bot.callback_query_handler(func=lambda c: c.data.startswith("manual_cancel_"))
    def cb_manual_cancel(call: types.CallbackQuery):
        oid = int(call.data[len("manual_cancel_"):])
        order = db.get_order(oid)
        if not order or order["telegram_id"] != call.from_user.id:
            bot.answer_callback_query(call.id, "Order not found.")
            return
        kb = types.InlineKeyboardMarkup(row_width=2)
        kb.add(
            emojis.button("Yes, cancel & refund", callback_data=f"manual_cancel_yes_{oid}",
                          emoji_id="5210952531676504517", lead="❌"),
            emojis.button("Keep my order", callback_data=f"order_detail_{oid}",
                          emoji_id="5255703720078879038", lead="⬅️"),
        )
        _reply(bot, call,
               f"<b>Cancel Order #{oid}?</b>\n\n"
               f"The full amount ({fmt_money(order['amount_mmk'])}) will go back to your wallet.",
               kb)
        bot.answer_callback_query(call.id)

    # ── my orders ─────────────────────────────────────────────────────────────

    @bot.callback_query_handler(func=lambda c: c.data == "my_orders")
    def cb_my_orders(call: types.CallbackQuery):
        uid    = call.from_user.id
        orders = db.list_orders(limit=15, telegram_id=uid)
        if not orders:
            bot.edit_message_text(
                "<b>No Orders Yet</b>\n\nYour purchases will appear here.",
                uid, call.message.message_id,
                reply_markup=back_kb("main_menu"),
            )
            bot.answer_callback_query(call.id)
            return
        kb = types.InlineKeyboardMarkup(row_width=1)
        for o in orders:
            kb.add(emojis.button(
                f"#{o['order_id']} · {o['product_name']} · {fmt_mmk(o['amount_mmk'])} · {o['created_at'][:10]}",
                callback_data=f"order_detail_{o['order_id']}",
            ))
        kb.add(emojis.button("Main Menu", callback_data="main_menu", emoji_id="5042022053356504092", lead="🏠"))
        bot.edit_message_text(
            "<b>Your Orders</b>\n\nSelect an order to view its credentials:",
            uid, call.message.message_id,
            reply_markup=kb,
        )
        bot.answer_callback_query(call.id)

    # ── order detail (re-delivery) ────────────────────────────────────────────

    @bot.callback_query_handler(func=lambda c: c.data.startswith("order_detail_"))
    def cb_order_detail(call: types.CallbackQuery):
        oid    = int(call.data[len("order_detail_"):])
        uid    = call.from_user.id
        orders = db.list_orders(limit=200, telegram_id=uid)
        order  = next((o for o in orders if o["order_id"] == oid), None)
        if not order:
            bot.answer_callback_query(call.id, "Order not found.")
            return
        _prod    = db.get_product(order["product_id"])
        # ── fulfillment v2: manual / pre-order detail ──────────────────────────
        _prod_mode = db.get_fulfillment(_prod) if _prod else "instant"
        _order_mode = db.get_order_fulfillment(order)
        if order["status"] in ("awaiting_info", "awaiting_fulfillment", "fulfilled") or \
           (order["status"] == "refunded" and _order_mode != "instant"):
            _mode = _order_mode
            _lines = [
                f"<b>Order #{oid}</b>\n",
                f"Product: {_esc(order['product_name'])}",
                f"Paid: {fmt_money(order['amount_mmk'])}",
                f"Date: {fmt_date(order['created_at'])}",
                f"Status: {fulfillment_status_line(order['status'])}",
            ]
            _info = db.get_manual_info(order)
            if _info:
                _lines.append("")
                _lines += [f"• <b>{_esc(k)}:</b> <code>{_esc(v)}</code>"
                           for k, v in _info.items()]
            _fmt = _prod["credential_format"] if _prod else ""
            if order["status"] == "fulfilled" and order["credential"]:
                _lines.append("")
                _lines.append(format_credential_block(order["credential"], _fmt))
            _kb = types.InlineKeyboardMarkup(row_width=1)
            if order["status"] in ("awaiting_info", "awaiting_fulfillment"):
                _kb.add(emojis.button("Cancel & refund", callback_data=f"manual_cancel_{oid}",
                                      emoji_id="5210952531676504517", lead="❌"))
            elif order["credential"] and not db.order_has_pending_report(oid):
                _kb.add(emojis.button("⚠️ Report Problem", callback_data=f"report_{oid}"))
            # Buy again — re-enters the mode's own flow.
            if _prod and _prod["active"]:
                _entry = {"preorder": f"preorder_{_prod['product_id']}"}.get(_mode)
                if _entry:
                    _kb.add(emojis.button("🔁 Buy Again", callback_data=_entry))
                elif db.stock_count(order["product_id"]) > 0:
                    _kb.add(emojis.button("🔁 Buy Again", callback_data=f"prod_{order['product_id']}"))
            _kb.add(emojis.button("Back", callback_data="my_orders", emoji_id="5255703720078879038", lead="⬅️"))
            _kb.add(emojis.button("Main Menu", callback_data="main_menu", emoji_id="5042022053356504092", lead="🏠"))
            bot.edit_message_text("\n".join(_lines), uid, call.message.message_id, reply_markup=_kb)
            bot.answer_callback_query(call.id)
            return
        _fmt     = _prod["credential_format"] if _prod else ""
        cred_text = format_credential_block(order["credential"], _fmt)
        text = (
            f"<b>Order #{oid}</b>\n\n"
            f"Product: {_esc(order['product_name'])}\n"
            f"Paid: {fmt_money(order['amount_mmk'])}\n"
            f"Date: {fmt_date(order['created_at'])}\n\n"
            f"{cred_text}"
        )
        kb = types.InlineKeyboardMarkup(row_width=1)
        if order["status"] != "refunded":
            if db.order_has_pending_report(oid):
                kb.add(emojis.button("⏳ Problem Reported — under review", callback_data="noop"))
            else:
                kb.add(emojis.button("⚠️ Report Problem", callback_data=f"report_{oid}"))
        # Buy again — jumps to the product page (which re-checks stock,
        # pricing and reseller discounts before purchase).
        if _prod and _prod["active"] and db.stock_count(order["product_id"]) > 0:
            kb.add(emojis.button("🔁 Buy Again", callback_data=f"prod_{order['product_id']}"))
        kb.add(emojis.button("Back", callback_data="my_orders", emoji_id="5255703720078879038", lead="⬅️"))
        kb.add(emojis.button("Main Menu",  callback_data="main_menu", emoji_id="5042022053356504092", lead="🏠"))
        bot.edit_message_text(text, uid, call.message.message_id, reply_markup=kb)
        bot.answer_callback_query(call.id)

    # ── report a problem with an order (warranty) ─────────────────────────────

    @bot.callback_query_handler(func=lambda c: c.data.startswith("report_") and c.data[len("report_"):].isdigit())
    def cb_report_problem(call: types.CallbackQuery):
        uid   = call.from_user.id
        oid   = int(call.data[len("report_"):])
        order = db.get_order(oid)
        if not order or order["telegram_id"] != uid:
            bot.answer_callback_query(call.id, "Order not found.")
            return
        if db.order_has_pending_report(oid):
            bot.answer_callback_query(call.id, "This order already has a report under review.", show_alert=True)
            return
        prompt = bot.edit_message_text(
            f"<b>Report a Problem</b> — Order #{oid}\n\n"
            f"Product: {_esc(order['product_name'])}\n\n"
            "Please briefly describe the issue (incorrect password, locked account, "
            "code already used, etc.) — or send <code>-</code> to skip:",
            uid, call.message.message_id,
            reply_markup=back_kb(f"order_detail_{oid}"),
        )
        set_state(uid, step="order_report", report_oid=oid, prompt_msg_id=prompt.message_id)
        bot.answer_callback_query(call.id)

    @bot.message_handler(func=lambda m: get_state(m.from_user.id).get("step") == "order_report")
    def msg_order_report(msg: types.Message):
        uid       = msg.from_user.id
        uname     = msg.from_user.username or ""
        st        = get_state(uid)
        oid       = st.get("report_oid")
        prompt_id = st.get("prompt_msg_id")
        text      = (msg.text or "").strip()
        silent_delete(bot, uid, msg.message_id)

        if not check_rate_limit(uid, "order_report", min_seconds=30):
            try:
                bot.edit_message_text(
                    "Please wait a moment before submitting another report.",
                    uid, prompt_id, reply_markup=back_kb("my_orders"),
                )
            except Exception:
                pass
            return

        order = db.get_order(oid)
        if not order or order["telegram_id"] != uid:
            clear_state(uid)
            return
        note   = None if text in ("-", "") else text[:500]
        rep_id = db.create_order_report(oid, uid, uname, note)
        clear_state(uid)

        kb = types.InlineKeyboardMarkup(row_width=1)
        kb.add(emojis.button("⬅️ Back to Order", callback_data=f"order_detail_{oid}"))
        kb.add(emojis.button("Main Menu", callback_data="main_menu", emoji_id="5042022053356504092", lead="🏠"))
        if rep_id is None:
            try:
                bot.edit_message_text(
                    "<b>Already Reported</b>\n\nThis order is under review — we'll notify you once resolved.",
                    uid, prompt_id, reply_markup=kb,
                )
            except Exception:
                pass
            return
        try:
            bot.edit_message_text(
                f"<b>Report Submitted</b> — Order #{oid}\n\n"
                "Our team will review it and either replace the item or issue a refund. "
                "You'll be notified here once resolved.",
                uid, prompt_id, reply_markup=kb,
            )
        except Exception:
            bot.send_message(uid, "Report submitted — we'll notify you once resolved.", reply_markup=kb)

        # alert admins with one-tap actions
        kb_admin = types.InlineKeyboardMarkup(row_width=3)
        kb_admin.add(
            emojis.button("🔁 Replace", callback_data=f"adm_rep_re_{rep_id}"),
            emojis.button("♻️ Refund",  callback_data=f"adm_rep_rf_{rep_id}"),
            emojis.button("✖️ Dismiss", callback_data=f"adm_rep_dm_{rep_id}"),
        )
        stock_left = db.stock_count(order["product_id"])
        alert = (
            f"<b>Problem Report #{rep_id}</b>\n\n"
            f"<blockquote>Order: #{oid} — {_esc(order['product_name'])}\n"
            f"User: @{_esc(uname) if uname else uid} (<code>{uid}</code>)\n"
            f"Paid: {fmt_money(order['amount_mmk'])} · {fmt_date(order['created_at'])}\n"
            f"Stock left for replacement: <b>{stock_left}</b></blockquote>\n"
        )
        if note:
            alert += f"\n<blockquote>{_esc(note)}</blockquote>"
        notify_admin(bot, alert, reply_markup=kb_admin)

    # ── wallet (merged balance + top-up hub) ─────────────────────────────────────
    # "My Balance" and "My Wallet" were two near-identical screens showing the
    # same numbers. They are now one screen: everything renders the wallet.
    # The old "my_balance" callback is kept as an alias so buttons in already-
    # sent messages keep working.

    def _wallet_content(uid: int):
        u     = db.get_user(uid)
        bal   = u["mmk_balance"] if u else 0
        spent = u["total_spent"] if u else 0
        bonus = u["bonus_earned"] if u and "bonus_earned" in u.keys() else 0
        tier_name, tier_disc = db.get_user_vip_tier(uid)
        vip_line = ""
        if tier_name:
            vip_line = f"\n{emojis.emoji('vip', '👑')} <b>VIP:</b> {tier_name}" + (f" ({tier_disc}% off)" if tier_disc else "")
        bonus_line = f"\n{emojis.emoji('gift', '🎁')} <b>Bonus earned:</b> {fmt_usd(bonus)}" if bonus else ""
        text = (
            f"<b>My Wallet</b> {emojis.emoji('wallet', '💼')}\n\n"
            f"<blockquote>Available balance: <b>{fmt_usd(bal)}</b>\n"
            f"Total spent: <b>{fmt_usd(spent)}</b>{bonus_line}{vip_line}</blockquote>\n\n"
            "Top up to buy right away — no waiting."
        )
        kb = types.InlineKeyboardMarkup(row_width=2)
        kb.add(
            emojis.button("💳 Top Up", callback_data="topup_menu", emoji_id="5445353829304387411", lead="💳"),
            emojis.button("📋 My Deposits", callback_data="my_deposits", emoji_id="5444856076954520455", lead="📋"),
        )
        kb.add(emojis.button("⚡ Products", callback_data="shop_categories", emoji_id="5312361253610475399", lead="⚡"))
        kb.add(emojis.button("Main Menu", callback_data="main_menu", emoji_id="5042022053356504092", lead="🏠"))
        return text, kb

    @bot.callback_query_handler(func=lambda c: c.data == "wallet_menu")
    def cb_wallet_menu(call: types.CallbackQuery):
        text, kb = _wallet_content(call.from_user.id)
        _reply(bot, call, text, kb)
        bot.answer_callback_query(call.id)

    @bot.callback_query_handler(func=lambda c: c.data == "my_balance")
    def cb_my_balance(call: types.CallbackQuery):
        # Alias for older messages — renders the same wallet screen.
        text, kb = _wallet_content(call.from_user.id)
        _reply(bot, call, text, kb)
        bot.answer_callback_query(call.id)

    # ── profile ─────────────────────────────────────────────────────────────────

    @bot.callback_query_handler(func=lambda c: c.data == "my_profile")
    def cb_my_profile(call: types.CallbackQuery):
        uid   = call.from_user.id
        uname = call.from_user.username or ""
        db.ensure_user(uid, uname)
        u        = db.get_user(uid)
        bal      = u["mmk_balance"] if u else 0
        spent    = u["total_spent"] if u else 0
        orders   = db.count_orders_for_user(uid)
        reseller = is_reseller(uid)

        role = "Reseller" if reseller else "Customer"
        text = (
            f"<b>My Profile</b> {emojis.emoji('profile', '👤')}\n\n"
            f"<blockquote>ID: <code>{uid}</code>\n"
            f"Username: @{_esc(uname) if uname else '—'}\n"
            f"Status: <b>{role}</b>\n"
            f"Balance: <b>{fmt_usd(bal)}</b>\n"
            f"Total spent: <b>{fmt_usd(spent)}</b>\n"
            f"Orders: <b>{orders}</b>\n"
            f"Member since: {fmt_date(u['registered_at']) if u else '—'}\n"
            f"</blockquote>"
        )
        if reseller and u:
            text += f"\n<i>Last reseller order: {fmt_date(u['last_reseller_purchase'])}</i>"
        kb = types.InlineKeyboardMarkup(row_width=2)
        kb.add(
            emojis.button("🧾 Purchase History", callback_data="my_orders", emoji_id="5444856076954520455", lead="🧾"),
            emojis.button("💎 Wallet", callback_data="wallet_menu", emoji_id="5445353829304387411", lead="💎"),
        )
        if reseller:
            kb.add(emojis.button("🏪 Reseller Hub", callback_data="reseller_hub", emoji_id="5039834781131474002", lead="🏪"))
        else:
            kb.add(emojis.button("🔑 Become a Reseller", callback_data="reseller_info", emoji_id="5039834781131474002", lead="🔑"))
        kb.add(emojis.button("Main Menu", callback_data="main_menu", emoji_id="5042022053356504092", lead="🏠"))
        _reply(bot, call, text, kb)
        bot.answer_callback_query(call.id)

    # ── my deposits ───────────────────────────────────────────────────────────

    @bot.callback_query_handler(func=lambda c: c.data == "my_deposits")
    def cb_my_deposits(call: types.CallbackQuery):
        uid  = call.from_user.id
        deps = db.list_deposits(limit=15, telegram_id=uid)
        status_icon = {"pending": f"{emojis.emoji('pending', '⏳')}", "approved": f"{emojis.emoji('success', '✅')}", "rejected": f"{emojis.emoji('rejected', '❌')}"}

        if not deps:
            bot.edit_message_text(
                "<b>No Deposits Yet</b>\n\nTop up to get started.",
                uid, call.message.message_id,
                reply_markup=back_kb("main_menu"),
            )
            bot.answer_callback_query(call.id)
            return

        kb = types.InlineKeyboardMarkup(row_width=1)
        for d in deps:
            icon   = status_icon.get(d["status"], f"{emojis.emoji('question', '❓')}")
            mlabel = _method_label(d["method"])
            kb.add(emojis.button(
                f"{icon} #{d['deposit_id']} · {mlabel} · {fmt_mmk(d['amount_mmk'])} · {d['created_at'][:10]}",
                callback_data=f"dep_detail_{d['deposit_id']}",
            ))
        kb.add(emojis.button("Main Menu", callback_data="main_menu", emoji_id="5042022053356504092", lead="🏠"))
        try:
            bot.edit_message_text(
                "<b>Your Deposits</b>\n\nSelect a deposit for details:",
                uid, call.message.message_id, reply_markup=kb,
            )
        except Exception:
            bot.send_message(uid, "<b>Your Deposits</b>\n\nSelect a deposit for details:", reply_markup=kb)
        bot.answer_callback_query(call.id)

    @bot.callback_query_handler(func=lambda c: c.data.startswith("dep_detail_"))
    def cb_dep_detail(call: types.CallbackQuery):
        uid    = call.from_user.id
        dep_id = int(call.data[len("dep_detail_"):])
        deps   = db.list_deposits(limit=50, telegram_id=uid)
        d      = next((x for x in deps if x["deposit_id"] == dep_id), None)
        if not d:
            bot.answer_callback_query(call.id, "Deposit not found.")
            return
        status_icon = {"pending": "Pending", "approved": "Approved", "rejected": "Rejected"}
        status_text = status_icon.get(d["status"], d["status"])
        text = (
            f"<b>Deposit #{dep_id}</b>\n\n"
            f"Method: {_method_label(d['method'])}\n"
            f"Amount: <b>{fmt_money(d['amount_mmk'])}</b>\n"
            f"Status: {status_text}\n"
            f"Date: {fmt_date(d['created_at'])}"
        )
        if d["status"] == "rejected" and d["note"]:
            text += f"\nReason: {_esc(d['note'])}"
        kb = types.InlineKeyboardMarkup(row_width=2)
        kb.add(
            emojis.button("Back", callback_data="my_deposits", emoji_id="5255703720078879038", lead="⬅️"),
            emojis.button("Main Menu",  callback_data="main_menu", emoji_id="5042022053356504092", lead="🏠"),
        )
        bot.edit_message_text(f"<blockquote>{text}</blockquote>", uid, call.message.message_id, reply_markup=kb)
        bot.answer_callback_query(call.id)

    @bot.callback_query_handler(func=lambda c: c.data == "noop")
    def cb_noop(call: types.CallbackQuery):
        bot.answer_callback_query(call.id)

    # ── notification opt-out ──────────────────────────────────────────────────

    @bot.callback_query_handler(func=lambda c: c.data == "notif_optout")
    def cb_notif_optout(call: types.CallbackQuery):
        uid = call.from_user.id
        db.set_notify_opt_out(uid, True)
        bot.answer_callback_query(
            call.id,
            "Notifications muted. You can re-enable them anytime.",
            show_alert=True,
        )
        _reply(bot, call, _main_menu_text(uid), main_menu_kb(uid))

    @bot.callback_query_handler(func=lambda c: c.data == "notif_optin")
    def cb_notif_optin(call: types.CallbackQuery):
        uid = call.from_user.id
        db.set_notify_opt_out(uid, False)
        bot.answer_callback_query(
            call.id,
            "Notifications enabled — you'll receive stock, offer, and giveaway updates.",
            show_alert=True,
        )
        _reply(bot, call, _main_menu_text(uid), main_menu_kb(uid))

    # ── dismiss (delete message from user's chat) ─────────────────────────────

    @bot.callback_query_handler(func=lambda c: c.data == "dismiss_msg")
    def cb_dismiss_msg(call: types.CallbackQuery):
        silent_delete(bot, call.from_user.id, call.message.message_id)
        bot.answer_callback_query(call.id)

    # ── reseller: info / become a reseller ────────────────────────────────────

    @bot.callback_query_handler(func=lambda c: c.data == "reseller_info")
    def cb_reseller_info(call: types.CallbackQuery):
        uid = call.from_user.id
        if is_reseller(uid):
            # already a reseller — bounce to the hub
            _render_reseller_hub(uid, call.message.message_id)
            bot.answer_callback_query(call.id)
            return
        days = db.cfg_get("reseller_inactivity_days", "30")
        pending = db.list_reseller_requests(status="pending")
        has_pending = any(r["telegram_id"] == uid for r in pending)
        text = (
            f"<b>Become a Reseller</b> {emojis.emoji('key', '🔑')}\n\n"
            "Get special reseller prices and buy in bigger amounts.\n\n"
            "<blockquote>"
            "• You get in with a one-time activation key.\n"
            f"• Just buy at least once every <b>{days} days</b> to keep it.\n"
            "• Lost access? You can always ask again."
            "</blockquote>\n\n"
            "Already have a key? Tap <b>Activate Key</b>.\n"
            "If not, tap <b>Request Access</b> and we'll take a look."
        )
        kb = types.InlineKeyboardMarkup(row_width=1)
        kb.add(emojis.button("🔑 Activate Key", callback_data="reseller_activate"))
        if has_pending:
            kb.add(emojis.button("⏳ Request Pending", callback_data="noop"))
        else:
            kb.add(emojis.button("📨 Request Access", callback_data="reseller_request"))
        kb.add(emojis.button("Main Menu", callback_data="main_menu", emoji_id="5042022053356504092", lead="🏠"))
        _reply(bot, call, text, kb)
        bot.answer_callback_query(call.id)

    # ── reseller: activate key ─────────────────────────────────────────────────

    @bot.callback_query_handler(func=lambda c: c.data == "reseller_activate")
    def cb_reseller_activate(call: types.CallbackQuery):
        uid = call.from_user.id
        if is_reseller(uid):
            bot.answer_callback_query(call.id, "You're already a reseller.", show_alert=True)
            return
        prompt = bot.edit_message_text(
            "<b>Activate Reseller Key</b>\n\n"
            "Paste your one-time key below (e.g. <code>A7K9-XPQ2-M4RT</code>):",
            uid, call.message.message_id, reply_markup=back_kb("reseller_info"),
        )
        set_state(uid, step="reseller_activate", prompt_msg_id=prompt.message_id)
        bot.answer_callback_query(call.id)

    @bot.message_handler(func=lambda m: get_state(m.from_user.id).get("step") == "reseller_activate")
    def msg_reseller_activate(msg: types.Message):
        uid       = msg.from_user.id
        uname     = msg.from_user.username or ""
        st        = get_state(uid)
        prompt_id = st.get("prompt_msg_id")
        code      = (msg.text or "").strip().upper()
        silent_delete(bot, uid, msg.message_id)

        if not check_rate_limit(uid, "reseller_activate", min_seconds=3):
            return

        db.ensure_user(uid, uname)
        result = db.redeem_reseller_key(code, uid)

        if result == "ok":
            clear_state(uid)
            kb = types.InlineKeyboardMarkup(row_width=1)
            kb.add(emojis.button("🏪 Open Reseller Hub", callback_data="reseller_hub", emoji_id="5039834781131474002", lead="🏪"))
            kb.add(emojis.button("Main Menu", callback_data="main_menu", emoji_id="5042022053356504092", lead="🏠"))
            try:
                bot.edit_message_text(
                    f"<b>Welcome, Reseller! {emojis.emoji('party', '🎉')}</b>\n\n"
                    "Your reseller prices are now on — open the Reseller Hub to start.\n\n"
                    f"<i>Heads up: buy at least once every "
                    f"{db.cfg_get('reseller_inactivity_days', '30')} days to keep it.</i>",
                    uid, prompt_id, reply_markup=kb,
                )
            except Exception:
                bot.send_message(uid, f"Welcome, reseller! {emojis.emoji('party', '🎉')}", reply_markup=kb)
            notify_admin(
                bot,
                f"<b>New Reseller Activated</b>\n\n"
                f"User: @{_esc(uname)} (<code>{uid}</code>)\n"
                f"Key: <code>{_esc(code)}</code>",
            )
            return

        errors = {
            "not_found":        "This key doesn't exist. Please check for typos and try again.",
            "used":             "This key has already been redeemed.",
            "revoked":          "This key has been revoked. Please request a new one from our team.",
            "already_reseller": "You already hold reseller status.",
        }
        try:
            bot.edit_message_text(
                f"<b>Activate Reseller Key</b>\n\n{errors.get(result, 'Activation unsuccessful.')}\n\n"
                "Please enter a valid key, or go back.",
                uid, prompt_id, reply_markup=back_kb("reseller_info"),
            )
        except Exception:
            pass

    # ── reseller: request access ───────────────────────────────────────────────

    @bot.callback_query_handler(func=lambda c: c.data == "reseller_request")
    def cb_reseller_request(call: types.CallbackQuery):
        uid = call.from_user.id
        if is_reseller(uid):
            bot.answer_callback_query(call.id, "You're already a reseller.", show_alert=True)
            return
        prompt = bot.edit_message_text(
            "<b>Request Reseller Access</b>\n\n"
            "Briefly tell us why you'd like reseller access — what you plan to offer, "
            "your expected volume, etc. (or send <code>-</code> to skip):",
            uid, call.message.message_id, reply_markup=back_kb("reseller_info"),
        )
        set_state(uid, step="reseller_request", prompt_msg_id=prompt.message_id)
        bot.answer_callback_query(call.id)

    @bot.message_handler(func=lambda m: get_state(m.from_user.id).get("step") == "reseller_request")
    def msg_reseller_request(msg: types.Message):
        uid       = msg.from_user.id
        uname     = msg.from_user.username or ""
        st        = get_state(uid)
        prompt_id = st.get("prompt_msg_id")
        message   = (msg.text or "").strip()
        clear_state(uid)
        silent_delete(bot, uid, msg.message_id)

        if not check_rate_limit(uid, "reseller_request", min_seconds=30):
            try:
                bot.edit_message_text(
                    "Please wait a moment before submitting again.",
                    uid, prompt_id, reply_markup=back_kb("main_menu"),
                )
            except Exception:
                pass
            return

        db.ensure_user(uid, uname)
        note   = None if message in ("-", "") else message
        req_id = db.create_reseller_request(uid, uname, note)

        kb = types.InlineKeyboardMarkup(row_width=1)
        kb.add(emojis.button("Main Menu", callback_data="main_menu", emoji_id="5042022053356504092", lead="🏠"))
        if req_id is None:
            try:
                bot.edit_message_text(
                    "<b>Request Already Pending</b>\n\n"
                    "Our team will review it shortly. Thank you for your patience.",
                    uid, prompt_id, reply_markup=kb,
                )
            except Exception:
                pass
            return

        try:
            bot.edit_message_text(
                "<b>Request Received</b>\n\n"
                "Our team will review your application and, if approved, send you a "
                "one-time activation key. You'll be notified here.",
                uid, prompt_id, reply_markup=kb,
            )
        except Exception:
            bot.send_message(uid, "Request submitted!", reply_markup=kb)

        # alert admins with a quick-action button
        kb_admin = types.InlineKeyboardMarkup()
        kb_admin.add(emojis.button("👀 Review Request", callback_data=f"adm_res_req_{req_id}"))
        u = db.get_user(uid)
        orders = db.count_orders_for_user(uid)
        spent  = fmt_money(u["total_spent"]) if u else "—"
        alert = (
            f"<b>New Reseller Request</b> {emojis.emoji('key', '🔑')}\n\n"
            f"<blockquote>@{_esc(uname)} (<code>{uid}</code>)\n"
            f"Orders: {orders} · Spent: {spent}</blockquote>\n"
        )
        if note:
            alert += f"{_esc(note)}\n"
        notify_admin(bot, alert, reply_markup=kb_admin)

    # ── reseller: hub ──────────────────────────────────────────────────────────

    def _render_reseller_hub(uid: int, msg_id: int):
        if not is_reseller(uid):
            # lost access (e.g. inactivity revoke) — send them to the info page
            kb = types.InlineKeyboardMarkup(row_width=1)
            kb.add(emojis.button("🔑 Become a Reseller", callback_data="reseller_info", emoji_id="5039834781131474002", lead="🔑"))
            kb.add(emojis.button("Main Menu", callback_data="main_menu", emoji_id="5042022053356504092", lead="🏠"))
            try:
                bot.edit_message_text(
                    "<b>Reseller Hub</b>\n\nReseller access is not currently active on your account.",
                    uid, msg_id, reply_markup=kb,
                )
            except Exception:
                bot.send_message(uid, "Reseller access is not currently active on your account.", reply_markup=kb)
            return
        u    = db.get_user(uid)
        bal  = u["mmk_balance"] if u else 0
        days = db.cfg_get("reseller_inactivity_days", "30")
        text = (
            f"<b>Reseller Hub</b> {emojis.emoji('store', '🏪')}\n\n"
            f"<blockquote>Balance: <b>{fmt_usd(bal)}</b>\n"
            f"Last purchase: {fmt_date(u['last_reseller_purchase']) if u else '—'}</blockquote>\n\n"
            f"You get reseller prices across the whole shop. "
            f"Just buy at least once every {days} days to keep it."
        )
        kb = types.InlineKeyboardMarkup(row_width=2)
        kb.add(
            emojis.button("🛍 Browse (Reseller Prices)", callback_data="shop_categories"),
            emojis.button("💳 Top Up", callback_data="topup_menu", emoji_id="5445353829304387411", lead="💳"),
        )
        kb.add(
            emojis.button("📦 My Orders", callback_data="my_orders", emoji_id="5444856076954520455", lead="📦"),
            emojis.button("💰 My Balance", callback_data="wallet_menu"),
        )
        kb.add(emojis.button("Main Menu", callback_data="main_menu", emoji_id="5042022053356504092", lead="🏠"))
        try:
            bot.edit_message_text(text, uid, msg_id, reply_markup=kb)
        except Exception:
            bot.send_message(uid, text, reply_markup=kb)

    @bot.callback_query_handler(func=lambda c: c.data == "reseller_hub")
    def cb_reseller_hub(call: types.CallbackQuery):
        _render_reseller_hub(call.from_user.id, call.message.message_id)
        bot.answer_callback_query(call.id)

    # ── support ───────────────────────────────────────────────────────────────

    @bot.callback_query_handler(func=lambda c: c.data == "support")
    def cb_support(call: types.CallbackQuery):
        url        = db.cfg_get("support_url", "")
        shop_name  = db.cfg_get("shop_name", "our shop")
        kb = types.InlineKeyboardMarkup(row_width=1)
        if url:
            kb.add(emojis.button("💬 Contact Support", url=url, emoji_id="5039532814865794117", lead="💬"))
        kb.add(emojis.button("Main Menu", callback_data="main_menu", emoji_id="5042022053356504092", lead="🏠"))
        if url:
            text = f"<b>Support</b>\n\nNeed assistance with {shop_name}? Our team is one tap away."
        else:
            admin_username = db.cfg_get("admin_username", "")
            if admin_username:
                text = f"<b>Support</b>\n\nReach us directly: @{admin_username}"
            else:
                text = f"<b>Support</b>\n\nFor help with your order or account, please reach out to our team directly."
        bot.edit_message_text(text, call.from_user.id, call.message.message_id, reply_markup=kb)
        bot.answer_callback_query(call.id)
