"""
MongoDB数据库连接和操作模块 (简化版)
"""

from pymongo import MongoClient
import os
import logging
import ssl

# 设置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 禁用SSL证书验证
ssl._create_default_https_context = ssl._create_unverified_context

# 从环境变量获取MongoDB连接字符串
MONGO_URI = os.environ.get("MONGO_URI", "mongodb+srv://yesclub0802:OWMjMxjzMPHGfPoA@cluster0.fy6uhn1.mongodb.net/?retryWrites=true&w=majority&appName=Cluster0" )
DB_NAME = "clock_bot_db"

# 创建MongoDB客户端连接
try:
    # 连接到MongoDB Atlas，添加SSL配置
    client = MongoClient(
        MONGO_URI,
        ssl=True,
        ssl_cert_reqs=ssl.CERT_NONE,
        connectTimeoutMS=30000,
        socketTimeoutMS=30000,
        serverSelectionTimeoutMS=30000
    )
    
    # 选择数据库
    db = client[DB_NAME]
    logger.info("MongoDB连接成功")
except Exception as e:
    logger.error(f"MongoDB连接失败: {str(e)}")
    db = None

def get_driver_logs():
    """获取所有司机打卡记录"""
    logs = {}
    if db is None:
        return logs
    try:
        for doc in db.driver_logs.find():
            user_id = doc["user_id"]
            date = doc["date"]
            if user_id not in logs:
                logs[user_id] = {}
            logs[user_id][date] = {
                "in": doc.get("clock_in", "N/A"),
                "out": doc.get("clock_out", "N/A")
            }
    except Exception as e:
        logger.error(f"获取打卡记录失败: {str(e)}")
    return logs

def save_driver_logs(logs):
    """保存司机打卡记录"""
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
    """获取所有司机薪资信息"""
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
    """保存司机薪资信息"""
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
    """获取所有司机账户信息"""
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
    """保存所有司机账户信息"""
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
    """添加报销记录"""
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
    """添加充值记录"""
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
