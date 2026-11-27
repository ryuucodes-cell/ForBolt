"""Lucky Draw: paid slot rounds where players fund the prize themselves.

Admin creates a round for a product (instant or pre-order), sets the entry
price, slot count and an optional deadline (or none). Players buy slots
from their wallet; when the round fills, a provably fair draw (seed hash
published at creation, seed revealed at the draw) picks one winner who
receives the product. Rounds that expire or are cancelled refund EVERY
entry automatically.

Locked rules (John, 2026-10-26):
- <= 10 slots: 1 slot per person. > 10 slots: max 3 per person.
- Instant prizes are reserved from stock at creation (like giveaways).
- Deadline is optional; a no-deadline round ends only on fill or cancel.
"""

import hashlib
import html
import logging
import secrets
import threading
import time
from datetime import datetime, timedelta, timezone


def _utcnow_naive() -> datetime:
    """UTC now as naive datetime for comparison with DB-stored naive timestamps."""
    return datetime.now(timezone.utc).replace(tzinfo=None)

import telebot
from telebot import types

import db
import emojis
from utils import (
    admin_only, is_admin,
    set_state, get_state, clear_state,
    back_kb, silent_delete, check_rate_limit, edit_or_send,
    notify_admin, admin_report, log_to_channel,
    fmt_money, fmt_mmk, user_link, mask_name,
    format_credential_block,
)

log = logging.getLogger("stockbot.lucky_draw")

def _esc(s) -> str:
    return html.escape(str(s))

# John's premium emoji IDs for this feature (2026-10-26)
E_DICE   = "5260547274957672345"   # 🎲
E_TICKET = "5285376730003954510"   # 🎟
E_TROPHY = "5188344996356448758"   # 🏆
E_MONEY  = "5278467510604160626"   # 💰
E_LOCK   = "5197288647275071607"   # 🔒
E_UNLOCK = "5465443379917629504"   # 🔓
E_PARTY  = "5317026657540780588"   # 🎉
E_FIRE   = "5334725814040674667"   # 🔥
E_EYES   = "5391042881219731337"   # 👀
E_TARGET = "5310278924616356636"   # 🎯
E_BACK   = "5255703720078879038"   # ⬅️
E_HOME   = "5042022053356504092"   # 🏠

DRAW_DELAY_S = 60          # suspense countdown between "FULL" and the result
_DEADLINE_HOURS = (24, 48, 72)

_active_draws = set()
_active_lock = threading.Lock()


# ── fairness (pure functions — unit-tested) ────────────────────────────────────

def compute_winner(seed: str, entries):
    """Deterministic winner from the revealed seed + the final slot list.

    Anyone can reproduce it: payload is the seed, then every entry as
    'slot_no:telegram_id' in slot order; the SHA-256 of that payload,
    taken as an integer, modulo the number of entries, picks the winner.
    """
    payload = seed + "|" + ",".join(f"{e['slot_no']}:{e['telegram_id']}" for e in entries)
    idx = int(hashlib.sha256(payload.encode()).hexdigest(), 16) % len(entries)
    return entries[idx]


def verify_seed(seed: str, seed_hash: str) -> bool:
    return hashlib.sha256(seed.encode()).hexdigest() == seed_hash


def _short_hash(h: str) -> str:
    return f"{h[:4]}…{h[-4:]}" if len(h) > 10 else h


# ── text builders ─────────────────────────────────────────────────────────────

def _progress_bar(filled: int, total: int) -> str:
    blocks = 10
    n = int(filled * blocks / total + 0.5) if total else 0
    return "▓" * n + "░" * (blocks - n)


def _time_left(r) -> str:
    if not r["deadline"]:
        return "No deadline — runs until full"
    try:
        dl = datetime.fromisoformat(r["deadline"])
    except (ValueError, TypeError):
        return "—"
    secs = int((dl - _utcnow_naive()).total_seconds())
    if secs <= 0:
        return "Closing…"
    if secs >= 86400:
        return f"{secs // 86400}d {(secs % 86400) // 3600}h left"
    if secs >= 3600:
        return f"{secs // 3600}h {(secs % 3600) // 60}m left"
    return f"{secs // 60}m left"


def _cap_line(r) -> str:
    cap = db.ld_cap_for(r["total_slots"])
    return "1 slot per person in this round" if cap == 1 else f"Up to {cap} slots per person"


def _round_text(r, entries_count: int, my_slots: list) -> str:
    e = emojis.emoji
    status = r["status"]
    lines = [
        f"{e('dice', '🎲')} <b>LUCKY DRAW — {_esc(r['product_name'])}</b>",
        "",
        f"{e('trophy', '🏆')} Prize: <b>{_esc(r['product_name'])}</b> (worth {fmt_money(r['prize_value'])})",
        f"{e('ld_ticket', '🎟')} Entry: <b>{fmt_money(r['entry_price'])}</b> per slot",
        f"👥 Slots: <b>{entries_count} / {r['total_slots']}</b> filled",
        _progress_bar(entries_count, r["total_slots"]),
    ]
    if status == "open":
        lines.append(f"{e('clock', '⏰')} {_time_left(r)}")
        lines.append(f"{e('target', '🎯')} {_cap_line(r)}")
        if my_slots:
            nums = ", ".join(f"#{s}" for s in my_slots)
            lines.append(f"\n{e('ld_ticket', '🎟')} Your slots: <b>{len(my_slots)}</b> ({nums})")
        lines.append(f"\n{e('ld_lock', '🔒')} Fairness code: <code>{_short_hash(r['seed_hash'])}</code> "
                     f"<i>(locked before the draw — the winner is computed from it, nobody can change it)</i>")
    elif status == "drawing":
        lines.append(f"\n{e('pending', '⏳')} <b>Round full — drawing now…</b>")
    elif status == "completed":
        wname = "—"
        lines.append(f"\n{e('party', '🎉')} <b>Winner: slot #{r['winner_slot']}</b>")
        lines.append(f"{e('unlock', '🔓')} Draw seed: <code>{_esc(r['seed'])}</code>")
        ok = verify_seed(r["seed"], r["seed_hash"])
        lines.append("Fairness code matched ✅" if ok else "Fairness check FAILED — contact admin")
    elif status == "expired":
        lines.append("\n<i>This round didn't fill in time — every entry was refunded.</i>")
    elif status == "cancelled":
        lines.append("\n<i>This round was cancelled — every entry was refunded.</i>")
    return "\n".join(lines)


