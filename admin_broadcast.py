"""Admin: broadcast message to all registered users."""

import threading
import time

import telebot
from telebot import types

import db
import emojis
from utils import admin_only, set_state, get_state, clear_state, back_kb, silent_delete


def _bar(done: int, total: int) -> str:
    blocks = 10
    n = int(done * blocks / total + 0.5) if total else 0
    return "▓" * n + "░" * (blocks - n)


def register(bot: telebot.TeleBot):

    @bot.callback_query_handler(func=lambda c: c.data == "adm_broadcast")
    @admin_only
    def cb_adm_broadcast(call: types.CallbackQuery):
        prompt = bot.edit_message_text(
            "<b>Broadcast</b>\n\nCompose your message below — it will be delivered to all users.\n"
            "<blockquote>You can include Telegram Premium (animated) emoji directly in the "
            "message — they'll be preserved exactly as you typed them.</blockquote>",
            call.from_user.id, call.message.message_id,
            reply_markup=back_kb("admin_panel"),
        )
        set_state(call.from_user.id, step="broadcast_draft", prompt_msg_id=prompt.message_id)
        bot.answer_callback_query(call.id)

    @bot.message_handler(func=lambda m: get_state(m.from_user.id).get("step") == "broadcast_draft")
    @admin_only
    def msg_broadcast_draft(msg: types.Message):
        uid       = msg.from_user.id
        text      = emojis.capture_html(msg).strip()
        prompt_id = get_state(uid).get("prompt_msg_id")
        clear_state(uid)
        silent_delete(bot, uid, msg.message_id)

        shop      = db.cfg_get("shop_name", "StockBot")
        formatted = f"<b>{shop} — Announcement</b>\n\n<blockquote>{text}</blockquote>"

        if len(formatted) > 4096:
            try:
                bot.edit_message_text(
                    f"Message too long ({len(formatted)} chars). Telegram limit is 4096.\n\n"
                    f"Please shorten your message by at least {len(formatted) - 4096} characters.",
                    uid, prompt_id, reply_markup=back_kb("admin_panel"),
                )
            except Exception:
                pass
            clear_state(uid)
            return

        set_state(uid, step="broadcast_confirm", bcast_text=text, prompt_msg_id=prompt_id)
        kb = types.InlineKeyboardMarkup(row_width=2)
        kb.add(
            emojis.button("Send", callback_data="adm_bcast_confirm", emoji_id="5039844895779455925", lead="✅"),
            emojis.button("Cancel", callback_data="adm_bcast_cancel", emoji_id="5210952531676504517", lead="❌"),
        )
        total = db.count_users()
        preview = (
            f"<b>Preview</b> (this is exactly what users will see):\n\n"
            f"{formatted}\n\n"
            f"Will send to <b>{total}</b> users. Confirm?"
        )
        try:
            bot.edit_message_text(preview, uid, prompt_id, reply_markup=kb)
        except Exception:
            m = bot.send_message(uid, preview, reply_markup=kb)
            set_state(uid, step="broadcast_confirm", bcast_text=text, prompt_msg_id=m.message_id)

    @bot.callback_query_handler(func=lambda c: c.data == "adm_bcast_cancel")
    @admin_only
    def cb_bcast_cancel(call: types.CallbackQuery):
        clear_state(call.from_user.id)
        bot.edit_message_text(
            "Broadcast cancelled.",
            call.from_user.id, call.message.message_id,
            reply_markup=back_kb("admin_panel"),
        )
        bot.answer_callback_query(call.id)

    @bot.callback_query_handler(func=lambda c: c.data == "adm_bcast_confirm")
    @admin_only
    def cb_bcast_confirm(call: types.CallbackQuery):
        uid  = call.from_user.id
        text = get_state(uid).get("bcast_text", "")
        clear_state(uid)
        if not text:
            bot.answer_callback_query(call.id, "Nothing to send.")
            return
        bot.edit_message_text(
            "Sending broadcast...",
            uid, call.message.message_id,
            reply_markup=None,
        )
        bot.answer_callback_query(call.id)

        def _send():
            shop      = db.cfg_get("shop_name", "StockBot")
            formatted = f"<b>{shop} — Announcement</b>\n\n<blockquote>{text}</blockquote>"
            kb_user   = types.InlineKeyboardMarkup()
            kb_user.add(emojis.button("Dismiss", callback_data="dismiss_msg", emoji_id="5210952531676504517", lead="✖️"))
            users = db.list_users(limit=100000)
            total = len(users)
            sent = failed = skipped = 0
            # Live progress on the admin's "Sending broadcast..." message.
            # Throttled (every 25 users or 3s) so the progress edits
            # themselves don't slow the blast or hit rate limits.
            prog = {"done": 0, "at": 0.0}

            def _progress(force=False):
                done = sent + failed + skipped
                now = time.time()
                if not force and done - prog["done"] < 25 and now - prog["at"] < 3:
                    return
                prog["done"] = done
                prog["at"] = now
                pct = int(done * 100 / total) if total else 100
                try:
                    bot.edit_message_text(
                        f"<b>Broadcast — sending…</b>\n\n"
                        f"{_bar(done, total)} {pct}%\n"
                        f"Users: {done} / {total}\n"
                        f"Sent: {sent} | Failed: {failed} | Skipped (muted): {skipped}",
                        uid, call.message.message_id,
                    )
                except Exception:
                    pass

            _progress(force=True)
            for u in users:
                # Respect the notification mute — users who turned alerts off
                # don't receive admin broadcasts.
                if u["notify_opt_out"]:
                    skipped += 1
                    _progress()
                    continue
                delivered = False
                for attempt in range(3):
                    try:
                        bot.send_message(u["telegram_id"], formatted, reply_markup=kb_user)
                        sent += 1
                        delivered = True
                        break
                    except Exception as e:
                        err = str(e)
                        if "429" in err:
                            import re
                            match = re.search(r"retry after (\d+)", err, re.IGNORECASE)
                            wait  = int(match.group(1)) if match else 5
                            time.sleep(wait)
                        else:
                            failed += 1
                            delivered = True  # non-429 error — don't retry
                            break
                if not delivered:
                    # exhausted all 3 retries on 429
                    failed += 1
                _progress()
                time.sleep(0.05)
            kb = back_kb("admin_panel")
            try:
                report = f"<b>Broadcast done.</b>\n\nSent: {sent} | Failed: {failed}"
                if skipped:
                    report += f" | Skipped (muted): {skipped}"
                bot.edit_message_text(
                    report,
                    uid, call.message.message_id,
                    reply_markup=kb,
                )
            except Exception:
                bot.send_message(uid, f"<b>Broadcast done.</b>\n\nSent: {sent} | Failed: {failed}", reply_markup=kb)

        threading.Thread(target=_send, daemon=True).start()
