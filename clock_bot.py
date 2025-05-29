# clock_bot.py

from flask import Flask, request
from telegram import Bot, Update, InputFile
from telegram.ext import Dispatcher, CommandHandler, MessageHandler, Filters
import datetime
import os

app = Flask(__name__)

# 从 Render 环境变量中读取 Telegram Token
TOKEN = os.environ['TOKEN']
ADMIN_ID = 123456789  # 替换成你的 Telegram user ID

bot = Bot(token=TOKEN)
dispatcher = Dispatcher(bot, None, use_context=True)

# 存储结构：打卡数据 + 司机账户余额 + 扣款记录
driver_logs = {}      # {user_id: {"YYYY-MM-DD": {"in": time, "out": time}}}
driver_salaries = {}  # {user_id: {"total_hours": float, "daily_log": {date: hours}}}
driver_accounts = {}  # {user_id: {"balance": float, "claims": [{"amount": float, "type": str, "date": str}] }}

# === 🚚 命令：/start ===
def start(update, context):
    user = update.effective_user.first_name
    user_id = update.effective_user.id

    # 初始化数据结构
    driver_logs.setdefault(user_id, {})
    driver_salaries.setdefault(user_id, {"total_hours": 0.0, "daily_log": {}})
    driver_accounts.setdefault(user_id, {"balance": 3000.0, "claims": []})

    msg = (
        f"👋 Hello {user}!\n"
        "Welcome to Driver ClockIn Bot.\n\n"
        "Available Commands:\n"
        "🕓 /clockin - Start work\n"
        "🏁 /clockout - End work\n"
        "📅 /offday - Mark off day\n"
        "💸 /claim <amount> <reason> - Deduct from travel fund\n"
        "📊 Admin: /balance /PDF /check"
    )
    update.message.reply_text(msg)

# === ⏱️ /clockin ===
def clockin(update, context):
    user_id = update.effective_user.id
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    today = datetime.datetime.now().strftime("%Y-%m-%d")

    driver_logs.setdefault(user_id, {}).setdefault(today, {})['in'] = now
    update.message.reply_text(f"✅ Clock-in at {now}")

# === 🏁 /clockout ===
def clockout(update, context):
    user_id = update.effective_user.id
    today = datetime.datetime.now().strftime("%Y-%m-%d")
    now = datetime.datetime.now()

    logs = driver_logs.get(user_id, {}).get(today, {})
    if 'in' not in logs:
        update.message.reply_text("❗ You haven't clocked in today.")
        return

    # 记录 clockout 时间
    logs['out'] = now.strftime("%Y-%m-%d %H:%M:%S")
    in_time = datetime.datetime.strptime(logs['in'], "%Y-%m-%d %H:%M:%S")
    hours = round((now - in_time).total_seconds() / 3600, 2)

    # 工资计算：1小时 = RM20.83
    salary = round(hours * 20.83, 2)
    driver_salaries[user_id]["total_hours"] += hours
    driver_salaries[user_id]["daily_log"][today] = hours

    update.message.reply_text(
        f"🏁 Clock-out at {logs['out']}\n"
        f"🕒 Worked: {hours} hours\n"
        f"💰 Earned: RM{salary}"
    )

# === 📅 /offday ===
def offday(update, context):
    today = datetime.datetime.now().strftime("%Y-%m-%d")
    user_id = update.effective_user.id
    driver_logs.setdefault(user_id, {}).setdefault(today, {})['off'] = True
    update.message.reply_text(f"🛌 Off day recorded for {today}")

# === 💸 /claim <amount> <type> ===
def claim(update, context):
    user_id = update.effective_user.id
    args = context.args

    if len(args) < 2:
        update.message.reply_text("Usage: /claim <amount> <type>")
        return

    try:
        amount = float(args[0])
        claim_type = " ".join(args[1:])
        today = datetime.datetime.now().strftime("%Y-%m-%d")

        # 扣除金额
        acc = driver_accounts.setdefault(user_id, {"balance": 3000.0, "claims": []})
        acc["balance"] -= amount
        acc["claims"].append({"amount": amount, "type": claim_type, "date": today})

        update.message.reply_text(
            f"🧾 Claimed RM{amount} for {claim_type}\n"
            f"💰 Remaining Balance: RM{acc['balance']:.2f}"
        )
    except:
        update.message.reply_text("Invalid amount.")

# === 📊 /balance（管理员可查所有司机余额）===
def balance(update, context):
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        update.message.reply_text("Unauthorized.")
        return

    msg = "🧾 Driver Balances:\n"
    for uid, data in driver_accounts.items():
        user = bot.get_chat(uid).first_name
        msg += f"👤 {user} - RM{data['balance']:.2f}\n"
    update.message.reply_text(msg)

# === 🧾 /check 显示所有司机今天状态 ===
def check(update, context):
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        update.message.reply_text("Unauthorized.")
        return

    today = datetime.datetime.now().strftime("%Y-%m-%d")
    msg = f"📋 Driver Status for {today}:\n"

    for uid, logs in driver_logs.items():
        user = bot.get_chat(uid).first_name
        entry = logs.get(today, {})
        if 'in' in entry and 'out' in entry:
            msg += f"✅ {user} clocked in & out\n"
        elif 'in' in entry:
            msg += f"🕓 {user} clocked in only\n"
        elif 'off' in entry:
            msg += f"🛌 {user} took off\n"
        else:
            msg += f"❌ {user} no record\n"
    update.message.reply_text(msg)

# === PDF 生成预留（下一阶段）===
def pdf(update, context):
    update.message.reply_text("📄 PDF report generation is coming soon!")

# === 注册指令 ===
dispatcher.add_handler(CommandHandler("start", start))
dispatcher.add_handler(CommandHandler("clockin", clockin))
dispatcher.add_handler(CommandHandler("clockout", clockout))
dispatcher.add_handler(CommandHandler("offday", offday))
dispatcher.add_handler(CommandHandler("claim", claim))
dispatcher.add_handler(CommandHandler("balance", balance))
dispatcher.add_handler(CommandHandler("check", check))
dispatcher.add_handler(CommandHandler("pdf", pdf))

# === Webhook 路由 ===
@app.route(f"/{TOKEN}", methods=["POST"])
def webhook():
    update = Update.de_json(request.get_json(force=True), bot)
    dispatcher.process_update(update)
    return "ok"

@app.route("/")
def home():
    return "Driver ClockBot is running."

# === 本地测试端口（Render 会自动识别） ===
if __name__ == "__main__":
    app.run(port=8443)