def _announce_text(r) -> str:
    """Channel announcement (Unicode fallbacks — premium emoji don't
    render in channel posts)."""
    e = emojis.emoji
    return (
        f"{e('dice', '🎲')} <b>LUCKY DRAW — {_esc(r['product_name'])}</b>\n\n"
        f"<blockquote>{e('trophy', '🏆')} Prize: <b>{_esc(r['product_name'])}</b> (worth {fmt_money(r['prize_value'])})\n"
        f"{e('ld_ticket', '🎟')} Entry: <b>{fmt_money(r['entry_price'])}</b> per slot · {r['total_slots']} slots\n"
        f"{e('clock', '⏰')} {_time_left(r)}</blockquote>\n\n"
        f"One winner takes the prize — the draw is provably fair and the "
        f"fairness code is locked before it starts. Open the shop and tap "
        f"<b>Lucky Draw</b> to join! {e('clover', '🍀')}"
    )


_bot_username = None


def _username(bot) -> str:
    """The bot's own @username, fetched once from Telegram itself — the
    channel join posts name it so readers know where to join."""
    global _bot_username
    if not _bot_username:
        try:
            _bot_username = bot.get_me().username or ""
        except Exception:
            _bot_username = ""
    return _bot_username


def _join_post_text(bot, r, slot_no: int, buyer_uname: str, filled: int) -> str:
    """Channel post for a slot purchase (Unicode fallbacks; the buyer is
    masked like all public posts). The last-slot variant doubles as the
    round-full announcement."""
    e = emojis.emoji
    total = r["total_slots"]
    left = total - filled
    at = f"@{_username(bot)}" if _username(bot) else "the bot"
    masked = mask_name(buyer_uname)
    if left <= 0:
        return (
            f"{e('ld_lock', '🔒')} <b>LAST SLOT TAKEN — ROUND FULL!</b>\n\n"
            f"<blockquote>{e('ld_ticket', '🎟')} Slot <b>#{slot_no}</b> by <b>{masked}</b>\n"
            f"{e('trophy', '🏆')} Prize: <b>{_esc(r['product_name'])}</b></blockquote>\n\n"
            f"{e('party', '🎉')} Winner drawn in {DRAW_DELAY_S} seconds… "
            f"good luck to everyone who joined via <b>{at}</b>! {e('clover', '🍀')}"
        )
    if left <= 3:
        return (
            f"{e('fire', '🔥')} <b>Almost Full!</b>\n\n"
            f"<blockquote>{e('ld_ticket', '🎟')} Slot <b>#{slot_no}</b> taken by <b>{masked}</b>\n"
            f"{e('trophy', '🏆')} Prize: <b>{_esc(r['product_name'])}</b> — only <b>{left} slot{'s' if left != 1 else ''} left!</b></blockquote>\n\n"
            f"Last chance — join at <b>{at}</b>, tap {e('dice', '🎲')} <b>Lucky Draw</b>!"
        )
    return (
        f"{e('dice', '🎲')} <b>Lucky Draw — New Player Joined!</b>\n\n"
        f"<blockquote>{e('ld_ticket', '🎟')} Slot <b>#{slot_no}</b> taken by <b>{masked}</b>\n"
        f"{e('trophy', '🏆')} Prize: <b>{_esc(r['product_name'])}</b> (worth {fmt_money(r['prize_value'])})\n"
        f"👥 Progress: <b>{filled} / {total}</b> slots — {left} left!</blockquote>\n\n"
        f"Join now at <b>{at}</b> — open the bot and tap {e('dice', '🎲')} <b>Lucky Draw</b>!"
    )


def _edit(bot, uid, msg_id, text, kb=None):
    try:
        bot.edit_message_text(text, uid, msg_id, reply_markup=kb)
    except Exception:
        bot.send_message(uid, text, reply_markup=kb)


# ── draw engine ───────────────────────────────────────────────────────────────

def _start_draw(bot, rid: int, delay: int = DRAW_DELAY_S):
    with _active_lock:
        if rid in _active_draws:
            return
        _active_draws.add(rid)
    threading.Thread(target=_run_draw, args=(bot, rid, delay), daemon=True).start()


def _run_draw(bot, rid: int, delay: int):
    try:
        r = db.get_ld_round(rid)
        if not r or r["status"] != "drawing":
            return
        entries = db.ld_entries(rid)
        if delay:
            # The suspense moment: DM every player, then count down. (The
            # channel already got the full announcement from the last
            # slot's join post — don't post twice.)
            seen = set()
            for en in entries:
                if en["telegram_id"] in seen:
                    continue
                seen.add(en["telegram_id"])
                try:
                    bot.send_message(
                        en["telegram_id"],
                        f"{emojis.emoji('dice', '🎲')} <b>Your Lucky Draw is FULL!</b>\n\n"
                        f"Prize: <b>{_esc(r['product_name'])}</b>\n"
                        f"The winner is being drawn right now — good luck! {emojis.emoji('clover', '🍀')}",
                        parse_mode="HTML",
                    )
                except Exception:
                    pass
            time.sleep(delay)
        r = db.get_ld_round(rid)
        if not r or r["status"] != "drawing":
            return  # cancelled/refunded during the countdown
        entries = db.ld_entries(rid)
        if not entries:
            return
        winner = compute_winner(r["seed"], entries)
        if not db.ld_finish_draw(rid, winner["telegram_id"], winner["slot_no"]):
            return  # someone else finished it
        _deliver_and_announce(bot, r, entries, winner)
    except Exception:
        log.exception("lucky draw %s failed", rid)
    finally:
        with _active_lock:
            _active_draws.discard(rid)


