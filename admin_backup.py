"""Admin: database backup and restore."""

import os
import shutil
import sqlite3
import telebot
from telebot import types

import db
import emojis
from utils import admin_only, set_state, get_state, clear_state, back_kb, silent_delete

_MAIN_REQUIRED_TABLES  = {"config", "users", "products", "stock", "orders", "deposits"}
_SOLD_REQUIRED_TABLES  = {"sold_stock"}

def _validate_db(path: str, required_tables: set) -> str | None:
    """Returns None if valid, or an error string if not."""
    try:
        conn = sqlite3.connect(path)
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        conn.close()
        missing = required_tables - tables
        if missing:
            return f"Missing tables: {', '.join(sorted(missing))}"
        return None
    except Exception as e:
        return str(e)


def register(bot: telebot.TeleBot):

    # ── backup ────────────────────────────────────────────────────────────────

    @bot.callback_query_handler(func=lambda c: c.data == "adm_backup")
    @admin_only
    def cb_adm_backup(call: types.CallbackQuery):
        import sqlite3
        from datetime import datetime
        uid = call.from_user.id
        bot.answer_callback_query(call.id)
        bot.send_message(uid, "Creating backup...")

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")

        def _safe_backup(src_path: str, fname: str, caption: str):
            tmp_path = src_path + f".backup_{ts}.tmp"
            try:
                conn = sqlite3.connect(src_path)
                conn.execute(f"VACUUM INTO '{tmp_path}'")
                conn.close()
                with open(tmp_path, "rb") as f:
                    bot.send_document(uid, f, visible_file_name=fname, caption=caption)
            except Exception as e:
                bot.send_message(uid, f"Backup failed for {fname}: {e}")
            finally:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)

        _safe_backup(
            db.DB_PATH,
            f"stockbot_{ts}.db",
            f"<b>Main DB</b> — {ts}\nConfig, users, products, available stock, orders, deposits",
        )
        _safe_backup(
            db.DB_SOLD_PATH,
            f"stockbot_sold_{ts}.db",
            f"<b>Sold DB</b> — {ts}\nAll sold stock items",
        )

        kb = back_kb("admin_panel")
        bot.send_message(uid, "Backup complete. Files include timestamp so you can keep multiple versions.", reply_markup=kb)

    # ── restore ───────────────────────────────────────────────────────────────

    @bot.callback_query_handler(func=lambda c: c.data == "adm_restore")
    @admin_only
    def cb_adm_restore(call: types.CallbackQuery):
        uid    = call.from_user.id
        prompt = bot.edit_message_text(
            "<b>Restore Database</b>\n\n"
            "<blockquote>This will overwrite the current databases completely.\n\n"
            "Step 1 of 2: Send the <b>stockbot.db</b> file (main database).</blockquote>",
            uid, call.message.message_id,
            reply_markup=back_kb("admin_panel"),
        )
        set_state(uid, step="restore_main_db", prompt_msg_id=prompt.message_id)
        bot.answer_callback_query(call.id)

    @bot.message_handler(
        content_types=["document"],
        func=lambda m: get_state(m.from_user.id).get("step") == "restore_main_db",
    )
    @admin_only
    def msg_restore_main_db(msg: types.Message):
        uid       = msg.from_user.id
        prompt_id = get_state(uid).get("prompt_msg_id")
        silent_delete(bot, uid, msg.message_id)

        doc = msg.document
        if not doc.file_name.endswith(".db"):
            try:
                bot.edit_message_text(
                    "<b>Restore Database</b>\n\n"
                    "That doesn't look like a .db file. Send the <b>stockbot.db</b> file:",
                    uid, prompt_id, reply_markup=back_kb("admin_panel"),
                )
            except Exception:
                pass
            return

        # download and save as a temp file first
        file_info = bot.get_file(doc.file_id)
        file_data = bot.download_file(file_info.file_path)
        tmp_path  = db.DB_PATH + ".restore_tmp"
        with open(tmp_path, "wb") as f:
            f.write(file_data)

        set_state(uid, step="restore_sold_db", prompt_msg_id=prompt_id, main_tmp=tmp_path)
        try:
            bot.edit_message_text(
                "<b>Restore Database</b>\n\n"
                "Main DB received.\n\n"
                "<blockquote>Step 2 of 2: Now send the <b>stockbot_sold.db</b> file (sold stock database).</blockquote>",
                uid, prompt_id, reply_markup=back_kb("admin_panel"),
            )
        except Exception:
            pass

    @bot.message_handler(
        content_types=["document"],
        func=lambda m: get_state(m.from_user.id).get("step") == "restore_sold_db",
    )
    @admin_only
    def msg_restore_sold_db(msg: types.Message):
        uid       = msg.from_user.id
        st        = get_state(uid)
        prompt_id = st.get("prompt_msg_id")
        main_tmp  = st.get("main_tmp")
        silent_delete(bot, uid, msg.message_id)

        doc = msg.document
        if not doc.file_name.endswith(".db"):
            try:
                bot.edit_message_text(
                    "<b>Restore Database</b>\n\n"
                    "That doesn't look like a .db file. Send the <b>stockbot_sold.db</b> file:",
                    uid, prompt_id, reply_markup=back_kb("admin_panel"),
                )
            except Exception:
                pass
            return

        file_info = bot.get_file(doc.file_id)
        file_data = bot.download_file(file_info.file_path)
        sold_tmp  = db.DB_SOLD_PATH + ".restore_tmp"
        with open(sold_tmp, "wb") as f:
            f.write(file_data)

        clear_state(uid)

        # validate both files before touching anything
        err_main = _validate_db(main_tmp, _MAIN_REQUIRED_TABLES)
        err_sold = _validate_db(sold_tmp, _SOLD_REQUIRED_TABLES)
        if err_main or err_sold:
            for tmp in (main_tmp, sold_tmp):
                if os.path.exists(tmp):
                    os.remove(tmp)
            msg = "Restore aborted — file validation failed:\n"
            if err_main:
                msg += f"\n<b>stockbot.db:</b> {err_main}"
            if err_sold:
                msg += f"\n<b>stockbot_sold.db:</b> {err_sold}"
            try:
                bot.edit_message_text(msg, uid, prompt_id, reply_markup=back_kb("admin_panel"))
            except Exception:
                bot.send_message(uid, msg, reply_markup=back_kb("admin_panel"))
            return

        # atomic swap — rotate .bak files (keep up to 3 generations)
        try:
            from datetime import datetime
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            if os.path.exists(db.DB_PATH):
                shutil.copy2(db.DB_PATH, db.DB_PATH + f".bak_{ts}")
            if os.path.exists(db.DB_SOLD_PATH):
                shutil.copy2(db.DB_SOLD_PATH, db.DB_SOLD_PATH + f".bak_{ts}")

            # clean up old .bak files, keep only 3 most recent per DB
            for base in (db.DB_PATH, db.DB_SOLD_PATH):
                baks = sorted(
                    [f for f in os.listdir(os.path.dirname(base) or ".")
                     if os.path.basename(base) + ".bak_" in f],
                    reverse=True,
                )
                for old in baks[3:]:
                    try:
                        os.remove(os.path.join(os.path.dirname(base) or ".", old))
                    except Exception:
                        pass

            shutil.move(main_tmp, db.DB_PATH)
            shutil.move(sold_tmp, db.DB_SOLD_PATH)

            db.init_db()

            bot.edit_message_text(
                "<b>Restore complete!</b>\n\n"
                "Both databases restored. Previous files saved as timestamped <code>.bak</code> files (3 generations kept).\n\n"
                "Tap <b>Restart Bot</b> in the admin panel to reload cleanly.",
                uid, prompt_id,
                reply_markup=back_kb("admin_panel"),
            )
        except Exception as e:
            # clean up temp files on failure
            for tmp in (main_tmp, sold_tmp):
                if tmp and os.path.exists(tmp):
                    os.remove(tmp)
            try:
                bot.edit_message_text(
                    f"Restore failed: {e}\n\nOriginal databases were not modified.",
                    uid, prompt_id, reply_markup=back_kb("admin_panel"),
                )
            except Exception:
                pass
