"""
Ryuu — Message Templates
────────────────────────────────────────────────────
Warm, friendly, simple English everyone can read.
Layout: bold title + <blockquote> info card. No ASCII
divider lines — they wrap badly on Android.
Premium emoji untouched.
"""

from emojis import emoji


# ── Deposit Messages ───────────────────────────────────────────────────────────

def deposit_approved(amount_mmk: str, new_balance: str, bonus: str = None) -> str:
    """Message sent to user when deposit is approved."""
    bonus_line = f"\n{emoji('gift')} <b>Bonus:</b> +{bonus}" if bonus else ""
    return (
        f"{emoji('success')} <b>Deposit Approved</b>\n\n"
        f"<blockquote>{emoji('money')} <b>Amount:</b> {amount_mmk}{bonus_line}\n"
        f"{emoji('balance')} <b>New balance:</b> {new_balance}</blockquote>\n\n"
        f"Your balance is topped up. Happy shopping! {emoji('special')}"
    )


def deposit_rejected(method: str, amount_mmk: str, deposit_id: int, reason: str = None) -> str:
    """Message sent to user when deposit is rejected."""
    msg = (
        f"{emoji('rejected')} <b>Deposit Not Approved</b>\n\n"
        f"<blockquote>{emoji('bank')} <b>Method:</b> {method}\n"
        f"{emoji('money')} <b>Amount:</b> {amount_mmk}\n"
        f"{emoji('id')} <b>Deposit ID:</b> #{deposit_id}"
    )
    if reason:
        msg += f"\n{emoji('message')} <b>Reason:</b> {reason}"
    msg += (
        f"</blockquote>\n\n"
        f"Need help? {emoji('support')} Just contact support and we'll sort it out."
    )
    return msg


def deposit_pending_exists(deposit_id: int, amount_mmk: str, method: str) -> str:
    """Message shown when user tries to submit another deposit while one is pending."""
    return (
        f"{emoji('pending')} <b>Deposit In Progress</b>\n\n"
        f"<blockquote>{emoji('id')} <b>Deposit #{deposit_id}</b>\n"
        f"{emoji('money')} <b>Amount:</b> {amount_mmk}\n"
        f"{emoji('bank')} <b>Method:</b> {method}</blockquote>\n\n"
        f"{emoji('waiting')} We're checking it right now.\n\n"
        f"Please wait until it's done before sending another one — "
        f"it helps us approve yours faster."
    )


# ── Admin Deposit Reports ──────────────────────────────────────────────────────

def admin_deposit_approved_report(user_link: str, method: str, amount: str, deposit_id: int, approved_by: str) -> str:
    """Private admin report when deposit is approved."""
    return (
        f"{emoji('success')} <b>DEPOSIT APPROVED</b>\n\n"
        f"<blockquote>{emoji('user')} <b>User:</b> {user_link}\n"
        f"{emoji('bank')} <b>Method:</b> {method}\n"
        f"{emoji('money')} <b>Amount:</b> {amount}\n"
        f"{emoji('id')} <b>Deposit:</b> #{deposit_id}\n"
        f"{emoji('badge')} <b>Approved by:</b> {approved_by}</blockquote>"
    )


def admin_deposit_rejected_report(user_link: str, method: str, amount: str, deposit_id: int, rejected_by: str, reason: str = None) -> str:
    """Private admin report when deposit is rejected."""
    msg = (
        f"{emoji('rejected')} <b>DEPOSIT NOT APPROVED</b>\n\n"
        f"<blockquote>{emoji('user')} <b>User:</b> {user_link}\n"
        f"{emoji('bank')} <b>Method:</b> {method}\n"
        f"{emoji('money')} <b>Amount:</b> {amount}\n"
        f"{emoji('id')} <b>Deposit:</b> #{deposit_id}\n"
        f"{emoji('badge')} <b>Reviewed by:</b> {rejected_by}"
    )
    if reason:
        msg += f"\n{emoji('message')} <b>Reason:</b> {reason}"
    msg += "</blockquote>"
    return msg


# ── Order & Purchase Messages ──────────────────────────────────────────────────

def purchase_complete(product_name: str, qty: int, unit_price: str, total: str, new_balance: str, order_ids: str, credentials: str) -> str:
    """Receipt message after successful purchase."""
    return (
        f"{emoji('success')} <b>Thank You For Your Order!</b>\n\n"
        f"<blockquote>{emoji('package')} <b>Product:</b> {product_name}\n"
        f"{emoji('cart')} <b>Quantity:</b> {qty}\n"
        f"{emoji('price')} <b>Unit price:</b> {unit_price}\n"
        f"{emoji('money')} <b>Total paid:</b> {total}\n"
        f"{emoji('balance')} <b>New balance:</b> {new_balance}\n"
        f"{emoji('list')} <b>Order IDs:</b> {order_ids}</blockquote>\n\n"
        f"{emoji('download')} <b>Here are your items:</b>\n\n"
        f"{credentials}\n\n"
        f"{emoji('save')} Save these somewhere safe — you can find them again in My Orders.\n"
        f"{emoji('warning')} Something wrong with an item? Let support know."
    )