def _deliver_and_announce(bot, r, entries, winner):
    rid = r["round_id"]
    wid = winner["telegram_id"]
    wname = winner["username"] or ""
    pname = r["product_name"]
    e = emojis.emoji
    delivered = False
    prize_note = ""

    if r["prize_mode"] == "instant":
        taken = db.claim_reserved_stock(f"ld_{rid}")
        cred = taken[1] if taken else db.claim_stock(r["product_id"], wid)
        if cred:
            oid = db.create_order(wid, wname, r["product_id"], pname, 0, cred,
                                  payment_method="lucky_draw")
            p = db.get_product(r["product_id"])
            fmt = (p["credential_format"] or "").strip() if p else ""
            try:
                bot.send_message(
                    wid,
                    f"{e('party', '🎉')} <b>YOU WON!</b>\n\n"
                    f"Prize: <b>{_esc(pname)}</b> — Lucky Draw round #{rid}\n\n"
                    f"{format_credential_block(cred, fmt)}\n\n"
                    f"It's also saved in <b>My Orders</b> (order #{oid}). Enjoy!",
                    parse_mode="HTML",
                )
            except Exception:
                pass
            delivered = True
        else:
            # Reserved item vanished somehow — keep the win tracked in the
            # fulfillment queue and alert the admin loudly.
            db.create_manual_order(wid, wname, r["product_id"], pname, 0,
                                   mode="preorder", payment_method="lucky_draw")
            try:
                bot.send_message(
                    wid,
                    f"{e('party', '🎉')} <b>YOU WON!</b>\n\n"
                    f"Prize: <b>{_esc(pname)}</b> — Lucky Draw round #{rid}\n"
                    f"Our team is preparing your prize and will deliver it shortly.",
                    parse_mode="HTML",
                )
            except Exception:
                pass
            admin_report(
                bot, "LUCKY DRAW PRIZE MISSING",
                [("", "Round", f"#{rid}"), ("", "Prize", _esc(pname)),
                 ("", "Winner", f"@{_esc(wname)}" if wname else str(wid)),
                 ("", "Note", "Reserved stock was gone at draw time — a fulfillment order was created instead.")],
                icon="‼",
            )
    else:  # pre-order prize → fulfillment queue, like a normal pre-order
        oid = db.create_manual_order(wid, wname, r["product_id"], pname, 0,
                                     mode="preorder", payment_method="lucky_draw")
        eta = (db.cfg_get("fulfillment_eta", "") or "").strip() or "usually within 24 hours"
        try:
            bot.send_message(
                wid,
                f"{e('party', '🎉')} <b>YOU WON!</b>\n\n"
                f"Prize: <b>{_esc(pname)}</b> — Lucky Draw round #{rid}\n\n"
                f"This is a pre-order prize: our team will deliver it {eta}. "
                f"Track it in <b>My Orders</b> (order #{oid}).",
                parse_mode="HTML",
            )
        except Exception:
            pass
        delivered = True
        try:
            kb = types.InlineKeyboardMarkup()
            kb.add(emojis.button("View order", callback_data=f"adm_fulfill_{oid}",
                                 emoji_id="5444856076954520455", lead="📋"))
            notify_admin(
                bot,
                f"{e('trophy', '🏆')} <b>New Pre-order (Lucky Draw prize)</b>\n\n"
                f"Product: <b>{_esc(pname)}</b>\n"
                f"Winner: {user_link(wid, wname)}\n"
                f"Order: #{oid}",
                reply_markup=kb,
            )
        except Exception:
            pass

    # losers — one short DM each
    seen = set()
    for en in entries:
        uid = en["telegram_id"]
        if uid == wid or uid in seen:
            continue
        seen.add(uid)
        try:
            bot.send_message(
                uid,
                f"{e('dice', '🎲')} <b>Lucky Draw result</b>\n\n"
                f"Not this time — <b>{mask_name(wname)}</b> took <b>{_esc(pname)}</b> "
                f"with slot #{winner['slot_no']}.\n"
                f"Next round opens soon {e('ld_eyes', '👀')}",
                parse_mode="HTML",
            )
        except Exception:
            pass

    # channel result — masked winner, seed revealed for verification
    try:
        ok = verify_seed(r["seed"], r["seed_hash"])
        log_to_channel(
            bot,
            f"{e('party', '🎉')} <b>LUCKY DRAW WINNER!</b>\n\n"
            f"<blockquote>{e('trophy', '🏆')} Prize: <b>{_esc(pname)}</b>\n"
            f"🎟 Winning slot: <b>#{winner['slot_no']}</b> — <b>{mask_name(wname)}</b></blockquote>\n\n"
            f"{e('unlock', '🔓')} Draw seed: <code>{_esc(r['seed'])}</code>\n"
            f"SHA-256(seed) = the fairness code published at creation "
            f"{'✅' if ok else '❌'} — anyone can verify this draw.",
        )
    except Exception:
        pass

    # admin summary
    try:
        pool = r["entry_price"] * len(entries)
        notify_admin(
            bot,
            f"{e('dice', '🎲')} <b>Lucky Draw #{rid} finished</b>\n\n"
            f"Prize: <b>{_esc(pname)}</b>\n"
            f"Entries: <b>{len(entries)}</b> · Pool: <b>{fmt_money(pool)}</b>\n"
            f"Winner: {user_link(wid, wname)} (slot #{winner['slot_no']})\n"
            f"Delivered: {'✅' if delivered else '⚠️ check the fulfillment queue'}",
        )
    except Exception:
        pass


# ── expiry / cancel refunds ────────────────────────────────────────────────────

def _refund_notify(bot, r, refunds, reason: str):
    e = emojis.emoji
    for uid, amt, _n in refunds:
        try:
            bot.send_message(
                uid,
                f"{e('dice', '🎲')} <b>Lucky Draw round #{r['round_id']} {reason}</b>\n\n"
                f"Prize: <b>{_esc(r['product_name'])}</b>\n"
                f"<b>{fmt_money(amt)}</b> is back in your wallet.",
                parse_mode="HTML",
            )
        except Exception:
            pass
    try:
        log_to_channel(
            bot,
            f"{e('dice', '🎲')} <b>Lucky Draw — {_esc(r['product_name'])}</b>\n\n"
            f"Round #{r['round_id']} {reason} — <b>all entries have been refunded</b>.",
        )
    except Exception:
        pass
    try:
        total = sum(a for _u, a, _n in refunds)
        notify_admin(
            bot,
            f"{e('dice', '🎲')} <b>Lucky Draw #{r['round_id']} {reason}</b>\n\n"
            f"Prize: <b>{_esc(r['product_name'])}</b>\n"
            f"Refunded: <b>{fmt_money(total)}</b> to {len(refunds)} player(s).",
        )
    except Exception:
        pass


# ── announce (admin-triggered blast to all bot users + log channel) ───────────

