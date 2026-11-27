"""
Premium Custom Emoji System for StockBot2
──────────────────────────────────────────
Uses ONLY the emoji IDs you provided.
- Buttons get automatic premium icons
- Premium emoji in blockquotes/messages work (just add <tg-emoji> tags manually)
"""

import html as _html_std
import inspect
import re as _re

from telebot import types

# Whether the installed pyTelegramBotAPI supports custom-emoji button icons
try:
    _ICON_SUPPORTED = "icon_custom_emoji_id" in inspect.signature(
        types.InlineKeyboardButton.__init__
    ).parameters
except Exception:
    _ICON_SUPPORTED = False

# Whether the installed pyTelegramBotAPI supports colored button styles
try:
    _STYLE_SUPPORTED = "style" in inspect.signature(
        types.InlineKeyboardButton.__init__
    ).parameters
except Exception:
    _STYLE_SUPPORTED = False


# ── Button Premium Emoji IDs (YOUR COMPLETE PROVIDED LIST — FINAL) ────────────

BUTTON_EMOJI_IDS = {
    # Navigation
    "Main Menu": "5042022053356504092",
    "main_menu": "5042022053356504092",
    "home": "5042022053356504092",
    "Back": "5255703720078879038",
    "back": "5255703720078879038",
    "prev": "5255703720078879038",
    "⬅️ Prev": "5255703720078879038",
    "⬅️ Back to Order": "5332581104941545115",
    "next": "5253767677670862169",
    "Next": "5253767677670862169",
    "Next ➡️": "5253767677670862169",

    # Shopping
    "Products": "5312361253610475399",
    "products": "5312361253610475399",
    "Buy": "5312361253610475399",
    "buy": "5312361253610475399",
    "🛒 Buy Now": "5312361253610475399",
    "🛒 View Product": "5312361253610475399",
    "🛍 Shop": "5406683434124859552",

    # Money & Wallet
    "Wallet": "5445353829304387411",
    "wallet": "5445353829304387411",
    "Topup": "5445353829304387411",
    "topup": "5445353829304387411",
    "💰 My Balance": "5039789890133296083",

    # Users & Profile
    "Profile": "5381970617505428041",
    "profile": "5381970617505428041",
    "Users": "5381970617505428041",
    "users": "5381970617505428041",

    # Orders, History, Deposits
    "Purchase History": "5444856076954520455",
    "purchase_history": "5444856076954520455",
    "My Deposits": "5444856076954520455",
    "my_deposits": "5444856076954520455",
    "Deposits": "5444856076954520455",
    "deposits": "5444856076954520455",
    "Orders": "5444856076954520455",
    "orders": "5444856076954520455",
    "history": "5444856076954520455",

    # Giveaway
    "Giveaway": "5039823300683891773",
    "giveaway": "5039823300683891773",
    "Join Giveaway": "5039823300683891773",
    "join_giveaway": "5039823300683891773",
    "John Giveaway": "5039823300683891773",
    "New Giveaway": "5039891861246838069",
    "new_giveaway": "5039891861246838069",
    "👀 View Giveaway": "5039623284056917259",
    "📢 Announce": "5298609030321691620",
    "🎲 Draw Now": "5260547274957672345",
    "🎲 Yes, draw now": "5260547274957672345",
    "🚫 Cancel Giveaway": "5039671744172917707",

    # Reseller
    "Become a Reseller": "5039834781131474002",
    "become_reseller": "5039834781131474002",
    "Resellers": "5039834781131474002",
    "resellers": "5039834781131474002",
    "Make Reseller": "5039816072253932764",
    "make_reseller": "5039816072253932764",
    "Requests": "5039827436737397847",
    "requests": "5039827436737397847",
    "Generate Key": "5042167377869932162",
    "generate_key": "5042167377869932162",
    "Keys": "5296369303661067030",
    "keys": "5296369303661067030",
    "Inactivity Window": "5040045238823945201",
    "inactivity_window": "5040045238823945201",
    "🔑 Activate Key": "5307843983102204243",
    "🔑 Activate Now": "5307843983102204243",
    "📨 Request Access": "5253742260054409879",
    "👀 Review Request": "5039623284056917259",
    "🚫 Revoke Key": "5039671744172917707",
    "🚫 Revoke Reseller Status": "5039671744172917707",
    "🏪❌ Revoke Reseller": "5040042498634810056",
    "No active resellers": "5040045238823945201",

    # Admin & System
    "Analytics": "5203993413346680064",
    "analytics": "5203993413346680064",
    "Settings": "5341715473882955310",
    "settings": "5341715473882955310",
    "System": "5341715473882955310",
    "system": "5341715473882955310",
    "Refresh": "5226702984204797593",
    "refresh": "5226702984204797593",
    "Broadcast": "5399967660052081305",
    "broadcast": "5399967660052081305",
    "Admin Panel": "5217822164362739968",
    "admin_panel": "5217822164362739968",

    # Support & Alerts
    "Support": "5039532814865794117",
    "support": "5039532814865794117",
    "Alerts: On": "5039844895779455925",
    "alerts_on": "5039844895779455925",
    "Alerts: Off": "5210952531676504517",
    "alerts_off": "5210952531676504517",

    # Actions - Positive / Approve
    "Confirm": "5039844895779455925",
    "confirm": "5039844895779455925",
    "approve": "5039844895779455925",
    "Maintenance: ON": "5039844895779455925",
    "maintenance_on": "5039844895779455925",
    "✅ Approve": "5039844895779455925",
    "✅ Approve & Send Key": "5039844895779455925",
    "✅ Payment Done": "5039844895779455925",
    "✅ Yes, cancel": "5039844895779455925",
    "✅ No pending requests": "5039844895779455925",
    "✅ No deposits here": "5039844895779455925",

    # Actions - Negative / Reject
    "Cancel": "5210952531676504517",
    "cancel": "5210952531676504517",
    "reject": "5210952531676504517",
    "dismiss": "5210952531676504517",
    "Maintenance: OFF": "5040042498634810056",
    "maintenance_off": "5040042498634810056",
    "❌ Reject": "5040042498634810056",
    "❌ No": "5040042498634810056",
    "❌ Remove Discount": "5040042498634810056",
    "✖️ Dismiss": "5040042498634810056",

    # Status Messages
    "⏳ Problem Reported — under review": "5386367538735104399",
    "⏳ Request Pending": "5386367538735104399",
    "⚠️ Report Problem": "5039665997506675838",
    "No keys yet": "5040045238823945201",

    # Edit Actions
    "✏️ Channel URL": "5395444784611480792",
    "✏️ Channel Username": "5395444784611480792",
    "✏️ Cred Format": "5395444784611480792",
    "✏️ Description": "5395444784611480792",
    "✏️ Enter Custom Rate": "5395444784611480792",
    "✏️ Log Channel ID": "5395444784611480792",
    "✏️ Name": "5395444784611480792",
    "✏️ Price": "5395444784611480792",
    "✏️ Shop Name": "5395444784611480792",
    "✏️ Sub-tagline": "5395444784611480792",
    "✏️ Support @user": "5395444784611480792",
    "✏️ Support URL": "5395444784611480792",
    "✏️ Tagline": "5395444784611480792",
    "✏️ Welcome Msg": "5395444784611480792",

    # Admin Settings
    "⭐ Set Primary Admin ID": "5447644863644320013",

    # Product Management
    "📂 Category": "5042186567783809934",
    "📥 Add Stock": "5443127283898405358",
    "📋 View Stock": "5197269100878907942",
    "😀 Icon": "5337142643677732671",
    "🔁 Replace": "5226702984204797593",
    "🔁 Try Again": "5226702984204797593",
    "🗑 Delete": "5039614900280754969",
    "🚫 Hide": "5039671744172917707",
    "🏪 Reseller Price": "5042022607407285100",
    "🏪❌ Clear Reseller": "5040042498634810056",
    "🏷 Set Discount": "5240228673738527951",

    # Credential Format Templates
    "📧 email | otp-link": "5368554037320900698",
    "📧 email | password": "5368554037320900698",
    "📧 email | password | 2FA key": "5368554037320900698",
    "📧 email | password | OTP link": "5368554037320900698",
    "📧 email | password | key": "5368554037320900698",
    "📧 email | password | recovery": "5368554037320900698",
    "👤 username | password": "5042295196096662988",
    "🎮 code | pin": "5361741454685256344",

    # Shop & Stock
    "Out of Stock": "5039671744172917707",
    "out_of_stock": "5039671744172917707",
    "Custom Amount": "5395444784611480792",
    "custom_amount": "5395444784611480792",
    "Shop Info": "5278702045883292456",
    "shop_info": "5278702045883292456",
    "Pricing & Stock": "5040025580758631490",
    "pricing_stock": "5040025580758631490",

    # Payment & Financial
    "Payment Methods": "5229064374403998351",
    "payment_methods": "5229064374403998351",
    "USDT MMK Rate": "5039789890133296083",
    "usd_mmk_rate": "5039789890133296083",
    "Low Stock Threshold": "5042042652019655612",
    "low_stock": "5042042652019655612",
    "Test Binance": "5039675231686361986",
    "test_binance": "5039675231686361986",
    "♻️ Refund": "5255981634527704754",

    # Channel & Logging
    "Channel & Gate": "5461151367559141950",
    "channel_gate": "5461151367559141950",
    "Logging": "5039579582764680065",
    "logging": "5039579582764680065",

    # Admin Management
    "Manage Admins": "5042295196096662988",
    "manage_admins": "5042295196096662988",

    # Database & System
    "Restore DBs": "5042040865313260562",
    "restore_dbs": "5042040865313260562",
    "Backup DBs": "5039600026809009149",
    "backup_dbs": "5039600026809009149",
    "Restart Bot": "5346269127059196142",
    "restart_bot": "5346269127059196142",
    "Problem Reports": "5039665997506675838",
    "problem_reports": "5039665997506675838",

    # Add/Remove Actions
    "Add Product": "5039891861246838069",
    "add_product": "5039891861246838069",
    "Add Balance": "5039891861246838069",
    "add_balance": "5039891861246838069",
    "add": "5039891861246838069",
    "Remove Balance": "5231265631042548053",
    "remove_balance": "5231265631042548053",
    "remove": "5231265631042548053",

    # Search
    "Search": "5039649904264217620",
    "search": "5039649904264217620",
    # ── auto-resolved button labels (Oct 2026) ──
    "✅ Mark Done": "5039844895779455925",
    "✏️ Ask for Correction": "5395444784611480792",
    "📤 Deliver Credential": "5445355530111437729",
    "✏️ Info Fields": "5395444784611480792",
    "⚡ Instant — auto-delivery from stock": "5042334757040423886",
    "👤 Manual activation — we ask the buyer for login, admin activates": "5431766464040283359",
    "📦 Pre-order — buyer waits, admin sends credentials later": "5298487770510020895",
    "🔗 activation link": "5042101437237036298",
    "🎮 cdk key": "5361741454685256344",
    "⏱️ Fulfillment ETA": "5382194935057372936",
    "📝 Send My Details": "5258500400918587241",
    "📦 My Orders": "5298487770510020895",
    "🔁 Buy Again": "5226702984204797593",
    "🛍 Browse (Reseller Prices)": "4958714256343171878",
    "📢 Join Channel": "5298609030321691620",
    "✅ I've Joined": "5039844895779455925",
    "🎁 Join Giveaway": "5039823300683891773",
    "🏠 Main Menu": "5197708768091061888",
    "🎲 Draw Now": "5260547274957672345",
    "🎲 Yes, draw now": "5260547274957672345",
    "📢 Announce": "5298609030321691620",
    "👀 View Giveaway": "5039623284056917259",
    "👀 Review Request": "5039623284056917259",
    "🚫 Cancel Giveaway": "5039671744172917707",
    "✅ Yes, cancel": "5039844895779455925",
    "❌ No": "5040042498634810056",
    "✖️ Dismiss": "5256110225848543598",
    "✖ Cancel": "5256110225848543598",
    "⚠️ Report Problem": "5039665997506675838",
    "⏳ Problem Reported — under review": "5386367538735104399",
    "♻️ Refund": "5226702984204797593",
    "⬅️ Back to Order": "5332581104941545115",
    "🔁 Try Again": "5226702984204797593",
    "✅ Payment Done": "5039844895779455925",
    "💰 My Balance": "5039789890133296083",
    "🛍 Shop": "4958714256343171878",
    "📨 Request Access": "5253742260054409879",
    "🔗 Invite Friends": "5042101437237036298",
    "✏️ Name": "5395444784611480792",
    "✏️ Price": "5395444784611480792",
    "😀 Icon": "5337142643677732671",
    "✏️ Description": "5395444784611480792",
    "✏️ Cred Format": "5395444784611480792",
    "📂 Category": "5042306247047513767",
    "🏪 Reseller Price": "5278702045883292456",
    "🏪❌ Clear Reseller": "5278702045883292456",
    "🚫 Hide": "5039671744172917707",
    "🚫 Show": "5039671744172917707",
    "📥 Add Stock": "5443127283898405358",
    "📋 View Stock": "5197269100878907942",
    "❌ Remove Discount": "5040042498634810056",
    "🏷 Set Discount": "5240228673738527951",
    "🗑 Delete": "5039614900280754969",
    "📧 email | password": "5368554037320900698",
    "📧 email | otp-link": "5368554037320900698",
    "📧 email | password | 2FA key": "5368554037320900698",
    "📧 email | password | OTP link": "5368554037320900698",
    "👤 username | password": "5431766464040283359",
    "⭐ Set Primary Admin ID": "5438496463044752972",
    "🚫 Revoke Key": "5039671744172917707",
    "🚫 Revoke Reseller Status": "5039671744172917707",
    "✅ Approve & Send Key": "5039844895779455925",
    "❌ Reject": "5040042498634810056",
    "⬅️ Prev": "5332581104941545115",
    "Next ➡️": "5253767677670862169",
}