def purchase_insufficient_balance(need: str, have: str, short: str) -> str:
    """Alert when user has insufficient balance."""
    return (
        f"{emoji('warning')} <b>Not Enough Balance</b>\n\n"
        f"<blockquote>{emoji('money')} <b>Needed:</b> {need}\n"
        f"{emoji('balance')} <b>You have:</b> {have}\n"
        f"{emoji('spending')} <b>Missing:</b> {short}</blockquote>\n\n"
        f"{emoji('bank')} Top up a little more and you're good to go."
    )


def purchase_out_of_stock(product_name: str) -> str:
    """Alert when product is out of stock."""
    return (
        f"{emoji('warning')} <b>Sold Out</b>\n\n"
        f"{emoji('package')} <b>{product_name}</b> is out of stock right now.\n\n"
        f"{emoji('notification')} We're adding more soon — come back later!"
    )


# ── Balance Adjustment Messages ────────────────────────────────────────────────

def balance_added_user(amount: str, new_balance: str, note: str = None) -> str:
    """Message to user when admin adds balance."""
    msg = (
        f"{emoji('success')} <b>Balance Added</b>\n\n"
        f"<blockquote>{emoji('money')} <b>Added:</b> {amount}\n"
        f"{emoji('balance')} <b>New balance:</b> {new_balance}"
    )
    if note:
        msg += f"\n{emoji('message')} <b>Note:</b> {note}"
    msg += "</blockquote>"
    return msg


def balance_removed_user(amount: str, new_balance: str, note: str = None) -> str:
    """Message to user when admin removes balance."""
    msg = (
        f"{emoji('warning')} <b>Balance Updated</b>\n\n"
        f"<blockquote>{emoji('spending')} <b>Removed:</b> {amount}\n"
        f"{emoji('balance')} <b>New balance:</b> {new_balance}"
    )
    if note:
        msg += f"\n{emoji('message')} <b>Note:</b> {note}"
    msg += "</blockquote>"
    return msg


def admin_balance_change_report(user_link: str, amount: str, new_balance: str, adjusted_by: str, action: str) -> str:
    """Admin report for balance adjustments."""
    action_text = "BALANCE ADDED" if action == "added" else "BALANCE REMOVED"
    action_emoji = emoji('success') if action == "added" else emoji('spending')

    return (
        f"{action_emoji} <b>{action_text}</b>\n\n"
        f"<blockquote>{emoji('user')} <b>User:</b> {user_link}\n"
        f"{emoji('money')} <b>Amount:</b> {amount}\n"
        f"{emoji('balance')} <b>New balance:</b> {new_balance}\n"
        f"{emoji('badge')} <b>Admin:</b> {adjusted_by}</blockquote>"
    )


# ── Stock Management Messages ──────────────────────────────────────────────────

def stock_added_confirmation(product_name: str, qty: int, total_stock: int) -> str:
    """Confirmation message after admin adds stock."""
    return (
        f"{emoji('success')} <b>Stock Added</b>\n\n"
        f"<blockquote>{emoji('package')} <b>Product:</b> {product_name}\n"
        f"{emoji('add')} <b>Added:</b> {qty} items\n"
        f"{emoji('inventory')} <b>Total in stock:</b> {total_stock}</blockquote>\n\n"
        f"{emoji('online')} Ready to sell!"
    )


def low_stock_alert(product_name: str, remaining: int) -> str:
    """Alert to admin when stock is low."""
    return (
        f"{emoji('warning')} <b>Low Stock</b>\n\n"
        f"<blockquote>{emoji('package')} <b>Product:</b> {product_name}\n"
        f"{emoji('inventory')} <b>Left:</b> {remaining} items</blockquote>\n\n"
        f"{emoji('notification')} Time to add more soon."
    )


def out_of_stock_alert(product_name: str, product_id: int, user_id: int = None) -> str:
    """Alert to admin when product goes out of stock."""
    msg = (
        f"{emoji('alert')} <b>Out Of Stock</b>\n\n"
        f"<blockquote>{emoji('package')} <b>Product:</b> {product_name}\n"
        f"{emoji('id')} <b>Product ID:</b> {product_id}"
    )
    if user_id:
        msg += f"\n{emoji('user')} <b>Requested by:</b> user {user_id}"
    msg += f"</blockquote>\n\n{emoji('add')} Please add more stock."
    return msg


def stock_announcement(product_name: str, emoji_html: str, added: int, total: int, product_id: int) -> str:
    """Public announcement when new stock is added."""
    return (
        f"{emoji('package')} <b>Back In Stock!</b>\n\n"
        f"<blockquote>{emoji_html} <b>{product_name}</b>\n"
        f"{emoji('add')} <b>Added:</b> {added} items\n"
        f"{emoji('inventory')} <b>In stock:</b> {total} items</blockquote>\n\n"
        f"{emoji('cart')} Come and get yours!"
    )


def product_announcement(product_name: str, emoji_html: str, mode: str) -> str:
    """Public announcement when a new pre-order product is added."""
    title = f"{emoji('package')} <b>New Pre-Order Product Available Now!</b>"
    how = (f"{emoji('clock')} Pay now — we'll send your login details "
           f"as soon as they're ready.")
    return (
        f"{title}\n\n"
        f"<blockquote>{emoji_html} <b>{product_name}</b>\n"
        f"{how}</blockquote>\n\n"
        f"{emoji('cart')} Come and get yours!"
    )


