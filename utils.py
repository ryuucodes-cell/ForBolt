"""Shared utilities: formatting, keyboards, per-user state, admin notify."""

from functools import wraps
import logging
import re as _re
import html as _html
import telebot
from telebot import types
import db
from msg_templates import public_vouch_sale, public_vouch_topup_approved, public_vouch_topup_rejected, public_vouch_balance_change

_log = logging.getLogger("stockbot")

# ── formatting ────────────────────────────────────────────────────────────────

def fmt_mmk(amount) -> str:
    """Primary money display. Canonical stored unit is whole MMK (kyats)."""
    return f"{int(amount):,} MMK"

def parse_mmk(text):
    """Parse an MMK string ('12000', '12,000', '12000 MMK', '12000 Ks') into a
    whole-kyat integer, or None. Kyats have no subunit, so decimals are rounded."""
    import math
    t = (str(text).strip().upper()
         .replace("MMK", "").replace("KS", "").replace(",", "").strip())
    try:
        val = float(t)
    except (ValueError, TypeError):
        return None
    # reject inf/-inf/nan before round() (round(inf) raises OverflowError)
    if not math.isfinite(val):
        return None
    try:
        amount = round(val)
    except (ValueError, OverflowError):
        return None
    return amount if amount > 0 else None

def fmt_usd(amount) -> str:
    """Back-compat alias — renders MMK (canonical stored unit is whole kyats).
    Kept so any call site not individually migrated still shows the correct
    currency instead of a bogus dollar amount."""
    return fmt_mmk(amount)

def parse_usd(text):
    """Back-compat alias — parses to whole kyats (canonical stored unit)."""
    return parse_mmk(text)

def mmk_str(mmk) -> str:
    """Format a raw MMK amount for local-payment 'send X MMK' instructions."""
    return f"{int(mmk):,} MMK"

def fmt_usdt(amount) -> str:
    """Display a USDT amount, e.g. '2.08 USDT'."""
    return f"{float(amount):,.2f} USDT"

# ── credential display ─────────────────────────────────────────────────────────

_B32_RE = _re.compile(r"^[A-Z2-7]{16,64}$")  # base32 = Google Authenticator setup key

def _cred_esc(s) -> str:
    return _html.escape(str(s))

def _is_url(s: str) -> bool:
    s = s.strip().lower()
    return s.startswith("http://") or s.startswith("https://")

def _is_2fa_secret(s: str) -> bool:
    t = s.strip().replace(" ", "").replace("-", "").upper()
    return bool(_B32_RE.match(t))

# Field-name tokens an admin can use in a product's credential_format string.
# Maps a token -> (emoji, human label). The bot labels each stored field by
# these, so receipts always read correctly.
_FIELD_TOKENS = {
    # John's 7 credential formats (v2.1) — trimmed to exactly what the shop uses.
    "email":           ("", "Email"),
    "username":        ("", "Username"),
    "password":        ("", "Password"),
    "otp-link":        ("", "OTP Link"),
    "2fa":             ("", "2FA Setup Key"),
    "cdk":             ("", "CDK"),
    "activation-link": ("", "Activation Link"),
    "activationlink":  ("", "Activation Link"),
}

_TWO_FA_HINT = ("<blockquote>Add this key to Google Authenticator "
                "(＋ → <b>Enter a setup key</b>) to set up 2FA and generate your login codes. "
                "It's a setup key, not a one-time code.</blockquote>")
_ACTIVATION_HINT = "<blockquote>Open the link to activate your account.</blockquote>"

def _render_cred_field(value: str, token) -> str:
    v = value.strip()
    # A URL is always shown as a tappable link, whatever the format says —
    # this keeps mixed stock (some items a link, some a key) correct.
    if _is_url(v):
        label = "Activation Link"
        if token in _FIELD_TOKENS and _FIELD_TOKENS[token][1] in ("Activation Link", "OTP Link"):
            label = _FIELD_TOKENS[token][1]
        return (f"{label}: <a href=\"{_cred_esc(v)}\">Tap here to open</a>\n{_ACTIVATION_HINT}")

    if token in _FIELD_TOKENS:
        _emoji, label = _FIELD_TOKENS[token]
        if label == "2FA Setup Key":
            return f"{label}: <code>{_cred_esc(v)}</code>\n{_TWO_FA_HINT}"
        if label == "Note":
            return f"{label}: {_cred_esc(v)}"
        return f"{label}: <code>{_cred_esc(v)}</code>"

    # No/unknown token → detect by content.
    if _is_2fa_secret(v):
        return f"2FA Setup Key: <code>{_cred_esc(v)}</code>\n{_TWO_FA_HINT}"
    return f"Key: <code>{_cred_esc(v)}</code>"

