"""Giveaway system: user join flow, main-menu view, admin management, and manual draw.

Prizes are products drawn from existing stock. Winners receive a credential via the
normal claim_stock + create_order path (at 0 MMK) so it appears in 📦 My Orders.
The draw is manual only; an optional end time just stops new entries.
"""

import html
import random
import threading
import time
from datetime import datetime, timezone

import telebot
from telebot import types

import db
import emojis
from utils import (
    admin_only, is_admin,
    set_state, get_state, clear_state,
    back_kb, silent_delete, check_rate_limit,
    notify_admin, admin_report, log_to_channel,
    fmt_money, user_link, fmt_datetime,
)

from datetime import timedelta as _timedelta


def _utcnow() -> datetime:
    """UTC-aware 'now' matching SQLite datetime('now')."""
    return datetime.now(timezone.utc)


def _utcnow_naive() -> datetime:
    """UTC now as a naive datetime for comparison with DB-stored naive timestamps."""
    return _utcnow().replace(tzinfo=None)


def _esc(s) -> str:
    return html.escape(str(s))

_bot_username = None


def _username(bot: telebot.TeleBot) -> str:
    global _bot_username
    if not _bot_username:
        try:
            _bot_username = bot.get_me().username or ""
        except Exception:
            _bot_username = ""
    return _bot_username


def invite_link(bot: telebot.TeleBot, gid: int, inviter_id: int) -> str:
    un = _username(bot)
    return f"https://t.me/{un}?start=give_{gid}_{inviter_id}" if un else ""


def _invites_enabled() -> bool:
    return db.cfg_get("giveaway_invite_enabled", "1") == "1"


def _invite_entries() -> int:
    try:
        return max(1, int(db.cfg_get("giveaway_invite_entries", "1") or 1))
    except Exception:
        return 1


def _is_fresh_user(uid: int, minutes: int = 15) -> bool:
    """Anti-abuse: an invite only credits when the invitee just registered."""
    try:
        u = db.get_user(uid)
        if not u or not u["registered_at"]:
            return True
        reg = datetime.fromisoformat(u["registered_at"])
        return (_utcnow_naive() - reg) <= _timedelta(minutes=minutes)
    except Exception:
        return True


def handle_deep_link(bot: telebot.TeleBot, uid: int, uname: str, payload: str) -> bool:
    """Handle /start give_<gid>_<inviter>. Returns True if consumed."""
    if not payload.startswith("give_"):
        return False
    parts = payload.split("_")
    if len(parts) != 3:
        return False
    try:
        gid = int(parts[1])
        inviter = int(parts[2])
    except ValueError:
        return False
    g = db.get_giveaway(gid)
    if not g or g["status"] != "active" or _end_time_passed(g):
        bot.send_message(uid, "This giveaway is no longer open.")
        return True
    # Stash the invite — credited when (and if) they join the giveaway.
    if inviter != uid and _invites_enabled():
        set_state(uid, pending_giveaway_invite={"gid": gid, "inviter": inviter})
    # Show the giveaway so they can join right away.
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(emojis.button("🎁 Join Giveaway", callback_data=f"gw_join_{gid}",
                         emoji_id="5039823300683891773", lead="🎁"))
    kb.add(emojis.button("🏠 Main Menu", callback_data="main_menu",
                         emoji_id="5042022053356504092", lead="🏠"))
    bot.send_message(uid, _giveaway_view_text(g) + "\n\nYou were invited — join for a chance to win!",
                     parse_mode="HTML", reply_markup=kb)
    return True


def _auto_giveaway_copy(product_name: str, slots: int, per_invite: int, mode: str = "instant"):
    """Hardcoded hype copy: John only picks the product, the text writes itself."""
    unit = "winner" if slots == 1 else "winners"
    title = f"Win {product_name}!"
    if mode == "preorder":
        delivery_line = "We'll deliver your prize to you as soon as it arrives!"
    else:
        delivery_line = "Your prize is delivered instantly after the draw!"
    desc = (
        f"We're giving away {product_name} to {slots} lucky {unit} — completely FREE!\n\n"
        f"Joining is easy: just tap \"Join Giveaway\" below.\n\n"
        f"Want better odds? After you join, you'll get your own invite link. "
        f"Every friend who joins with your link earns you +{per_invite} bonus entry. "
        f"More friends = more entries = more chances to win!\n\n"
        f"{delivery_line}\n\n"
        f"Winners are drawn at random. Good luck!"
    )
    return title, desc


# ── helpers ───────────────────────────────────────────────────────────────────

