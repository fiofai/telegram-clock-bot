# clock_bot.py

from flask import Flask, request
from telegram import Bot, Update, ReplyKeyboardMarkup
from telegram.ext import Dispatcher, CommandHandler, MessageHandler, Filters, ConversationHandler
import datetime
import pytz
import os

app = Flask(__name__)

# === 配置 ===
TOKEN = os.environ.get("TOKEN")
ADMIN_IDS = [1165249082]  # 管理员 Telegram 用户 ID，可添加多个

# 初始化 bot
bot = Bot(token=TOKEN)
dispatcher = Dispatcher(bot, None, use_context=True)

# === 全局数据结构 ===
driver_logs = {}
driver_salaries = {}
driver_accounts = {}

# === /claim 用于状态追踪 ===
CLAIM_TYPE, CLAIM_AMOUNT, CLAIM_PROOF, CLAIM_OTHER_TYPE = range(4)
claim_state = {}  # 存储正在报销的司机状态

# === 时区设置：马来西亚时间 ===
tz = pytz.timezone("Asia/Kuala_Lumpur")

# === /start 命令 ===
def start(update, context):
    user = update.effective_user
    user_id = user.id

    driver_logs.setdefault(user_id, {})
    driver_salaries.setdefault(user_id, {"total_hours": 0.0, "daily_log": {}})
    driver_accounts.setdefault(user_id, {"balance": 0.0, "claims": []})

    msg = (
        f"👋 Hello {user.first_name}!\n"
        "Welcome to Driver ClockIn Bot.\n\n"
        "Available Commands:\n"
        "🕑 /clockin - Start work\n"
        "🏁 /clockout - End work\n"
        "📅 /offday - Mark off day\n"
        "💸 /claim - Deduct from travel fund"
    )

    if user_id in ADMIN_IDS:
        msg += (
            "\n\n🔐 Admin Commands:\n"
            "📊 /balance - View all balances\n"
            "📄 /check - View all drivers' status\n"
            "🧾 /PDF - Generate monthly report\n"
            "💵 /topup <user_id> <amount> - Top up driver balance"
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
    now = datetime.datetime.now(tz)
    today = now.strftime("%Y-%m-%d")
    clock_time = now.strftime("%Y-%m-%d %H:%M:%S")

    if today not in driver_logs.get(user_id, {}) or 'in' not in driver_logs[user_id][today]:
        update.message.reply_text("❌ You haven't clocked in today.")
        return

    driver_logs[user_id][today]['out'] = clock_time

    in_time = datetime.datetime.strptime(driver_logs[user_id][today]['in'], "%Y-%m-%d %H:%M:%S")
    duration = now - in_time
    total_seconds = duration.total_seconds()
    hours = int(total_seconds // 3600)
    minutes = int((total_seconds % 3600) // 60)

    if hours and minutes:
        time_str = f"{hours}hour{'s' if hours > 1 else ''} and {minutes}min"
    elif hours:
        time_str = f"{hours}hour{'s' if hours > 1 else ''}"
    else:
        time_str = f"{minutes}min"

    # 累计工时
    driver_salaries[user_id]['total_hours'] += total_seconds / 3600
    driver_salaries[user_id]['daily_log'][today] = total_seconds / 3600

    update.message.reply_text(f"🏁 Clocked out at {clock_time}. Worked {time_str}.")

# === /offday ===
def offday(update, context):
    user_id = update.effective_user.id
    today = datetime.datetime.now(tz).strftime("%Y-%m-%d")
    driver_logs.setdefault(user_id, {})[today] = {"in": "OFF", "out": "OFF"}
    update.message.reply_text(f"📅 Marked {today} as off day.")

# === /balance（仅管理员）===
def balance(update, context):
    if update.effective_user.id not in ADMIN_IDS:
        return
    msg = "📊 Driver Balances:\n"
    for uid, acc in driver_accounts.items():
        chat = bot.get_chat(uid)
        name = f"@{chat.username}" if chat.username else chat.first_name
        msg += f"• {name}: RM{acc['balance']:.2f}\n"
    update.message.reply_text(msg)

# === /check（仅管理员）===
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

# === /topup（仅管理员）===
def topup(update, context):
    if update.effective_user.id not in ADMIN_IDS:
        return
    try:
        uid = int(context.args[0])
        amount = float(context.args[1])
        driver_accounts.setdefault(uid, {"balance": 0.0, "claims": []})["balance"] += amount
        update.message.reply_text(f"✅ Added RM{amount:.2f} to user {uid}.")
    except:
        update.message.reply_text("❌ Usage: /topup <user_id> <amount>")

# === /claim 分阶段 ===
def claim_start(update, context):
    reply_keyboard = [["toll", "petrol", "other"]]
    update.message.reply_text(
        "🚗 Select claim type:",
        reply_markup=ReplyKeyboardMarkup(reply_keyboard, one_time_keyboard=True)
    )
    return CLAIM_TYPE

def claim_type(update, context):
    user_id = update.effective_user.id
    text = update.message.text.lower()
    claim_state[user_id] = {"type": text}
    if text == "other":
        update.message.reply_text("✍️ Please enter the type description:")
        return CLAIM_OTHER_TYPE
    update.message.reply_text("💰 Enter amount (number):")
    return CLAIM_AMOUNT

def claim_other_type(update, context):
    user_id = update.effective_user.id
    claim_state[user_id]["type"] = update.message.text
    update.message.reply_text("💰 Enter amount (number):")
    return CLAIM_AMOUNT

def claim_amount(update, context):
    user_id = update.effective_user.id
    try:
        amount = float(update.message.text)
        claim_state[user_id]["amount"] = amount
        update.message.reply_text("📎 Now send the proof photo:")
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
    update.message.reply_text("❌ Claim cancelled.")
    return ConversationHandler.END

# === Webhook入口 ===
@app.route(f"/{TOKEN}", methods=["POST"])
def webhook():
    update = Update.de_json(request.get_json(force=True), bot)
    dispatcher.process_update(update)
    return "ok"

# === 注册指令 ===
dispatcher.add_handler(CommandHandler("start", start))
dispatcher.add_handler(CommandHandler("clockin", clockin))
dispatcher.add_handler(CommandHandler("clockout", clockout))
dispatcher.add_handler(CommandHandler("offday", offday))
dispatcher.add_handler(CommandHandler("balance", balance))
dispatcher.add_handler(CommandHandler("check", check))
dispatcher.add_handler(CommandHandler("topup", topup))

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