# ── Message Premium Emoji IDs (for text messages, not buttons) ────────────────

MESSAGE_EMOJI_IDS = {
    # Status & Indicators
    "success": "5039844895779455925",           # ✅
    "approved": "5039844895779455925",
    "rejected": "5040042498634810056",          # ❌
    "failed": "5040042498634810056",
    "pending": "5386367538735104399",           # ⏳
    "processing": "5386367538735104399",
    "warning": "5039665997506675838",           # ⚠️
    "alert": "5039665997506675838",
    "premium": "5042050649248760772",           # 💎
    "featured": "5042050649248760772",
    "notification": "5042111805288089118",      # 🔔
    "bell": "5298487770510020895",
    "pinned": "5039600026809009149",            # 📌
    "important": "5039600026809009149",
    "highlight": "5040016479722931047",         # ✨
    "special": "5040016479722931047",
    "target": "5310278924616356636",            # 🎯
    "goal": "5310278924616356636",
    "star": "5438496463044752972",              # ⭐
    "rating": "5438496463044752972",
    
    # Money & Transactions
    "balance": "5039789890133296083",           # 💰
    "money": "5039789890133296083",
    "payment": "5445353829304387411",           # 💳
    "card": "5445353829304387411",
    "cash": "5040025580758631490",              # 💵
    "mmk": "5040025580758631490",
    "spending": "5409048419211682843",          # 💸
    "deduction": "5409048419211682843",
    "bank": "5332455502917949981",              # 🏦
    "pm_kbzpay": "6073179985286274037",           # 🏦 KBZPay
    "pm_wavepay": "6073307408376013348",          # 🌊 WavePay
    "pm_cbpay": "6152412120328706839",            # 🏛 CB Pay
    "pm_uabpay": "6338978473100448456",           # 🏧 UAB Pay
    "pm_ayapay": "6298531636497617345",           # 💳 AYA Pay
    "pm_binance": "6298693015598797101",          # 🟡 Binance
    "financial": "5332455502917949981",
    "usdt": "5042050649248760772",              # 💎
    "crypto": "5042050649248760772",
    "coin": "5199552030615558774",              # 🪙
    "token": "5199552030615558774",
    "statistics": "5042290883949495533",        # 📊
    "chart": "5042290883949495533",
    
    # Shopping & Products
    "cart": "5312361253610475399",              # 🛒
    "buy": "5312361253610475399",
    "shopping": "5406683434124859552",          # 🛍️
    "shop": "5406683434124859552",
    "package": "5298487770510020895",           # 📦
    "product": "5298487770510020895",
    "list": "5197269100878907942",              # 📋
    "inventory": "5197269100878907942",
    "price": "5240228673738527951",             # 🏷️
    "tag": "5240228673738527951",
    "gift": "5039823300683891773",              # 🎁
    "bonus": "5039823300683891773",
    "ticket": "5217954011268797485",            # 🎫
    "code": "5217954011268797485",
    "key": "5307843983102204243",               # 🔑
    "access": "5307843983102204243",
    "download": "5443127283898405358",          # 📥
    "receive": "5443127283898405358",
    "upload": "5445355530111437729",            # 📤
    "send": "5445355530111437729",
    
    # User & Profile
    "user": "5870692618244984670",              # 👤
    "profile": "5197288647275071607",
    "users": "5332724926216428039",             # 👥
    "group": "5332724926216428039",
    "id": "5197288647275071607",                # 🆔
    "identity": "5197288647275071607",
    "vip": "5039727497143387500",               # 👑
    "reseller": "5039727497143387500",
    "badge": "5440539497383087970",             # 🎖️
    "achievement": "5440539497383087970",
    "medal": "5440539497383087970",             # 🏅
    "winner": "5440539497383087970",
    "nametag": "5370675038200541160",           # 📛
    "role": "5420191780580635479",              # 🎭
    "permission": "5420191780580635479",
    
    # Communication
    "message": "5253742260054409879",           # 📨
    "email": "5253742260054409879",
    "inbox": "5253742260054409879",             # 📬
    "received": "5253742260054409879",
    "outbox": "5287533898803211359",            # 📮
    "sent": "5287533898803211359",
    "chat": "5040036030414062506",              # 💬
    "support": "5040036030414062506",
    "announcement": "5253997827788385948",      # 📢
    "broadcast": "5041888071851705019",         # 📣
    "contact": "5318779098686826724",           # 📞
    "call": "5318779098686826724",
    "letter": "5253742260054409879",            # ✉️
    "mail": "5253742260054409879",
    "special_message": "5041784013384057970",   # 💌
    
    # Actions & Controls
    "edit": "5395444784611480792",              # ✏️
    "modify": "5395444784611480792",
    "delete": "5039614900280754969",            # 🗑️
    "remove": "5039614900280754969",
    "add": "5039891861246838069",               # ➕
    "plus": "5039891861246838069",
    "subtract": "5382261056078881010",          # ➖
    "minus": "5382261056078881010",
    "refresh": "5280546416509340552",           # 🔄
    "reload": "5280546416509340552",
    "repeat": "5280546416509340552",            # 🔁
    "retry": "5280546416509340552",
    "settings": "5341715473882955310",          # ⚙️
    "config": "5341715473882955310",
    "tools": "5413398757226076065",             # 🔧
    "manage": "5413398757226076065",
    "lock": "5296369303661067030",              # 🔒
    "secure": "5296369303661067030",
    "unlock": "5465443379917629504",            # 🔓
    "open": "5465443379917629504",
    "view": "5039984684080038649",              # 👁️
    "preview": "5039984684080038649",
    "search": "5039649904264217620",            # 🔍
    "find": "5039649904264217620",
    
    # Time & Dates
    "clock": "5787488119490088755",             # ⏰
    "time": "5386415655253730366",
    "calendar": "5413879192267805083",          # 📅
    "date": "5413879192267805083",
    "waiting": "5440621591387980068",           # ⌛
    "timer": "5440621591387980068",
    "hour": "5440621591387980068",              # 🕐
    "schedule": "5834822129425584578",          # 📆
    "event": "5834822129425584578",
    "stopwatch": "5386415655253730366",         # ⏱️
    "duration": "5386415655253730366",
    
    # Location & Navigation
    "home": "5197708768091061888",              # 🏠
    "main": "5197708768091061888",
    "location": "5039895103947146186",          # 📍
    "pin": "5039895103947146186",
    "map": "5235794253149394263",                # 🗺️
    "navigate": "5235794253149394263",
    "direction": "5199412938099666948",         # 🧭
    "guide": "5199412938099666948",
    "return": "5255703720078879038",            # ↩️
    "back": "5255703720078879038",
    "up": "5197399766668956510",                # ⤴️
    
    # Documents & Data
    "document": "5472279086657199080",          # 📄
    "file": "5472279086657199080",
    "page": "5472279086657199080",              # 📃
    "paper": "5472279086657199080",
    "report": "5042290883949495533",            # 📊
    "analytics": "5042290883949495533",
    "growth": "5039808285478224750",            # 📈
    "increase": "5039808285478224750",
    "decline": "5039759318556083411",           # 📉
    "decrease": "5039759318556083411",
    "archive": "5346288231073723227",           # 🗃️
    "storage": "5346288231073723227",
    "folder": "5345987742276797245",            # 📁
    "category": "5345987742276797245",
    "save": "5462956611033117422",              # 💾
    "backup": "5462956611033117422",
    
    # System & Status
    "offline": "5042042652019655612",           # 🔴
    "inactive": "5042042652019655612",
    "online": "5039928501612839813",            # 🟢
    "active": "5039928501612839813",
    "pending_status": "5440638590868543606",    # 🟡
    "warning_status": "5440638590868543606",
    "fast": "5042334757040423886",              # ⚡
    "power": "5042334757040423886",
    "battery": "5454125707300978880",           # 🔋
    "energy": "5454125707300978880",
    "network": "5042186567783809934",           # 🌐
    "global": "5042186567783809934",
    "security": "5042328396193864923",          # 🛡️
    "protection": "5042328396193864923",
    # Fulfillment v2 order statuses
    "fulfill_waiting": "5458770564107743497",     # ⏳
    "fulfill_working": "5870692618244984670",     # 🔧
    "fulfill_done": "5206607081334906820",        # ✅
    "fulfill_refunded": "5332581104941545115",    # ↩️
    # CDK activation products (IDs supplied by John, 2026-10-10)
    "cdk_chatgpt_go": "5246801734638002426",      # ⚡
    "cdk_chatgpt_plus": "5246801734638002426",    # ✨
    "cdk_claude_pro": "5417984549642601364",      # 🟣
    "cdk_perplexity_pro": "5413586627685549158",  # 🔮
    "cdk_super_grok": "5440892071248431491",      # 🚀
    "cdk_supergrok_3m": "5440892071248431491",    # 🪐
    "cdk_activate": "5042303713016808496",        # 🎮
    "cdk_back": "5042156073516008537",            # 🎮
    "cdk_history": "5197269100878907942",         # 📋 My Activations
    "cdk_check_status": "5039649904264217620",    # 🔍 Check Status
    "cdk_sending": "5445355530111437729",         # 📤 sending stage
    "cdk_binding": "5042101437237036298",         # 🔗 binding stage
    "cdk_starting": "5406966974980828470",        # 🚀 starting stage
    "cdk_retry": "5226702984204797593",           # 🔁 try again
    "cdk_refresh": "5226702984204797593",         # 🔄 refresh
    # ── John's full premium set (Oct 2026) ───────────────────────────────
    "wallet": "5445221832074483553",            # 💼
    "profile": "5431766464040283359",           # 👤
    "crown": "5041792560368977040",             # 👑
    "folder": "5042306247047513767",            # 📂
    "shop_bag": "4958714256343171878",          # 🛍
    "repeat": "5226702984204797593",            # 🔁
    "back_arrow": "5332581104941545115",        # ⬅️
    "question": "5436113877181941026",          # ❓
    "party": "5317026657540780588",             # 🎉
    "dice": "5260547274957672345",              # 🎲 (Lucky Draw)
    "ld_ticket": "5285376730003954510",         # 🎟 (Lucky Draw)
    "ld_money": "5278467510604160626",          # 💰 (Lucky Draw)
    "ld_lock": "5197288647275071607",           # 🔒 (Lucky Draw)
    "fire": "5334725814040674667",              # 🔥 (Lucky Draw)
    "ld_eyes": "5391042881219731337",           # 👀 (Lucky Draw)
    "clover": "5258040062028822951",            # 🍀
    "trophy": "5188344996356448758",            # 🏆
    "memo": "5258500400918587241",              # 📝
    "green_dot": "5039928501612839813",         # 🟢
    "no_entry": "5039671744172917707",          # 🚫
    "love_letter": "5332709704852321964",       # 💌
    "raising_hand": "5215248074498128418",      # 🙋
    "hourglass_done": "5454415424319931791",    # ⌛
    "mega": "5298609030321691620",              # 📢
    "point_down": "5231102735817918643",        # 👇
    "zap": "5042334757040423886",               # ⚡
    "key": "5307843983102204243",               # 🔑
    "receipt": "5444856076954520455",           # 🧾
    "speech": "5040036030414062506",            # 💬
    "stopwatch": "5382194935057372936",         # ⏱
    "x_mark": "5256110225848543598",            # ✖️
    "chart": "5042290883949495533",             # 📊
    "inbox": "5253742260054409879",             # 📨
    "store": "5278702045883292456",             # 🏪
    "pencil": "5395444784611480792",            # ✏️
    "inbox_tray": "5443127283898405358",        # 📥
    "tag": "5240228673738527951",               # 🏷
    "trash": "5039614900280754969",             # 🗑
    "email": "5368554037320900698",             # 📧
    "game": "5361741454685256344",              # 🎮
    "star": "5438496463044752972",              # ⭐
    "grin": "5337142643677732671",              # 😀
    "arrow_right": "5253767677670862169",       # ➡️
    "lock": "5296369303661067030",              # 🔒
    "dart": "5310278924616356636",              # 🎯
    "money": "5039789890133296083",             # 💰
    "money_wings": "5201691993775818138",       # 💸
    "eyes": "5039623284056917259",              # 👀
    "dice": "5260547274957672345",              # 🎲
    # AM Pro (Alight Motion subscription)
    "am_menu": "5041866884778034602",
    "am_upgrade": "5368554037320900698",       # same icon as email
    "am_generate": "5042334757040423886",      # same icon as zap
    "am_bulk": "5298487770510020895",          # same icon as package

}