def _announce_dm_text(r, filled: int) -> str:
    e = emojis.emoji
    return (
        f"<b>{e('dice', '🎲')} LUCKY DRAW!</b>\n\n"
        f"<blockquote>{e('trophy', '🏆')} Prize: <b>{_esc(r['product_name'])}</b> (worth {fmt_money(r['prize_value'])})\n"
        f"{e('ld_ticket', '🎟')} Entry: <b>{fmt_money(r['entry_price'])}</b> per slot\n"
        f"👥 Slots: <b>{filled} / {r['total_slots']}</b> filled\n"
        f"{e('clock', '⏰')} {_time_left(r)}</blockquote>\n\n"
        f"One winner takes the prize — the draw is provably fair, the "
        f"fairness code is locked before it starts. Grab your slot! "
        f"{e('clover', '🍀')}"
    )


_ANNOUNCE_EVERY = 25      # progress edit every N processed users…
_ANNOUNCE_EVERY_S = 3     # …or every N seconds, whichever comes first


def _announce_round(bot, rid: int, admin_uid: int, progress_msg_id=None):
    """DM-blast the round to every bot user (respecting the Alerts
    opt-out) and mirror it to the log channel. Runs in a thread. When
    progress_msg_id is given, that admin message shows a live progress
    bar and ends as the final report."""
    r = db.get_ld_round(rid)
    if not r or r["status"] != "open":
        return
    filled = db.ld_entry_count(rid)
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(emojis.button("Join the Draw", callback_data=f"ld_view_{rid}",
                         emoji_id=E_DICE, lead="🎲"))
    kb.add(emojis.button("✖️ Dismiss", callback_data="dismiss_msg"))
    text = _announce_dm_text(r, filled)

    users = db.list_users(limit=100000)
    total = len(users)
    sent = failed = 0
    prog = {"done": 0, "at": 0.0}
    _progress_skipped = [0]

    def _tick(force=False):
        if not progress_msg_id:
            return
        done = sent + failed + _progress_skipped[0]
        now = time.time()
        if not force and done - prog["done"] < _ANNOUNCE_EVERY and now - prog["at"] < _ANNOUNCE_EVERY_S:
            return
        prog["done"] = done
        prog["at"] = now
        pct = int(done * 100 / total) if total else 100
        try:
            bot.edit_message_text(
                f"📢 <b>Announcing Lucky Draw #{rid}…</b>\n\n"
                f"{_progress_bar(done, total)} {pct}%\n"
                f"Users: {done} / {total}\n"
                f"Sent: {sent} | Failed: {failed}",
                admin_uid, progress_msg_id,
            )
        except Exception:
            pass

    _tick(force=True)
    for u in users:
        if u["notify_opt_out"]:
            _progress_skipped[0] += 1
            _tick()
            continue
        delivered = False
        for _ in range(3):
            try:
                bot.send_message(u["telegram_id"], text, reply_markup=kb)
                sent += 1
                delivered = True
                break
            except Exception as ex:
                err = str(ex)
                if "429" in err:
                    import re
                    mt = re.search(r"retry after (\d+)", err, re.IGNORECASE)
                    time.sleep(int(mt.group(1)) if mt else 5)
                else:
                    failed += 1
                    delivered = True
                    break
        if not delivered:
            failed += 1
        _tick()
        time.sleep(0.05)
    report = f"Lucky Draw announced.\nSent: {sent} | Failed: {failed}"
    if progress_msg_id:
        kb_back = types.InlineKeyboardMarkup(row_width=1)
        kb_back.add(emojis.button("Back to Round", callback_data=f"adm_ld_view_{rid}",
                                  emoji_id=E_BACK, lead="⬅️"))
        edit_or_send(bot, admin_uid, progress_msg_id, f"📢 {report}", kb_back)
    else:
        try:
            bot.send_message(admin_uid, report)
        except Exception:
            pass
    try:
        log_to_channel(bot, _announce_text(r))
    except Exception:
        pass


def sweep(bot):
    """Housekeeping (called every minute from bot.py): expire open rounds
    past their deadline with full refunds, and recover rounds stuck in
    'drawing' after a restart."""
    now = _utcnow_naive()
    try:
        for r in db.list_ld_rounds(status="open", limit=200):
            if not r["deadline"]:
                continue
            try:
                dl = datetime.fromisoformat(r["deadline"])
            except (ValueError, TypeError):
                continue
            if dl <= now:
                refunds = db.ld_refund_round(r["round_id"], "expired")
                if refunds is not None:
                    _refund_notify(bot, r, refunds, "didn't fill in time")
    except Exception:
        log.exception("lucky draw expiry sweep failed")
    try:
        for r in db.list_ld_rounds(status="drawing", limit=50):
            with _active_lock:
                active = r["round_id"] in _active_draws
            if not active:
                _start_draw(bot, r["round_id"], delay=0)
    except Exception:
        log.exception("lucky draw recovery sweep failed")


# ── user + admin handlers ──────────────────────────────────────────────────────

