"""Admin: database backup info (SQLite file-based)."""

import telebot
from telebot import types
import db
from utils import admin_only, back_kb


def register(bot: telebot.TeleBot):

    @bot.callback_query_handler(func=lambda c: c.data == "adm_backup")
    @admin_only
    def cb_adm_backup(call: types.CallbackQuery):
        uid = call.from_user.id
        bot.answer_callback_query(call.id)
        import os
        size = 0
        if os.path.exists(db.DB_PATH):
            size = os.path.getsize(db.DB_PATH)
        size_str = f"{size / 1024 / 1024:.1f} MB" if size > 1024 * 1024 else f"{size / 1024:.0f} KB"
        bot.send_message(
            uid,
            "<b>Database Info</b>\n\n"
            "<blockquote>Your database is a SQLite file stored inside the "
            "Docker container at /app/data/shop.db. It persists across "
            "restarts via a Docker volume.</blockquote>\n\n"
            f"File: <code>{db.DB_PATH}</code>\n"
            f"Size: <b>{size_str}</b>\n\n"
            "A daily VACUUM runs automatically to keep the file compact. "
            "To create a manual backup, copy the file from the Docker volume.",
            reply_markup=back_kb("admin_panel"),
        )

    @bot.callback_query_handler(func=lambda c: c.data == "adm_restore")
    @admin_only
    def cb_adm_restore(call: types.CallbackQuery):
        uid = call.from_user.id
        bot.answer_callback_query(call.id)
        bot.send_message(
            uid,
            "<b>Database Restore</b>\n\n"
            "<blockquote>To restore, replace the file at /app/data/shop.db "
            "with your backup copy, then restart the container with "
            "<code>docker restart stockbot</code>.</blockquote>",
            reply_markup=back_kb("admin_panel"),
        )
