"""
MongoDB数据库连接和操作模块（安全版，支持重试 + 环境变量 + TLS）
"""

import os
import time
import logging
from pymongo import MongoClient
from pymongo.errors import ConnectionFailure

# 日志设置
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("db_mongo")

# 获取连接字符串（强烈建议从环境变量获取）
MONGO_URI = os.environ.get("MONGO_URI")
DB_NAME = os.environ.get("DB_NAME", "clock_bot_db")

# 初始化数据库引用
db = None

# === 安全连接 + 重试机制 ===
logger.info("正在初始化MongoDB连接...")

for attempt in range(5):
    try:
        logger.info(f"尝试连接MongoDB (尝试 {attempt + 1}/5)")
        client = MongoClient(
            MONGO_URI,
            serverSelectionTimeoutMS=30000,
            connectTimeoutMS=30000,
            socketTimeoutMS=30000,
            tls=True
        )
        client.server_info()  # 强制触发连接尝试
        db = client[DB_NAME]
        logger.info("✅ MongoDB连接成功")
        break
    except Exception as e:
        logger.warning(f"⚠️ MongoDB连接失败 (尝试 {attempt + 1}/5): {str(e)}")
        time.sleep(5 * (attempt + 1))
else:
    logger.error("❌ 无法连接MongoDB，请检查 MONGO_URI 是否正确或 Render 网络是否受限")

# === 以下是数据库操作函数 ===

def get_driver_logs():
    logs = {}
    if db is None:
        return logs
    try:
        for doc in db.driver_logs.find():
            user_id = doc["user_id"]
            date = doc["date"]
            logs.setdefault(user_id, {})[date] = {
                "in": doc.get("clock_in", "N/A"),
                "out": doc.get("clock_out", "N/A")
            }
    except Exception as e:
        logger.error(f"获取打卡记录失败: {str(e)}")
    return logs

def save_driver_logs(logs):
    if db is None:
        return
    try:
        for user_id, dates in logs.items():
            for date, log_data in dates.items():
                db.driver_logs.update_one(
                    {"user_id": user_id, "date": date},
                    {"$set": {
                        "clock_in": log_data.get("in", "N/A"),
                        "clock_out": log_data.get("out", "N/A")
                    }},
                    upsert=True
                )
    except Exception as e:
        logger.error(f"保存打卡记录失败: {str(e)}")

def get_driver_salaries():
    salaries = {}
    if db is None:
        return salaries
    try:
        for doc in db.driver_salaries.find():
            user_id = doc["user_id"]
            salaries[user_id] = {
                "monthly_salary": doc.get("monthly_salary", 3500.00),
                "total_hours": doc.get("total_hours", 0),
                "last_updated": doc.get("last_updated", "")
            }
    except Exception as e:
        logger.error(f"获取薪资信息失败: {str(e)}")
    return salaries

def save_driver_salaries(salaries):
    if db is None:
        return
    try:
        for user_id, salary_data in salaries.items():
            db.driver_salaries.update_one(
                {"user_id": user_id},
                {"$set": salary_data},
                upsert=True
            )
    except Exception as e:
        logger.error(f"保存薪资信息失败: {str(e)}")

def get_driver_accounts():
    accounts = {}
    if db is None:
        return accounts
    try:
        for doc in db.driver_accounts.find():
            user_id = doc["user_id"]
            accounts[user_id] = {
                "balance": doc.get("balance", 0.0),
                "claims": doc.get("claims", []),
                "topup_history": doc.get("topup_history", [])
            }
    except Exception as e:
        logger.error(f"获取账户信息失败: {str(e)}")
    return accounts

def save_driver_accounts(accounts):
    if db is None:
        return
    try:
        for user_id, account_data in accounts.items():
            db.driver_accounts.update_one(
                {"user_id": user_id},
                {"$set": account_data},
                upsert=True
            )
    except Exception as e:
        logger.error(f"保存账户信息失败: {str(e)}")

def add_claim(user_id, claim_data):
    if db is None:
        return
    try:
        db.driver_accounts.update_one(
            {"user_id": user_id},
            {
                "$push": {"claims": claim_data},
                "$inc": {"balance": -claim_data["amount"]}
            },
            upsert=True
        )
    except Exception as e:
        logger.error(f"添加报销记录失败: {str(e)}")

def add_topup(user_id, topup_data):
    if db is None:
        return
    try:
        db.driver_accounts.update_one(
            {"user_id": user_id},
            {
                "$push": {"topup_history": topup_data},
                "$inc": {"balance": topup_data["amount"]}
            },
            upsert=True
        )
    except Exception as e:
        logger.error(f"添加充值记录失败: {str(e)}")
