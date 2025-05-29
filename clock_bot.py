from flask import Flask, request
from telegram import (
    Bot, Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
)
from telegram.ext import (
    Dispatcher, CommandHandler, MessageHandler, Filters, ConversationHandler
)
import datetime
import pytz
import os
import logging
import traceback

app = Flask(__name__)

TOKEN = os.environ.get("TOKEN")
ADMIN_IDS = [1165249082]

bot = Bot(token=TOKEN)
dispatcher = Dispatcher(bot, None, use_context=True)

# === 日志设置 ===
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# === 全局数据结构 ===
driver_logs = {}
driver_salaries = {}
driver_accounts = {}
topup_state = {}
claim_state = {}

tz = pytz.timezone("Asia/Kuala_Lumpur")

# === conversation 状态 ===
TOPUP_USER, TOPUP_AMOUNT = range(2)
CLAIM_TYPE, CLAIM_OTHER_TYPE, CLAIM_AMOUNT, CLAIM_PROOF = range(4)

# === /start ===
def start(update, context):
    user = update.effective_user
    user_id = user.id
    username = user.username or str(user_id)

    driver_logs.setdefault(user_id, {})
    driver_salaries.setdefault(user_id, {"total_hours": 0.0, "daily_log": {}})
    driver_accounts.setdefault(user_id, {"balance": 0.0, "claims": []})

    msg = (
        f"👋 Hello {user.first_name}!\n"
        "Welcome to Driver ClockIn Bot.\n\n"
        "Available Commands:\n"
        "🕑 /clockin\n"
        "🏁 /clockout\n"
        "📅 /offday\n"
        "💸 /claim"
    )
    if user_id in ADMIN_IDS:
        msg += (
            "\n\n🔐 Admin Commands:\n"
            "📊 /balance\n"
            "📄 /check\n"
            "🧾 /PDF\n"
            "💵 /topup"
        )

    update.message.reply_text(msg)

# === /clockin ===
def clockin(update, context):
    user_id = update.effective_user.id
    now = datetime.datetime.now(tz)
    today = now.strftime("%Y-%m-%d")
    clock_time = now.strftime("%Y-%m-%d %H:%M:%S")

    driver_logs.setdefault(user_id, {}).setdefault(today, {})['in'] = clock_time
    update.message.reply_text(f"✅ Clocked in at {clock_time}")

