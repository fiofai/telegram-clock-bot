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
    logger.error("Exception while handling an update:", exc_info=context.error)
    try:
        if update and update.effective_message:
            update.effective_message.reply_text(
                "⚠️ An unexpected error occurred. Please try again later."
            )
    except:
        logger.error("Failed to send error message to user")
    tb_list = traceback.format_exception(None, context.error, context.error.__traceback__)
    tb_string = ''.join(tb_list)
    logger.error(f"Full traceback:\n{tb_string}")

# === 功能函数省略 (start, clockin, clockout, offday, balance, check, topup, claim) ===
# === 在原文基础上保持不变 ===

# === 新增 viewclaims ===
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
        for c in claims[-5:]:
            msg += f"• {c['date']} - RM{c['amount']} ({c['type']})\n"

    update.message.reply_text(msg)

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

# === 注册错误处理 ===
dispatcher.add_error_handler(error_handler)

# === Run ===
if __name__ == "__main__":
    logger.info("Bot server started.")
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
