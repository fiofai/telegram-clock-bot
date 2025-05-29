from flask import Flask, request
from telegram import (
    Bot, Update, ReplyKeyboardMarkup, ReplyKeyboardRemove, BotCommand
)
from telegram.ext import (
    Dispatcher, CommandHandler, MessageHandler, Filters, ConversationHandler
)
import datetime
import pytz
import os
import logging
import traceback
import telegram

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
fired_drivers = set()  # 存储已被解雇的司机ID

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

# === 设置菜单命令 ===
def set_user_commands(update, context):
    """根据用户权限设置菜单命令"""
    user_id = update.effective_user.id
    
    if user_id in ADMIN_IDS:
        # 管理员命令
        commands = [
            BotCommand("clockin", "Clock in to start work"),
            BotCommand("clockout", "Clock out to end work"),
            BotCommand("offday", "Mark today as off day"),
            BotCommand("claim", "Submit a claim for travel fund"),
            BotCommand("help", "Show help information"),
            BotCommand("balance", "View all balances (Admin)"),
            BotCommand("check", "View all drivers' status (Admin)"),
            BotCommand("topup", "Top up driver balance (Admin)"),
            BotCommand("viewclaims", "View submitted claims (Admin)")
        ]
    else:
        # 普通用户命令
        commands = [
            BotCommand("clockin", "Clock in to start work"),
            BotCommand("clockout", "Clock out to end work"),
            BotCommand("offday", "Mark today as off day"),
            BotCommand("claim", "Submit a claim for travel fund"),
            BotCommand("help", "Show help information")
        ]
    
    try:
        context.bot.set_my_commands(commands)
        logger.info(f"Commands set for user {user_id}")
    except telegram.error.BadRequest as e:
        logger.error(f"Bad request when setting commands: {str(e)}")
    except Exception as e:
        logger.error(f"Error setting commands: {str(e)}")

# === /start ===
def start(update, context):
    user = update.effective_user
    user_id = user.id
    username = user.username or str(user_id)
    
    # 检查是否已被解雇
    if user_id in fired_drivers:
        update.message.reply_text("🚫 You have been fired and no longer have access to this bot.")
        return
    
    # 初始化数据结构
    driver_logs.setdefault(user_id, {})
    driver_salaries.setdefault(user_id, {"total_hours": 0.0, "daily_log": {}})
    driver_accounts.setdefault(user_id, {"balance": 0.0, "claims": []})
    
    # 设置用户菜单命令
    set_user_commands(update, context)
    
    # 构建欢迎消息
    msg = (
        f"👋 Hello {user.first_name}!\n"
        "Welcome to Driver ClockIn Bot.\n\n"
        "Available Commands:\n"
        "🕑 /clockin - Start work\n"
        "🏁 /clockout - End work\n"
        "📅 /offday - Mark off day\n"
        "💸 /claim - Deduct from travel fund\n"
        "❓ /help - Show help information"
    )

    if user_id in ADMIN_IDS:
        msg += (
            "\n\n🔐 Admin Commands:\n"
            "📊 /balance - View all balances\n"
            "📄 /check - View all drivers' status\n"
            "💵 /topup - Top up driver balance\n"
            "📷 /viewclaims - View submitted claims"
        )

    update.message.reply_text(msg)
    logger.info(f"User {username} started the bot")

# === /help ===
def help_command(update, context):
    user_id = update.effective_user.id
    
    # 检查是否已被解雇
    if user_id in fired_drivers:
        update.message.reply_text("🚫 You have been fired and no longer have access to this bot.")
        return
    
    # 根据用户权限显示不同的帮助信息
    if user_id in ADMIN_IDS:
        msg = (
            "🆘 Admin Help:\n\n"
            "Basic Commands:\n"
            "🕑 /clockin - Clock in to start work\n"
            "🏁 /clockout - Clock out to end work\n"
            "📅 /offday - Mark today as off day\n"
            "💸 /claim - Submit a travel fund claim\n\n"
            "Admin Commands:\n"
            "📊 /balance - View all driver balances\n"
            "📄 /check - View current driver status\n"
            "💵 /topup - Add funds to a driver's account\n"
            "📷 /viewclaims - View all submitted claims\n\n"
            "Type any command for more details."
        )
    else:
        msg = (
            "🆘 User Help:\n\n"
            "Available Commands:\n"
            "🕑 /clockin - Clock in to start work\n"
            "🏁 /clockout - Clock out to end work\n"
            "📅 /offday - Mark today as off day\n"
            "💸 /claim - Submit a travel fund claim\n\n"
            "Type any command for more details."
        )
    
    update.message.reply_text(msg)

