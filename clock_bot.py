# clock_bot.py

# === 导入所需模块 ===
from flask import Flask, request
from telegram import Bot, Update
from telegram.ext import Dispatcher, CommandHandler
import datetime
import os

# === 从环境变量中读取你的 Telegram Bot Token ===
TOKEN = os.environ['TOKEN']

# === 创建 Flask 应用 ===
app = Flask(__name__)

# === 创建 Telegram Bot 实例与 Dispatcher ===
bot = Bot(token=TOKEN)
dispatcher = Dispatcher(bot, None, use_context=True)

# === 用于存储用户打卡数据的简单内存字典（可扩展为数据库） ===
user_logs = {}

# === /start 指令：欢迎介绍 ===
def start(update, context):
    welcome_msg = (
        "👋 Welcome to ClockInOut Bot!\n\n"
        "Use the following commands:\n\n"
        "✅ /clockin — Clock in (start work)\n"
        "🏁 /clockout — Clock out (end work)\n\n"
        "This bot helps you track your working hours.\n\n"
        "📊 Features coming soon:\n"
        "- Daily and monthly reports\n"
        "- Salary calculation\n"
        "- Export to CSV\n\n"
        "Developed by Fio."
    )
    update.message.reply_text(welcome_msg)

# === /clockin 指令：上班打卡 ===
def clockin(update, context):
    user = update.effective_user.first_name
    now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    user_logs.setdefault(update.effective_user.id, {})['in'] = now
    update.message.reply_text(f"{user} ✅ Clock-in successful at {now}")

# === /clockout 指令：下班打卡 ===
def clockout(update, context):
    user = update.effective_user.first_name
    now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    user_logs.setdefault(update.effective_user.id, {})['out'] = now
    update.message.reply_text(f"{user} 🏁 Clock-out successful at {now}")

# === 注册以上指令到 Dispatcher ===
dispatcher.add_handler(CommandHandler("start", start))
dispatcher.add_handler(CommandHandler("clockin", clockin))
dispatcher.add_handler(CommandHandler("clockout", clockout))

# === Telegram Webhook 接口（POST）===
@app.route(f'/{TOKEN}', methods=['POST'])
def webhook():
    update = Update.de_json(request.get_json(force=True), bot)
    dispatcher.process_update(update)
    return 'ok'

# === 默认首页路由（GET）===
@app.route('/')
def index():
    return 'Bot is running.'

# === 启动 Flask（Render 会自动调用）===
if __name__ == '__main__':
    app.run(port=8443)