# ── Reseller Messages ──────────────────────────────────────────────────────────

def reseller_approved(activation_key: str) -> str:
    """Message when reseller request is approved."""
    return (
        f"{emoji('success')} <b>You're A Reseller Now!</b>\n\n"
        f"{emoji('key')} <b>Your activation key:</b>\n"
        f"<code>{activation_key}</code> <i>(tap to copy)</i>\n\n"
        f"{emoji('info')} Tap <b>Activate Now</b> below and paste your key.\n\n"
        f"{emoji('premium')} You now get special reseller prices. Enjoy!"
    )


def reseller_rejected() -> str:
    """Message when reseller request is rejected."""
    return (
        f"{emoji('rejected')} <b>Reseller Request Not Approved</b>\n\n"
        f"{emoji('info')} Sorry, we couldn't approve it this time.\n\n"
        f"{emoji('cart')} You can still shop as usual — and try again later."
    )


def reseller_revoked(reason: str = "inactivity") -> str:
    """Message when reseller access is revoked."""
    return (
        f"{emoji('alert')} <b>Reseller Access Removed</b>\n\n"
        f"{emoji('info')} Your reseller access was removed ({reason}).\n\n"
        f"{emoji('cart')} You can still shop normally. Questions? Just ask support."
    )


def reseller_approved_report(user_link, key, approved_by, expiry=None):
    """Admin report when reseller is approved."""
    msg = (
        f"{emoji('success')} <b>RESELLER APPROVED</b>\n\n"
        f"<blockquote>{emoji('user')} <b>User:</b> {user_link}\n"
        f"{emoji('key')} <b>Key:</b> <code>{key}</code>\n"
        f"{emoji('badge')} <b>Approved by:</b> {approved_by}"
    )
    if expiry:
        msg += f"\n{emoji('calendar')} <b>Expires:</b> {expiry}"
    msg += "</blockquote>"
    return msg


def giveaway_winner(product_name: str, credentials: str) -> str:
    """Message to giveaway winner."""
    return (
        f"{emoji('gift')} {emoji('success')} <b>You Won!</b> {emoji('success')} {emoji('gift')}\n\n"
        f"{emoji('medal')} <b>Congratulations!</b>\n"
        f"{emoji('package')} <b>Prize:</b> {product_name}\n\n"
        f"{emoji('download')} Your prize is in My Orders.\n"
        f"{emoji('special')} Enjoy!"
    )


# ── Giveaway Messages ──────────────────────────────────────────────────────────

def giveaway_announcement(product_name: str, entries: int, draw_time: str) -> str:
    """Public giveaway announcement."""
    return (
        f"{emoji('gift')} <b>Giveaway Time!</b> {emoji('gift')}\n\n"
        f"<blockquote>{emoji('package')} <b>Prize:</b> {product_name}\n"
        f"{emoji('users')} <b>Entries:</b> {entries}\n"
        f"{emoji('calendar')} <b>Draw:</b> {draw_time}</blockquote>\n\n"
        f"{emoji('special')} Good luck everyone!\n"
        f"{emoji('star')} Winner announced soon."
    )


def giveaway_winner_notification(product_name: str) -> str:
    """Message to giveaway winner."""
    return (
        f"{emoji('gift')} {emoji('success')} <b>You Won!</b> {emoji('success')} {emoji('gift')}\n\n"
        f"<blockquote>{emoji('medal')} <b>Congratulations!</b>\n"
        f"{emoji('package')} <b>Prize:</b> {product_name}</blockquote>\n\n"
        f"{emoji('download')} Your prize is waiting in My Orders.\n"
        f"{emoji('special')} Enjoy!"
    )


# ── Payment Instruction Messages ───────────────────────────────────────────────

def payment_instructions(method: str, account_info: str, amount: str, instructions: str = None) -> str:
    """Generic payment instruction template."""
    msg = (
        f"{emoji('bank')} <b>Pay with {method}</b>\n\n"
        f"{emoji('payment')} <b>Send the money to:</b>\n"
        f"<code>{account_info}</code>\n\n"
        f"{emoji('money')} <b>Amount:</b> {amount}\n"
    )
    if instructions:
        msg += f"\n{emoji('important')} <b>Please note:</b>\n{instructions}\n"
    msg += (
        f"\n{emoji('upload')} After you pay, send your screenshot here.\n"
        f"{emoji('pending')} We'll check it and add the money to your balance soon."
    )
    return msg


# ── Error & System Messages ────────────────────────────────────────────────────

def maintenance_mode() -> str:
    """Maintenance mode message."""
    return (
        f"{emoji('tools')} <b>We're Taking A Short Break</b>\n\n"
        f"{emoji('settings')} Making things better for you.\n\n"
        f"{emoji('clock')} Back very soon — thanks for waiting! {emoji('notification')}"
    )


def membership_required(channel_url: str, channel_name: str) -> str:
    """Message when user needs to join channel."""
    return (
        f"{emoji('lock')} <b>One Quick Step</b>\n\n"
        f"{emoji('announcement')} Please join our channel to start shopping:\n\n"
        f"{emoji('pin')} <b>{channel_name}</b>\n"
        f"{channel_url}\n\n"
        f"{emoji('important')} Then tap <b>I've Joined</b> below."
    )


