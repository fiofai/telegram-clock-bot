from flask import Flask, request
from telegram import Bot, Update, ReplyKeyboardMarkup
from telegram.ext import Dispatcher, CommandHandler, MessageHandler, Filters, ConversationHandler
import datetime
import pytz
import os
import logging
import traceback

app = Flask(__name__)

# === 配置 ===
TOKEN = os.environ.get("TOKEN")
ADMIN_IDS = [1165249082]

# 日志系统
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# 初始化 Bot 和 Dispatcher
bot = Bot(token=TOKEN)
dispatcher = Dispatcher(bot, None, use_context=True)

# === 数据结构 ===
driver_logs = {}
driver_salaries = {}
driver_accounts = {}
claim_state = {}
topup_state = {}

# === 时区设定 ===
tz = pytz.timezone("Asia/Kuala_Lumpur")

# === 状态常量 ===
CLAIM_TYPE, CLAIM_AMOUNT, CLAIM_PROOF, CLAIM_OTHER_TYPE = range(4)
SELECT_DRIVER, INPUT_AMOUNT = range(2)

# === /start ===
def start(update, context):
    uid = update.effective_user.id
    name = update.effective_user.first_name
    driver_logs.setdefault(uid, {})
    driver_salaries.setdefault(uid, {"total_hours": 0.0, "daily_log": {}})
    driver_accounts.setdefault(uid, {"balance": 0.0, "claims": []})

    msg = f"👋 Hello {name}!\nWelcome to Driver ClockIn Bot.\n\n" \
          "Commands:\n🕑 /clockin\n🏁 /clockout\n📅 /offday\n💸 /claim"
    if uid in ADMIN_IDS:
        msg += "\n\n🔐 Admin:\n📊 /balance\n📄 /check\n💵 /topup"
    update.message.reply_text(msg)

# === /clockin ===
def clockin(update, context):
    uid = update.effective_user.id
    now = datetime.datetime.now(tz)
    today = now.strftime("%Y-%m-%d")
    driver_logs.setdefault(uid, {}).setdefault(today, {})['in'] = now.strftime("%Y-%m-%d %H:%M:%S")
    update.message.reply_text(f"✅ Clocked in at {now.strftime('%Y-%m-%d %H:%M:%S')}")

