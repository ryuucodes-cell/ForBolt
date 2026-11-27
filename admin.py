"""Admin panel entry point — wires all sub-modules."""

import html
import os
import subprocess
import threading
import telebot
from telebot import types

from utils import admin_only, back_kb, fmt_mmk, fmt_money
import db
import emojis
import binance_pay
import admin_products
import admin_deposits
import admin_users
import admin_orders
import admin_settings
import admin_broadcast
import admin_backup
import admin_reseller
import admin_fulfillment


def register(bot: telebot.TeleBot):

    @bot.callback_query_handler(func=lambda c: c.data == "admin_panel")
    @admin_only
    def cb_admin_panel(call: types.CallbackQuery):
        try:
            bot.edit_message_text(
                _dashboard_text(),
                call.from_user.id, call.message.message_id,
                reply_markup=_admin_main_kb(),
            )
        except Exception:
            pass
        bot.answer_callback_query(call.id)

    def _dashboard_text() -> str:
        cfg = db.cfg_all()
        mm  = "ON" if cfg.get("maintenance_mode") == "1" else "OFF"
        bnc = "On" if binance_pay.is_configured() else "Off"

        total_users   = db.count_users()
        pending_deps  = db.count_deposits(status="pending")
        pending_reports = db.count_order_reports(status="pending")
        total_orders  = db.count_orders()
        total_resell  = db.count_resellers()
        pending_res   = db.count_reseller_requests(status="pending")
        pending_fulfill = db.count_fulfillment_orders()
        today_revenue = db.revenue_today()
        total_revenue = db.revenue_total()
        low_stock     = db.products_low_stock()
        out_of_stock  = db.products_out_of_stock()

        def _bullets(items, fmt, cap=5):
            out = [f"• {fmt(it)}" for it in items[:cap]]
            if len(items) > cap:
                out.append(f"• …and {len(items) - cap} more")
            return out

        lines = [
            "<b>Admin Dashboard</b>",
            "",
            f"Users: <b>{total_users}</b>",
            f"Orders: <b>{total_orders}</b>",
            f"Pending deposits: <b>{pending_deps}</b>"
            + (f" · Problem reports: <b>{pending_reports}</b>" if pending_reports else "")
            + (f" · Fulfillment: <b>{pending_fulfill}</b>" if pending_fulfill else ""),
            f"Resellers: <b>{total_resell}</b>" + (f" · Requests: <b>{pending_res}</b>" if pending_res else ""),
            "",
            "<b>Revenue</b>",
            f"• Today: <b>{fmt_money(today_revenue)}</b>",
            f"• All-time: <b>{fmt_money(total_revenue)}</b>",
            "",
            "<b>Stock</b>",
        ]
        if out_of_stock:
            lines.append(f"Out of stock ({len(out_of_stock)}):")
            lines += _bullets(out_of_stock, lambda r: r["name"])
        if low_stock:
            lines.append(f"Low stock ({len(low_stock)}):")
            lines += _bullets(low_stock, lambda r: f"{r['name']} — {r['cnt']} left")
        if not out_of_stock and not low_stock:
            lines.append("All products well stocked")

        lines += [
            "",
            f"Binance: {bnc} · Maintenance: {mm}",
        ]
        body = "\n".join(lines)
        return f"<blockquote>{body}</blockquote>"

    def _admin_main_kb():
        kb = types.InlineKeyboardMarkup(row_width=2)
        fulfill_n = db.count_fulfillment_orders()
        fulfill_label = f"Fulfillment ({fulfill_n})" if fulfill_n else "Fulfillment"
        # daily operations
        kb.add(
            emojis.button("Products",  callback_data="adm_products", emoji_id="5312361253610475399", lead="📦"),
            emojis.button("Deposits",  callback_data="adm_deposits", emoji_id="5444856076954520455", lead="💳"),
            emojis.button("Users",     callback_data="adm_users_0", emoji_id="5381970617505428041", lead="👥"),
            emojis.button("Orders",    callback_data="adm_orders", emoji_id="5444856076954520455", lead="📋"),
            emojis.button(fulfill_label, callback_data="adm_fulfill", emoji_id="5444856076954520455", lead="📋"),
            emojis.button("Giveaways", callback_data="adm_gw", emoji_id="5039823300683891773", lead="🎁"),
            emojis.button("Lucky Draw", callback_data="adm_ld", emoji_id="5260547274957672345", lead="🎲"),
            emojis.button("Resellers", callback_data="adm_reseller", emoji_id="5039834781131474002", lead="🔑"),
            emojis.button("Analytics", callback_data="adm_analytics", emoji_id="5203993413346680064", lead="📊"),
        )
        # configuration & messaging
        kb.add(
            emojis.button("Settings",  callback_data="adm_settings", emoji_id="5341715473882955310", lead="⚙️"),
            emojis.button("Broadcast", callback_data="adm_broadcast", emoji_id="5399967660052081305", lead="📢"),
        )
        # system tools grouped away from daily actions
        kb.add(
            emojis.button("System",  callback_data="adm_system", emoji_id="5341715473882955310", lead="🛠"),
            emojis.button("Refresh", callback_data="admin_panel", emoji_id="5226702984204797593", lead="🔄"),
        )
        kb.add(emojis.button("Main Menu", callback_data="main_menu", emoji_id="5042022053356504092", lead="🏠"))
        return kb

    @bot.callback_query_handler(func=lambda c: c.data == "adm_system")
    @admin_only
    def cb_adm_system(call: types.CallbackQuery):
        bnc = "Online" if binance_pay.is_configured() else "No API keys"
        text = (
            "<b>System &amp; Maintenance</b>\n\n"
            f"Binance auto-verify: {bnc}\n\n"
            "<blockquote>Backup sends both databases to you as files. Restore overwrites the live "
            "databases. Restart reloads the bot cleanly in a few seconds.</blockquote>"
        )
        kb = types.InlineKeyboardMarkup(row_width=2)
        kb.add(emojis.button("Test Binance", callback_data="adm_binance_test", emoji_id="5039675231686361986", lead="🟡"))
        kb.add(emojis.button("Test Log Channel", callback_data="adm_test_log", emoji_id="5042290883949495533", lead="📢"))
        kb.add(
            emojis.button("Backup DBs",  callback_data="adm_backup", emoji_id="5039600026809009149", lead="📦"),
            emojis.button("Restore DBs", callback_data="adm_restore", emoji_id="5042040865313260562", lead="♻️"),
        )
        kb.add(emojis.button("Restart Bot", callback_data="adm_restart", emoji_id="5346269127059196142", lead="🔄"))
        kb.add(
            emojis.button("Back", callback_data="admin_panel", emoji_id="5255703720078879038", lead="⬅️"),
            emojis.button("Main Menu", callback_data="main_menu", emoji_id="5042022053356504092", lead="🏠"),
        )
        try:
            bot.edit_message_text(text, call.from_user.id, call.message.message_id, reply_markup=kb)
        except Exception:
            pass
        bot.answer_callback_query(call.id)

    @bot.callback_query_handler(func=lambda c: c.data == "adm_restart")
    @admin_only
    def cb_adm_restart(call: types.CallbackQuery):
        uid = call.from_user.id
        bot.answer_callback_query(call.id)
        bot.edit_message_text(
            "Restarting bot...\n\nThis usually takes 5–10 seconds. Send /start when it's back.",
            uid, call.message.message_id,
        )

        def _do_restart():
            container = os.environ.get("CONTAINER_NAME", "stockbot")
            try:
                subprocess.run(["docker", "restart", container], check=True, timeout=30)
            except Exception as e:
                try:
                    bot.send_message(uid, f"Restart failed: {e}")
                except Exception:
                    pass

        threading.Thread(target=_do_restart, daemon=True).start()

    @bot.callback_query_handler(func=lambda c: c.data == "adm_binance_test")
    @admin_only
    def cb_adm_binance_test(call: types.CallbackQuery):
        bot.answer_callback_query(call.id, "Testing Binance connection...")
        res  = binance_pay.health_check()
        hint = ""
        if res["status"] == "blocked":
            hint = "\n\n<blockquote>Move the server to a Binance-allowed region (e.g. Singapore) or route API calls through a proxy.</blockquote>"
        elif res["status"] == "unconfigured":
            hint = "\n\n<blockquote>Add BINANCE_API_KEY and BINANCE_API_SECRET to .env, then restart the bot.</blockquote>"
        kb = types.InlineKeyboardMarkup(row_width=2)
        kb.add(emojis.button("Test Again", callback_data="adm_binance_test", emoji_id="5226702984204797593", lead="🔄"))
        kb.add(
            emojis.button("Back", callback_data="admin_panel", emoji_id="5255703720078879038", lead="⬅️"),
            emojis.button("Main Menu",  callback_data="main_menu", emoji_id="5042022053356504092", lead="🏠"),
        )
        try:
            bot.edit_message_text(
                f"<b>Binance Auto-Verify Test</b>\n\n"
                f"Status: <b>{res['status'].upper()}</b>\n"
                f"{res['detail']}{hint}",
                call.from_user.id, call.message.message_id, reply_markup=kb,
            )
        except Exception:
            bot.send_message(call.from_user.id, f"{res['status'].upper()} — {res['detail']}", reply_markup=kb)

    @bot.callback_query_handler(func=lambda c: c.data == "adm_test_log")
    @admin_only
    def cb_adm_test_log(call: types.CallbackQuery):
        """Send a test post to the log channel and report the real result.

        Unlike log_to_channel (which stays quiet), this surfaces the actual
        Telegram error so a broken channel link can be diagnosed instantly.
        """
        uid = call.from_user.id
        bot.answer_callback_query(call.id, "Sending test post...")
        cid = db.cfg_get("log_channel_id", "").strip()
        if not cid:
            bot.send_message(
                uid,
                "<b>Log channel is not configured.</b>\n\n"
                "Set it first: Admin Panel → Settings → Log Channel.",
            )
            return
        try:
            bot.send_message(
                cid,
                f"<b>{emojis.emoji('mega', '📢')} Log channel test</b>\n\n"
                "If you can see this in the channel, sale / top-up / giveaway / "
                "balance vouches will post here too.",
                parse_mode="HTML",
            )
            bot.send_message(uid, f"{emojis.emoji('success', '✅')} Test post sent — check your log channel now.")
        except Exception as e:
            bot.send_message(
                uid,
                f"<b>{emojis.emoji('rejected', '❌')} Test post failed</b>\n\n"
                f"<code>{html.escape(str(e))[:500]}</code>\n\n"
                "<blockquote>Usual causes: the bot is not an admin in the channel, "
                "or the channel link/ID in Settings is wrong.</blockquote>",
            )

    # Register sub-modules
    admin_products.register(bot)
    admin_deposits.register(bot)
    admin_users.register(bot)
    admin_orders.register(bot)
    admin_settings.register(bot)
    admin_broadcast.register(bot)
    admin_backup.register(bot)
    admin_reseller.register(bot)
    admin_fulfillment.register(bot)

    @bot.callback_query_handler(func=lambda c: c.data == "adm_analytics")
    @admin_only
    def cb_adm_analytics(call: types.CallbackQuery):
        from datetime import date
        bot.answer_callback_query(call.id)

        daily     = db.revenue_last_n_days(7)
        orders_7d = db.orders_last_n_days(7)
        users_7d  = db.new_users_last_n_days(7)
        top       = db.top_products(5)
        total_rev = db.revenue_total()
        today_rev = db.revenue_today()

        lines = ["<b>Analytics — Last 7 Days</b>\n"]

        lines.append("<b>Daily Revenue</b>")
        if daily:
            for row in daily:
                bar = "█" * max(1, int(row["total"] / max(r["total"] for r in daily) * 10)) if any(r["total"] for r in daily) else ""
                lines.append(f"  {row['day']}  {fmt_mmk(row['total'])}  {bar}")
        else:
            lines.append("  No orders yet.")

        lines.append(f"\nOrders (7d): <b>{orders_7d}</b>")
        lines.append(f"New users (7d): <b>{users_7d}</b>")
        lines.append(f"Revenue today: <b>{fmt_money(today_rev)}</b>")
        lines.append(f"All-time revenue: <b>{fmt_money(total_rev)}</b>")

        if top:
            lines.append("\n<b>Top Products (all time)</b>")
            for i, p in enumerate(top, 1):
                lines.append(f"  {i}. {p['product_name']} — {p['cnt']} sold · {fmt_mmk(p['revenue'])}")

        kb = types.InlineKeyboardMarkup(row_width=2)
        kb.add(
            emojis.button("Back", callback_data="admin_panel", emoji_id="5255703720078879038", lead="⬅️"),
            emojis.button("Main Menu",  callback_data="main_menu", emoji_id="5042022053356504092", lead="🏠"),
        )
        bot.edit_message_text(
            f"<blockquote>{chr(10).join(lines)}</blockquote>",
            call.from_user.id, call.message.message_id,
            reply_markup=kb,
        )