# === /clockin ===
def clockin(update, context):
    user_id = update.effective_user.id
    
    # 检查是否已被解雇
    if user_id in fired_drivers:
        update.message.reply_text("🚫 You have been fired and no longer have access to this bot.")
        return
    
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
    
    # 检查是否已被解雇
    if user_id in fired_drivers:
        update.message.reply_text("🚫 You have been fired and no longer have access to this bot.")
        return
    
    username = update.effective_user.username or str(user_id)
    now = datetime.datetime.now(tz)
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
        minutes = int((total_seconds % 3600) // 60)
        
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
    
    # 检查是否已被解雇
    if user_id in fired_drivers:
        update.message.reply_text("🚫 You have been fired and no longer have access to this bot.")
        return
    
    username = update.effective_user.username or str(user_id)
    today = datetime.datetime.now(tz).strftime("%Y-%m-%d")
    driver_logs.setdefault(user_id, {})[today] = {"in": "OFF", "out": "OFF"}
    update.message.reply_text(f"📅 Marked {today} as off day.")
    logger.info(f"User {username} marked {today} as off day")

# === /balance（管理员）===
def balance(update, context):
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        update.message.reply_text("❌ You are not authorized to use this command.")
        return
    
    logger.info(f"Admin {user_id} requested balance")
    
    msg = "📊 Driver Balances:\n"
    for uid, acc in driver_accounts.items():
        # 跳过已解雇的司机
        if uid in fired_drivers:
            continue
            
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
        update.message.reply_text("❌ You are not authorized to use this command.")
        return
    
    logger.info(f"Admin {user_id} requested check")
    
    today = datetime.datetime.now(tz).strftime("%Y-%m-%d")
    msg = "📄 Today's Status:\n"
    for uid, log in driver_logs.items():
        # 跳过已解雇的司机
        if uid in fired_drivers:
            continue
            
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

# === /viewclaims（仅管理员）===
def viewclaims(update, context):
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        update.message.reply_text("❌ You are not authorized to use this command.")
        return

    any_claims = False
    for uid, acc in driver_accounts.items():
        # 跳过已解雇的司机
        if uid in fired_drivers:
            continue
            
        claims = acc.get("claims", [])
        if not claims:
            continue

        any_claims = True
        try:
            chat = bot.get_chat(uid)
            username = f"@{chat.username}" if chat.username else chat.first_name
        except:
            username = str(uid)

        for entry in claims:
            date = entry.get("date", "N/A")
            ctype = entry.get("type", "N/A")
            amount = entry.get("amount", 0)
            photo_id = entry.get("photo", None)

            caption = f"🧾 Claim from {username}\nDate: {date}\nType: {ctype}\nAmount: RM{amount:.2f}"
            if photo_id:
                bot.send_photo(chat_id=update.effective_chat.id, photo=photo_id, caption=caption)
            else:
                update.message.reply_text(caption)

    if not any_claims:
        update.message.reply_text("✅ No claims have been submitted yet.")

# === /topup (交互流程管理员专用) ===
def topup_start(update, context):
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        update.message.reply_text("❌ You are not authorized to use this command.")
        return ConversationHandler.END
    
    logger.info(f"Admin {user_id} started topup process")
    
    keyboard = []
    topup_state[user_id] = {}
    for uid in driver_accounts:
        # 跳过已解雇的司机
        if uid in fired_drivers:
            continue
            
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
        update.message.reply_text("❌ No active drivers found.")
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
    
    # 检查是否已被解雇
    if user_id in fired_drivers:
        update.message.reply_text("🚫 You have been fired and no longer have access to this bot.")
        return ConversationHandler.END
    
    username = update.effective_user.username or str(user_id)
    
    # 清除旧的报销状态（如果存在）
    if user_id in claim_state:
        del claim_state[user_id]
    
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
    if not update.message.photo:
        update.message.reply_text("❌ Please send a photo as proof.")
        return CLAIM_PROOF
        
    file_id = update.message.photo[-1].file_id
    date = datetime.datetime.now(tz).strftime("%Y-%m-%d")
    
    # 确保状态存在
    if user_id not in claim_state or "amount" not in claim_state[user_id] or "type" not in claim_state[user_id]:
        update.message.reply_text("❌ Claim process error. Please start over.")
        return ConversationHandler.END

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
    
    # 清理状态
    if user_id in claim_state:
        del claim_state[user_id]
    
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
dispatcher.add_handler(CommandHandler("help", help_command))
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
    conversation_timeout=300  # 5分钟超时
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
    conversation_timeout=300  # 5分钟超时
))

# === 注册错误处理器 ===
dispatcher.add_error_handler(error_handler)

# === 初始命令设置 ===
def set_default_commands():
    """设置默认命令（普通用户命令）"""
    default_commands = [
        BotCommand("clockin", "Clock in to start work"),
        BotCommand("clockout", "Clock out to end work"),
        BotCommand("offday", "Mark today as off day"),
        BotCommand("claim", "Submit a claim for travel fund"),
        BotCommand("help", "Show help information")
    ]
    
    try:
        bot.set_my_commands(default_commands)
        logger.info("Default bot commands set successfully.")
    except telegram.error.BadRequest as e:
        logger.error(f"Bad request when setting default commands: {str(e)}")
    except Exception as e:
        logger.error(f"Error setting default commands: {str(e)}")

# === Run ===
if __name__ == "__main__":
    logger.info("Bot server started.")
    set_default_commands()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