def _gw_membership_check(bot: telebot.TeleBot, uid: int) -> bool:
    """Giveaway-specific membership check — required regardless of the global gate.

    Mirrors utils.check_membership internals but WITHOUT the channel_gate_enabled
    short-circuit. Checks ALL configured channels. Fail-closed on check errors
    (the old "except: return True" was a bypass hole).
    """
    channels = []
    for key in ("channel_username", "channel_username2"):
        u = db.cfg_get(key, "").strip().lstrip("@")
        if u:
            channels.append(u)
    if not channels:
        return True
    for username in channels:
        try:
            member = bot.get_chat_member(f"@{username}", uid)
            if member.status not in ("member", "administrator", "creator", "restricted"):
                return False
        except Exception as e:
            if "chat not found" in str(e).lower():
                continue
            return False
    return True


def _parse_end_time(raw: str):
    """Parse a combined duration ('2h', '1d', '1h30m') into a future ISO timestamp.

    Returns the ISO string, or None if the input has no valid positive duration.
    """
    import re
    total = 0
    for value, unit in re.findall(r"(\d+)\s*([dhm])", (raw or "").lower()):
        v = int(value)
        total += v * {"d": 86400, "h": 3600, "m": 60}[unit]
    if total <= 0:
        return None
    return (_utcnow_naive() + _timedelta(seconds=total)).isoformat()


def _end_time_passed(g) -> bool:
    if not g["end_time"]:
        return False
    try:
        return _utcnow_naive() > datetime.fromisoformat(g["end_time"])
    except Exception:
        return False


def _time_left(g) -> str:
    if not g["end_time"]:
        return "no end time"
    try:
        diff = datetime.fromisoformat(g["end_time"]) - _utcnow_naive()
    except Exception:
        return "no end time"
    secs = int(diff.total_seconds())
    if secs <= 0:
        return "ended"
    d, rem = divmod(secs, 86400)
    h, rem = divmod(rem, 3600)
    m, _ = divmod(rem, 60)
    parts = []
    if d: parts.append(f"{d}d")
    if h: parts.append(f"{h}h")
    if m and not d: parts.append(f"{m}m")
    # NOTE: never return "<1m" — the "<" breaks Telegram HTML parsing
    # when this is wrapped in <b>...</b> (was: "Unsupported start tag").
    return " ".join(parts) or "under 1m"


def _product_name(product_id: int) -> str:
    p = db.get_product(product_id)
    return p["name"] if p else f"#{product_id}"


def _giveaway_view_text(g) -> str:
    pname = _esc(_product_name(g["product_id"]))
    return (
        f"<b>{emojis.emoji('gift', '🎁')} {_esc(g['title'])}</b>\n\n"
        f"<blockquote>{_esc(g['description'])}\n\n"
        f"Prize: <b>{pname}</b>\n"
        f"Winners: <b>{g['winner_slots']}</b>\n"
        f"Entrants: <b>{db.count_entries(g['giveaway_id'])}</b>\n"
        f"Time left: <b>{_time_left(g)}</b></blockquote>"
    )


# ── draw engine (module-level so it's unit-testable) ──────────────────────────

def run_draw(bot, gid) -> dict:
    """Manual draw. Claims the draw atomically, selects winners, delivers prizes.

    Returns {'error': str} on rejection, else {'entrants','selected','delivered','shortfall'}.
    """
    # Claim the draw atomically so it can never run twice.
    with db._lock:
        g = db.get_giveaway(gid)
        if g is None:
            return {"error": "Giveaway not found."}
        if g["status"] == "drawn":
            return {"error": "This giveaway was already drawn."}
        if g["status"] == "cancelled":
            return {"error": "A cancelled giveaway cannot be drawn."}
        db.set_giveaway_status(gid, "drawn", drawn_at=_utcnow_naive().isoformat())

    product_id = g["product_id"]
    product = db.get_product(product_id)
    pname = product["name"] if product else _product_name(product_id)

    entrants = list(db.list_entrants(gid))
    slots = g["winner_slots"]
    # Weighted draw: each entrant's tickets = base 1 + bonus entries from invites.
    pool = []
    for e in entrants:
        try:
            tickets = max(1, int(e["entries"] or 1))
        except Exception:
            tickets = 1
        pool.extend([e] * tickets)
    winners, seen = [], set()
    for cand in random.sample(pool, len(pool)) if pool else []:
        wid = cand["telegram_id"]
        if wid in seen:
            continue
        seen.add(wid)
        winners.append(cand)
        if len(winners) >= min(slots, len(entrants)):
            break


    mode = db.get_fulfillment(product) if product else "instant"
    delivered = shortfall = 0
    for w in winners:
        wid = w["telegram_id"]
        wname = w["username"] or ""
        # A per-winner failure must not abort the whole draw (the giveaway is already
        # flipped to "drawn" and can't be retried). Isolate each winner: on error,
        # record a shortfall and alert the admin so it's never silently lost.
        try:
            if mode == "instant":
                # Prize stock was reserved at creation (V1.5) — claim from
                # the reservation first; fall back to the general pool.
                taken = db.claim_reserved_stock(f"giveaway_{gid}")
                cred = taken[1] if taken else db.claim_stock(product_id, wid)
                if cred is not None:
                    oid = db.create_order(wid, wname, product_id, pname, 0, cred, payment_method="giveaway")
                    db.add_giveaway_winner(gid, wid, wname, credential=cred, delivered=1, order_id=oid)
                    delivered += 1
                    _notify_winner(bot, wid, g["title"], pname)
                else:
                    db.add_giveaway_winner(gid, wid, wname, credential=None, delivered=0, order_id=None)
                    shortfall += 1
            else:
                # preorder: no stock to claim — the prize is a fulfillment
                # order, handled by the admin queue like a purchase.
                oid = db.create_manual_order(wid, wname, product_id, pname, 0,
                                             mode=mode, payment_method="giveaway")
                db.add_giveaway_winner(gid, wid, wname, credential=None, delivered=1, order_id=oid)
                delivered += 1
                _notify_fulfillment_winner(bot, wid, g["title"], pname, mode, oid)
                _notify_admin_fulfillment(bot, oid)
        except Exception as e:
            shortfall += 1
            try:
                db.add_giveaway_winner(gid, wid, wname, credential=None, delivered=0, order_id=None)
            except Exception:
                pass
            admin_report(
                bot, "GIVEAWAY DELIVERY FAILED",
                [
                    ("", "Giveaway", _esc(g["title"])),
                    ("", "Prize", _esc(pname)),
                    ("", "Winner", f"@{_esc(wname)}" if wname else str(wid)),
                    ("", "Error", _esc(str(e))),
                ],
                icon="‼",
            )

    # Release any prize stock still reserved for this giveaway (fewer
    # entrants than slots, or reserved rows the claims didn't use).
    try:
        db.release_reservations(f"giveaway_{gid}")
    except Exception:
        pass

    if winners:
        _post_winners(bot, g, pname, winners)
    if shortfall:
        admin_report(
            bot, "GIVEAWAY STOCK SHORTFALL",
            [
                ("", "Giveaway", _esc(g["title"])),
                ("", "Prize", _esc(pname)),
                ("", "Delivered", str(delivered)),
                ("", "Not delivered", str(shortfall)),
            ],
            icon="",
        )
    return {"entrants": len(entrants), "selected": len(winners),
            "delivered": delivered, "shortfall": shortfall}