def format_credential(cred: str, fmt: str = "") -> str:
    """Render a stored credential for display, using the product's credential
    format (a '|'-separated list of field names) to label each field, with
    content-based detection as a fallback.

    Examples of fmt: 'email|password', 'email|password|2fa',
    'email|password|recovery', 'code|pin', 'key'.
    """
    parts  = str(cred).split("|")
    tokens = [t.strip().lower() for t in str(fmt).split("|")] if fmt else []

    # single value, no format hint → a bare link (activation) or a key
    if len(parts) == 1 and not tokens:
        v = parts[0].strip()
        if _is_url(v):
            return (f"Activation Link: <a href=\"{_cred_esc(v)}\">Tap here to open</a>\n"
                    f"{_ACTIVATION_HINT}")
        return f"<code>{_cred_esc(v)}</code>"

    lines = []
    for i, part in enumerate(parts):
        if not part.strip():
            continue
        token = tokens[i] if i < len(tokens) else None
        # If no format was provided, infer the common email/username + password shape.
        if token is None and not tokens:
            if i == 0:
                token = "email" if "@" in part else "username"
            elif i == 1:
                token = "password"
        lines.append(_render_cred_field(part, token))
    return "\n".join(lines)

def format_credential_block(cred: str, fmt: str = "") -> str:
    """Format a credential for display, one credential per line (bulk pre-orders).
    Each line is rendered with format_credential() using the product's format."""
    lines = [ln.strip() for ln in str(cred).split("\n") if ln.strip()]
    return "\n\n".join(format_credential(ln, fmt) for ln in lines)

# ── config shortcuts ──────────────────────────────────────────────────────────

def admin_id() -> int:
    """Primary admin — receives auto-backups and is the default single-target admin."""
    v = db.cfg_get("admin_chat_id", "0")
    return int(v) if v and str(v).lstrip("-").isdigit() else 0

def admin_ids() -> list[int]:
    """All admins: the primary admin_chat_id plus any extra IDs in the admin_ids config.

    admin_ids config is a comma/space separated list of Telegram numeric IDs.
    """
    ids = []
    primary = admin_id()
    if primary:
        ids.append(primary)
    extra = db.cfg_get("admin_ids", "") or ""
    for part in extra.replace(",", " ").split():
        p = part.strip()
        if p.lstrip("-").isdigit():
            v = int(p)
            if v not in ids:
                ids.append(v)
    return ids

def is_admin(uid: int) -> bool:
    return uid in admin_ids()

def is_reseller(uid: int) -> bool:
    return db.is_reseller(uid)

# ── premium (custom) emoji ──────────────────────────────────────────────────

_TG_EMOJI_RE = _re.compile(r'<tg-emoji\s+emoji-id="[^"]*">(.*?)</tg-emoji>', _re.DOTALL)

def tg_emoji(fallback: str, emoji_id=None) -> str:
    """Build a premium custom-emoji span for HTML messages.

    `fallback` is a normal Unicode emoji shown if the custom one can't render.
    Returns just the fallback when no id is given, so callers can pass an
    optional id unconditionally. Custom emoji work ONLY in message text/captions,
    never in inline-button labels (Telegram Bot API limitation).
    """
    eid = str(emoji_id).strip() if emoji_id not in (None, "", 0, "0") else ""
    if not eid.isdigit():
        return fallback
    return f'<tg-emoji emoji-id="{eid}">{fallback}</tg-emoji>'

def strip_tg_emoji(text: str) -> str:
    """Replace every <tg-emoji …>X</tg-emoji> with its plain fallback X.

    Used by the global safe-send net so a message with an emoji the bot isn't
    entitled to still goes out (as plain emoji) instead of failing entirely.
    """
    if not text or "<tg-emoji" not in text:
        return text
    return _TG_EMOJI_RE.sub(r"\1", text)

def maintenance() -> bool:
    return db.cfg_get("maintenance_mode", "0") == "1"

# ── membership gate ───────────────────────────────────────────────────────────

