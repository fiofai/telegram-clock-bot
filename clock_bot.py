# 导入 Flask 和 Telegram Bot 所需模块
from flask import Flask, request
from telegram import Bot, Update
from telegram.ext import Dispatcher, CommandHandler
import datetime

# 替换成你从 BotFather 获得的 Bot Token
TOKEN = 8048874124:AAEui2d17usur6oSQmsjjdN0IiSmngjO4mA

# 创建 Flask 应用
app = Flask(__name__)

# 创建 Telegram Bot 实例
bot = Bot(token=TOKEN)

# 创建 Dispatcher 来处理指令（不使用 updater）
dispatcher = Dispatcher(bot, None, use_context=True)

# 简单用一个字典存用户打卡记录（可升级为数据库）
user_logs = {}

# 上班打卡指令
def clockin(update, context):
    user = update.effective_user.first_name  # 用户名
    now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    user_logs.setdefault(update.effective_user.id, {})['in'] = now
    update.message.reply_text(f"{user} ✅ 上班打卡成功：{now}")

# 下班打卡指令
def clockout(update, context):
    user = update.effective_user.first_name
    now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    user_logs.setdefault(update.effective_user.id, {})['out'] = now
    update.message.reply_text(f"{user} 🏁 下班打卡成功：{now}")

# 注册两个命令处理器
dispatcher.add_handler(CommandHandler("clockin", clockin))
dispatcher.add_handler(CommandHandler("clockout", clockout))

# 接收 Telegram 的 Webhook POST 请求
@app.route(f'/{TOKEN}', methods=['POST'])
def webhook():
    update = Update.de_json(request.get_json(force=True), bot)
    dispatcher.process_update(update)
    return 'ok'

# 默认首页
@app.route('/')
def index():
    return 'Bot 正在运行中'

# Flask 启动（Render 会调用）
if __name__ == '__main__':
    app.run(port=8443)