def check_end_reminders(bot):
    """Called periodically. DMs admins once when an active giveaway's end time passes.

    The draw stays manual — this is only a nudge with a Draw Now button.
    """
    try:
        due = db.list_giveaways_needing_end_reminder()
    except Exception:
        return
    for g in due:
        gid = g["giveaway_id"]
        try:
            entrants = db.count_entries(gid)
            pname = _esc(_product_name(g["product_id"]))
            kb = types.InlineKeyboardMarkup()
            kb.add(emojis.button("🎲 Draw Now", callback_data=f"adm_gw_draw_{gid}"))
            notify_admin(
                bot,
                f"<b>Giveaway Ended</b> {emojis.emoji('warning', '🏁')}\n\n"
                f"<blockquote>{_esc(g['title'])}\n"
                f"Prize: <b>{pname}</b>\n"
                f"Entrants: <b>{entrants}</b></blockquote>\n\n"
                f"Entries are closed — tap below to pick the winners!",
                reply_markup=kb,
            )
        except Exception:
            pass
        finally:
            db.mark_giveaway_reminded(gid)


def _notify_winner(bot, uid, title, pname):
    try:
        kb = types.InlineKeyboardMarkup(row_width=1)
        kb.add(emojis.button("📦 My Orders", callback_data="my_orders", emoji_id="5444856076954520455", lead="📦"))
        bot.send_message(
            uid,
            f"<b>Congratulations — you're a winner</b>\n\n"
            f"Giveaway: <b>{_esc(title)}</b>\n"
            f"Prize: <b>{_esc(pname)}</b>\n\n"
            f"Your prize is waiting in <b>My Orders</b>.",
            reply_markup=kb,
        )
    except Exception:
        pass


def _notify_fulfillment_winner(bot, uid, title, pname, mode, order_id):
    """Winner DM for pre-order prizes."""
    try:
        kb = types.InlineKeyboardMarkup(row_width=1)
        tail = "We'll deliver it to you as soon as it arrives."
        kb.add(emojis.button("📦 My Orders", callback_data="my_orders",
                             emoji_id="5444856076954520455", lead="📦"))
        bot.send_message(
            uid,
            f"<b>Congratulations — you're a winner! {emojis.emoji('party', '🎉')}</b>\n\n"
            f"Giveaway: <b>{_esc(title)}</b>\n"
            f"Prize: <b>{_esc(pname)}</b>\n\n"
            f"{tail}",
            parse_mode="HTML",
            reply_markup=kb,
        )
    except Exception:
        pass


