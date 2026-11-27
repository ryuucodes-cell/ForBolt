"""Admin: product and stock management."""

import html
import threading
import time

import telebot
from telebot import types

import db
import emojis
from utils import fmt_mmk, parse_mmk, admin_only, set_state, get_state, clear_state, back_kb, silent_delete
from msg_templates import stock_announcement, product_announcement


def _esc(s) -> str:
    return html.escape(str(s))


def _extract_emoji(msg) -> tuple:
    """Return (fallback_char, custom_emoji_id) from an admin's icon message.

    If they sent a Telegram Premium (custom) emoji, its numeric id is captured
    from the message entities and the visible glyph is kept as the fallback.
    Otherwise the id is '' and we just use the typed character.
    """
    text = (msg.text or "").strip()
    emoji_id = ""
    entities = msg.entities or []
    for ent in entities:
        if getattr(ent, "type", "") == "custom_emoji":
            cid = getattr(ent, "custom_emoji_id", None)
            if cid:
                emoji_id = str(cid)
                # the fallback glyph is the substring the entity covers
                try:
                    text = text[ent.offset:ent.offset + ent.length]
                except Exception:
                    pass
                break
    return text[:8], emoji_id


def register(bot: telebot.TeleBot):

    # ── product list ──────────────────────────────────────────────────────────

    @bot.callback_query_handler(func=lambda c: c.data == "adm_products")
    @admin_only
    def cb_adm_products(call: types.CallbackQuery):
        prods = db.list_products(active_only=False)
        kb    = types.InlineKeyboardMarkup(row_width=1)
        for p in prods:
            cnt    = db.stock_count(p["product_id"])
            status = f"{emojis.emoji('success', '✅')}" if p["active"] else f"{emojis.emoji('no_entry', '🚫')}"
            emoji  = db.product_emoji(p, default="")
            kb.add(emojis.button(
                f"{status} {(emoji + ' ') if emoji else ''}{p['name']} | {fmt_mmk(p['price_mmk'])} | Stock: {cnt}",
                callback_data=f"adm_prod_{p['product_id']}",
            ))
        kb.add(
            emojis.button("➕ Add Product", callback_data="adm_add_product", emoji_id="5039891861246838069", lead="➕"),
            emojis.button("Back",        callback_data="admin_panel", emoji_id="5255703720078879038", lead="⬅️"),
            emojis.button("Main Menu",         callback_data="main_menu", emoji_id="5042022053356504092", lead="🏠"),
        )
        bot.edit_message_text(
            "<b>Products</b>",
            call.from_user.id, call.message.message_id, reply_markup=kb,
        )
        bot.answer_callback_query(call.id)

    # ── product detail ────────────────────────────────────────────────────────

    def _show_prod_detail(uid: int, msg_id: int, pid: int):
        p   = db.get_product(pid)
        if not p:
            return
        cnt        = db.stock_count(pid)
        disc_active = db.is_discount_active(p)
        eff_price  = db.get_effective_price(p)

        disc_line = ""
        if disc_active:
            from datetime import datetime, timezone, timedelta
            _ygn = timezone(timedelta(hours=6, minutes=30))
            until_dt  = datetime.fromisoformat(p["discount_until"]).replace(tzinfo=timezone.utc).astimezone(_ygn)
            disc_line = f"\nDiscount: <b>{p['discount_pct']}% OFF</b> → {fmt_mmk(eff_price)} (until {until_dt.strftime('%H:%M, %d %b')})"

        rp = db._reseller_price(p)
        reseller_line = f"\nReseller price: <b>{fmt_mmk(rp)}</b>" if rp is not None else "\nReseller price: <i>not set (uses retail)</i>"

        emoji    = db.product_emoji(p, default="")
        emoji_id = db.product_emoji_id(p)
        icon_html = db.product_emoji_html(p, default="")
        icon_desc = (f"{icon_html} <i>(premium)</i>" if emoji_id else emoji) or f"<i>none (uses {emojis.emoji('package', '📦')})</i>"
        _fmode = db.get_fulfillment(p)
        _flabel = {"instant": "Instant",
                   "preorder": "Pre-order"}.get(_fmode, _fmode)
        text = (
            f"{(icon_html + ' ') if emoji else ''}<b>{p['name']}</b>\n"
            f"Icon: {icon_desc}\n"
            f"Category: {(p['category'] or '').strip() or '<i>none (shows under Other)</i>'}\n"
            f"Price: {fmt_mmk(p['price_mmk'])}{disc_line}{reseller_line}\n"
            f"Description: {p['description']}\n"
            f"Credential format: <code>{p['credential_format']}</code>\n"
            f"Fulfillment: <b>{_flabel}</b>\n"
            f"Stock: {cnt}\n"
            f"Status: {'Active' if p['active'] else 'Hidden'}"
        )
        kb = types.InlineKeyboardMarkup(row_width=2)
        kb.add(
            emojis.button("✏️ Name",        callback_data=f"adm_pe_{pid}_name"),
            emojis.button("✏️ Price",       callback_data=f"adm_pe_{pid}_price_mmk"),
            emojis.button("😀 Icon",         callback_data=f"adm_pe_{pid}_emoji"),
            emojis.button("✏️ Description", callback_data=f"adm_pe_{pid}_description"),
            emojis.button("✏️ Cred Format", callback_data=f"adm_pe_{pid}_credential_format"),
        )
        kb.add(emojis.button("📂 Category", callback_data=f"adm_pe_{pid}_category"))
        # fulfillment mode switch (current mode marked ✓)
        _fm = db.get_fulfillment(p)
        kb.add(
            emojis.button(f"{'✅ ' if _fm=='instant' else ''}⚡ Instant",
                          callback_data=f"adm_pfulfill_{pid}_instant",
                          emoji_id="5042334757040423886", lead="⚡"),
            emojis.button(f"{'✅ ' if _fm=='preorder' else ''}📦 Pre-order",
                          callback_data=f"adm_pfulfill_{pid}_preorder",
                          emoji_id="5440621591387980068", lead="📦"),
        )
        rp_btns = [emojis.button("🏪 Reseller Price", callback_data=f"adm_pe_{pid}_reseller_price_mmk")]
        if rp is not None:
            rp_btns.append(emojis.button("🏪❌ Clear Reseller", callback_data=f"adm_prpclear_{pid}"))
        kb.add(*rp_btns)
        kb.add(
            emojis.button("🚫 Hide" if p["active"] else "Show",
                callback_data=f"adm_ptoggle_{pid}",
            ),
            emojis.button("📥 Add Stock",  callback_data=f"adm_addstock_{pid}"),
            emojis.button("📋 View Stock", callback_data=f"adm_stocklist_{pid}_0"),
        )
        if disc_active:
            kb.add(emojis.button("❌ Remove Discount", callback_data=f"adm_discoff_{pid}"))
        else:
            kb.add(emojis.button("🏷 Set Discount", callback_data=f"adm_disc_{pid}"))
        kb.add(
            emojis.button("🗑 Delete",  callback_data=f"adm_pdel_{pid}"),
            emojis.button("Back",    callback_data="adm_products", emoji_id="5255703720078879038", lead="⬅️"),
            emojis.button("Main Menu",    callback_data="main_menu", emoji_id="5042022053356504092", lead="🏠"),
        )
        try:
            bot.edit_message_text(text, uid, msg_id, reply_markup=kb)
        except Exception:
            pass

    @bot.callback_query_handler(func=lambda c: c.data.startswith("adm_prod_") and len(c.data.split("_")) == 3)
    @admin_only
    def cb_adm_prod_detail(call: types.CallbackQuery):
        pid = int(call.data.split("_")[2])
        _show_prod_detail(call.from_user.id, call.message.message_id, pid)
        bot.answer_callback_query(call.id)

    # ── toggle active ─────────────────────────────────────────────────────────

    @bot.callback_query_handler(func=lambda c: c.data.startswith("adm_ptoggle_"))
    @admin_only
    def cb_adm_ptoggle(call: types.CallbackQuery):
        pid = int(call.data[len("adm_ptoggle_"):])
        p   = db.get_product(pid)
        db.update_product(pid, active=0 if p["active"] else 1)
        _show_prod_detail(call.from_user.id, call.message.message_id, pid)
        bot.answer_callback_query(call.id, "Toggled.")

    # ── fulfillment mode (instant / manual activation / pre-order) ────────────

    def _ask_fulfillment_mode(uid: int, msg_id: int, pid: int, wizard: bool = False):
        kb = types.InlineKeyboardMarkup(row_width=1)
        kb.add(emojis.button("⚡ Instant — auto-delivery from stock",
                             callback_data=f"adm_pfulfill_{pid}_instant",
                             emoji_id="5042334757040423886", lead="⚡"))
        kb.add(emojis.button("📦 Pre-order — buyer waits, admin sends credentials later",
                             callback_data=f"adm_pfulfill_{pid}_preorder",
                             emoji_id="5440621591387980068", lead="📦"))
        text = ("<b>Choose Fulfillment</b>\n\nHow is this product delivered?" if wizard
                else "<b>Fulfillment</b>\n\nHow is this product delivered?")
        try:
            bot.edit_message_text(text, uid, msg_id, reply_markup=kb)
        except Exception:
            bot.send_message(uid, text, reply_markup=kb)

    @bot.callback_query_handler(func=lambda c: c.data.startswith("adm_pfulfill_"))
    @admin_only
    def cb_adm_pfulfill(call: types.CallbackQuery):
        # adm_pfulfill_<pid>_<mode>  (mode may itself contain underscores)
        rest = call.data[len("adm_pfulfill_"):].split("_")
        pid  = int(rest[0])
        mode = "_".join(rest[1:])
        p = db.get_product(pid)
        if not p or mode not in ("instant", "preorder"):
            bot.answer_callback_query(call.id, "Invalid.")
            return
        # brand-new product? (set by the add-product wizard — not when editing)
        st = get_state(call.from_user.id)
        is_new = st.get("step") == "fulfill_wizard" and st.get("pid") == pid
        clear_state(call.from_user.id)
        db.set_product_fulfillment(pid, mode, "")
        bot.answer_callback_query(call.id, "Fulfillment updated.")
        _show_prod_detail(call.from_user.id, call.message.message_id, pid)
        if is_new and mode == "preorder":
            threading.Thread(target=_announce_new_product, args=(bot, pid), daemon=True).start()

    # ── clear reseller price ──────────────────────────────────────────────────

    @bot.callback_query_handler(func=lambda c: c.data.startswith("adm_prpclear_"))
    @admin_only
    def cb_adm_prpclear(call: types.CallbackQuery):
        pid = int(call.data[len("adm_prpclear_"):])
        db.update_product(pid, reseller_price_mmk=None)
        _show_prod_detail(call.from_user.id, call.message.message_id, pid)
        bot.answer_callback_query(call.id, "Reseller price cleared — retail pricing applies.")

    # ── delete product ────────────────────────────────────────────────────────

    @bot.callback_query_handler(func=lambda c: c.data.startswith("adm_pdel_") and not c.data.startswith("adm_pdel_confirm_"))
    @admin_only
    def cb_adm_pdel(call: types.CallbackQuery):
        pid = int(call.data[len("adm_pdel_"):])
        p   = db.get_product(pid)
        if not p:
            bot.answer_callback_query(call.id, "Product not found.")
            return
        cnt = db.stock_count(pid)
        if cnt > 0:
            bot.answer_callback_query(call.id, f"Cannot delete — {cnt} items remain in stock. Please remove stock first.", show_alert=True)
            return
        kb = types.InlineKeyboardMarkup(row_width=2)
        kb.add(
            emojis.button("Yes, Delete", callback_data=f"adm_pdel_confirm_{pid}", emoji_id="5039844895779455925", lead="🗑"),
            emojis.button("Cancel",       callback_data=f"adm_prod_{pid}", emoji_id="5210952531676504517", lead="❌"),
        )
        bot.edit_message_text(
            f"<b>Delete Product?</b>\n\n<b>{p['name']}</b>\n\nThis action cannot be undone.",
            call.from_user.id, call.message.message_id, reply_markup=kb,
        )
        bot.answer_callback_query(call.id)

    @bot.callback_query_handler(func=lambda c: c.data.startswith("adm_pdel_confirm_"))
    @admin_only
    def cb_adm_pdel_confirm(call: types.CallbackQuery):
        pid = int(call.data[len("adm_pdel_confirm_"):])
        ok  = db.delete_product(pid)
        if ok:
            bot.answer_callback_query(call.id, "Deleted.")
            bot.edit_message_text(
                "Product deleted.",
                call.from_user.id, call.message.message_id,
                reply_markup=back_kb("adm_products"),
            )
        else:
            bot.answer_callback_query(call.id, "Cannot delete — remove stock first.", show_alert=True)

    # ── edit field ────────────────────────────────────────────────────────────

    _FIELD_LABELS = {
        "name":               "Product Name",
        "category":           "Category",
        "price_mmk":          "Price (MMK)",
        "description":        "Description",
        "credential_format":  "Credential Format",
        "reseller_price_mmk": "Reseller Price (MMK)",
        "emoji":              "Product Icon",
    }

    _FIELD_HINTS = {
        "name": (
            "The product name shown to users in the shop.\n"
            "Example: <code>Netflix Premium 1 Month</code>"
        ),
        "category": (
            "Groups this product in the shop. Buyers first pick a category, then a "
            "product. Products with the same category name (exact match) are grouped "
            "together; products with no category appear under <b>Other</b>.\n"
            "The category menu only shows once you use 2 or more different groups — "
            "with 1 or none, the shop stays a simple flat list.\n"
            "Example: <code>Streaming</code>, <code>Gaming</code>, <code>VPN</code>\n"
            "Send <code>-</code> to clear (moves the product to Other)."
        ),
        "price_mmk": (
            "Price in MMK, whole kyats.\n"
            "Example: <code>12000</code> for 12,000 MMK"
        ),
        "reseller_price_mmk": (
            "Wholesale price resellers pay, in MMK. Should be lower than retail.\n"
            "Resellers see this instead of the retail price, and it ignores time-limited discounts.\n"
            "Example: <code>9000</code>. Leave via the Clear button to fall back to retail."
        ),
        "emoji": (
            "The icon for this product.\n"
            "• Send a normal emoji () — shows everywhere, including buttons.\n"
            "• Or send a <b>Telegram Premium</b> emoji — the bot captures its ID automatically "
            "and shows the animated version on the product page &amp; announcements "
            "(buttons still show the normal fallback — Telegram can't put custom emoji on buttons).\n"
            "• Send <code>-</code> to clear it (falls back to)."
        ),
        "description": (
            "Shown to users on the product page as a block quote. Explain what they get.\n"
            "Example: <code>After purchase you will receive a login email and password.</code>"
        ),
        "credential_format": (
            "Defines how each stock item is structured. Separate fields with <code>|</code>.\n\n"
            "Field names you can use:\n"
            "<code>email</code>, <code>username</code>, <code>password</code>, "
            "<code>otp-link</code>, <code>2fa</code>, <code>cdk</code>, "
            "<code>activation-link</code>\n\n"
            "The buyer's receipt labels each field by its name. Any field whose value is a link is "
            "always shown as tappable, so mixed stock still displays right.\n\n"
            "Format examples:\n"
            "• <code>email|password</code>\n"
            "• <code>email|password|2fa</code>\n"
            "• <code>email|password|recovery</code>\n"
            "• <code>code|pin</code>\n"
            "• <code>key</code>\n\n"
            "Matching stock lines:\n"
            "• <code>abc@gmail.com|Pass123</code>\n"
            "• <code>abc@gmail.com|Pass123|JBSWY3DPEHPK3PXP</code>\n"
            "• <code>abc@gmail.com|Pass123|backup@gmail.com</code>\n"
            "• <code>ABCD-1234-EFGH|4821</code>"
        ),
    }

    @bot.callback_query_handler(func=lambda c: c.data.startswith("adm_pe_"))
    @admin_only
    def cb_adm_pe(call: types.CallbackQuery):
        # format: adm_pe_{pid}_{field}  — field may contain underscores
        remainder = call.data[len("adm_pe_"):]          # "1_credential_format"
        pid_str, field = remainder.split("_", 1)         # "1", "credential_format"
        pid   = int(pid_str)
        hint  = _FIELD_HINTS.get(field, "")
        hint_block = f"\n\n<blockquote>{hint}</blockquote>" if hint else ""
        prompt = bot.edit_message_text(
            f"<b>{_FIELD_LABELS.get(field, field)}</b>"
            f"{hint_block}\n\n"
            f"Enter new value:",
            call.from_user.id, call.message.message_id,
            reply_markup=back_kb("adm_products"),
        )
        set_state(call.from_user.id, step="prod_edit_field", epid=pid, efield=field, prompt_msg_id=prompt.message_id)
        bot.answer_callback_query(call.id)

    @bot.message_handler(func=lambda m: get_state(m.from_user.id).get("step") == "prod_edit_field")
    @admin_only
    def msg_prod_edit_field(msg: types.Message):
        uid       = msg.from_user.id
        st        = get_state(uid)
        pid       = st["epid"]
        field     = st["efield"]
        prompt_id = st.get("prompt_msg_id")
        value     = msg.text.strip()
        if field in ("price_mmk", "reseller_price_mmk"):
            amount = parse_mmk(value)
            if amount is None:
                bot.send_message(uid, "Please enter a valid MMK price, e.g. <code>10000</code>")
                return
            db.update_product(pid, **{field: amount})
        elif field == "emoji":
            if value == "-":
                db.update_product(pid, emoji="", emoji_id="")
            else:
                fallback, eid = _extract_emoji(msg)
                db.update_product(pid, emoji=fallback, emoji_id=eid)
        elif field == "category":
            db.update_product(pid, category=("" if value == "-" else value))
        elif field == "description":
            # Capture premium emojis in description
            desc_html = emojis.capture_html(msg)
            db.update_product(pid, description=desc_html)
        else:
            db.update_product(pid, **{field: value})
        clear_state(uid)
        silent_delete(bot, uid, msg.message_id)
        try:
            _show_prod_detail(uid, prompt_id, pid)
        except Exception:
            bot.send_message(uid, f"Updated.", reply_markup=back_kb("adm_products"))

    # ── add product (multi-step) ──────────────────────────────────────────────

    @bot.callback_query_handler(func=lambda c: c.data == "adm_add_product")
    @admin_only
    def cb_adm_add_product(call: types.CallbackQuery):
        prompt = bot.edit_message_text(
            "<b>Add Product</b> (1/5)\n\n"
            "<blockquote>The name shown to users in the shop.\nExample: <code>Netflix Premium 1 Month</code></blockquote>\n\n"
            "Enter product name:",
            call.from_user.id, call.message.message_id,
            reply_markup=back_kb("adm_products"),
        )
        set_state(call.from_user.id, step="addprod_name", prompt_msg_id=prompt.message_id)
        bot.answer_callback_query(call.id)

    @bot.message_handler(func=lambda m: get_state(m.from_user.id).get("step", "").startswith("addprod_"))
    @admin_only
    def msg_addprod(msg: types.Message):
        uid       = msg.from_user.id
        step      = get_state(uid).get("step")
        text      = msg.text.strip()
        prompt_id = get_state(uid).get("prompt_msg_id")
        silent_delete(bot, uid, msg.message_id)

        def _update_prompt(new_text, step_name, **extra):
            try:
                m = bot.edit_message_text(new_text, uid, prompt_id, reply_markup=back_kb("adm_products"))
                set_state(uid, step=step_name, prompt_msg_id=m.message_id, **extra)
            except Exception:
                m = bot.send_message(uid, new_text, reply_markup=back_kb("adm_products"))
                set_state(uid, step=step_name, prompt_msg_id=m.message_id, **extra)

        if step == "addprod_name":
            _update_prompt(
                "<b>Add Product</b> (2/5)\n\n"
                "<blockquote>Pick an icon for this product.\n"
                "Send a normal emoji () or a <b>Telegram Premium</b> emoji "
                "— the bot captures premium ones automatically.\n"
                f"Send <code>-</code> to skip (uses {emojis.emoji('package', '📦')}).</blockquote>\n\n"
                "Send the product icon:",
                "addprod_emoji", addprod_name=text,
            )
        elif step == "addprod_emoji":
            if text == "-":
                emoji, emoji_id = "", ""
            else:
                emoji, emoji_id = _extract_emoji(msg)
            _update_prompt(
                "<b>Add Product</b> (3/5)\n\n"
                "<blockquote>Price in MMK, whole kyats.\nExample: <code>12000</code> for 12,000 MMK</blockquote>\n\n"
                "Enter price in MMK:",
                "addprod_price", addprod_emoji=emoji, addprod_emoji_id=emoji_id,
            )
        elif step == "addprod_price":
            amount = parse_mmk(text)
            if amount is None:
                bot.send_message(uid, "Please enter a valid MMK price, e.g. <code>12000</code>")
                return
            _update_prompt(
                "<b>Add Product</b> (4/5)\n\n"
                "<blockquote>Shown on the product page. Tell users what they will receive.\nExample: <code>After purchase you will get a login email and password.</code></blockquote>\n\n"
                "Enter description:",
                "addprod_desc", addprod_price=amount,
            )
        elif step == "addprod_desc":
            # Capture premium emojis in description using emojis.capture_html()
            desc_html = emojis.capture_html(msg)
            pid_temp = get_state(uid).get("addprod_price")  # not pid yet, just reuse prompt
            kb = types.InlineKeyboardMarkup(row_width=1)
            kb.add(
                emojis.button("📧 email | password",            callback_data="adm_credpick_email|password"),
                emojis.button("📧 email | otp-link",              callback_data="adm_credpick_email|otp-link"),
                emojis.button("📧 email | password | 2FA key",    callback_data="adm_credpick_email|password|2fa"),
                emojis.button("📧 email | password | OTP link",   callback_data="adm_credpick_email|password|otp-link"),
                emojis.button("🔗 activation link",               callback_data="adm_credpick_activation-link"),
                emojis.button("👤 username | password",           callback_data="adm_credpick_username|password"),
                emojis.button("🎮 cdk key",                       callback_data="adm_credpick_cdk"),
            )
            kb.add(emojis.button("Main Menu", callback_data="main_menu", emoji_id="5042022053356504092", lead="🏠"))
            set_state(uid, step="addprod_cred", addprod_desc=desc_html)
            try:
                bot.edit_message_text(
                    "<b>Add Product</b> (5/5)\n\n"
                    "<blockquote>Pick a format below, or <b>type your own</b> using field names "
                    "separated by <code>|</code>.\n\n"
                    "Supported field names:\n"
                    "<code>email</code>, <code>username</code>, <code>password</code>, "
                    "<code>otp-link</code>, <code>2fa</code>, <code>cdk</code>, "
                    "<code>activation-link</code>\n\n"
                    "Example: <code>email|password|2fa</code>\n\n"
                    "The buyer's receipt labels each field automatically. Links are always shown as "
                    "tappable, so mixed stock still displays correctly.</blockquote>\n\n"
                    "Select a format, or type your own:",
                    uid, prompt_id, reply_markup=kb,
                )
            except Exception:
                m = bot.send_message(
                    uid,
                    "<b>Add Product</b> (5/5) — Select credential format:",
                    reply_markup=kb,
                )
                set_state(uid, step="addprod_cred", addprod_desc=text, prompt_msg_id=m.message_id)
        elif step == "addprod_cred":
            # fallback: user typed manually instead of using buttons
            st  = get_state(uid)
            pid = db.add_product(
                name=st["addprod_name"],
                category="",
                description=st["addprod_desc"],
                price_mmk=st["addprod_price"],
                credential_format=text,
            )
            if st.get("addprod_emoji") or st.get("addprod_emoji_id"):
                db.update_product(pid, emoji=st.get("addprod_emoji", ""), emoji_id=st.get("addprod_emoji_id", ""))
            # mark as a brand-new product so the fulfillment step can trigger an announcement
            set_state(uid, step="fulfill_wizard", pid=pid)
            try:
                _ask_fulfillment_mode(uid, prompt_id, pid, wizard=True)
            except Exception:
                bot.send_message(uid, f"Product <b>{st['addprod_name']}</b> created.", reply_markup=back_kb("adm_products"))

    @bot.callback_query_handler(func=lambda c: c.data.startswith("adm_credpick_"))
    @admin_only
    def cb_adm_credpick(call: types.CallbackQuery):
        uid    = call.from_user.id
        fmt    = call.data[len("adm_credpick_"):]
        st     = get_state(uid)
        if st.get("step") != "addprod_cred":
            bot.answer_callback_query(call.id, "Session expired. Please start again.")
            return
        prompt_id = st.get("prompt_msg_id")
        pid = db.add_product(
            name=st["addprod_name"],
            category="",
            description=st["addprod_desc"],
            price_mmk=st["addprod_price"],
            credential_format=fmt,
        )
        if st.get("addprod_emoji") or st.get("addprod_emoji_id"):
            db.update_product(pid, emoji=st.get("addprod_emoji", ""), emoji_id=st.get("addprod_emoji_id", ""))
        # mark as a brand-new product so the fulfillment step can trigger an announcement
        set_state(uid, step="fulfill_wizard", pid=pid)
        bot.answer_callback_query(call.id)
        try:
            _ask_fulfillment_mode(uid, call.message.message_id, pid, wizard=True)
        except Exception:
            bot.send_message(uid, f"Product <b>{st['addprod_name']}</b> created.", reply_markup=back_kb("adm_products"))

    # ── add stock (bulk) ──────────────────────────────────────────────────────

    @bot.callback_query_handler(func=lambda c: c.data.startswith("adm_addstock_"))
    @admin_only
    def cb_adm_addstock(call: types.CallbackQuery):
        pid = int(call.data[len("adm_addstock_"):])
        p   = db.get_product(pid)
        set_state(call.from_user.id, step="bulk_stock", bstock_pid=pid)
        fmt = p["credential_format"] if p else ""
        prompt = bot.edit_message_text(
            f"<b>Add Stock — {p['name'] if p else pid}</b>\n\n"
            f"<blockquote>Paste one credential per line. Each line must match the format: <code>{fmt}</code>\n\n"
            f"Examples for <code>{fmt}</code>:\n"
            + (
                "• <code>user@gmail.com|Pass123</code>\n• <code>user2@gmail.com|Pass456</code>"
                if "|" in fmt and "http" not in fmt.lower() else
                "• <code>abc@gmail.com|https://generator.email/abc@gmail.com</code>"
                if "http" in fmt.lower() else
                "• <code>XXXX-XXXX-XXXX-XXXX</code>\n• <code>YYYY-YYYY-YYYY-YYYY</code>"
            )
            + "\n\nBlank lines are ignored. Duplicate lines are added as-is.</blockquote>\n\n"
            f"Paste credentials, one per line:",
            call.from_user.id, call.message.message_id,
            reply_markup=back_kb("adm_products"),
        )
        set_state(call.from_user.id, step="bulk_stock", bstock_pid=pid, prompt_msg_id=prompt.message_id)
        bot.answer_callback_query(call.id)

    @bot.message_handler(func=lambda m: get_state(m.from_user.id).get("step") == "bulk_stock")
    @admin_only
    def msg_bulk_stock(msg: types.Message):
        uid       = msg.from_user.id
        st        = get_state(uid)
        pid       = st.get("bstock_pid")
        prompt_id = st.get("prompt_msg_id")
        creds     = [l.strip() for l in msg.text.splitlines() if l.strip()]
        clear_state(uid)
        added = db.add_stock_bulk(pid, creds)
        silent_delete(bot, uid, msg.message_id)
        try:
            _show_prod_detail(uid, prompt_id, pid)
        except Exception:
            bot.send_message(uid, f"Added <b>{added}</b> item(s).", reply_markup=back_kb("adm_products"))

        if added > 0:
            threading.Thread(target=_announce_stock, args=(bot, pid, added), daemon=True).start()

    def _announce_stock(bot: telebot.TeleBot, pid: int, added: int):
        if db.cfg_get("stock_announce_enabled", "1") != "1":
            return
        p     = db.get_product(pid)
        if not p:
            return
        stock = db.stock_count(pid)
        emoji_html = db.product_emoji_html(p)
        
        text = stock_announcement(
            product_name=_esc(p['name']),
            emoji_html=emoji_html,
            added=added,
            total=stock,
            product_id=pid
        )
        
        kb = types.InlineKeyboardMarkup(row_width=1)
        kb.add(emojis.button("View Product", callback_data=f"prod_{pid}", emoji_id="5312361253610475399", lead="🛒"))
        kb.add(emojis.button("Dismiss", callback_data="dismiss_msg", emoji_id="5210952531676504517", lead="✖️"))
        
        users = db.list_users(limit=100000)
        for u in users:
            if u["notify_opt_out"]:
                continue
            try:
                bot.send_message(u["telegram_id"], text, parse_mode='HTML', reply_markup=kb)
            except Exception:
                pass
            time.sleep(0.05)

    def _announce_new_product(bot: telebot.TeleBot, pid: int):
        """Announce a newly added pre-order product to all users."""
        if db.cfg_get("stock_announce_enabled", "1") != "1":
            return
        p = db.get_product(pid)
        if not p:
            return
        mode = db.get_fulfillment(p)
        if mode != "preorder":
            return
        emoji_html = db.product_emoji_html(p)
        text = product_announcement(
            product_name=_esc(p['name']),
            emoji_html=emoji_html,
            mode=mode,
        )
        kb = types.InlineKeyboardMarkup(row_width=1)
        kb.add(emojis.button("View Product", callback_data=f"prod_{pid}", emoji_id="5312361253610475399", lead="🛒"))
        kb.add(emojis.button("Dismiss", callback_data="dismiss_msg", emoji_id="5210952531676504517", lead="✖️"))
        users = db.list_users(limit=100000)
        for u in users:
            if u["notify_opt_out"]:
                continue
            try:
                bot.send_message(u["telegram_id"], text, parse_mode='HTML', reply_markup=kb)
            except Exception:
                pass
            time.sleep(0.05)

    # ── discount: set ─────────────────────────────────────────────────────────

    @bot.callback_query_handler(func=lambda c: c.data.startswith("adm_disc_") and not c.data.startswith("adm_discoff_"))
    @admin_only
    def cb_adm_disc(call: types.CallbackQuery):
        pid    = int(call.data[len("adm_disc_"):])
        prompt = bot.edit_message_text(
            "<b>Set Discount</b>\n\nEnter discount percentage (e.g. <code>20</code> for 20% off):",
            call.from_user.id, call.message.message_id,
            reply_markup=back_kb(f"adm_prod_{pid}"),
        )
        set_state(call.from_user.id, step="disc_pct", disc_pid=pid, prompt_msg_id=prompt.message_id)
        bot.answer_callback_query(call.id)

    @bot.message_handler(func=lambda m: get_state(m.from_user.id).get("step") == "disc_pct")
    @admin_only
    def msg_disc_pct(msg: types.Message):
        uid       = msg.from_user.id
        st        = get_state(uid)
        pid       = st["disc_pid"]
        prompt_id = st["prompt_msg_id"]
        silent_delete(bot, uid, msg.message_id)
        text = msg.text.strip().replace("%", "")
        if not text.isdigit() or not (1 <= int(text) <= 99):
            try:
                bot.edit_message_text(
                    "<b>Set Discount</b>\n\nEnter a number between 1 and 99:",
                    uid, prompt_id, reply_markup=back_kb(f"adm_prod_{pid}"),
                )
            except Exception:
                pass
            return
        pct    = int(text)
        prompt = bot.edit_message_text(
            f"<b>Set Discount — {pct}% off</b>\n\n"
            f"Enter duration (e.g. <code>2h</code> for 2 hours, <code>1d</code> for 1 day):",
            uid, prompt_id,
            reply_markup=back_kb(f"adm_prod_{pid}"),
        )
        set_state(uid, step="disc_duration", disc_pid=pid, disc_pct=pct, prompt_msg_id=prompt.message_id)

    @bot.message_handler(func=lambda m: get_state(m.from_user.id).get("step") == "disc_duration")
    @admin_only
    def msg_disc_duration(msg: types.Message):
        import re
        from datetime import datetime, timedelta, timezone
        uid       = msg.from_user.id
        st        = get_state(uid)
        pid       = st["disc_pid"]
        pct       = st["disc_pct"]
        prompt_id = st["prompt_msg_id"]
        silent_delete(bot, uid, msg.message_id)
        raw = msg.text.strip().lower()

        # parse combined durations: "2h", "1d", "30m", "1h30m", "1d2h", etc.
        total_seconds = 0
        for value, unit in re.findall(r"(\d+)\s*([dhm])", raw):
            v = int(value)
            if unit == "d":
                total_seconds += v * 86400
            elif unit == "h":
                total_seconds += v * 3600
            elif unit == "m":
                total_seconds += v * 60

        if total_seconds <= 0:
            try:
                bot.edit_message_text(
                    f"<b>Set Discount — {pct}% off</b>\n\n"
                    f"Invalid format. Examples: <code>2h</code>, <code>1d</code>, <code>30m</code>, <code>1h30m</code>, <code>1d12h</code>:",
                    uid, prompt_id, reply_markup=back_kb(f"adm_prod_{pid}"),
                )
            except Exception:
                pass
            return

        delta    = timedelta(seconds=total_seconds)
        until_dt = datetime.now(timezone.utc) + delta
        until_iso = until_dt.strftime("%Y-%m-%d %H:%M:%S")
        db.set_discount(pid, pct, until_iso)
        clear_state(uid)
        try:
            _show_prod_detail(uid, prompt_id, pid)
        except Exception:
            bot.send_message(uid, f"Discount set.", reply_markup=back_kb("adm_products"))
        threading.Thread(target=_announce_discount, args=(bot, pid), daemon=True).start()

    # ── discount: remove ──────────────────────────────────────────────────────

    @bot.callback_query_handler(func=lambda c: c.data.startswith("adm_discoff_"))
    @admin_only
    def cb_adm_discoff(call: types.CallbackQuery):
        pid = int(call.data[len("adm_discoff_"):])
        db.clear_discount(pid)
        _show_prod_detail(call.from_user.id, call.message.message_id, pid)
        bot.answer_callback_query(call.id, "Discount removed.")

    def _announce_discount(bot: telebot.TeleBot, pid: int):
        from datetime import datetime, timezone
        from msg_templates import discount_announcement
        
        p = db.get_product(pid)
        if not p or not db.is_discount_active(p):
            return
        eff   = db.get_effective_price(p)
        pct   = p["discount_pct"]
        until = datetime.fromisoformat(p["discount_until"])
        # human-readable time remaining
        diff  = until - datetime.now(timezone.utc).replace(tzinfo=None)
        hrs   = int(diff.total_seconds() // 3600)
        mins  = int((diff.total_seconds() % 3600) // 60)
        if hrs > 0:
            ends_in = f"{hrs}h {mins}m" if mins else f"{hrs}h"
        else:
            ends_in = f"{mins}m"
        
        emoji_html = db.product_emoji_html(p)
        text = discount_announcement(
            product_name=_esc(p['name']),
            emoji_html=emoji_html,
            original_price=fmt_mmk(p['price_mmk']),
            discounted_price=fmt_mmk(eff),
            discount_pct=pct,
            ends_in=ends_in
        )
        
        kb = types.InlineKeyboardMarkup(row_width=1)
        kb.add(emojis.button("Buy Now", callback_data=f"prod_{pid}", emoji_id="5312361253610475399", lead="🛒"))
        kb.add(emojis.button("Dismiss", callback_data="dismiss_msg", emoji_id="5210952531676504517", lead="✖️"))
        users = db.list_users(limit=100000)
        for u in users:
            if u["notify_opt_out"]:
                continue
            try:
                bot.send_message(u["telegram_id"], text, parse_mode='HTML', reply_markup=kb)
            except Exception:
                pass
            time.sleep(0.05)

    # ── view stock ────────────────────────────────────────────────────────────

    @bot.callback_query_handler(func=lambda c: c.data.startswith("adm_stocklist_"))
    @admin_only
    def cb_adm_stocklist(call: types.CallbackQuery):
        parts  = call.data.split("_")   # adm_stocklist_{pid}_{page}
        pid    = int(parts[2])
        page   = int(parts[3])
        items  = db.list_stock_items(pid, limit=500)
        p      = db.get_product(pid)
        pname  = p["name"] if p else str(pid)
        per_pg = 10
        start  = page * per_pg
        chunk  = items[start:start + per_pg]

        kb = types.InlineKeyboardMarkup(row_width=1)
        for item in chunk:
            preview = item["credential"][:30] + ("…" if len(item["credential"]) > 30 else "")
            kb.add(emojis.button(f"🗑 {preview}",
                callback_data=f"adm_dsc_{item['stock_id']}_{pid}_{page}",
            ))
        nav = []
        if page > 0:
            nav.append(emojis.button("Prev", callback_data=f"adm_stocklist_{pid}_{page-1}", emoji_id="5255703720078879038", lead="⬅️"))
        if start + per_pg < len(items):
            nav.append(emojis.button("Next", callback_data=f"adm_stocklist_{pid}_{page+1}", emoji_id="5253767677670862169", lead="➡️"))
        if nav:
            kb.row(*nav)
        kb.add(
            emojis.button("Back", callback_data=f"adm_prod_{pid}", emoji_id="5255703720078879038", lead="⬅️"),
            emojis.button("Main Menu",  callback_data="main_menu", emoji_id="5042022053356504092", lead="🏠"),
        )
        bot.edit_message_text(
            f"<b>Stock: {pname}</b> — {len(items)} total | Page {page+1}",
            call.from_user.id, call.message.message_id, reply_markup=kb,
        )
        bot.answer_callback_query(call.id)

    @bot.callback_query_handler(func=lambda c: c.data.startswith("adm_dsc_"))
    @admin_only
    def cb_adm_dsc_confirm(call: types.CallbackQuery):
        parts    = call.data.split("_")   # adm_dsc_{stock_id}_{pid}_{page}
        stock_id = int(parts[2])
        pid      = int(parts[3])
        page     = int(parts[4])
        item     = next((i for i in db.list_stock_items(pid, limit=1000) if i["stock_id"] == stock_id), None)
        preview  = (item["credential"][:40] + "…") if item else f"#{stock_id}"
        kb = types.InlineKeyboardMarkup(row_width=2)
        kb.add(
            emojis.button("Yes, delete", callback_data=f"adm_delstock_{stock_id}_{pid}_{page}", emoji_id="5039844895779455925", lead="✅"),
            emojis.button("Cancel",      callback_data=f"adm_stocklist_{pid}_{page}", emoji_id="5210952531676504517", lead="❌"),
        )
        try:
            bot.edit_message_text(
                f"<b>Delete this stock item?</b>\n\n<code>{preview}</code>\n\nThis cannot be undone.",
                call.from_user.id, call.message.message_id, reply_markup=kb,
            )
        except Exception:
            pass
        bot.answer_callback_query(call.id)

    @bot.callback_query_handler(func=lambda c: c.data.startswith("adm_delstock_"))
    @admin_only
    def cb_adm_delstock(call: types.CallbackQuery):
        parts    = call.data.split("_")   # adm_delstock_{stock_id}_{pid}_{page}
        stock_id = int(parts[2])
        pid      = int(parts[3])
        page     = int(parts[4])
        db.delete_stock_item(stock_id)
        bot.answer_callback_query(call.id, "Deleted.")
        call.data = f"adm_stocklist_{pid}_{page}"
        cb_adm_stocklist(call)