def rate_limit_exceeded(action: str, wait_seconds: int) -> str:
    """Rate limit message."""
    return (
        f"{emoji('warning')} <b>Slow Down A Little</b>\n\n"
        f"{emoji('timer')} That was a bit too fast.\n"
        f"{emoji('clock')} Please wait {wait_seconds} seconds and try again."
    )


# ── Public Log Channel Messages (Masked Names) ─────────────────────────────────
# NOTE: custom emoji never render in channel posts (Telegram restriction),
# so every emoji() call here passes a simple Unicode fallback.

def public_vouch_sale(buyer: str, product: str, qty: int, amount: str = None) -> str:
    """Public sale announcement for log channel.

    amount=None hides the amount line — used for reseller purchases so
    wholesale pricing isn't leaked to retail viewers of the public channel.

    Uses plain unicode emoji only — channel posts can't render premium emoji.
    """
    from utils import ts
    lines = [
        f"{emoji('success', '✅')} <b>New Sale! {emoji('party', '🎉')}</b>",
        "",
        f"<blockquote>{emoji('profile', '👤')} <b>Buyer:</b> {buyer}",
        f"{emoji('package', '📦')} <b>Product:</b> {product} × {qty}",
    ]
    if amount is not None:
        lines.append(f"{emoji('money', '💰')} <b>Amount:</b> {amount}")
    lines += [
        f"{emoji('stopwatch', '🕐')} {ts()}</blockquote>",
        "",
        "✨ Thanks for shopping with us!",
    ]
    return "\n".join(lines)


def public_vouch_topup_approved(buyer: str, amount: str, method: str) -> str:
    """Public top-up approval announcement for log channel (unicode only)."""
    from utils import ts
    return (
        f"{emoji('success', '✅')} <b>Top-Up Done! {emoji('money', '💰')}</b>\n\n"
        f"<blockquote>{emoji('profile', '👤')} {buyer}\n"
        f"{emoji('money', '💰')} <b>Amount:</b> {amount}\n"
        f"{emoji('bank')} <b>Method:</b> {method}\n"
        f"{emoji('stopwatch', '🕐')} {ts()}</blockquote>\n\n"
        f"{emoji('money', '💰')} Balance ready — happy shopping!"
    )


def public_vouch_topup_rejected(buyer: str, amount: str, method: str) -> str:
    """Public top-up rejection announcement for log channel (unicode only)."""
    from utils import ts
    return (
        f"{emoji('rejected', '❌')} <b>Top-Up Not Approved</b>\n\n"
        f"<blockquote>{emoji('profile', '👤')} {buyer}\n"
        f"{emoji('money', '💰')} <b>Amount:</b> {amount}\n"
        f"{emoji('bank')} <b>Method:</b> {method}\n"
        f"{emoji('stopwatch', '🕐')} {ts()}</blockquote>"
    )


def public_vouch_balance_change(buyer: str, amount: str, action: str) -> str:
    """Public balance adjustment for log channel (unicode only)."""
    from utils import ts
    title = f"Balance Added {emoji('party', '🎉')}" if action == "added" else "Balance Removed"
    title_emoji = f"{emoji('success', '✅')}" if action == "added" else f"{emoji('money_wings', '💸')}"

    return (
        f"{title_emoji} <b>{title}</b>\n\n"
        f"<blockquote>{emoji('profile', '👤')} {buyer}\n"
        f"{emoji('money', '💰')} <b>Amount:</b> {amount}\n"
        f"{emoji('stopwatch', '🕐')} {ts()}</blockquote>"
    )


def public_vouch_refund(buyer: str, amount: str, order_id: int) -> str:
    """Public refund announcement for log channel (masked buyer name, unicode only)."""
    from utils import ts
    return (
        f"{emoji('money_wings', '💸')} <b>Refund Done</b>\n\n"
        f"<blockquote>{emoji('profile', '👤')} {buyer}\n"
        f"{emoji('package', '📦')} <b>Order:</b> #{order_id}\n"
        f"{emoji('money', '💰')} <b>Amount:</b> {amount}\n"
        f"{emoji('stopwatch', '🕐')} {ts()}</blockquote>"
    )


# ── Admin Action Confirmations ─────────────────────────────────────────────────

def admin_action_success(action: str, details: str = None) -> str:
    """Generic admin success message."""
    msg = f"{emoji('success')} <b>{action} — done!</b>"
    if details:
        msg += f"\n\n<blockquote>{details}</blockquote>"
    return msg


def admin_action_failed(action: str, reason: str) -> str:
    """Generic admin failure message."""
    return (
        f"{emoji('failed')} <b>{action} didn't work</b>\n\n"
        f"<blockquote>{emoji('warning')} <b>Why:</b> {reason}</blockquote>"
    )


# ── Discount Announcement ───────────────────────────────────────────────────────

def discount_announcement(product_name: str, emoji_html: str, original_price: str,
                         discounted_price: str, discount_pct: int, ends_in: str) -> str:
    """Discount announcement broadcast to all users."""
    return (
        f"{emoji('premium')} <b>Special Offer! 🏷️</b>\n\n"
        f"<blockquote>{emoji_html} <b>{product_name}</b>\n\n"
        f"{emoji('price')} <s>{original_price}</s> → <b>{discounted_price}</b>\n"
        f"{emoji('badge')} <b>{discount_pct}% OFF</b>\n"
        f"{emoji('clock')} <b>Ends in:</b> {ends_in}</blockquote>\n\n"
        f"{emoji('fast')} Only for a short time — don't miss it!"
    )