def check_membership(bot: telebot.TeleBot, uid: int) -> bool:
    """Returns True if gate is off or user is a member of ALL configured channels.

    Fail-closed: if Telegram won't tell us the membership status (bot not admin,
    network error, ...), we block instead of waving the user through — the old
    "except: return True" was a bypass hole.
    """
    if db.cfg_get("channel_gate_enabled", "0") != "1":
        return True
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
            err = str(e).lower()
            if "chat not found" in err:
                # Misconfigured username — skip it rather than blocking everyone.
                _log.warning("check_membership: channel @%s not found, skipping", username)
                continue
            _log.warning("check_membership: cannot verify @%s for uid %s: %s",
                         username, uid, e)
            return False
    return True

def membership_required_msg(bot: telebot.TeleBot, uid: int):
    """Send the 'join channel(s) first' message with join buttons."""
    import emojis  # lazy — avoids emojis<->utils circular import
    channels = []
    for ukey, lkey in (("channel_username", "channel_url"),
                       ("channel_username2", "channel_url2")):
        user = db.cfg_get(ukey, "").strip().lstrip("@")
        url = db.cfg_get(lkey, "").strip()
        if user or url:
            channels.append((user, url))
    kb = types.InlineKeyboardMarkup()
    for i, (user, url) in enumerate(channels, 1):
        if url:
            label = f"Join Channel {i}" if len(channels) > 1 else "Join Channel"
            kb.add(emojis.button(label, url=url,
                                 emoji_id="5461151367559141950", lead="📢"))
    kb.add(emojis.button("I've Joined", callback_data="check_membership",
                         emoji_id="5039844895779455925", lead="✅"))
    if channels:
        names = ", ".join(f"@{u}" if u else "our channel" for u, _ in channels)
        display = names
    else:
        display = "our channel"
    bot.send_message(
        uid,
        f"<b>Almost There</b>\n\n"
        f"Please join {display} to start shopping — that's where we post updates and new arrivals.\n\n"
        f"Select <b>Join</b> below, then <b>I've Joined</b>.",
        reply_markup=kb,
    )

# ── log to channel ────────────────────────────────────────────────────────────

def log_to_channel(bot: telebot.TeleBot, text: str):
    """Send a log message to the configured log channel.

    Still silent toward users, but failures are now written to the bot logs
    (docker logs stockbot) so a broken channel link can't go unnoticed.
    """
    cid = db.cfg_get("log_channel_id", "").strip()
    if not cid:
        _log.warning("log_to_channel: skipped — log_channel_id is not configured")
        return
    try:
        bot.send_message(cid, text, parse_mode="HTML")
    except Exception as e:
        _log.error("log_to_channel: failed to post to channel %r: %s", cid, e)

# ── log formatting helpers ────────────────────────────────────────────────────

def _rate() -> float:
    """MMK per 1 USDT. Stored under the legacy key `usd_to_mmk_rate` (kept to
    avoid another config migration); semantically it's the MMK/USDT rate."""
    try:
        return float(db.cfg_get("usd_to_mmk_rate", "4800") or 4800)
    except Exception:
        return 4800.0

def usd_str(amount) -> str:
    """Back-compat alias — renders MMK (canonical stored unit is native MMK)."""
    return fmt_mmk(amount)

def fmt_money(amount) -> str:
    """Back-compat alias — renders MMK (canonical stored unit is native MMK)."""
    return fmt_mmk(amount)

def usdt_to_send(mmk) -> float:
    """USDT amount a Binance user should send for an MMK top-up: mmk / rate.
    Rounded to 2 decimals for display (USDT is shown to 2dp in Binance)."""
    rate = _rate()
    if rate <= 0:
        rate = 4800.0
    return round(int(mmk) / rate, 2)

def mmk_from_usdt(usdt) -> int:
    """MMK to credit for a received USDT amount: usdt * rate, whole kyats.
    Guards the rate (same as usdt_to_send) so a corrupted/zero config rate can
    never credit 0 or negative MMK for a real incoming USDT payment."""
    rate = _rate()
    if rate <= 0:
        rate = 4800.0
    return int(round(float(usdt) * rate))

def user_link(uid, username: str = None) -> str:
    """Tappable user mention for PRIVATE admin logs/alerts."""
    uname = (username or "").lstrip("@")
    if uname:
        return f"<a href=\"tg://user?id={uid}\">@{uname}</a> (<code>{uid}</code>)"
    return f"<a href=\"tg://user?id={uid}\"><code>{uid}</code></a>"

