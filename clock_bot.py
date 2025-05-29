from flask import Flask, request
from telegram import Bot, Update, ReplyKeyboardMarkup
from telegram.ext import Dispatcher, CommandHandler, MessageHandler, Filters, ConversationHandler
import datetime
import pytz
import os
import logging  # 添加日志模块
import traceback  # 添加异常追踪模块

app = Flask(__name__)

# === 配置 ===
TOKEN = os.environ.get("TOKEN")
ADMIN_IDS = [1165249082]  # 管理员 Telegram 用户 ID，可添加多个

# 初始化日志系统
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

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
    
    logger.info(f"User {user_id} started the bot")

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
    username = update.effective_user.username or str(user_id)
    now = datetime.datetime.now(tz)
    today = now.strftime("%Y-%m-%d")
    clock_time = now.strftime("%Y-%m-%d %H:%M:%S")

    driver_logs.setdefault(user_id, {}).setdefault(today, {})['in'] = clock_time
    update.message.reply_text(f"✅ Clocked in at {clock_time}")
    logger.info(f"User {username} clocked in at {clock_time}")

# === /clockout ===
def clockout(update, context):
    user_id = update.effective_user.id
    username = update.effective_user.username or str(user_id)
    now = datetime.datetime.now(tz)
    today = now.strftime("%Y-%m-%d")
    clock_time = now.strftime("%Y-%m-%d %H:%M:%S")
    
    # 记录调试信息
    debug_info = [
        f"User: {username}",
        f"Current time: {now} ({now.tzinfo})",
        f"Driver logs exists: {user_id in driver_logs}",
        f"Today exists: {today in driver_logs.get(user_id, {})}",
        f"In time exists: {'in' in driver_logs.get(user_id, {}).get(today, {})}"
    ]
    
    # 检查打卡状态
    if user_id not in driver_logs or today not in driver_logs[user_id] or 'in' not in driver_logs[user_id][today]:
        error_msg = "❌ You haven't clocked in today."
        debug_info.append(error_msg)
        logger.warning(error_msg)
        update.message.reply_text(error_msg)
        return
    
    try:
        # 保存打卡时间
        driver_logs[user_id][today]['out'] = clock_time
        
        # 获取打卡时间并解析
        in_time_str = driver_logs[user_id][today]['in']
        
        # 解析时间字符串为无时区对象
        naive_in_time = datetime.datetime.strptime(in_time_str, "%Y-%m-%d %H:%M:%S")
        
        # 将当前时间转换为无时区对象（同一时区）
        now_naive = now.replace(tzinfo=None)
        
        # 计算时间差
        duration = now_naive - naive_in_time
        total_seconds = duration.total_seconds()
        
        # 确保时间差为正数
        if total_seconds < 0:
            logger.warning(f"Negative time difference detected: {total_seconds} seconds")
            total_seconds = abs(total_seconds)
        
        # 简单的时间差计算
        if total_seconds < 60:  # 少于1分钟
            time_str = f"{int(total_seconds)} seconds"
        elif total_seconds < 3600:  # 少于1小时
            minutes = int(total_seconds // 60)
            seconds = int(total_seconds % 60)
            time_str = f"{minutes} minutes {seconds} seconds"
        else:
            hours = int(total_seconds // 3600)
            minutes = int((total_seconds % 3600) // 60)
            time_str = f"{hours} hours {minutes} minutes"
        
        # 更新薪资记录
        if user_id not in driver_salaries:
            driver_salaries[user_id] = {'total_hours': 0.0, 'daily_log': {}}
        
        hours_worked = total_seconds / 3600
        driver_salaries[user_id]['total_hours'] += hours_worked
        driver_salaries[user_id]['daily_log'][today] = hours_worked
        
        # 发送成功消息
        response = f"🏁 Clocked out at {clock_time}. Worked {time_str}."
        update.message.reply_text(response)
        logger.info(f"User {username} clocked out: {response}")
        
    except Exception as e:
        # 记录错误日志
        error_trace = traceback.format_exc()
        logger.error(f"Clockout error for user {username}: {str(e)}")
        logger.error(f"Debug info: {' | '.join(debug_info)}")
        logger.error(error_trace)
        
        # 发送简洁的错误消息给用户
        update.message.reply_text("⚠️ An error occurred during clockout. Please try again.")

# === /offday ===
def offday(update, context):
    user_id = update.effective_user.id
    username = update.effective_user.username or str(user_id)
    today = datetime.datetime.now(tz).strftime("%Y-%m-%d")
    driver_logs.setdefault(user_id, {})[today] = {"in": "OFF", "out": "OFF"}
    update.message.reply_text(f"📅 Marked {today} as off day.")
    logger.info(f"User {username} marked {today} as off day")

# === /balance（仅管理员）===
def balance(update, context):
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        return
    
    logger.info(f"Admin {user_id} requested balance")
    
    msg = "📊 Driver Balances:\n"
    for uid, acc in driver_accounts.items():
        chat = bot.get_chat(uid)
        name = f"@{chat.username}" if chat.username else chat.first_name
        msg += f"• {name}: RM{acc['balance']:.2f}\n"
    update.message.reply_text(msg)

# === /check（仅管理员）===
def check(update, context):
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        return
    
    logger.info(f"Admin {user_id} requested check")
    
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
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        return
    
    try:
        uid = int(context.args[0])
        amount = float(context.args[1])
        driver_accounts.setdefault(uid, {"balance": 0.0, "claims": []})["balance"] += amount
        
        # 获取用户名用于日志
        try:
            chat = bot.get_chat(uid)
            username = f"@{chat.username}" if chat.username else chat.first_name
        except:
            username = str(uid)
        
        update.message.reply_text(f"✅ Added RM{amount:.2f} to user {uid}.")
        logger.info(f"Admin {user_id} topped up RM{amount:.2f} to user {username}")
    except Exception as e:
        update.message.reply_text("❌ Usage: /topup <user_id> <amount>")
        logger.error(f"Topup error: {str(e)}")

# === /claim 分阶段 ===
def claim_start(update, context):
    user_id = update.effective_user.id
    username = update.effective_user.username or str(user_id)
    
    logger.info(f"User {username} started claim process")
    
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
    
    logger.info(f"User {user_id} selected claim type: {text}")
    
    if text == "other":
        update.message.reply_text("✍️ Please enter the type description:")
        return CLAIM_OTHER_TYPE
    update.message.reply_text("💰 Enter amount (number):")
    return CLAIM_AMOUNT

def claim_other_type(update, context):
    user_id = update.effective_user.id
    claim_state[user_id]["type"] = update.message.text
    
    logger.info(f"User {user_id} entered custom claim type: {update.message.text}")
    
    update.message.reply_text("💰 Enter amount (number):")
    return CLAIM_AMOUNT

def claim_amount(update, context):
    user_id = update.effective_user.id
    try:
        amount = float(update.message.text)
        claim_state[user_id]["amount"] = amount
        
        logger.info(f"User {user_id} entered claim amount: {amount}")
        
        update.message.reply_text("📎 Now send the proof photo:")
        return CLAIM_PROOF
    except:
        update.message.reply_text("❌ Please enter a valid number.")
        return CLAIM_AMOUNT

def claim_proof(update, context):
    user_id = update.effective_user.id
    username = update.effective_user.username or str(user_id)
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

    response = f"✅ RM{entry['amount']} claimed for {entry['type']} on {entry['date']}."
    update.message.reply_text(response)
    
    logger.info(f"User {username} completed claim: {response}")
    
    return ConversationHandler.END

def cancel(update, context):
    user_id = update.effective_user.id
    username = update.effective_user.username or str(user_id)
    
    update.message.reply_text("❌ Claim cancelled.")
    logger.info(f"User {username} cancelled claim process")
    
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

# === 启动服务器 ===
if __name__ == "__main__":
    logger.info("Starting bot server...")
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