# ── Fulfillment v2: pre-order ─────────────────────────────────────────────────

def preorder_confirm_text(product_name: str, qty: int, amount: str) -> str:
    """Confirm screen for a pre-order (bulk allowed)."""
    return (
        f"{emoji('badge')} <b>Confirm Pre-order</b>\n\n"
        f"Product: <b>{product_name}</b>\n"
        f"Quantity: <b>{qty}</b>\n"
        f"Total: <b>{amount}</b>\n\n"
        f"Our team will prepare your order and deliver it right here when it's ready."
    )


def preorder_received() -> str:
    """After the customer places a pre-order."""
    return (
        f"{emoji('success')} <b>Got it!</b>\n\n"
        f"We're on it now — we'll notify you the moment your order is ready."
    )


def preorder_cancelled() -> str:
    """Customer cancelled a pending pre-order."""
    return (
        f"{emoji('info')} <b>Cancelled</b>\n\n"
        f"Your order was cancelled and the amount is back in your wallet."
    )


def preorder_delivered(product_name: str, credential_html: str) -> str:
    """Deliver an admin-supplied credential for a pre-order.
    credential_html must already be formatted (format_credential_block)."""
    return (
        f"{emoji('success')} <b>Your {product_name} is ready!</b>\n\n"
        f"<blockquote>{credential_html}</blockquote>"
    )


def fulfillment_status_line(status: str) -> str:
    """Human-readable status for manual/pre-order orders."""
    return {
        "awaiting_info": f"{emoji('fulfill_waiting', '⏳')} <i>Waiting for your info</i>",
        "awaiting_fulfillment": f"{emoji('fulfill_working', '🔧')} <i>We're working on it</i>",
        "fulfilled": f"{emoji('fulfill_done', '✅')} <i>Done</i>",
        "refunded": f"{emoji('fulfill_refunded', '↩️')} <i>Refunded</i>",
    }.get(status, f"<i>{status}</i>")


def admin_fulfill_new(product_name: str, mode_label: str, buyer: str,
                      order_id: int, amount: str, info: dict) -> str:
    """Admin alert: new pre-order needs work."""
    lines = [
        f"{emoji('alert')} <b>New {mode_label} order #{order_id}</b>",
        "",
        f"Product: <b>{product_name}</b>",
        f"Buyer: {buyer}",
        f"Amount: <b>{amount}</b>",
    ]
    if info:
        lines.append("")
        lines.append("<b>Customer info:</b>")
        for k, v in info.items():
            lines.append(f"• <b>{k}:</b> <code>{v}</code>")
    else:
        lines.append("")
        lines.append("<i>No customer info needed for this order.</i>")
    return "\n".join(lines)


# ── CDK Activation ─────────────────────────────────────────────────────────────

def _cdk_step2_line(provider: str) -> str:
    """Step-2 instruction line for the credential each provider needs."""
    if provider == "claude":
        return (
            f"{emoji('id')} <b>Step 2:</b> Send your Claude Organization ID — "
            f"you'll find it at claude.ai under Settings, then Account."
        )
    if provider in ("grok1m", "grok3m"):
        return (
            f"{emoji('id')} <b>Step 2:</b> Send your Grok user ID or session JSON — "
            f"open grok.com/api/auth/session in your browser and copy it."
        )
    if provider == "perplexity":
        return (
            f"{emoji('download')} <b>Step 2:</b> Send your session JSON — "
            f"paste the text here or attach the file."
        )
    return (
        f"{emoji('download')} <b>Step 2:</b> Send your session.json — open "
        f"chatgpt.com/api/auth/session in your browser, save it, then send "
        f"the file here or paste the text."
    )


def cdk_menu() -> str:
    """CDK menu: pick the product the key is for."""
    return (
        f"{emoji('key')} <b>Activate CDK</b>\n\n"
        f"Got a CDK key? Pick the product it's for and we'll activate it "
        f"on your account — right here, no waiting.\n\n"
        f"<blockquote>{emoji('info')} Your key and account info are only used "
        f"for this activation.</blockquote>"
    )


def cdk_tutorial(name: str, icon_html: str, provider: str, error: str = None) -> str:
    """Product tutorial card + Step 1 (send the key). Error text must be pre-escaped."""
    msg = ""
    if error:
        msg += f"{emoji('failed')} {error}\n\n"
    msg += (
        f"{icon_html} <b>{name}</b>\n\n"
        f"Here's how it works:\n\n"
        f"<blockquote>{emoji('key')} <b>Step 1:</b> Send your CDK license key.\n"
        f"{_cdk_step2_line(provider)}</blockquote>\n\n"
        f"{emoji('lock')} Only share these with the bot in this chat — "
        f"never forward them to anyone.\n\n"
        f"{emoji('point_down', '👇')} <b>Step 1:</b> Send your CDK key now."
    )
    return msg