def emoji(name: str, fallback: str = "🎯") -> str:
    """Get premium emoji HTML tag for use in messages.

    Returns <tg-emoji emoji-id="ID">fallback</tg-emoji> which Telegram
    parses when parse_mode='HTML' is used with bot.send_message().

    Args:
        name: Emoji name from MESSAGE_EMOJI_IDS
        fallback: Plain Unicode emoji shown where the custom one can't render
            (e.g. channel posts — Telegram only allows custom emoji in
            private/group/supergroup chats for Premium-owner bots). Pick one
            that makes sense in context so channel posts still look right.

    Returns:
        HTML <tg-emoji> tag or empty string if not found

    Example:
        f"{emoji('success', '✅')} Order completed!"
    """
    eid = MESSAGE_EMOJI_IDS.get(name.lower())
    if eid:
        # Return the exact format Telegram expects for custom emoji in HTML mode
        return f'<tg-emoji emoji-id="{eid}">{fallback}</tg-emoji>'
    return fallback


# ── Button Builder ────────────────────────────────────────────────────────────

def button(text: str, callback_data=None, *, key: str = None, emoji_id=None,
           lead: str = None, url: str = None, **kw) -> types.InlineKeyboardButton:
    """Build an InlineKeyboardButton with premium emoji icon support.

    Args:
        text: Button label text
        callback_data: Callback data for the button
        key: Emoji key from BUTTON_EMOJI_IDS dict
        emoji_id: Direct custom emoji ID (for product-specific icons)
        lead: Plain emoji prefix to strip from label when using icon
        url: URL for URL buttons
        style: Optional colored style (danger/success/primary)

    Returns:
        InlineKeyboardButton with premium icon if supported
    """
    # Safety: button labels are plain text — Telegram never parses HTML in them.
    # Strip any <tg-emoji> tags that slipped in (they'd show as raw text).
    if "<tg-emoji" in text:
        import re as _re
        text = _re.sub(r"<tg-emoji[^>]*>(.*?)</tg-emoji>", r"\1", text)
    style = kw.pop("style", None)
    if style is not None:
        style = str(style).strip().lower()
        if _STYLE_SUPPORTED and style in ("danger", "success", "primary"):
            kw["style"] = style

    # If key is provided, get the emoji_id from BUTTON_EMOJI_IDS
    if key and not emoji_id:
        emoji_id = BUTTON_EMOJI_IDS.get(key)
    
    # Auto-lookup: if no emoji_id provided, try to match button text with BUTTON_EMOJI_IDS
    if not emoji_id:
        emoji_id = BUTTON_EMOJI_IDS.get(text)

    eid = str(emoji_id).strip() if emoji_id not in (None, "", 0, "0") else ""
    if eid and eid.isdigit() and _ICON_SUPPORTED:
        label = text
        # Strip the lead emoji from label if provided
        if lead and label.startswith(lead):
            label = label[len(lead):].lstrip()
        # Auto-strip: if no lead provided but label has emoji, strip it from beginning or end
        elif not lead and label:
            # Strip leading AND trailing emoji characters (comprehensive Unicode emoji ranges)
            import re
            # Comprehensive emoji pattern including arrows, symbols, and variation selectors
            emoji_pattern = r'[\U0001F300-\U0001F9FF\u2600-\u26FF\u2700-\u27BF\U0001F600-\U0001F64F\U0001F680-\U0001F6FF\U0001F1E0-\U0001F1FF\u2B00-\u2BFF\uFE00-\uFE0F]+'
            # Strip leading emojis (with optional whitespace after)
            label = re.sub(r'^' + emoji_pattern + r'\s*', '', label)
            # Strip trailing emojis (with optional whitespace before)
            label = re.sub(r'\s*' + emoji_pattern + r'$', '', label)
        try:
            return types.InlineKeyboardButton(
                label or text, callback_data=callback_data, url=url,
                icon_custom_emoji_id=eid, **kw
            )
        except TypeError:
            pass  # library rejected the kwarg — fall through to plain button

    return types.InlineKeyboardButton(text, callback_data=callback_data, url=url, **kw)