# === /clockout ===
def clockout(update, context):
    uid = update.effective_user.id
    now = datetime.datetime.now(tz)
    today = now.strftime("%Y-%m-%d")
    try:
        if today not in driver_logs.get(uid, {}) or 'in' not in driver_logs[uid][today]:
            update.message.reply_text("❌ You haven't clocked in today.")
            return
        in_time = datetime.datetime.strptime(driver_logs[uid][today]['in'], "%Y-%m-%d %H:%M:%S").replace(tzinfo=tz)
        duration = now - in_time
        total_seconds = duration.total_seconds()
        hours = int(total_seconds // 3600)
        minutes = int((total_seconds % 3600) // 60)
        time_str = f"{hours}h {minutes}m" if hours else f"{minutes}min"
        driver_logs[uid][today]['out'] = now.strftime("%Y-%m-%d %H:%M:%S")
        driver_salaries[uid]["total_hours"] += total_seconds / 3600
        driver_salaries[uid]["daily_log"][today] = total_seconds / 3600
        update.message.reply_text(f"🏁 Clocked out at {now.strftime('%Y-%m-%d %H:%M:%S')}. Worked {time_str}.")
    except Exception as e:
        logger.error(f"Clockout error: {e}")
        update.message.reply_text("⚠️ Error during clockout.")

# === /offday ===
def offday(update, context):
    uid = update.effective_user.id
    today = datetime.datetime.now(tz).strftime("%Y-%m-%d")
    driver_logs.setdefault(uid, {})[today] = {"in": "OFF", "out": "OFF"}
    update.message.reply_text(f"📅 Marked {today} as off day.")

# === /claim ===
def claim_start(update, context):
    keyboard = [["toll", "petrol", "other"]]
    update.message.reply_text("🚗 Select claim type:", reply_markup=ReplyKeyboardMarkup(keyboard, one_time_keyboard=True))
    return CLAIM_TYPE

def claim_type(update, context):
    uid = update.effective_user.id
    choice = update.message.text.lower()
    claim_state[uid] = {"type": choice}
    if choice == "other":
        update.message.reply_text("✍️ Enter description:")
        return CLAIM_OTHER_TYPE
    update.message.reply_text("💰 Enter amount:")
    return CLAIM_AMOUNT

def claim_other_type(update, context):
    uid = update.effective_user.id
    claim_state[uid]["type"] = update.message.text
    update.message.reply_text("💰 Enter amount:")
    return CLAIM_AMOUNT

def claim_amount(update, context):
    uid = update.effective_user.id
    try:
        amt = float(update.message.text)
        claim_state[uid]["amount"] = amt
        update.message.reply_text("📎 Send photo:")
        return CLAIM_PROOF
    except:
        update.message.reply_text("❌ Invalid number.")
        return CLAIM_AMOUNT

def claim_proof(update, context):
    uid = update.effective_user.id
    file_id = update.message.photo[-1].file_id
    now = datetime.datetime.now(tz)
    entry = {
        "amount": claim_state[uid]["amount"],
        "type": claim_state[uid]["type"],
        "date": now.strftime("%Y-%m-%d"),
        "photo": file_id
    }
    driver_accounts[uid]["claims"].append(entry)
    driver_accounts[uid]["balance"] -= entry["amount"]
    update.message.reply_text(f"✅ RM{entry['amount']} claimed for {entry['type']} on {entry['date']}.")
    return ConversationHandler.END

def cancel(update, context):
    update.message.reply_text("❌ Cancelled.")
    return ConversationHandler.END

# === /topup（选择司机）===
def topup_start(update, context):
    uid = update.effective_user.id
    if uid not in ADMIN_IDS:
        return
    keyboard = []
    name_map = {}
    for driver_id in driver_accounts:
        chat = bot.get_chat(driver_id)
        name = f"@{chat.username}" if chat.username else chat.first_name
        keyboard.append([name])
        name_map[name] = driver_id
    context.user_data["name_map"] = name_map
    update.message.reply_text("👤 Select driver:", reply_markup=ReplyKeyboardMarkup(keyboard, one_time_keyboard=True))
    return SELECT_DRIVER

def topup_choose_driver(update, context):
    name = update.message.text
    name_map = context.user_data["name_map"]
    if name not in name_map:
        update.message.reply_text("❌ Invalid driver.")
        return SELECT_DRIVER
    topup_state[update.effective_user.id] = {"uid": name_map[name], "name": name}
    update.message.reply_text(f"💰 Enter top-up amount for {name}:")
    return INPUT_AMOUNT

def topup_input_amount(update, context):
    admin = update.effective_user.id
    try:
        amount = float(update.message.text)
        uid = topup_state[admin]["uid"]
        name = topup_state[admin]["name"]
        driver_accounts[uid]["balance"] += amount
        update.message.reply_text(f"✅ RM{amount:.2f} added to {name}")
        return ConversationHandler.END
    except:
        update.message.reply_text("❌ Invalid amount.")
        return INPUT_AMOUNT

# === /balance ===
def balance(update, context):
    if update.effective_user.id not in ADMIN_IDS:
        return
    msg = "📊 Driver Balances:\n"
    for uid, acc in driver_accounts.items():
        chat = bot.get_chat(uid)
        name = f"@{chat.username}" if chat.username else chat.first_name
        msg += f"• {name}: RM{acc['balance']:.2f}\n"
    update.message.reply_text(msg)

# === /check ===
def check(update, context):
    if update.effective_user.id not in ADMIN_IDS:
        return
    today = datetime.datetime.now(tz).strftime("%Y-%m-%d")
    msg = "📄 Today's Status:\n"
    for uid, logs in driver_logs.items():
        in_time = logs.get(today, {}).get("in", "❌")
        out_time = logs.get(today, {}).get("out", "❌")
        chat = bot.get_chat(uid)
        name = f"@{chat.username}" if chat.username else chat.first_name
        msg += f"• {name}: IN: {in_time}, OUT: {out_time}\n"
    update.message.reply_text(msg)

# === Webhook ===
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

dispatcher.add_handler(ConversationHandler(
    entry_points=[CommandHandler("topup", topup_start)],
    states={
        SELECT_DRIVER: [MessageHandler(Filters.text & ~Filters.command, topup_choose_driver)],
        INPUT_AMOUNT: [MessageHandler(Filters.text & ~Filters.command, topup_input_amount)],
    },
    fallbacks=[CommandHandler("cancel", cancel)],
))

# === 启动服务 ===
if __name__ == "__main__":
    logger.info("✅ Server started.")
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