def cdk_checking(name: str) -> str:
    """Brief state while the key is being verified."""
    return (
        f"{emoji('pending')} <b>Checking your key…</b>\n\n"
        f"Give us a moment."
    )


def cdk_ask_credential(name: str, provider: str, error: str = None) -> str:
    """Step 2 prompt. Error text must be pre-escaped."""
    msg = ""
    if error:
        msg += f"{emoji('failed')} {error}\n\n"
    msg += (
        f"{emoji('success')} <b>Key accepted!</b>\n\n"
        f"{_cdk_step2_line(provider)}\n\n"
        f"You can paste the text or attach the file.\n\n"
        f"{emoji('lock')} Only share this with the bot in this chat — "
        f"never forward it to anyone."
    )
    return msg


def cdk_activating(name: str) -> str:
    """Progress state while the gateway activates (runs in a background thread)."""
    return (
        f"{emoji('zap', '⚡')} <b>Activating your {name}…</b>\n\n"
        f"We're connecting to the activation server now. This can take up "
        f"to a minute — please wait, don't close this chat."
    )


def cdk_success(name: str, detail: str, ref: str = None) -> str:
    """Activation succeeded. Detail text must be pre-escaped."""
    ref_line = f"\n{emoji('receipt', '🧾')} <b>Order ref:</b> <code>{ref}</code>" if ref else ""
    return (
        f"{emoji('party', '🎉')} <b>{name} Activated!</b>\n\n"
        f"<blockquote>{detail}{ref_line}</blockquote>\n\n"
        f"Your subscription is now live on your account. Enjoy!"
    )


def cdk_failed(name: str, reason: str) -> str:
    """Activation failed. Reason text must be pre-escaped."""
    return (
        f"{emoji('failed')} <b>Activation Didn't Complete</b>\n\n"
        f"<blockquote>{reason}</blockquote>\n\n"
        f"Your key was not used up. Check the details and try again — "
        f"or contact support and we'll help you out."
    )


# ── growth: gifts ─────────────────────────────────────────────────────────────

def gift_created(product_name: str, gift_link: str, expiry_days: int) -> str:
    return (
        f"{emoji('gift', '🎁')} <b>Gift Ready!</b>\n\n"
        f"<blockquote>{emoji('package', '📦')} <b>Product:</b> {product_name}\n"
        f"{emoji('cdk_binding', '🔗')} <b>Gift link:</b> {gift_link}\n"
        f"{emoji('pending', '⏳')} <b>Expires in:</b> {expiry_days} days</blockquote>\n\n"
        f"Send the link to your friend — they'll get the product the moment they open it."
    )


def gift_received(product_name: str, sender_name: str) -> str:
    return (
        f"{emoji('gift', '🎁')} <b>You Got a Gift!</b>\n\n"
        f"<blockquote>{emoji('package', '📦')} <b>Product:</b> {product_name}\n"
        f"{emoji('love_letter', '💌')} <b>From:</b> {sender_name}</blockquote>\n\n"
        f"Tap below to claim it — it's yours."
    )


def gift_claim_invalid(reason: str) -> str:
    return (
        f"{emoji('failed')} <b>Can't Claim This Gift</b>\n\n"
        f"<blockquote>{reason}</blockquote>"
    )


def gift_claimed_notice(product_name: str, claimer: str) -> str:
    """DM to the sender when their gift is claimed."""
    return (
        f"{emoji('gift', '🎁')} <b>Gift Claimed!</b>\n\n"
        f"<blockquote>{emoji('package', '📦')} <b>Product:</b> {product_name}\n"
        f"{emoji('raising_hand', '🙋')} <b>Claimed by:</b> {claimer}</blockquote>\n\n"
        f"Your friend got their gift. Nice one!"
    )


# ── growth: viral giveaways ───────────────────────────────────────────────────

def giveaway_invite_block(entries: int, invite_link: str, per_invite: int) -> str:
    return (
        f"\n\n{emoji('gift', '🎁')} <b>Want more entries?</b>\n"
        f"Share your invite link — +{per_invite} entry for every friend who joins!\n"
        f"{emoji('cdk_binding', '🔗')} <code>{invite_link}</code>\n"
        f"Your entries: <b>{entries}</b>"
    )


def giveaway_invite_earned(new_total: int, invitee: str) -> str:
    return (
        f"{emoji('party', '🎉')} <b>+1 Giveaway Entry!</b>\n\n"
        f"{invitee} joined through your invite.\n"
        f"You now have <b>{new_total}</b> entries. Keep sharing!"
    )


def giveaway_leaderboard(rows) -> str:
    """rows: list of (display_name, invite_count)."""
    if not rows:
        return ""
    lines = [f"{i}. {name} — {c} invite{'s' if c != 1 else ''}" for i, (name, c) in enumerate(rows, 1)]
    return f"\n\n{emoji('trophy', '🏆')} <b>Top inviters</b>\n" + "\n".join(lines)


# ── growth: VIP ───────────────────────────────────────────────────────────────

def vip_tier_up(new_tier: str, discount: int) -> str:
    perk = f" You now get <b>{discount}% off</b> everything!" if discount else " Enjoy your new status!"
    return (
        f"{emoji('vip', '👑')} <b>You're now {new_tier} VIP!</b>\n\n"
        f"Thanks for shopping with us — your loyalty just paid off.{perk}"
    )