# ── Premium Emoji in Messages & Blockquotes ───────────────────────────────────

# Regex to detect custom emoji tags already in text
_CUSTOM_EMOJI_RE = _re.compile(r'<tg-emoji\s+emoji-id="(\d+)">([^<]*)</tg-emoji>')

def premiumize(text: str) -> str:
    """Enable premium emoji in text by preserving existing <tg-emoji> tags.

    This allows shop descriptions, announcements, and blockquotes to use
    premium emoji. Just add <tg-emoji emoji-id="...">fallback</tg-emoji> tags
    in your text and they'll render as premium emoji.

    Returns text unchanged (preserves your custom emoji tags).
    """
    return str(text or "")


def capture_html(msg) -> str:
    """Capture a Telegram message's text as ready-to-send Telegram HTML.

    Whatever the admin types AND any Telegram Premium (custom) emoji they
    insert are preserved: custom emoji become
    <tg-emoji emoji-id="ID">fallback</tg-emoji> tags, and any bold/italic/etc.
    formatting the admin applied is preserved too. Storing/sending this string
    directly (with parse_mode HTML, no further escaping) reproduces exactly
    what the admin typed — including every premium emoji they added.

    Falls back to escaped plain text if the installed pyTelegramBotAPI
    version doesn't expose entity-to-HTML conversion.
    """
    text = getattr(msg, "text", None) or ""
    try:
        html_text = getattr(msg, "html_text", None)
        if html_text:
            return html_text
    except Exception:
        pass
    try:
        from telebot import formatting
        entities = getattr(msg, "entities", None) or []
        converted = formatting.apply_html_entities(text, entities, None)
        if converted:
            return converted
    except Exception:
        pass
    return _html_std.escape(text)


# ── Legacy Compatibility ──────────────────────────────────────────────────────

def E(key: str) -> str:
    """Legacy hook - returns empty string."""
    return ""

def raw(key: str) -> str:
    """Legacy hook - returns empty string."""
    return ""