def _notify_admin_fulfillment(bot, order_id):
    """Alert admins about a giveaway fulfillment order (mirrors the purchase flow)."""
    try:
        from msg_templates import admin_fulfill_new
        from utils import user_link
        order = db.get_order(order_id)
        if not order:
            return
        mode = db.get_order_fulfillment(order)
        label = {"preorder": "Pre-order"}.get(mode, mode)
        buyer = user_link(order["telegram_id"], order["username"])
        info = {_esc(k): _esc(v) for k, v in db.get_manual_info(order).items()}
        text = admin_fulfill_new(_esc(order["product_name"]), label + " (giveaway)",
                                 buyer, order_id, "FREE", info)
        kb = types.InlineKeyboardMarkup()
        kb.add(emojis.button("View order", callback_data=f"adm_fulfill_{order_id}",
                             emoji_id="5444856076954520455", lead="📋"))
        notify_admin(bot, text, reply_markup=kb)
    except Exception:
        pass


def _post_winners(bot, g, pname, winners):
    # Channel post: custom emoji never render in channels, so pass real
    # Unicode fallbacks — emojis.emoji() handles both cases.
    lines = [
        f"{emojis.emoji('gift', '🎁')} <b>Giveaway Winners! 🎉</b>",
        "",
        f"<blockquote>{_esc(g['title'])}\n"
        f"{emojis.emoji('package', '📦')} Prize: <b>{_esc(pname)}</b></blockquote>",
        "",
        f"{emojis.emoji('special', '🎉')} <b>Our lucky winners:</b>",
    ]
    for w in winners:
        uname = (w["username"] or "").lstrip("@")
        if uname:
            lines.append(f"• @{_esc(uname)}")
        else:
            lines.append(f"• <a href=\"tg://user?id={w['telegram_id']}\">a winner</a>")
    lines.append("")
    lines.append("With thanks to everyone who took part.")
    log_to_channel(bot, "\n".join(lines))


