# clock_bot.py

from flask import Flask, request
from telegram import Bot, Update
from telegram.ext import Dispatcher, CommandHandler
import datetime
import os

# === 初始化 Flask 和 Bot ===
app = Flask(__name__)
TOKEN = os.environ['TOKEN']
ADMIN_IDS = [1165249082]  # 支持多个管理员

bot = Bot(token=TOKEN)
dispatcher = Dispatcher(bot, None, use_context=True)

# === 数据结构初始化 ===
driver_logs = {}      # {user_id: {"YYYY-MM-DD": {"in": time, "out": time}}}
driver_salaries = {}  # {user_id: {"total_hours": float, "daily_log": {date: hours}}}
driver_accounts = {}  # {user_id: {"balance": float, "claims": [{"amount": float, "type": str, "date": str}] }}

# === /start ===
def start(update, context):
    user = update.effective_user.first_name
    user_id = update.effective_user.id

    driver_logs.setdefault(user_id, {})
    driver_salaries.setdefault(user_id, {"total_hours": 0.0, "daily_log": {}})
    driver_accounts.setdefault(user_id, {"balance": 3000.0, "claims": []})

    msg = (
        f"\U0001F44B Hello {user}!\n"
        "Welcome to Driver ClockIn Bot.\n\n"
        "Available Commands:\n"
        "\U0001F551 /clockin - Start work\n"
        "\U0001F3C1 /clockout - End work\n"
        "\U0001F4C5 /offday - Mark off day\n"
        "\U0001F4B8 /claim <amount> <reason> - Deduct from travel fund"
    )

    if user_id in ADMIN_IDS:
        msg += (
            "\n\n\U0001F510 Admin Commands:\n"
            "\U0001F4CA /balance - View all balances\n"
            "\U0001F4C4 /check - View all drivers' status\n"
            "\U0001F9FE /PDF - Generate monthly report\n"
            "\U0001F4B5 /topup <user_id> <amount> - Top up driver balance"
        )

    update.message.reply_text(msg)

# === /clockin ===
def clockin(update, context):
    user_id = update.effective_user.id
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    today = datetime.datetime.now().strftime("%Y-%m-%d")

    driver_logs.setdefault(user_id, {}).setdefault(today, {})['in'] = now
    update.message.reply_text(f"\u2705 Clock-in at {now}")

# === /topup ===
def topup(update, context):
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        update.message.reply_text("Unauthorized.")
        return

    try:
        target_id = int(context.args[0])
        amount = float(context.args[1])
    except (IndexError, ValueError):
        update.message.reply_text("Usage: /topup <user_id> <amount>")
        return

    driver_accounts.setdefault(target_id, {"balance": 3000.0, "claims": []})
    driver_accounts[target_id]['balance'] += amount
    update.message.reply_text(f"✅ Topped up RM{amount:.2f} to user {target_id}. New balance: RM{driver_accounts[target_id]['balance']:.2f}")

# === 注册指令 ===
dispatcher.add_handler(CommandHandler("start", start))
dispatcher.add_handler(CommandHandler("clockin", clockin))
dispatcher.add_handler(CommandHandler("topup", topup))

# === Webhook 接收 ===
@app.route(f"/{TOKEN}", methods=["POST"])
def webhook():
    update = Update.de_json(request.get_json(force=True), bot)
    dispatcher.process_update(update)
    return "ok"

# === 默认首页 ===
@app.route("/")
def home():
    return "Driver ClockBot is running."

if __name__ == "__main__":
    app.run(port=8443)