def mask_name(username: str = None) -> str:
    """Masked, non-clickable name for PUBLIC posts, e.g. 'ryuu' -> 'ry***'.

    No '@' prefix, so Telegram can't turn it into a tappable mention.
    """
    uname = (username or "").strip().lstrip("@")
    if not uname:
        return "a customer"
    visible = uname[:2] if len(uname) >= 2 else uname[:1]
    return f"{visible}***"

def mask_email(email: str = None) -> str:
    """Masked email for log posts, e.g. 'customer@gmail.com' -> 'cu***@gmail.com'.

    Same idea as mask_name: enough for the owner to identify the record,
    not enough for anyone else to contact the customer.
    """
    email = (email or "").strip().lower()
    if "@" not in email:
        return mask_name(email)
    local, domain = email.split("@", 1)
    visible = local[:2] if len(local) >= 2 else local[:1]
    return f"{visible}***@{domain}"

def ts() -> str:
    """Current server timestamp, e.g. '03 Jul 2026 · 14:32'."""
    from datetime import datetime
    return datetime.now().strftime("%d %b %Y · %H:%M")

def fmt_date(s) -> str:
    """Format a stored timestamp as a clean date, e.g. '03 Jul 2026'."""
    from datetime import datetime
    if not s:
        return "—"
    try:
        return datetime.fromisoformat(str(s)).strftime("%d %b %Y")
    except Exception:
        return str(s)[:10]

def fmt_datetime(s) -> str:
    """Format a stored timestamp with time, e.g. '03 Jul 2026 · 14:32'."""
    from datetime import datetime
    if not s:
        return "—"
    try:
        return datetime.fromisoformat(str(s)).strftime("%d %b %Y · %H:%M")
    except Exception:
        return str(s)[:16]

def _format_event(title: str, rows, icon: str = "", note: str = None) -> str:
    """Build a consistently-formatted event block. rows: (emoji, label, value)
    tuples — the emoji slot is legacy and ignored when empty.

    Layout uses a <blockquote> card instead of ASCII divider lines, which
    wrap badly on Android.
    """
    head = f"{icon} <b>{title}</b>" if icon else f"<b>{title}</b>"
    lines = [head, "", "<blockquote>"]
    for emoji, label, value in rows:
        prefix = f"{emoji} " if emoji else ""
        lines.append(f"{prefix}<b>{label}:</b> {value}")
    lines.append("</blockquote>")
    if note:
        lines.append("")
        lines.append(note)
    lines.append("")
    lines.append(f"<i>{ts()}</i>")
    return "\n".join(lines)

def admin_report(bot: telebot.TeleBot, title: str, rows, icon: str = "", note: str = None):
    """Send a detailed, private event report to all admins (never the public channel).

    Fully defensive: a reporting failure must never break the calling action
    (deposit approval, refund, balance adjustment, etc.).
    """
    try:
        notify_admin(bot, _format_event(title, rows, icon, note))
    except Exception:
        pass

def log_vouch(bot: telebot.TeleBot, product_name: str, qty: int, amount, username: str = None):
    """Post a privacy-safe sale vouch to the PUBLIC log channel.

    Shows a masked, non-clickable buyer name (e.g. 'ry***'); never the full username,
    ID, or balance. Pass amount=None to hide the amount line (reseller sales —
    keeps wholesale pricing off the public channel).
    """
    log_to_channel(bot, public_vouch_sale(
        buyer=mask_name(username),
        product=product_name,
        qty=qty,
        amount=fmt_money(amount) if amount is not None else None,
    ))

def log_topup(bot: telebot.TeleBot, amount_mmk, method_label: str, approved: bool, username: str = None):
    """Public top-up notice with a masked, non-clickable name."""
    if approved:
        text = public_vouch_topup_approved(
            buyer=mask_name(username),
            amount=fmt_money(amount_mmk),
            method=method_label
        )
    else:
        text = public_vouch_topup_rejected(
            buyer=mask_name(username),
            amount=fmt_money(amount_mmk),
            method=method_label
        )
    log_to_channel(bot, text)

def log_balance_change(bot: telebot.TeleBot, amount_mmk, added: bool, username: str = None):
    """Public balance adjustment notice with a masked, non-clickable name."""
    log_to_channel(bot, public_vouch_balance_change(
        buyer=mask_name(username),
        amount=fmt_money(amount_mmk),
        action="added" if added else "removed"
    ))