def register(bot: telebot.TeleBot):

    # ═══ USER: main-menu giveaway view ════════════════════════════════════════

    @bot.callback_query_handler(func=lambda c: c.data == "gw_menu")
    def cb_gw_menu(call: types.CallbackQuery):
        uid = call.from_user.id
        _render_giveaway_view(bot, uid, call.message.message_id, call)

    def _render_giveaway_view(bot, uid, msg_id, call=None):
        g = db.get_active_giveaway()
        if not g:
            kb = back_kb("main_menu")
            _edit_or_send(bot, uid, msg_id,
                          "<b>Giveaway</b>\n\nThere is no active giveaway at the moment.\n"
                          "Please check back soon.", kb)
            if call:
                bot.answer_callback_query(call.id)
            return

        kb = types.InlineKeyboardMarkup(row_width=1)
        text = _giveaway_view_text(g)
        gid = g["giveaway_id"]
        if _end_time_passed(g):
            text += "\n\nEntries are now closed."
        elif db.has_entered(gid, uid):
            text += "\n\nYou're already entered. Best of luck!"
            if _invites_enabled():
                n = _invite_entries()
                entries = db.get_giveaway_entry_count(gid, uid)
                link = invite_link(bot, gid, uid)
                text += (f"\n\n{emojis.emoji('gift', '🎁')} <b>Want more entries?</b>\n"
                         f"Share your link — <b>+{n}</b> for every friend who joins!\n"
                         f"Your entries: <b>{entries}</b>")
                if link:
                    kb.add(types.InlineKeyboardButton("🔗 Invite Friends", url=link))
                leaders = db.get_giveaway_invite_leaderboard(gid, 5)
                if leaders:
                    names = []
                    for row in leaders:
                        try:
                            u = db.get_user(row["inviter_id"])
                            nm = "@" + u["username"] if u and u["username"] else f"user {row['inviter_id']}"
                        except Exception:
                            nm = f"user {row['inviter_id']}"
                        names.append((nm, row["c"]))
                    from msg_templates import giveaway_leaderboard
                    text += giveaway_leaderboard(names)
        else:
            kb.add(emojis.button("🎁 Join Giveaway", callback_data=f"gw_join_{gid}", emoji_id="5039823300683891773", lead="🎁"))
        kb.add(emojis.button("🏠 Main Menu", callback_data="main_menu", emoji_id="5042022053356504092", lead="🏠"))
        _edit_or_send(bot, uid, msg_id, text, kb)
        if call:
            bot.answer_callback_query(call.id)

    def _edit_or_send(bot, uid, msg_id, text, kb):
        try:
            bot.edit_message_text(text, uid, msg_id, reply_markup=kb)
        except Exception:
            bot.send_message(uid, text, reply_markup=kb)

    # ═══ USER: join flow ══════════════════════════════════════════════════════

    @bot.callback_query_handler(func=lambda c: c.data.startswith("gw_join_"))
    def cb_gw_join(call: types.CallbackQuery):
        uid = call.from_user.id
        gid = int(call.data[len("gw_join_"):])

        if not check_rate_limit(uid, "gw_join", min_seconds=3):
            bot.answer_callback_query(call.id, "Please wait a moment.", show_alert=True)
            return

        g = db.get_giveaway(gid)
        if not g or g["status"] != "active":
            bot.answer_callback_query(call.id, "This giveaway is not open for entries.", show_alert=True)
            return
        if _end_time_passed(g):
            bot.answer_callback_query(call.id, "This giveaway has ended.", show_alert=True)
            return

        # membership required to join (independent of the global gate)
        if not _gw_membership_check(bot, uid):
            bot.answer_callback_query(call.id)
            from utils import membership_required_msg
            membership_required_msg(bot, uid)
            return

        uname = call.from_user.username or ""
        db.ensure_user(uid, uname)
        added = db.add_giveaway_entry(gid, uid, uname)
        if not added:
            bot.answer_callback_query(call.id, "You've already joined this giveaway (one entry per user).", show_alert=True)
            _render_giveaway_view(bot, uid, call.message.message_id)
            return

        # Viral invite credit: if they arrived via a friend's link, the friend
        # earns bonus entries (only for genuinely new users — anti-abuse).
        try:
            pending = get_state(uid).get("pending_giveaway_invite")
            if pending and pending.get("gid") == gid and _invites_enabled():
                inviter = int(pending["inviter"])
                set_state(uid, pending_giveaway_invite=None)
                if inviter != uid and _is_fresh_user(uid):
                    if db.record_giveaway_invite(gid, inviter, uid):
                        n = _invite_entries()
                        total = db.add_giveaway_bonus_entries(gid, inviter, n)
                        if total:
                            try:
                                from msg_templates import giveaway_invite_earned
                                bot.send_message(
                                    inviter,
                                    giveaway_invite_earned(total, "@" + uname if uname else "A friend"),
                                    parse_mode="HTML",
                                )
                            except Exception:
                                pass
        except Exception:
            pass

        bot.answer_callback_query(call.id, "You're entered. Best of luck!", show_alert=True)
        _render_giveaway_view(bot, uid, call.message.message_id)

    # ═══ ADMIN: management home ═══════════════════════════════════════════════

    @bot.callback_query_handler(func=lambda c: c.data == "adm_gw")
    @admin_only
    def cb_adm_gw(call: types.CallbackQuery):
        _render_admin_home(bot, call.from_user.id, call.message.message_id)
        bot.answer_callback_query(call.id)

    def _render_admin_home(bot, uid, msg_id):
        gws = db.list_giveaways(limit=20)
        kb = types.InlineKeyboardMarkup(row_width=1)
        status_icon = {"active": f"{emojis.emoji('green_dot', '🟢')}", "drawn": f"{emojis.emoji('trophy', '🏆')}", "cancelled": f"{emojis.emoji('no_entry', '🚫')}"}
        for g in gws:
            icon = status_icon.get(g["status"], f"{emojis.emoji('question', '❓')}")
            kb.add(emojis.button(
                f"{icon} {g['title']} · {_product_name(g['product_id'])} · {db.count_entries(g['giveaway_id'])} joined",
                callback_data=f"adm_gw_view_{g['giveaway_id']}",
            ))
        kb.add(emojis.button("➕ New Giveaway", callback_data="adm_gw_create", emoji_id="5039891861246838069", lead="➕"))
        kb.add(
            emojis.button("⬅️ Back", callback_data="admin_panel", emoji_id="5255703720078879038", lead="⬅️"),
            emojis.button("🏠 Main Menu", callback_data="main_menu", emoji_id="5042022053356504092", lead="🏠"),
        )
        header = "<b>Giveaways</b>\n\nTap one to manage, or create a new one." if gws else \
                 "<b>Giveaways</b>\n\nNo giveaways yet. Create your first one!"
        try:
            bot.edit_message_text(header, uid, msg_id, reply_markup=kb)
        except Exception:
            bot.send_message(uid, header, reply_markup=kb)

    # ═══ ADMIN: giveaway detail / actions ═════════════════════════════════════

    @bot.callback_query_handler(func=lambda c: c.data.startswith("adm_gw_view_"))
    @admin_only
    def cb_adm_gw_view(call: types.CallbackQuery):
        gid = int(call.data[len("adm_gw_view_"):])
        _render_admin_detail(bot, call.from_user.id, call.message.message_id, gid)
        bot.answer_callback_query(call.id)

    def _render_admin_detail(bot, uid, msg_id, gid):
        g = db.get_giveaway(gid)
        if not g:
            bot.send_message(uid, "Giveaway not found.")
            return
        pname = _esc(_product_name(g["product_id"]))
        entrants = db.count_entries(gid)
        text = (
            f"<b>{_esc(g['title'])}</b>\n\n"
            f"<blockquote>Status: <b>{g['status'].capitalize()}</b>\n"
            f"Prize: <b>{pname}</b> (stock: {db.stock_count(g['product_id'])})\n"
            f"Winners: <b>{g['winner_slots']}</b>\n"
            f"Entrants: <b>{entrants}</b>\n"
            f"Ends: {fmt_datetime(g['end_time']) if g['end_time'] else 'no end time'}\n"
            f"Created: {fmt_datetime(g['created_at'])}</blockquote>"
        )
        kb = types.InlineKeyboardMarkup(row_width=2)
        if g["status"] == "active":
            kb.add(
                emojis.button("🎲 Draw Now", callback_data=f"adm_gw_draw_{gid}"),
                emojis.button("📢 Announce", callback_data=f"adm_gw_announce_{gid}"),
            )
            kb.add(emojis.button("🚫 Cancel Giveaway", callback_data=f"adm_gw_cancel_{gid}"))
        elif g["status"] == "drawn":
            winners = db.list_winners(gid)
            if winners:
                text += "\n\n<b>Winners:</b>"
                for w in winners:
                    mark = "" if w["delivered"] else "(not delivered)"
                    text += f"\n• {user_link(w['telegram_id'], w['username'])} {mark}"
        kb.add(
            emojis.button("⬅️ Back", callback_data="adm_gw", emoji_id="5255703720078879038", lead="⬅️"),
            emojis.button("🏠 Main Menu", callback_data="main_menu", emoji_id="5042022053356504092", lead="🏠"),
        )
        try:
            bot.edit_message_text(text, uid, msg_id, reply_markup=kb)
        except Exception:
            bot.send_message(uid, text, reply_markup=kb)

    # ═══ ADMIN: creation wizard ═══════════════════════════════════════════════

    @bot.callback_query_handler(func=lambda c: c.data == "adm_gw_create")
    @admin_only
    def cb_adm_gw_create(call: types.CallbackQuery):
        uid = call.from_user.id
        if db.get_active_giveaway():
            bot.answer_callback_query(call.id, "An active giveaway already exists. Draw or cancel it first.", show_alert=True)
            return
        prods = db.list_products(active_only=True)
        if not prods:
            bot.answer_callback_query(call.id, "No active products to give away.", show_alert=True)
            return
        kb = types.InlineKeyboardMarkup(row_width=1)
        _mode_tag = {"instant": f"{emojis.emoji('zap', '⚡')} instant", "preorder": f"{emojis.emoji('package', '📦')} pre-order"}
        for p in prods:
            _mode = db.get_fulfillment(p)
            _tag = _mode_tag.get(_mode, _mode)
            _stock = f" (stock: {db.stock_count(p['product_id'])})" if _mode == "instant" else ""
            kb.add(emojis.button(
                f"{p['name']} [{_tag}]{_stock}",
                callback_data=f"adm_gw_pick_{p['product_id']}",
            ))
        kb.add(emojis.button("⬅️ Back", callback_data="adm_gw", emoji_id="5255703720078879038", lead="⬅️"))
        bot.edit_message_text(
            "<b>New Giveaway</b> (1/3)\n\nChoose the prize product:",
            uid, call.message.message_id, reply_markup=kb,
        )
        bot.answer_callback_query(call.id)

    @bot.callback_query_handler(func=lambda c: c.data.startswith("adm_gw_pick_"))
    @admin_only
    def cb_adm_gw_pick(call: types.CallbackQuery):
        uid = call.from_user.id
        pid = int(call.data[len("adm_gw_pick_"):])
        prompt = bot.edit_message_text(
            "<b>New Giveaway</b> (2/3)\n\nHow many winners? Enter a number (1 or more):",
            uid, call.message.message_id, reply_markup=back_kb("adm_gw"),
        )
        set_state(uid, step="gw_create_slots", gw_pid=pid, prompt_msg_id=prompt.message_id)
        bot.answer_callback_query(call.id)

    @bot.message_handler(func=lambda m: get_state(m.from_user.id).get("step", "").startswith("gw_create_"))
    @admin_only
    def msg_gw_create(msg: types.Message):
        uid = msg.from_user.id
        st = get_state(uid)
        step = st.get("step")
        text = msg.text.strip()
        prompt_id = st.get("prompt_msg_id")
        silent_delete(bot, uid, msg.message_id)

        def _edit(new_text, **extra):
            try:
                m = bot.edit_message_text(new_text, uid, prompt_id, reply_markup=back_kb("adm_gw"))
                set_state(uid, prompt_msg_id=m.message_id, **extra)
            except Exception:
                m = bot.send_message(uid, new_text, reply_markup=back_kb("adm_gw"))
                set_state(uid, prompt_msg_id=m.message_id, **extra)

        if step == "gw_create_slots":
            clean = text.replace(",", "")
            if not clean.isdigit() or int(clean) < 1:
                _edit("Enter a whole number of 1 or greater:")
                return
            _edit("<b>New Giveaway</b> (3/3)\n\nEnter an end time like <code>2h</code>, <code>1d</code>, "
                  "<code>1d12h</code> \u2014 or send <code>-</code> for no end time:",
                  step="gw_create_end", gw_slots=int(clean))
        elif step == "gw_create_end":
            end_time = None
            if text.lower() not in ("-", "skip", "none", ""):
                end_time = _parse_end_time(text)
                if end_time is None:
                    _edit("Invalid. Examples: <code>2h</code>, <code>1d</code>, <code>1h30m</code>, or <code>-</code> to skip:")
                    return
            # final single-active guard — title/description are auto-written hype copy
            _prod = db.get_product(st["gw_pid"])
            _pname = _prod["name"] if _prod else "Prize"
            _mode = db.get_fulfillment(_prod) if _prod else "instant"
            _per_invite = 1
            try:
                _per_invite = max(1, int(db.cfg_get("giveaway_invite_entries", "1") or 1))
            except Exception:
                pass
            _auto_title, _auto_desc = _auto_giveaway_copy(_pname, st["gw_slots"], _per_invite, _mode)
            gid = db.create_giveaway(
                product_id=st["gw_pid"], title=_auto_title, description=_auto_desc,
                winner_slots=st["gw_slots"], end_time=end_time, created_by=uid,
            )
            clear_state(uid)
            if gid is None:
                try:
                    bot.edit_message_text("An active giveaway already exists.", uid, prompt_id,
                                          reply_markup=back_kb("adm_gw"))
                except Exception:
                    pass
                return
            if gid == -1:
                try:
                    bot.edit_message_text(
                        "<b>Not enough stock.</b>\n\nThis prize is an instant product — "
                        "the winner stock is set aside the moment the giveaway is created, "
                        "and there isn't enough unreserved stock to cover the winners. "
                        "Add more stock or reduce the winner count, then try again.",
                        uid, prompt_id, reply_markup=back_kb("adm_gw"))
                except Exception:
                    pass
                return
            try:
                _render_admin_detail(bot, uid, prompt_id, gid)
            except Exception:
                bot.send_message(uid, "Giveaway created.", reply_markup=back_kb("adm_gw"))

    # ═══ ADMIN: cancel ════════════════════════════════════════════════════════

    @bot.callback_query_handler(func=lambda c: c.data.startswith("adm_gw_cancel_") and not c.data.startswith("adm_gw_cancelgo_"))
    @admin_only
    def cb_adm_gw_cancel(call: types.CallbackQuery):
        gid = int(call.data[len("adm_gw_cancel_"):])
        kb = types.InlineKeyboardMarkup(row_width=2)
        kb.add(
            emojis.button("✅ Yes, cancel", callback_data=f"adm_gw_cancelgo_{gid}"),
            emojis.button("❌ No", callback_data=f"adm_gw_view_{gid}"),
        )
        bot.edit_message_text(
            "<b>Cancel this giveaway?</b>\n\nNo winners will be drawn. This cannot be undone.",
            call.from_user.id, call.message.message_id, reply_markup=kb,
        )
        bot.answer_callback_query(call.id)

    @bot.callback_query_handler(func=lambda c: c.data.startswith("adm_gw_cancelgo_"))
    @admin_only
    def cb_adm_gw_cancelgo(call: types.CallbackQuery):
        gid = int(call.data[len("adm_gw_cancelgo_"):])
        g = db.get_giveaway(gid)
        if not g or g["status"] != "active":
            bot.answer_callback_query(call.id, "Only active giveaways can be cancelled.", show_alert=True)
            _render_admin_detail(bot, call.from_user.id, call.message.message_id, gid)
            return
        db.set_giveaway_status(gid, "cancelled", cancelled_at=_utcnow_naive().isoformat())
        # Hand the protected prize stock back to the shop.
        try:
            db.release_reservations(f"giveaway_{gid}")
        except Exception:
            pass
        bot.answer_callback_query(call.id, "Cancelled.")
        _render_admin_detail(bot, call.from_user.id, call.message.message_id, gid)

    # ═══ ADMIN: announce ══════════════════════════════════════════════════════

    @bot.callback_query_handler(func=lambda c: c.data.startswith("adm_gw_announce_"))
    @admin_only
    def cb_adm_gw_announce(call: types.CallbackQuery):
        gid = int(call.data[len("adm_gw_announce_"):])
        g = db.get_giveaway(gid)
        if not g or g["status"] != "active":
            bot.answer_callback_query(call.id, "Only active giveaways can be announced.", show_alert=True)
            return
        bot.answer_callback_query(call.id, "Announcing to all users...")
        threading.Thread(target=_announce_giveaway, args=(bot, gid, call.from_user.id), daemon=True).start()

    def _announce_giveaway(bot, gid, admin_uid):
        g = db.get_giveaway(gid)
        if not g:
            return
        pname = _esc(_product_name(g["product_id"]))
        text = (
            f"<b>{emojis.emoji('gift', '🎁')} GIVEAWAY!</b>\n\n"
            f"<b>{_esc(g['title'])}</b>\n\n"
            f"<blockquote>{_esc(g['description'])}\n\n"
            f"Prize: <b>{pname}</b>\n"
            f"Winners: <b>{g['winner_slots']}</b>\n"
            f"Ends in: <b>{_time_left(g)}</b></blockquote>\n\n"
            f"Entry is free — tap Join below to take part! {emojis.emoji('clover', '🍀')}"
        )
        kb = types.InlineKeyboardMarkup(row_width=1)
        kb.add(emojis.button("🎁 Join Giveaway", callback_data=f"gw_join_{gid}", emoji_id="5039823300683891773", lead="🎁"))
        kb.add(emojis.button("✖️ Dismiss", callback_data="dismiss_msg"))

        users = db.list_users(limit=100000)
        sent = failed = 0
        for u in users:
            if u["notify_opt_out"]:
                continue
            delivered = False
            for _ in range(3):
                try:
                    bot.send_message(u["telegram_id"], text, reply_markup=kb)
                    sent += 1
                    delivered = True
                    break
                except Exception as e:
                    err = str(e)
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
            time.sleep(0.05)
        try:
            bot.send_message(admin_uid, f"Giveaway announced.\nSent: {sent} | Failed: {failed}")
        except Exception:
            pass
        # Mirror the announcement to the public log channel (Unicode fallbacks —
        # custom emoji don't render in channel posts).
        try:
            log_to_channel(
                bot,
                f"{emojis.emoji('gift', '🎁')} <b>GIVEAWAY!</b>\n\n"
                f"<blockquote>{_esc(g['title'])}\n"
                f"{emojis.emoji('package', '📦')} Prize: <b>{pname}</b>\n"
                f"{emojis.emoji('user', '👤')} Winners: <b>{g['winner_slots']}</b>\n"
                f"{emojis.emoji('clock', '⏰')} Ends in: <b>{_time_left(g)}</b></blockquote>\n\n"
                f"Entry is free — open the shop and tap Join to take part. {emojis.emoji('clover', '🍀')}",
            )
        except Exception:
            pass

    # ═══ ADMIN: draw ══════════════════════════════════════════════════════════

    @bot.callback_query_handler(func=lambda c: c.data.startswith("adm_gw_draw_") and not c.data.startswith("adm_gw_drawgo_"))
    @admin_only
    def cb_adm_gw_draw(call: types.CallbackQuery):
        gid = int(call.data[len("adm_gw_draw_"):])
        g = db.get_giveaway(gid)
        if not g or g["status"] != "active":
            bot.answer_callback_query(call.id, "This giveaway can't be drawn.", show_alert=True)
            return
        entrants = db.count_entries(gid)
        kb = types.InlineKeyboardMarkup(row_width=2)
        kb.add(
            emojis.button("🎲 Yes, draw now", callback_data=f"adm_gw_drawgo_{gid}"),
            emojis.button("❌ No", callback_data=f"adm_gw_view_{gid}"),
        )
        bot.edit_message_text(
            f"<b>Draw winners now?</b>\n\n"
            f"Entrants: <b>{entrants}</b>\nWinners to pick: <b>{min(entrants, g['winner_slots'])}</b>\n"
            f"Prize stock: <b>{db.stock_count(g['product_id'])}</b>\n\n"
            f"This ends the giveaway.",
            call.from_user.id, call.message.message_id, reply_markup=kb,
        )
        bot.answer_callback_query(call.id)

    @bot.callback_query_handler(func=lambda c: c.data.startswith("adm_gw_drawgo_"))
    @admin_only
    def cb_adm_gw_drawgo(call: types.CallbackQuery):
        gid = int(call.data[len("adm_gw_drawgo_"):])
        bot.answer_callback_query(call.id, "Drawing...")
        result = run_draw(bot, gid)
        if result.get("error"):
            bot.answer_callback_query(call.id, result["error"], show_alert=True)
            _render_admin_detail(bot, call.from_user.id, call.message.message_id, gid)
            return
        summary = (
            f"<b>Draw Complete</b>\n\n"
            f"Entrants: <b>{result['entrants']}</b>\n"
            f"Winners: <b>{result['selected']}</b>\n"
            f"Delivered: <b>{result['delivered']}</b>"
        )
        if result["shortfall"]:
            summary += f"\nNot delivered (out of stock): <b>{result['shortfall']}</b>"
        if result["selected"] == 0:
            summary = "<b>Draw Complete</b>\n\nThere were no entrants."
        kb = types.InlineKeyboardMarkup(row_width=1)
        kb.add(emojis.button("👀 View Giveaway", callback_data=f"adm_gw_view_{gid}"))
        kb.add(emojis.button("🎁 Giveaways", callback_data="adm_gw", emoji_id="5039823300683891773", lead="🎁"))
        try:
            bot.edit_message_text(summary, call.from_user.id, call.message.message_id, reply_markup=kb)
        except Exception:
            bot.send_message(call.from_user.id, summary, reply_markup=kb)

    # draw engine + notifications live at module level (run_draw / _notify_winner / _post_winners)
