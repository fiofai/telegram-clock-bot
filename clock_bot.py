# clock_bot.py

from flask import Flask, request
from telegram import Bot, Update, ReplyKeyboardMarkup
from telegram.ext import Dispatcher, CommandHandler, MessageHandler, Filters, ConversationHandler
import os
import datetime

app = Flask(__name__)
TOKEN = os.environ['TOKEN']
bot = Bot(token=TOKEN)
dispatcher = Dispatcher(bot, None, use_context=True)

# 支持多个管理员 ID
ADMIN_IDS = [1165249082]

# 所有司机的打卡/余额/报销记录
driver_logs = {}
driver_salaries = {}
driver_accounts = {}

# 报销流程状态码
CLAIM_TYPE, CLAIM_OTHER, CLAIM_AMOUNT, CLAIM_PROOF = range(4)

# /start
def start(update, context):
    user_id = update.effective_user.id
    name = update.effective_user.first_name

    driver_logs.setdefault(user_id, {})
    driver_salaries.setdefault(user_id, {"total_hours": 0.0, "daily_log": {}})
    driver_accounts.setdefault(user_id, {"balance": 0.0, "claims": []})

    msg = (
        f"👋 Hello {name}!\n"
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

# /clockin
def clockin(update, context):
    user_id = update.effective_user.id
    now = datetime.datetime.now()
    date = now.strftime("%Y-%m-%d")
    driver_logs.setdefault(user_id, {}).setdefault(date, {})["in"] = now.strftime("%Y-%m-%d %H:%M:%S")
    update.message.reply_text(f"✅ Clocked in at {now.strftime('%H:%M:%S')}")

# /clockout
def clockout(update, context):
    user_id = update.effective_user.id
    now = datetime.datetime.now()
    date = now.strftime("%Y-%m-%d")
    log = driver_logs.setdefault(user_id, {}).setdefault(date, {})
    log["out"] = now.strftime("%Y-%m-%d %H:%M:%S")

    if "in" in log:
        time_in = datetime.datetime.strptime(log["in"], "%Y-%m-%d %H:%M:%S")
        worked = (now - time_in).total_seconds() / 3600.0
        salary = driver_salaries[user_id]
        salary["total_hours"] += worked
        salary["daily_log"][date] = worked
        update.message.reply_text(f"🏁 Clocked out at {now.strftime('%H:%M:%S')}. Worked {worked:.2f} hours.")
    else:
        update.message.reply_text("⚠️ You didn't clock in today.")

# /topup
def topup(update, context):
    if update.effective_user.id not in ADMIN_IDS:
        return update.message.reply_text("❌ You are not authorized.")
    if len(context.args) != 2:
        return update.message.reply_text("Usage: /topup <user_id> <amount>")
    try:
        target = int(context.args[0])
        amount = float(context.args[1])
        driver_accounts.setdefault(target, {"balance": 0.0, "claims": []})
        driver_accounts[target]["balance"] += amount
        update.message.reply_text(f"✅ Topped up RM{amount:.2f} to user {target}")
    except:
        update.message.reply_text("❌ Invalid input.")

# /balance
def balance(update, context):
    if update.effective_user.id not in ADMIN_IDS:
        return update.message.reply_text("❌ You are not authorized.")
    msg = "📊 Driver Balances:\n"
for uid, acc in driver_accounts.items():
    user_obj = bot.get_chat(uid)
    name = f"@{user_obj.username}" if user_obj.username else user_obj.first_name
    msg += f"• {name}: RM{acc['balance']:.2f}\n"
    update.message.reply_text(msg)

# /check
def check(update, context):
    if update.effective_user.id not in ADMIN_IDS:
        return update.message.reply_text("❌ You are not authorized.")
    date = datetime.datetime.now().strftime("%Y-%m-%d")
    msg = "📄 Today's Status:\n"
    for uid, log in driver_logs.items():
        today = log.get(date, {})
        in_time = today.get("in", "❌")
        out_time = today.get("out", "❌")

         # 获取用户名或昵称（优先 username）
    user_obj = bot.get_chat(uid)
    name = f"@{user_obj.username}" if user_obj.username else user_obj.first_name
    
    msg += f"• {name}: IN: {in_time}, OUT: {out_time}\n"
    update.message.reply_text(msg)

# /claim 引导
def claim_start(update, context):
    reply = ReplyKeyboardMarkup([["toll", "petrol", "other"]], one_time_keyboard=True, resize_keyboard=True)
    update.message.reply_text("💬 Select claim type:", reply_markup=reply)
    return CLAIM_TYPE

def claim_type(update, context):
    if update.message.text == "other":
        update.message.reply_text("🔤 Enter type:")
        return CLAIM_OTHER
    context.user_data["type"] = update.message.text
    update.message.reply_text("💰 Enter amount:")
    return CLAIM_AMOUNT

def claim_other(update, context):
    context.user_data["type"] = update.message.text
    update.message.reply_text("💰 Enter amount:")
    return CLAIM_AMOUNT

def claim_amount(update, context):
    try:
        amt = float(update.message.text)
        context.user_data["amount"] = amt
        update.message.reply_text("📎 Please upload a photo (receipt/proof):")
        return CLAIM_PROOF
    except:
        update.message.reply_text("❌ Please enter a valid number.")
        return CLAIM_AMOUNT

def claim_proof(update, context):
    user_id = update.effective_user.id
    claim = {
        "type": context.user_data["type"],
        "amount": context.user_data["amount"],
        "proof": update.message.photo[-1].file_id,
        "date": datetime.datetime.now().strftime("%Y-%m-%d")
    }
    driver_accounts[user_id]["claims"].append(claim)
    driver_accounts[user_id]["balance"] -= claim["amount"]
    update.message.reply_text("✅ Claim recorded.")
    return ConversationHandler.END

def cancel(update, context):
    update.message.reply_text("❌ Claim cancelled.")
    return ConversationHandler.END

# 注册指令
dispatcher.add_handler(CommandHandler("start", start))
dispatcher.add_handler(CommandHandler("clockin", clockin))
dispatcher.add_handler(CommandHandler("clockout", clockout))
dispatcher.add_handler(CommandHandler("topup", topup))
dispatcher.add_handler(CommandHandler("balance", balance))
dispatcher.add_handler(CommandHandler("check", check))

conv_handler = ConversationHandler(
    entry_points=[CommandHandler("claim", claim_start)],
    states={
        CLAIM_TYPE: [MessageHandler(Filters.text & ~Filters.command, claim_type)],
        CLAIM_OTHER: [MessageHandler(Filters.text & ~Filters.command, claim_other)],
        CLAIM_AMOUNT: [MessageHandler(Filters.text & ~Filters.command, claim_amount)],
        CLAIM_PROOF: [MessageHandler(Filters.photo, claim_proof)],
    },
    fallbacks=[CommandHandler("cancel", cancel)]
)
dispatcher.add_handler(conv_handler)

@app.route(f"/{TOKEN}", methods=["POST"])
def webhook():
    update = Update.de_json(request.get_json(force=True), bot)
    dispatcher.process_update(update)
    return "OK"