# === /clockout ===
def clockout(update, context):
    user_id = update.effective_user.id
    now = datetime.datetime.now(ttz)
    today = now.strftime("%Y-%m-%d")
    clock_time = now.strftime("%Y-%m-%d %H:%M:%S")

    if today not in driver_logs.get(user_id, {}) or 'in' not in driver_logs[user_id][today]:
        update.message.reply_text("❌ You haven't clocked in today.")
        return

    try:
        driver_logs[user_id][today]['out'] = clock_time
        in_time = datetime.datetime.strptime(driver_logs[user_id][today]['in'], "%Y-%m-%d %H:%M:%S").replace(tzinfo=tz)
        duration = now - in_time
        total_seconds = duration.total_seconds()
        hours = int(total_seconds // 3600)
        minutes = int((total_seconds % 3600) % 60)

        if hours and minutes:
            time_str = f"{hours} hours {minutes} minutes"
        elif hours:
            time_str = f"{hours} hours"
        else:
            time_str = f"{minutes} minutes"

        driver_salaries.setdefault(user_id, {'total_hours': 0.0, 'daily_log': {}})
        driver_salaries[user_id]['total_hours'] += total_seconds / 3600
        driver_salaries[user_id]['daily_log'][today] = total_seconds / 3600

        update.message.reply_text(f"🏁 Clocked out at {clock_time}. Worked {time_str}.")
    except Exception as e:
        update.message.reply_text(f"⚠️ Error during clockout: {str(e)}")

# === /offday ===
def offday(update, context):
    user_id = update.effective_user.id
    today = datetime.datetime.now(tz).strftime("%Y-%m-%d")
    driver_logs.setdefault(user_id, {})[today] = {"in": "OFF", "out": "OFF"}
    update.message.reply_text(f"📅 Marked {today} as off day.")

# === /balance（管理员）===
def balance(update, context):
    if update.effective_user.id not in ADMIN_IDS:
        return

    msg = "📊 Driver Balances:\n"
    for uid, acc in driver_accounts.items():
        chat = bot.get_chat(uid)
        name = f"@{chat.username}" if chat.username else chat.first_name
        msg += f"• {name}: RM{acc['balance']:.2f}\n"
    update.message.reply_text(msg)

# === /check（管理员）===
def check(update, context):
    if update.effective_user.id not in ADMIN_IDS:
        return

    today = datetime.datetime.now(tz).strftime("%Y-%m-%d")
    msg = "📄 Today's Status:\n"
    for uid, log in driver_logs.items():
        day = log.get(today, {})
        in_time = day.get("in", "❌")
        out_time = day.get("out", "❌")
        chat = bot.get_chat(uid)
        name = f"@{chat.username}" if chat.username else chat.first_name
        msg += f"• {name}: IN: {in_time}, OUT: {out_time}\n"
    update.message.reply_text(msg)

# === /topup (交互流程管理员专用) ===
def topup_start(update, context):
    if update.effective_user.id not in ADMIN_IDS:
        return

    keyboard = []
    topup_state[update.effective_user.id] = {}
    for uid in driver_accounts:
        chat = bot.get_chat(uid)
        name = f"@{chat.username}" if chat.username else chat.first_name
        keyboard.append([name])
        topup_state[update.effective_user.id][name] = uid

    update.message.reply_text(
        "👤 Select driver to top up:",
        reply_markup=ReplyKeyboardMarkup(keyboard, one_time_keyboard=True)
    )
    return TOPUP_USER

def topup_user(update, context):
    admin_id = update.effective_user.id
    selected = update.message.text.strip()

    if selected not in topup_state[admin_id]:
        update.message.reply_text("❌ Invalid selection.")
        return ConversationHandler.END

    context.user_data["topup_uid"] = topup_state[admin_id][selected]
    update.message.reply_text("💰 Enter amount (RM):", reply_markup=ReplyKeyboardRemove())
    return TOPUP_AMOUNT

def topup_amount(update, context):
    try:
        amount = float(update.message.text.strip())
        uid = context.user_data["topup_uid"]

        driver_accounts.setdefault(uid, {"balance": 0.0, "claims": []})["balance"] += amount
        chat = bot.get_chat(uid)
        name = f"@{chat.username}" if chat.username else chat.first_name

        update.message.reply_text(f"✅ Topped up RM{amount:.2f} to {name}.")
    except Exception as e:
        update.message.reply_text("❌ Invalid amount.")
    return ConversationHandler.END

# === /claim 分阶段 ===
def claim_start(update, context):
    keyboard = [["toll", "petrol", "other"]]
    update.message.reply_text("🚗 Select claim type:", reply_markup=ReplyKeyboardMarkup(keyboard, one_time_keyboard=True))
    return CLAIM_TYPE

def claim_type(update, context):
    user_id = update.effective_user.id
    text = update.message.text.lower()
    claim_state[user_id] = {"type": text}

    if text == "other":
        update.message.reply_text("✍️ Please enter the type description:")
        return CLAIM_OTHER_TYPE
    update.message.reply_text("💰 Enter amount:")
    return CLAIM_AMOUNT

def claim_other_type(update, context):
    user_id = update.effective_user.id
    claim_state[user_id]["type"] = update.message.text
    update.message.reply_text("💰 Enter amount:")
    return CLAIM_AMOUNT

def claim_amount(update, context):
    user_id = update.effective_user.id
    try:
        amount = float(update.message.text)
        claim_state[user_id]["amount"] = amount
        update.message.reply_text("📎 Now send proof photo:")
        return CLAIM_PROOF
    except:
        update.message.reply_text("❌ Please enter a valid number.")
        return CLAIM_AMOUNT

def claim_proof(update, context):
    user_id = update.effective_user.id
    file_id = update.message.photo[-1].file_id
    date = datetime.datetime.now(tz).strftime("%Y-%m-%d")

    entry = {
        "amount": claim_state[user_id]["amount"],
        "type": claim_state[user_id]["type"],
        "date": date,
        "photo": file_id
    }

    driver_accounts.setdefault(user_id, {"balance": 0.0, "claims": []})
    driver_accounts[user_id]["claims"].append(entry)
    driver_accounts[user_id]["balance"] -= entry["amount"]

    update.message.reply_text(f"✅ RM{entry['amount']} claimed for {entry['type']} on {entry['date']}.")
    return ConversationHandler.END

def cancel(update, context):
    update.message.reply_text("❌ Cancelled.")
    return ConversationHandler.END

# === Webhook ===
@app.route(f"/{TOKEN}", methods=["POST"])
def webhook():
    update = Update.de_json(request.get_json(force=True), bot)
    dispatcher.process_update(update)
    return "ok"

# === Dispatcher 注册 ===
dispatcher.add_handler(CommandHandler("start", start))
dispatcher.add_handler(CommandHandler("clockin", clockin))
dispatcher.add_handler(CommandHandler("clockout", clockout))
dispatcher.add_handler(CommandHandler("offday", offday))
dispatcher.add_handler(CommandHandler("balance", balance))
dispatcher.add_handler(CommandHandler("check", check))

# === topup handler ===
dispatcher.add_handler(ConversationHandler(
    entry_points=[CommandHandler("topup", topup_start)],
    states={
        TOPUP_USER: [MessageHandler(Filters.text & ~Filters.command, topup_user)],
        TOPUP_AMOUNT: [MessageHandler(Filters.text & ~Filters.command, topup_amount)],
    },
    fallbacks=[CommandHandler("cancel", cancel)],
))

# === claim handler ===
dispatcher.add_handler(ConversationHandler(
    entry_points=[CommandHandler("claim", claim_start)],
    states={
        CLAIM_TYPE: [MessageHandler(Filters.text & ~Filters.command, claim_type)],
        CLAIM_OTHER_TYPE: [MessageHandler(Filters.text & ~Filters.command, claim_other_type)],
        CLAIM_AMOUNT: [MessageHandler(Filters.text & ~Filters.command, claim_amount)],
        CLAIM_PROOF: [MessageHandler(Filters.photo, claim_proof)],
    },
    fallbacks=[CommandHandler("cancel", cancel)],
))

# === Run ===
if __name__ == "__main__":
    logger.info("Bot server started.")
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