# ── message cleanup ───────────────────────────────────────────────────────────

def silent_delete(bot: telebot.TeleBot, chat_id: int, message_id: int):
    """Delete a message, ignoring errors (already deleted, no permission, etc)."""
    try:
        bot.delete_message(chat_id, message_id)
    except Exception:
        pass


def edit_or_send(bot: telebot.TeleBot, uid: int, msg_id, text: str, reply_markup=None):
    """Edit a prompt message in place (dynamic UI); fall back to sending a new
    message if the edit fails. Returns the message id now in use."""
    try:
        bot.edit_message_text(text, uid, msg_id, reply_markup=reply_markup)
        return msg_id
    except Exception:
        sent = bot.send_message(uid, text, reply_markup=reply_markup)
        return sent.message_id



# ── rate limiting ─────────────────────────────────────────────────────────────

import time as _time

_rate_limits: dict[str, dict[int, float]] = {}
_rate_limit_calls = 0

def check_rate_limit(uid: int, action: str, min_seconds: int = 5) -> bool:
    """Returns True if allowed, False if rate limited."""
    global _rate_limit_calls
    now    = _time.time()
    bucket = _rate_limits.setdefault(action, {})
    last   = bucket.get(uid, 0)
    if now - last < min_seconds:
        return False
    bucket[uid] = now

    # periodic cleanup: every 500 calls, prune entries older than 1 hour
    # (iterate over snapshots — other handler threads write to these
    # dicts concurrently, and a live view can raise mid-iteration)
    _rate_limit_calls += 1
    if _rate_limit_calls >= 500:
        _rate_limit_calls = 0
        cutoff = now - 3600
        for b in list(_rate_limits.values()):
            stale = [k for k, v in list(b.items()) if v < cutoff]
            for k in stale:
                b.pop(k, None)

    return True

def set_state(uid: int, **kwargs):
    current = db.state_get(uid)
    current.update(kwargs)
    db.state_set(uid, current)

def get_state(uid: int) -> dict:
    return db.state_get(uid)

def clear_state(uid: int):
    db.state_clear(uid)

# ── keyboards ─────────────────────────────────────────────────────────────────

def main_menu_kb(uid: int) -> types.InlineKeyboardMarkup:
    import emojis  # lazy import — emojis imports utils, so keep this out of module scope
    B = emojis.button
    kb = types.InlineKeyboardMarkup(row_width=2)
    # shopping
    kb.add(
        B("Products", callback_data="shop_categories", emoji_id="5312361253610475399", lead="⚡"),
        B("Wallet",   callback_data="wallet_menu",     emoji_id="5445353829304387411", lead="💎"),
    )
    # account
    kb.add(
        B("Profile",          callback_data="my_profile", emoji_id="5381970617505428041", lead="👤"),
        B("Purchase History", callback_data="my_orders",  emoji_id="5444856076954520455", lead="🧾"),
    )
    kb.add(
        B("My Deposits", callback_data="my_deposits", emoji_id="5444856076954520455", lead="📋"),
        B("Giveaway",    callback_data="gw_menu",     emoji_id="5039823300683891773", lead="🎁"),
    )
    kb.add(B("Lucky Draw", callback_data="ld_list", emoji_id="5260547274957672345", lead="🎲"))
    kb.add(B("Activate CDK", callback_data="cdk_menu", emoji_id="5042303713016808496", lead="🎮"))
    kb.add(B("Activate AlightMotion", callback_data="am_menu", emoji_id="5041866884778034602", lead="🎬"))
    # reseller entry: hub for active resellers, activation prompt for everyone else
    if is_reseller(uid):
        kb.add(B("Reseller Hub", callback_data="reseller_hub", emoji_id="5039834781131474002", lead="👑"))
    else:
        kb.add(B("Become a Reseller", callback_data="reseller_info", emoji_id="5039834781131474002", lead="🔑"))
    # support + notifications toggle share a row
    alerts_btn = (
        B("Alerts: Off", callback_data="notif_optin", emoji_id="5210952531676504517", lead="🔕")
        if db.get_notify_opt_out(uid)
        else B("Alerts: On", callback_data="notif_optout", emoji_id="5039844895779455925", lead="🔔")
    )
    kb.add(B("Support", callback_data="support", emoji_id="5039532814865794117", lead="🆘"), alerts_btn)
    if is_admin(uid):
        kb.add(B("Admin Panel", callback_data="admin_panel", emoji_id="5217822164362739968", lead="⚙️"))
    return kb

