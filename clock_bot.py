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
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
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

# === 错误处理函数 ===
def error_handler(update, context):
    """处理所有未捕获的异常"""
    logger.error("Exception while handling an update:", exc_info=context.error)
    
    # 尝试发送错误消息给用户
    try:
        if update and update.effective_message:
            update.effective_message.reply_text(
                "⚠️ An unexpected error occurred. Please try again later."
            )
    except:
        logger.error("Failed to send error message to user")
    
    # 记录完整的错误信息
    tb_list = traceback.format_exception(None, context.error, context.error.__traceback__)
    tb_string = ''.join(tb_list)
    logger.error(f"Full traceback:\n{tb_string}")

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
            "💵 /topup\n"
            "📷 /viewclaims"
        )

    update.message.reply_text(msg)
    logger.info(f"User {username} started the bot")

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
    now = datetime.datetime.now(tz)  # 修复：ttz -> tz
    today = now.strftime("%Y-%m-%d")
    clock_time = now.strftime("%Y-%m-%d %H:%M:%S")

    # 检查是否已打卡
    if user_id not in driver_logs or today not in driver_logs[user_id] or 'in' not in driver_logs[user_id][today]:
        error_msg = "❌ You haven't clocked in today."
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
        
        # 计算小时和分钟
        hours = int(total_seconds // 3600)
        minutes = int((total_seconds % 3600) // 60)  # 修复：使用整除而不是取模
        
        # 格式化时间字符串
        if hours and minutes:
            time_str = f"{hours} hours {minutes} minutes"
        elif hours:
            time_str = f"{hours} hours"
        else:
            time_str = f"{minutes} minutes"

        # 确保薪资记录存在
        if user_id not in driver_salaries:
            driver_salaries[user_id] = {'total_hours': 0.0, 'daily_log': {}}
        
        # 更新工时
        hours_worked = total_seconds / 3600
        driver_salaries[user_id]['total_hours'] += hours_worked
        driver_salaries[user_id]['daily_log'][today] = hours_worked

        update.message.reply_text(f"🏁 Clocked out at {clock_time}. Worked {time_str}.")
        logger.info(f"User {username} clocked out: worked {time_str}")
    except Exception as e:
        # 记录错误日志
        logger.error(f"Clockout error for user {username}: {str(e)}")
        logger.exception(e)
        
        # 发送错误消息
        update.message.reply_text("⚠️ An error occurred during clockout. Please try again.")

# === /offday ===
def offday(update, context):
    user_id = update.effective_user.id
    username = update.effective_user.username or str(user_id)
    today = datetime.datetime.now(tz).strftime("%Y-%m-%d")
    driver_logs.setdefault(user_id, {})[today] = {"in": "OFF", "out": "OFF"}
    update.message.reply_text(f"📅 Marked {today} as off day.")
    logger.info(f"User {username} marked {today} as off day")

# === /balance（管理员）===
def balance(update, context):
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        return
    
    logger.info(f"Admin {user_id} requested balance")
    
    msg = "📊 Driver Balances:\n"
    for uid, acc in driver_accounts.items():
        try:
            chat = bot.get_chat(uid)
            name = f"@{chat.username}" if chat.username else chat.first_name
            msg += f"• {name}: RM{acc['balance']:.2f}\n"
        except Exception as e:
            logger.error(f"Error getting chat for user {uid}: {str(e)}")
            msg += f"• User {uid}: RM{acc['balance']:.2f}\n"
    
    update.message.reply_text(msg)

# === /check（管理员）===
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
        try:
            chat = bot.get_chat(uid)
            name = f"@{chat.username}" if chat.username else chat.first_name
        except Exception as e:
            logger.error(f"Error getting chat for user {uid}: {str(e)}")
            name = f"User {uid}"
        msg += f"• {name}: IN: {in_time}, OUT: {out_time}\n"
    update.message.reply_text(msg)

# === /viewclaims（管理员）===
def viewclaims(update, context):
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        return update.message.reply_text("❌ You are not an admin.")
    
    msg = "📷 Claim Summary:\n"
    for uid, account in driver_accounts.items():
        claims = account.get("claims", [])
        if not claims:
            continue
        try:
            chat = bot.get_chat(uid)
            name = f"@{chat.username}" if chat.username else chat.first_name
        except:
            name = str(uid)
        
        msg += f"\n🧾 {name}'s Claims:\n"
        for c in claims[-5:]:  # 显示最多 5 条
            msg += f"• {c['date']} - RM{c['amount']} ({c['type']})\n"

    update.message.reply_text(msg)

# === /topup (交互流程管理员专用) ===
def topup_start(update, context):
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        return
    
    logger.info(f"Admin {user_id} started topup process")
    
    keyboard = []
    topup_state[user_id] = {}
    for uid in driver_accounts:
        try:
            chat = bot.get_chat(uid)
            name = f"@{chat.username}" if chat.username else chat.first_name
            keyboard.append([name])
            topup_state[user_id][name] = uid
        except Exception as e:
            logger.error(f"Error getting chat for user {uid}: {str(e)}")
            name = f"User {uid}"
            keyboard.append([name])
            topup_state[user_id][name] = uid

    if not keyboard:
        update.message.reply_text("❌ No drivers found.")
        return ConversationHandler.END

    update.message.reply_text(
        "👤 Select driver to top up:",
        reply_markup=ReplyKeyboardMarkup(keyboard, one_time_keyboard=True)
    )
    return TOPUP_USER

def topup_user(update, context):
    admin_id = update.effective_user.id
    selected = update.message.text.strip()
    
    logger.info(f"Admin {admin_id} selected: {selected}")

    if admin_id not in topup_state or selected not in topup_state[admin_id]:
        update.message.reply_text("❌ Invalid selection.")
        return ConversationHandler.END

    context.user_data["topup_uid"] = topup_state[admin_id][selected]
    update.message.reply_text("💰 Enter amount (RM):", reply_markup=ReplyKeyboardRemove())
    return TOPUP_AMOUNT

def topup_amount(update, context):
    admin_id = update.effective_user.id
    try:
        amount = float(update.message.text.strip())
        uid = context.user_data.get("topup_uid")
        
        if not uid:
            update.message.reply_text("❌ Error: No user selected.")
            return ConversationHandler.END
            
        driver_accounts.setdefault(uid, {"balance": 0.0, "claims": []})
        driver_accounts[uid]["balance"] += amount
        
        try:
            chat = bot.get_chat(uid)
            name = f"@{chat.username}" if chat.username else chat.first_name
        except:
            name = f"User {uid}"
            
        update.message.reply_text(f"✅ Topped up RM{amount:.2f} to {name}.")
        logger.info(f"Admin {admin_id} topped up RM{amount:.2f} to {name}")
    except ValueError:
        update.message.reply_text("❌ Invalid amount. Please enter a number.")
        return TOPUP_AMOUNT
    except Exception as e:
        logger.error(f"Topup error: {str(e)}")
        update.message.reply_text("❌ An error occurred during topup.")
    return ConversationHandler.END

# === /claim 分阶段 ===
def claim_start(update, context):
    user_id = update.effective_user.id
    username = update.effective_user.username or str(user_id)
    
    logger.info(f"User {username} started claim process")
    
    keyboard = [["toll", "petrol", "other"]]
    update.message.reply_text(
        "🚗 Select claim type:",
        reply_markup=ReplyKeyboardMarkup(keyboard, one_time_keyboard=True)
    )
    return CLAIM_TYPE

def claim_type(update, context):
    user_id = update.effective_user.id
    username = update.effective_user.username or str(user_id)
    text = update.message.text.lower()
    claim_state[user_id] = {"type": text}
    
    logger.info(f"User {username} selected claim type: {text}")
    
    if text == "other":
        update.message.reply_text("✍️ Please enter the type description:")
        return CLAIM_OTHER_TYPE
    update.message.reply_text("💰 Enter amount:")
    return CLAIM_AMOUNT

def claim_other_type(update, context):
    user_id = update.effective_user.id
    username = update.effective_user.username or str(user_id)
    claim_state[user_id]["type"] = update.message.text
    
    logger.info(f"User {username} entered custom claim type: {update.message.text}")
    
    update.message.reply_text("💰 Enter amount:")
    return CLAIM_AMOUNT

def claim_amount(update, context):
    user_id = update.effective_user.id
    username = update.effective_user.username or str(user_id)
    try:
        amount = float(update.message.text)
        claim_state[user_id]["amount"] = amount
        
        logger.info(f"User {username} entered claim amount: {amount}")
        
        update.message.reply_text("📎 Now send proof photo:")
        return CLAIM_PROOF
    except ValueError:
        update.message.reply_text("❌ Please enter a valid number.")
        return CLAIM_AMOUNT
    except Exception as e:
        logger.error(f"Claim amount error: {str(e)}")
        update.message.reply_text("❌ An error occurred.")
        return CLAIM_AMOUNT

def claim_proof(update, context):
    user_id = update.effective_user.id
    username = update.effective_user.username or str(user_id)

    # 提取照片 file_id
    file_id = update.message.photo[-1].file_id
    date = datetime.datetime.now(tz).strftime("%Y-%m-%d")

    entry = {
        "amount": claim_state[user_id]["amount"],
        "type": claim_state[user_id]["type"],
        "date": date,
        "photo": file_id  # 只保存 file_id，后续 PDF 会用到
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
    
    update.message.reply_text("❌ Operation cancelled.")
    
    # 清理状态
    if user_id in claim_state:
        del claim_state[user_id]
    if user_id in topup_state:
        del topup_state[user_id]
    
    logger.info(f"User {username} cancelled operation")
    
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
dispatcher.add_handler(CommandHandler("viewclaims", viewclaims))

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

# === 注册错误处理器 ===
dispatcher.add_error_handler(error_handler)

# === Run ===
if __name__ == "__main__":
    logger.info("Bot server started.")
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