# ── CDK status/history (ported from Shop_CDK_V1) ──────────────────────────


def _cdk_short_dt(s) -> str:
    """Short datetime for CDK history, e.g. '17 Oct 2026 · 14:32'."""
    if not s:
        return "—"
    try:
        from datetime import datetime
        return datetime.fromisoformat(str(s)).strftime("%d %b %Y · %H:%M")
    except Exception:
        return str(s)[:16]


def cdk_history_detail(r, masked_key: str, safe_msg: str = None, masked_ref: str = None) -> str:
    """Activation detail view. Message text must be pre-escaped by the caller."""
    icon, label = cdk_status_display(r["status"])
    ref = masked_ref or r["provider_ref"] or "—"
    msg = safe_msg if safe_msg is not None else r["message"] or "—"
    dur = f"{r['duration_ms'] / 1000:.0f}s" if r["duration_ms"] else "—"
    extra = ""
    if (r["status"] or "").lower() == "interrupted":
        extra = "\n<i>The bot restarted while this was running — the result is unknown. Check Status or try again.</i>"
    return (
        f"{icon} <b>{r['product_name']}</b> — {label}\n\n"
        f"<blockquote>{emoji('key', '🔑')} <b>Key:</b> <code>{masked_key}</code>\n"
        f"{emoji('receipt', '🧾')} <b>Order ref:</b> <code>{ref}</code>\n"
        f"{emoji('speech', '💬')} <b>Details:</b> {msg}\n"
        f"{emoji('stopwatch', '⏱')} <b>Duration:</b> {dur}\n"
        f"{emoji('stopwatch', '🕐')} <b>Time:</b> {_cdk_short_dt(r['created_at'])}</blockquote>{extra}"
    )


def cdk_history_list(records) -> str:
    """My Activations list. Records are sqlite rows."""
    title = f"{emoji('cdk_history', '📋')} <b>My Activations</b>\n\n"
    if not records:
        return (
            f"{title}"
            f"You haven't activated anything yet.\n\n"
            f"Pick a product from the CDK menu to start."
        )
    lines = []
    for r in records:
        icon, label = cdk_status_display(r["status"])
        lines.append(f"{icon} <b>{r['product_name']}</b> — {label}\n"
                     f"<i>{_cdk_short_dt(r['created_at'])}</i>")
    return f"{title}Tap one to see the details.\n\n" + "\n\n".join(lines)


def cdk_pending(name: str, detail: str, ref: str = None) -> str:
    """Honest 'we sent it but can't confirm yet' card — never fake success."""
    ref_line = f"\n{emoji('receipt', '🧾')} <b>Order ref:</b> <code>{ref}</code>" if ref else ""
    return (
        f"{emoji('pending', '⏳')} <b>{name} — Still Processing</b>\n\n"
        f"<blockquote>{detail}{ref_line}</blockquote>\n\n"
        f"The provider is taking longer than usual. Nothing is lost — "
        f"check back in My Activations or Check Status for the final result."
    )


def cdk_stage(name: str, provider: str, stage: str) -> str:
    """Honest in-progress line — each stage fires at a real HTTP call."""
    if stage == "binding":
        line = f"{emoji('cdk_binding', '🔗')} <b>Connecting your Grok account…</b>"
    elif stage == "starting":
        line = f"{emoji('cdk_starting', '🚀')} <b>Starting the activation…</b>"
    elif stage == "waiting":
        if provider == "grok1m":
            line = f"{emoji('pending', '⏳')} <b>Checking its progress…</b>"
        else:
            line = f"{emoji('pending', '⏳')} <b>Waiting for the provider to confirm…</b>"
    else:  # sending
        line = f"{emoji('cdk_sending', '📤')} <b>Sending your key to the {name} activation server…</b>"
    return (
        f"{line}\n\n"
        f"<i>This can take up to a minute — please wait, don't close this chat.</i>"
    )


def cdk_status_ask(name: str, icon_html: str, error: str = None) -> str:
    msg = ""
    if error:
        msg += f"{emoji('failed')} {error}\n\n"
    msg += (
        f"{icon_html} <b>{name}</b>\n\n"
        f"Send the license key you want to check:"
    )
    return msg


def cdk_status_display(status: str):
    """Public helper: (icon_html, short label) for an activation status."""
    s = (status or "").lower()
    if s == "success":
        return emoji("success", "✅"), "Activated"
    if s == "failed":
        return emoji("failed", "❌"), "Failed"
    if s == "pending":
        return emoji("pending", "⏳"), "Finalizing…"
    if s == "interrupted":
        return emoji("failed", "❌"), "Interrupted"
    return emoji("pending", "⏳"), "Activating…"


def cdk_status_menu() -> str:
    return (
        f"{emoji('cdk_check_status', '🔍')} <b>Check Status</b>\n\n"
        f"Pick the product your key is for, then send the key — "
        f"we'll ask the provider for its live status."
    )


