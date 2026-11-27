"""Admin: database backup info (Supabase manages backups automatically)."""

import telebot
from telebot import types

import db
import emojis
from utils import admin_only, back_kb


def register(bot: telebot.TeleBot):

    @bot.callback_query_handler(func=lambda c: c.data == "adm_backup")
    @admin_only
    def cb_adm_backup(call: types.CallbackQuery):
        uid = call.from_user.id
        bot.answer_callback_query(call.id)
        bot.send_message(
            uid,
            "<b>Database Backups</b>\n\n"
            "<blockquote>Your database is hosted on Supabase, which manages "
            "automatic daily backups and point-in-time recovery. "
            "You can download snapshots or restore from the Supabase Dashboard "
            "→ Database → Backups.</blockquote>\n\n"
            "No manual file backup is needed.",
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
            "<blockquote>Restore is handled through the Supabase Dashboard "
            "→ Database → Backups → Restore to a previous timestamp.</blockquote>\n\n"
            "Contact your Supabase project admin if you need help.",
            reply_markup=back_kb("admin_panel"),
        )