def back_kb(target: str) -> types.InlineKeyboardMarkup:
    """Back button + Home button. target='main_menu' shows only Home to avoid duplicate."""
    import emojis  # lazy — avoids emojis<->utils circular import
    kb = types.InlineKeyboardMarkup(row_width=2)
    if target == "main_menu":
        kb.add(emojis.button("Main Menu", callback_data="main_menu", emoji_id="5042022053356504092", lead="🏠"))
    else:
        kb.add(
            emojis.button("Back", callback_data=target, emoji_id="5255703720078879038", lead="⬅️"),
            emojis.button("Main Menu", callback_data="main_menu", emoji_id="5042022053356504092", lead="🏠"),
        )
    return kb

# ── decorators ────────────────────────────────────────────────────────────────

_bot_instance = None

# Substrings that mean "this custom emoji isn't allowed for this bot". When we see
# one, we retry the same call with all <tg-emoji> tags stripped to plain fallbacks.
_EMOJI_ERR_HINTS = (
    "custom_emoji", "custom emoji", "emoji_invalid", "emoji is not allowed",
    "button_icon", "icon_custom_emoji",
    # entity-level errors: our only injected entities are <tg-emoji> spans, so a
    # bad mapping should degrade to plain text rather than break the whole screen.
    "message_entities_invalid", "entities_invalid", "can't parse entities",
    "message entities",
)


def _strip_button_icons(markup):
    """Remove icon_custom_emoji_id from every button in an inline keyboard, so a
    send rejected over a bad/unauthorized icon can be retried as plain buttons.
    Mutates in place; never raises."""
    try:
        for row in (getattr(markup, "keyboard", None) or []):
            for b in row:
                if getattr(b, "icon_custom_emoji_id", None):
                    b.icon_custom_emoji_id = None
    except Exception:
        pass

def _is_emoji_error(exc) -> bool:
    s = str(exc).lower()
    return any(h in s for h in _EMOJI_ERR_HINTS)

def _install_emoji_safety_net(bot):
    """Wrap the bot's text-sending methods to preserve custom emoji tags.

    This ensures premium emoji in blockquotes/messages work correctly.
    Your <tg-emoji> tags pass through untouched.
    """
    def _pm_is_html(kwargs) -> bool:
        pm = kwargs.get("parse_mode", getattr(bot, "parse_mode", None))
        return str(pm).upper() == "HTML"

    for name in ("send_message", "edit_message_text", "send_photo", "edit_message_caption"):
        orig = getattr(bot, name, None)
        if orig is None or getattr(orig, "_emoji_wrapped", False):
            continue

        def make(orig_fn, fname):
            @wraps(orig_fn)
            def wrapper(*args, **kwargs):
                # NO auto-conversion! Your custom emoji tags pass through as-is.
                # Only the error-recovery fallback is active (strips broken emoji tags).
                try:
                    return orig_fn(*args, **kwargs)
                except Exception as e:
                    if not _is_emoji_error(e):
                        raise
                    # retry with premium button icons removed from the keyboard
                    if kwargs.get("reply_markup") is not None:
                        _strip_button_icons(kwargs["reply_markup"])
                    return orig_fn(*args, **kwargs)
            wrapper._emoji_wrapped = True
            return wrapper

        setattr(bot, name, make(orig, name))

def set_bot_instance(bot):
    global _bot_instance
    _bot_instance = bot
    try:
        _install_emoji_safety_net(bot)
    except Exception:
        pass  # never let the safety-net install block startup

def admin_only(fn):
    @wraps(fn)
    def wrapper(arg, *args, **kwargs):
        uid = arg.from_user.id if hasattr(arg, "from_user") else arg.message.from_user.id
        if not is_admin(uid):
            # answer callback queries so Telegram spinner stops immediately
            if _bot_instance and hasattr(arg, "id") and hasattr(arg, "data"):
                try:
                    _bot_instance.answer_callback_query(arg.id)
                except Exception:
                    pass
            return
        return fn(arg, *args, **kwargs)
    return wrapper

def notify_admin(bot: telebot.TeleBot, text: str, **kwargs):
    """Send an alert to every configured admin so any of them can act."""
    kwargs.setdefault("parse_mode", "HTML")
    for aid in admin_ids():
        try:
            bot.send_message(aid, text, **kwargs)
        except Exception:
            pass