def cdk_status_result(name: str, icon_html: str, masked_key: str, ok: bool,
                      status: str, message: str, ref: str) -> str:
    """Live lookup result. Status/message text must be pre-escaped."""
    if not ok:
        return (
            f"{icon_html} <b>{name}</b>\n\n"
            f"{emoji('failed')} <b>Couldn't Check Status</b>\n\n"
            f"<blockquote>{message}</blockquote>\n\n"
            f"If the key was activated in this bot, check My Activations for the saved result."
        )
    return (
        f"{icon_html} <b>{name}</b>\n\n"
        f"{emoji('key', '🔑')} <b>Key:</b> <code>{masked_key}</code>\n\n"
        f"<blockquote>{emoji('chart', '📊')} <b>Status:</b> {status}\n"
        f"{emoji('speech', '💬')} <b>Message:</b> {message}\n"
        f"{emoji('receipt', '🧾')} <b>Order ref:</b> <code>{ref or '—'}</code></blockquote>"
    )


def log_cdk_failed(name: str, masked_key: str, user_html: str, reason: str, dur: str) -> str:
    """Reason text must be pre-escaped."""
    return (
        f"{emoji('rejected', '❌')} <b>CDK Activation Failed</b>\n\n"
        f"<blockquote>{emoji('package', '📦')} <b>Product:</b> {name}\n"
        f"{emoji('profile', '👤')} <b>User:</b> {user_html}\n"
        f"{emoji('key', '🔑')} <b>Key:</b> <code>{masked_key}</code>\n"
        f"{emoji('speech', '💬')} <b>Reason:</b> {reason}\n"
        f"{emoji('stopwatch', '⏱')} <b>Duration:</b> {dur}</blockquote>"
    )


def log_cdk_pending(name: str, masked_key: str, user_html: str, ref: str, dur: str) -> str:
    # No provider ref? Drop the line entirely — a bare "—" looks broken.
    ref_line = (f"{emoji('receipt', '🧾')} <b>Ref:</b> <code>{ref}</code>\n"
                if ref and ref != "—" else "")
    return (
        f"{emoji('pending', '⏳')} <b>CDK Activation Pending</b>\n\n"
        f"<blockquote>{emoji('package', '📦')} <b>Product:</b> {name}\n"
        f"{emoji('profile', '👤')} <b>User:</b> {user_html}\n"
        f"{emoji('key', '🔑')} <b>Key:</b> <code>{masked_key}</code>\n"
        f"{ref_line}"
        f"{emoji('stopwatch', '⏱')} <b>Duration:</b> {dur}\n\n"
        f"The key was sent to the provider but no final confirmation came back "
        f"in time. Check My Activations / Check Status for updates.</blockquote>"
    )


# ── growth: gifts ─────────────────────────────────────────────────────────────


def log_cdk_success(name: str, masked_key: str, user_html: str, ref: str, dur: str) -> str:
    # No provider ref? Drop the line entirely — a bare "—" looks broken.
    ref_line = (f"{emoji('receipt', '🧾')} <b>Ref:</b> <code>{ref}</code>\n"
                if ref and ref != "—" else "")
    return (
        f"{emoji('success', '✅')} <b>CDK Activated</b>\n\n"
        f"<blockquote>{emoji('package', '📦')} <b>Product:</b> {name}\n"
        f"{emoji('profile', '👤')} <b>User:</b> {user_html}\n"
        f"{emoji('key', '🔑')} <b>Key:</b> <code>{masked_key}</code>\n"
        f"{ref_line}"
        f"{emoji('stopwatch', '⏱')} <b>Duration:</b> {dur}</blockquote>"
    )


def cdk_gateway_down(name: str) -> str:
    """Log-channel alert: a provider gateway stopped answering (plain Unicode)."""
    return (
        f"🔴 <b>CDK Gateway Down</b>\n\n"
        f"<blockquote>{emoji('package', '📦')} <b>Product:</b> {name}\n"
        f"The activation server isn't answering. Customer activations for this "
        f"product will fail until it's back — check the provider.</blockquote>"
    )


def cdk_gateway_recovered(name: str) -> str:
    """Log-channel alert: a provider gateway is answering again (plain Unicode)."""
    return (
        f"{emoji('green_dot', '🟢')} <b>CDK Gateway Recovered</b>\n\n"
        f"<blockquote>{emoji('package', '📦')} <b>Product:</b> {name}\n"
        f"The activation server is answering again.</blockquote>"
    )


# ── CDK log-channel posts (plain Unicode — channels can't render custom emoji) ──


def log_am_subscribed(buyer: str, amount: str) -> str:
    """buyer = mask_name(username), same as the other log posts — masked and
    non-clickable so nobody can steal customers from the log."""
    return (
        f"{emoji('crown', '👑')} <b>AM Pro Subscribed</b>\n\n"
        f"<blockquote>{emoji('profile', '👤')} <b>Buyer:</b> {buyer}\n"
        f"{emoji('money', '💰')} <b>Paid:</b> {amount}\n"
        f"{emoji('stopwatch', '⏱')} <b>Duration:</b> 30 days</blockquote>"
    )


def log_am_activated(buyer: str, email: str) -> str:
    """buyer = mask_name(username), email = mask_email(address) — same
    privacy pattern as the other log posts."""
    return (
        f"{emoji('am_menu')} <b>AM Pro Activated</b>\n\n"
        f"<blockquote>{emoji('profile', '👤')} <b>User:</b> {buyer}\n"
        f"{emoji('email', '📧')} <b>Email:</b> <code>{email}</code></blockquote>"
    )
