import os
import logging
import datetime
from flask import Flask, request
from telegram import Update
from telegram.ext import (
    Updater,
    CommandHandler,
    CallbackContext,
    Dispatcher,
)
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

# === 配置 ===
TOKEN = os.getenv("BOT_TOKEN") or "YOUR_BOT_TOKEN"
DATABASE_URL = os.getenv("DATABASE_URL") or "postgresql://neondb_owner:<password>@ep-xxx.eastus2.azure.neon.tech/neondb?sslmode=require"

# === 日志配置 ===
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# === Flask 应用 ===
app = Flask(__name__)

# === 数据库配置 ===
engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(bind=engine)
Base = declarative_base()

class Driver(Base):
    __tablename__ = "drivers"
    id = Column(Integer, primary_key=True, index=True)
    telegram_id = Column(String, unique=True, index=True)
    username = Column(String)
    balance = Column(Float, default=0.0)

class ClockIn(Base):
    __tablename__ = "clockins"
    id = Column(Integer, primary_key=True)
    telegram_id = Column(String)
    timestamp = Column(DateTime, default=datetime.datetime.utcnow)

# === 初始化 Telegram Bot ===
updater = Updater(token=TOKEN, use_context=True)
dispatcher: Dispatcher = updater.dispatcher

# === 指令：start ===
def start(update: Update, context: CallbackContext):
    update.message.reply_text("👋 欢迎使用打卡 Bot！请输入 /clockin 开始打卡")

# === 指令：clockin（使用 Neon 数据库）===
def clockin(update: Update, context: CallbackContext):
    session = SessionLocal()
    telegram_id = str(update.effective_user.id)
    username = update.effective_user.username or telegram_id

    # 添加打卡记录
    clock = ClockIn(telegram_id=telegram_id)
    session.add(clock)

    # 确保用户存在
    driver = session.query(Driver).filter_by(telegram_id=telegram_id).first()
    if not driver:
        driver = Driver(telegram_id=telegram_id, username=username)
        session.add(driver)

    session.commit()
    session.close()

    update.message.reply_text("✅ 打卡成功！")

# === 其它功能用的内存数据（之后会迁移到数据库）===
driver_accounts = {}

def balance(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    balance = driver_accounts.get(user_id, {}).get("balance", 0.0)
    update.message.reply_text(f"💰 当前余额: RM{balance:.2f}")

def clockout(update: Update, context: CallbackContext):
    update.message.reply_text("⏳ Clock-out 功能正在开发中...")

def offday(update: Update, context: CallbackContext):
    update.message.reply_text("📆 请假功能正在开发中...")

# === Dispatcher 注册 ===
dispatcher.add_handler(CommandHandler("start", start))
dispatcher.add_handler(CommandHandler("clockin", clockin))
dispatcher.add_handler(CommandHandler("clockout", clockout))
dispatcher.add_handler(CommandHandler("offday", offday))
dispatcher.add_handler(CommandHandler("balance", balance))

# === Webhook 接口 ===
@app.route("/webhook", methods=["POST"])
def webhook():
    update = Update.de_json(request.get_json(force=True), updater.bot)
    dispatcher.process_update(update)
    return "ok", 200

# === 测试接口 ===
@app.route("/", methods=["GET"])
def home():
    return "Bot is running!"

# === 本地运行入口（开发用）===
if __name__ == "__main__":
    Base.metadata.create_all(bind=engine)
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