def register(bot: telebot.TeleBot):

    # ═══ USER: list ═══════════════════════════════════════════════════════════

    def _render_list(uid, msg_id):
        rounds = [r for r in db.list_ld_rounds(limit=50)
                  if r["status"] in ("open", "drawing")]
        kb = types.InlineKeyboardMarkup(row_width=1)
        lines = [f"{emojis.emoji('dice', '🎲')} <b>Lucky Draw</b>", ""]
        if not rounds:
            lines.append("No open rounds right now — a new one opens soon!")
        for r in rounds:
            n = db.ld_entry_count(r["round_id"])
            tag = "⏳ drawing…" if r["status"] == "drawing" else f"{n}/{r['total_slots']} slots"
            kb.add(emojis.button(
                f"{r['product_name']} — {tag} · {fmt_mmk(r['entry_price'])}",
                callback_data=f"ld_view_{r['round_id']}", emoji_id=E_DICE, lead="🎲"))
            lines.append(f"🎲 <b>{_esc(r['product_name'])}</b> — {tag} · entry {fmt_money(r['entry_price'])}")
        kb.add(emojis.button("My Slots", callback_data="ld_mine", emoji_id=E_TICKET, lead="🎟"))
        kb.add(
            emojis.button("Back", callback_data="main_menu", emoji_id=E_BACK, lead="⬅️"),
            emojis.button("Main Menu", callback_data="main_menu", emoji_id=E_HOME, lead="🏠"),
        )
        _edit(bot, uid, msg_id, "\n".join(lines), kb)

    @bot.callback_query_handler(func=lambda c: c.data == "ld_list")
    def cb_ld_list(call: types.CallbackQuery):
        _render_list(call.from_user.id, call.message.message_id)
        bot.answer_callback_query(call.id)

    # ═══ USER: round detail ═══════════════════════════════════════════════════

    def _render_round(uid, msg_id, rid):
        r = db.get_ld_round(rid)
        if not r:
            _render_list(uid, msg_id)
            return
        entries = db.ld_entries(rid)
        mine = [en["slot_no"] for en in entries if en["telegram_id"] == uid]
        text = _round_text(r, len(entries), mine)
        kb = types.InlineKeyboardMarkup(row_width=1)
        if r["status"] == "open":
            cap = db.ld_cap_for(r["total_slots"])
            if len(mine) >= cap:
                kb.add(emojis.button("You're in ✅ — max reached for this round",
                                     callback_data=f"ld_view_{rid}", emoji_id=E_TICKET, lead="🎟"))
            else:
                left = r["total_slots"] - len(entries)
                fire = f" {emojis.emoji('fire', '🔥')}" if left <= 3 else ""
                kb.add(emojis.button(f"Buy a Slot — {fmt_mmk(r['entry_price'])}{fire}",
                                     callback_data=f"ld_buy_{rid}", emoji_id=E_TICKET, lead="🎟"))
            kb.add(emojis.button("See Players", callback_data=f"ld_players_{rid}",
                                 emoji_id="5870692618244984670", lead="👥"))
        kb.add(emojis.button("My Slots", callback_data="ld_mine", emoji_id=E_TICKET, lead="🎟"))
        kb.add(
            emojis.button("All Rounds", callback_data="ld_list", emoji_id=E_BACK, lead="⬅️"),
            emojis.button("Main Menu", callback_data="main_menu", emoji_id=E_HOME, lead="🏠"),
        )
        _edit(bot, uid, msg_id, text, kb)

    @bot.callback_query_handler(func=lambda c: c.data.startswith("ld_view_"))
    def cb_ld_view(call: types.CallbackQuery):
        rid = int(call.data[len("ld_view_"):])
        _render_round(call.from_user.id, call.message.message_id, rid)
        bot.answer_callback_query(call.id)

    @bot.callback_query_handler(func=lambda c: c.data.startswith("ld_players_"))
    def cb_ld_players(call: types.CallbackQuery):
        uid = call.from_user.id
        rid = int(call.data[len("ld_players_"):])
        r = db.get_ld_round(rid)
        if not r:
            bot.answer_callback_query(call.id, "Round not found.")
            return
        entries = db.ld_entries(rid)
        lines = [f"{emojis.emoji('dice', '🎲')} <b>Players — {_esc(r['product_name'])}</b>",
                 f"{len(entries)} / {r['total_slots']} slots taken", ""]
        for en in entries[:40]:
            mark = " ← <b>You</b>" if en["telegram_id"] == uid else ""
            lines.append(f"#{en['slot_no']} · {mask_name(en['username'])}{mark}")
        if len(entries) > 40:
            lines.append(f"…and {len(entries) - 40} more")
        if not entries:
            lines.append("<i>No players yet — be the first!</i>")
        kb = types.InlineKeyboardMarkup(row_width=1)
        kb.add(emojis.button("Back to Round", callback_data=f"ld_view_{rid}",
                             emoji_id=E_BACK, lead="⬅️"))
        _edit(bot, uid, call.message.message_id, "\n".join(lines), kb)
        bot.answer_callback_query(call.id)

    # ═══ USER: buy a slot ═════════════════════════════════════════════════════

    @bot.callback_query_handler(func=lambda c: c.data.startswith("ld_buy_") and not c.data.startswith("ld_buygo_"))
    def cb_ld_buy(call: types.CallbackQuery):
        uid = call.from_user.id
        rid = int(call.data[len("ld_buy_"):])
        r = db.get_ld_round(rid)
        if not r or r["status"] != "open":
            bot.answer_callback_query(call.id, "This round is closed.", show_alert=True)
            return
        db.ensure_user(uid, call.from_user.username or "")
        bal = db.get_balance(uid)
        mine = db.ld_user_entries(rid, uid)
        cap = db.ld_cap_for(r["total_slots"])
        kb = types.InlineKeyboardMarkup(row_width=1)
        if len(mine) >= cap:
            text = (f"{emojis.emoji('ld_target', '🎯')} <b>You're already in!</b>\n\n"
                    f"You hold slot(s) {', '.join('#' + str(en['slot_no']) for en in mine)} "
                    f"for <b>{_esc(r['product_name'])}</b> — that's the max for this round "
                    f"({_cap_line(r)}).")
        elif bal < r["entry_price"]:
            text = (f"<b>{_esc(r['product_name'])}</b>\n"
                    f"Entry: <b>{fmt_money(r['entry_price'])}</b>\n\n"
                    f"<b>Not enough balance</b> — you have {fmt_money(bal)}, "
                    f"you're <b>{fmt_money(r['entry_price'] - bal)}</b> short.\n\n"
                    f"Top up and come right back — your round is waiting.")
            kb.add(emojis.button("Top Up", callback_data="topup_menu",
                                 emoji_id="5445353829304387411", lead="💳"))
        else:
            text = (f"{emojis.emoji('ld_ticket', '🎟')} <b>Confirm your slot</b>\n\n"
                    f"Prize: <b>{_esc(r['product_name'])}</b>\n"
                    f"Entry: <b>{fmt_money(r['entry_price'])}</b>\n"
                    f"Your balance after: <b>{fmt_money(bal - r['entry_price'])}</b>\n\n"
                    f"{_cap_line(r)}.")
            kb.add(emojis.button(f"Confirm — pay {fmt_mmk(r['entry_price'])}",
                                 callback_data=f"ld_buygo_{rid}",
                                 emoji_id="5039844895779455925", lead="✅"))
        kb.add(emojis.button("Back to Round", callback_data=f"ld_view_{rid}",
                             emoji_id=E_BACK, lead="⬅️"))
        _edit(bot, uid, call.message.message_id, text, kb)
        bot.answer_callback_query(call.id)

    @bot.callback_query_handler(func=lambda c: c.data.startswith("ld_buygo_"))
    def cb_ld_buygo(call: types.CallbackQuery):
        uid = call.from_user.id
        uname = call.from_user.username or ""
        rid = int(call.data[len("ld_buygo_"):])
        if not check_rate_limit(uid, "ld_buy", min_seconds=3):
            bot.answer_callback_query(call.id, "One moment…", show_alert=True)
            return
        db.ensure_user(uid, uname)
        res = db.ld_buy_slot(rid, uid, uname)
        if not res.get("ok"):
            err = res.get("error")
            msg = {
                "insufficient": "Not enough balance anymore — top up and try again.",
                "cap": "You've reached the max slots for this round.",
                "full": "Round just filled!",
                "closed": "This round is closed.",
            }.get(err, "Something went wrong.")
            bot.answer_callback_query(call.id, msg, show_alert=True)
            _render_round(uid, call.message.message_id, rid)
            return
        bot.answer_callback_query(call.id, f"🎟 Slot #{res['slot_no']} is yours!")
        _render_round(uid, call.message.message_id, rid)
        # Public join post to the log channel (masked name; the last
        # slot's post doubles as the round-full announcement).
        try:
            r = db.get_ld_round(rid)
            if r:
                filled = db.ld_entry_count(rid)
                log_to_channel(bot, _join_post_text(bot, r, res["slot_no"], uname, filled))
        except Exception:
            pass
        if res["filled"]:
            _start_draw(bot, rid)

    # ═══ USER: my slots ═══════════════════════════════════════════════════════

    @bot.callback_query_handler(func=lambda c: c.data == "ld_mine")
    def cb_ld_mine(call: types.CallbackQuery):
        uid = call.from_user.id
        rows = db.ld_user_round_entries(uid, limit=60)
        kb = types.InlineKeyboardMarkup(row_width=1)
        lines = [f"{emojis.emoji('ld_ticket', '🎟')} <b>My Slots</b>", ""]
        if not rows:
            lines.append("You haven't joined any rounds yet.")
        by_round = {}
        for row in rows:
            by_round.setdefault(row["round_id"], []).append(row)
        for rid, items in list(by_round.items())[:15]:
            first = items[0]
            slots = ", ".join(f"#{i['slot_no']}" for i in items)
            st = first["round_status"]
            if st == "completed":
                state = (f"{emojis.emoji('party', '🎉')} <b>YOU WON!</b>"
                         if first["winner_id"] == uid else "Finished")
            elif st == "open":
                state = "⏳ Open"
            elif st == "drawing":
                state = "⏳ Drawing…"
            else:
                state = "↩️ Refunded"
            lines.append(f"🎲 <b>{_esc(first['product_name'])}</b> — slot(s) {slots} · {state}")
            kb.add(emojis.button(f"Round #{rid} — {first['product_name']}",
                                 callback_data=f"ld_view_{rid}", emoji_id=E_DICE, lead="🎲"))
        kb.add(
            emojis.button("All Rounds", callback_data="ld_list", emoji_id=E_BACK, lead="⬅️"),
            emojis.button("Main Menu", callback_data="main_menu", emoji_id=E_HOME, lead="🏠"),
        )
        _edit(bot, uid, call.message.message_id, "\n".join(lines), kb)
        bot.answer_callback_query(call.id)

    # ═══ ADMIN: home + detail ═════════════════════════════════════════════════

    _STATUS_ICON = {"open": "🟢", "drawing": "⏳", "completed": "🏆",
                    "expired": "↩️", "cancelled": "🚫"}

    def _render_adm_home(uid, msg_id):
        rounds = db.list_ld_rounds(limit=10)
        kb = types.InlineKeyboardMarkup(row_width=1)
        kb.add(emojis.button("Create Round", callback_data="adm_ld_create",
                             emoji_id=E_DICE, lead="🎲"))
        lines = [f"{emojis.emoji('dice', '🎲')} <b>Lucky Draw — Admin</b>", ""]
        if not rounds:
            lines.append("No rounds yet. Create the first one!")
        for r in rounds:
            n = db.ld_entry_count(r["round_id"])
            lines.append(f"{_STATUS_ICON.get(r['status'], '')} <b>#{r['round_id']} {_esc(r['product_name'])}</b>"
                         f" — {n}/{r['total_slots']} · {fmt_mmk(r['entry_price'])}/slot")
            kb.add(emojis.button(f"#{r['round_id']} · {r['product_name']} · {r['status']}",
                                 callback_data=f"adm_ld_view_{r['round_id']}",
                                 emoji_id=E_DICE, lead="🎲"))
        kb.add(emojis.button("Back", callback_data="admin_panel", emoji_id=E_BACK, lead="⬅️"))
        _edit(bot, uid, msg_id, "\n".join(lines), kb)

    @bot.callback_query_handler(func=lambda c: c.data == "adm_ld")
    @admin_only
    def cb_adm_ld(call: types.CallbackQuery):
        _render_adm_home(call.from_user.id, call.message.message_id)
        bot.answer_callback_query(call.id)

    def _render_adm_detail(uid, msg_id, rid):
        r = db.get_ld_round(rid)
        if not r:
            _render_adm_home(uid, msg_id)
            return
        entries = db.ld_entries(rid)
        pool = r["entry_price"] * len(entries)
        lines = [
            f"{emojis.emoji('dice', '🎲')} <b>Lucky Draw #{rid}</b> — {_STATUS_ICON.get(r['status'], '')} {r['status']}",
            "",
            f"Prize: <b>{_esc(r['product_name'])}</b> ({'⚡ Instant' if r['prize_mode'] == 'instant' else '📦 Pre-order'}, worth {fmt_money(r['prize_value'])})",
            f"Entry: <b>{fmt_money(r['entry_price'])}</b> · Slots: <b>{len(entries)}/{r['total_slots']}</b> · Pool so far: <b>{fmt_money(pool)}</b>",
            f"Deadline: <b>{_time_left(r) if r['status'] == 'open' else (r['deadline'] or 'none')}</b>",
            f"Fairness code: <code>{_esc(r['seed_hash'])}</code>",
        ]
        if r["status"] == "completed":
            w = next((en for en in entries if en["slot_no"] == r["winner_slot"]), None)
            lines.append(f"Winner: {user_link(r['winner_id'], w['username'] if w else '')} (slot #{r['winner_slot']})")
            lines.append(f"Seed: <code>{_esc(r['seed'])}</code>")
        if entries:
            lines.append("\n<b>Players:</b>")
            for en in entries[:30]:
                lines.append(f"#{en['slot_no']} · {user_link(en['telegram_id'], en['username'])}")
            if len(entries) > 30:
                lines.append(f"…and {len(entries) - 30} more")
        kb = types.InlineKeyboardMarkup(row_width=1)
        if r["status"] == "open":
            kb.add(emojis.button("Announce to all users", callback_data=f"adm_ld_announce_{rid}",
                                 emoji_id="5399967660052081305", lead="📢"))
        if r["status"] in ("open", "drawing"):
            kb.add(emojis.button("Cancel Round (refund all)", callback_data=f"adm_ld_cancel_{rid}",
                                 emoji_id="5210952531676504517", lead="❌"))
        kb.add(emojis.button("Back", callback_data="adm_ld", emoji_id=E_BACK, lead="⬅️"))
        _edit(bot, uid, msg_id, "\n".join(lines), kb)

    @bot.callback_query_handler(func=lambda c: c.data.startswith("adm_ld_view_"))
    @admin_only
    def cb_adm_ld_view(call: types.CallbackQuery):
        _render_adm_detail(call.from_user.id, call.message.message_id,
                           int(call.data[len("adm_ld_view_"):]))
        bot.answer_callback_query(call.id)

    @bot.callback_query_handler(func=lambda c: c.data.startswith("adm_ld_announce_"))
    @admin_only
    def cb_adm_ld_announce(call: types.CallbackQuery):
        r = db.get_ld_round(int(call.data[len("adm_ld_announce_"):]))
        if r and r["status"] == "open":
            bot.answer_callback_query(call.id, "Announcing to all users...")
            msg_id = call.message.message_id
            _edit(bot, call.from_user.id, msg_id,
                  f"📢 <b>Announcing Lucky Draw #{r['round_id']}…</b>\n\n"
                  f"{_progress_bar(0, 1)} 0%\nSent: 0 | Failed: 0")
            threading.Thread(target=_announce_round,
                             args=(bot, r["round_id"], call.from_user.id, msg_id),
                             daemon=True).start()
        else:
            bot.answer_callback_query(call.id, "Round is not open.", show_alert=True)

    @bot.callback_query_handler(func=lambda c: c.data.startswith("adm_ld_cancel_") and not c.data.startswith("adm_ld_cancelgo_"))
    @admin_only
    def cb_adm_ld_cancel(call: types.CallbackQuery):
        rid = int(call.data[len("adm_ld_cancel_"):])
        kb = types.InlineKeyboardMarkup(row_width=2)
        kb.add(
            emojis.button("Yes, cancel & refund", callback_data=f"adm_ld_cancelgo_{rid}"),
            emojis.button("No", callback_data=f"adm_ld_view_{rid}"),
        )
        _edit(bot, call.from_user.id, call.message.message_id,
              f"<b>Cancel round #{rid}?</b>\n\nEvery entry is refunded in full. This cannot be undone.", kb)
        bot.answer_callback_query(call.id)

    @bot.callback_query_handler(func=lambda c: c.data.startswith("adm_ld_cancelgo_"))
    @admin_only
    def cb_adm_ld_cancelgo(call: types.CallbackQuery):
        rid = int(call.data[len("adm_ld_cancelgo_"):])
        r = db.get_ld_round(rid)
        refunds = db.ld_refund_round(rid, "cancelled") if r else None
        if refunds is None:
            bot.answer_callback_query(call.id, "Round already finished.", show_alert=True)
        else:
            _refund_notify(bot, r, refunds, "was cancelled")
            bot.answer_callback_query(call.id, "Cancelled — everyone refunded.")
        _render_adm_detail(call.from_user.id, call.message.message_id, rid)

    # ═══ ADMIN: creation wizard ═══════════════════════════════════════════════

    @bot.callback_query_handler(func=lambda c: c.data == "adm_ld_create")
    @admin_only
    def cb_adm_ld_create(call: types.CallbackQuery):
        prods = db.list_products(active_only=True)
        if not prods:
            bot.answer_callback_query(call.id, "No active products.", show_alert=True)
            return
        kb = types.InlineKeyboardMarkup(row_width=1)
        for p in prods:
            mode = db.get_fulfillment(p)
            tag = "⚡ instant" if mode == "instant" else "📦 pre-order"
            stock = f" (stock: {db.stock_count(p['product_id'])})" if mode == "instant" else ""
            kb.add(emojis.button(f"{p['name']} [{tag}]{stock}",
                                 callback_data=f"adm_ld_pick_{p['product_id']}"))
        kb.add(emojis.button("Back", callback_data="adm_ld", emoji_id=E_BACK, lead="⬅️"))
        _edit(bot, call.from_user.id, call.message.message_id,
              f"{emojis.emoji('dice', '🎲')} <b>New Lucky Draw</b> (1/4)\n\nPick the prize product:", kb)
        bot.answer_callback_query(call.id)

    @bot.callback_query_handler(func=lambda c: c.data.startswith("adm_ld_pick_"))
    @admin_only
    def cb_adm_ld_pick(call: types.CallbackQuery):
        pid = int(call.data[len("adm_ld_pick_"):])
        p = db.get_product(pid)
        if not p:
            bot.answer_callback_query(call.id, "Product not found.", show_alert=True)
            return
        set_state(call.from_user.id, step="ld_price", ld_pid=pid,
                  prompt_msg_id=call.message.message_id)
        _edit(bot, call.from_user.id, call.message.message_id,
              f"{emojis.emoji('dice', '🎲')} <b>New Lucky Draw</b> (2/4)\n\n"
              f"Prize: <b>{_esc(p['name'])}</b>\n\n"
              f"Send the <b>entry price</b> per slot (in Ks, numbers only):",
              back_kb("adm_ld"))
        bot.answer_callback_query(call.id)

    @bot.message_handler(func=lambda m: get_state(m.from_user.id).get("step") in ("ld_price", "ld_slots"))
    def msg_ld_wizard(msg: types.Message):
        uid = msg.from_user.id
        if not is_admin(uid):
            return
        st = get_state(uid)
        step = st.get("step")
        prompt_id = st.get("prompt_msg_id")
        silent_delete(bot, uid, msg.message_id)
        text = (msg.text or "").strip()
        if text == "/cancel":
            clear_state(uid)
            _render_adm_home(uid, prompt_id)
            return
        try:
            val = int(text.replace(",", ""))
        except ValueError:
            val = None
        if step == "ld_price":
            if not val or val <= 0:
                edit_or_send(bot, uid, prompt_id, "Please send a valid entry price (numbers only), or /cancel:")
                return
            set_state(uid, step="ld_slots", ld_pid=st.get("ld_pid"), ld_price=val,
                      prompt_msg_id=prompt_id)
            p = db.get_product(st.get("ld_pid"))
            edit_or_send(bot, uid, prompt_id,
                         f"{emojis.emoji('dice', '🎲')} <b>New Lucky Draw</b> (3/4)\n\n"
                         f"Prize: <b>{_esc(p['name']) if p else ''}</b> · Entry: <b>{fmt_money(val)}</b>\n\n"
                         f"Send the <b>number of slots</b> (2 or more):")
        elif step == "ld_slots":
            if not val or val < 2 or val > 1000:
                edit_or_send(bot, uid, prompt_id, "Please send a slot count between 2 and 1000, or /cancel:")
                return
            set_state(uid, step="ld_deadline", ld_pid=st.get("ld_pid"),
                      ld_price=st.get("ld_price"), ld_slots=val, prompt_msg_id=prompt_id)
            kb = types.InlineKeyboardMarkup(row_width=2)
            kb.add(
                emojis.button("24h", callback_data="adm_ld_dl_24"),
                emojis.button("48h", callback_data="adm_ld_dl_48"),
                emojis.button("72h", callback_data="adm_ld_dl_72"),
                emojis.button("No deadline", callback_data="adm_ld_dl_none"),
            )
            p = db.get_product(st.get("ld_pid"))
            edit_or_send(bot, uid, prompt_id,
                         f"{emojis.emoji('dice', '🎲')} <b>New Lucky Draw</b> (4/4)\n\n"
                         f"Prize: <b>{_esc(p['name']) if p else ''}</b> · Entry: <b>{fmt_money(st.get('ld_price', 0))}</b> · Slots: <b>{val}</b>\n\n"
                         f"Pick a <b>deadline</b> — if the round doesn't fill by then, everyone is refunded:",
                         kb)

    @bot.callback_query_handler(func=lambda c: c.data.startswith("adm_ld_dl_"))
    @admin_only
    def cb_adm_ld_deadline(call: types.CallbackQuery):
        uid = call.from_user.id
        st = get_state(uid)
        if st.get("step") != "ld_deadline":
            bot.answer_callback_query(call.id, "Wizard expired — start again.", show_alert=True)
            return
        choice = call.data[len("adm_ld_dl_"):]
        deadline = None
        if choice in ("24", "48", "72"):
            deadline = (_utcnow_naive() + timedelta(hours=int(choice))).isoformat(timespec="seconds")
            dl_label = f"{choice} hours"
        else:
            dl_label = "No deadline (runs until full)"
        p = db.get_product(st.get("ld_pid"))
        if not p:
            clear_state(uid)
            bot.answer_callback_query(call.id, "Product not found.", show_alert=True)
            return
        mode = db.get_fulfillment(p)
        price = int(st.get("ld_price", 0))
        slots = int(st.get("ld_slots", 0))
        set_state(uid, step="ld_preview", ld_pid=p["product_id"], ld_price=price,
                  ld_slots=slots, ld_deadline=deadline, prompt_msg_id=call.message.message_id)
        cap = db.ld_cap_for(slots)
        text = (
            f"{emojis.emoji('dice', '🎲')} <b>Lucky Draw — Preview</b>\n\n"
            f"{emojis.emoji('trophy', '🏆')} Prize: <b>{_esc(p['name'])}</b> "
            f"({'⚡ Instant' if mode == 'instant' else '📦 Pre-order'}, worth {fmt_money(p['price_mmk'])})\n"
            f"{emojis.emoji('ld_ticket', '🎟')} Entry: <b>{fmt_money(price)}</b> · Slots: <b>{slots}</b>\n"
            f"{emojis.emoji('ld_money', '💰')} Pool when full: <b>{fmt_money(price * slots)}</b>\n"
            f"{emojis.emoji('clock', '⏰')} Deadline: <b>{dl_label}</b>\n"
            f"{emojis.emoji('ld_target', '🎯')} Per player: <b>{'1 slot only' if cap == 1 else f'up to {cap} slots'}</b>\n\n"
            f"Publishing reserves the prize from stock (instant) and locks the fairness code."
        )
        kb = types.InlineKeyboardMarkup(row_width=2)
        kb.add(
            emojis.button("Publish", callback_data="adm_ld_pub",
                          emoji_id="5039844895779455925", lead="✅"),
            emojis.button("Cancel", callback_data="adm_ld",
                          emoji_id="5210952531676504517", lead="❌"),
        )
        _edit(bot, uid, call.message.message_id, text, kb)
        bot.answer_callback_query(call.id)

    @bot.callback_query_handler(func=lambda c: c.data == "adm_ld_pub")
    @admin_only
    def cb_adm_ld_publish(call: types.CallbackQuery):
        uid = call.from_user.id
        st = get_state(uid)
        if st.get("step") != "ld_preview":
            bot.answer_callback_query(call.id, "Wizard expired — start again.", show_alert=True)
            return
        p = db.get_product(st.get("ld_pid"))
        clear_state(uid)
        if not p or not p["active"]:
            bot.answer_callback_query(call.id, "Product not available anymore.", show_alert=True)
            _render_adm_home(uid, call.message.message_id)
            return
        mode = db.get_fulfillment(p)
        seed = secrets.token_hex(16)
        seed_hash = hashlib.sha256(seed.encode()).hexdigest()
        rid = db.create_ld_round(
            product_id=p["product_id"], product_name=p["name"], prize_mode=mode,
            prize_value=p["price_mmk"], entry_price=int(st.get("ld_price", 0)),
            total_slots=int(st.get("ld_slots", 0)), deadline=st.get("ld_deadline"),
            created_by=uid, seed=seed, seed_hash=seed_hash,
        )
        if rid == -1:
            bot.answer_callback_query(
                call.id,
                "No stock to reserve the prize — add stock for this product first.",
                show_alert=True)
            _render_adm_home(uid, call.message.message_id)
            return
        bot.answer_callback_query(call.id, "Round published 🎲 — tap Announce when you want to blast it")
        _render_adm_detail(uid, call.message.message_id, rid)
