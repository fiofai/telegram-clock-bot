"""
MongoDB数据库连接和操作模块 (完整修复版)
"""

from pymongo import MongoClient
from pymongo.errors import AutoReconnect, ConnectionFailure, ServerSelectionTimeoutError
import os
import logging
import time
import ssl  # 添加SSL支持

# 设置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# 从环境变量获取MongoDB连接字符串
MONGO_URI = os.environ.get("MONGO_URI")
if not MONGO_URI:
    logger.error("MongoDB connection string not set in environment variable MONGO_URI")
    # 在Render中无法继续，需要停止服务
    raise ValueError("MongoDB connection string is required")

DB_NAME = "clock_bot_db"

def create_mongo_client():
    """创建MongoDB客户端连接，包含重试机制"""
    retries = 5
    delay = 5  # 每次重试的延迟时间（秒）
    
    for attempt in range(retries):
        try:
            logger.info(f"尝试连接MongoDB (尝试 {attempt+1}/{retries})")
            
            # 使用更稳定的连接选项
            client = MongoClient(
                MONGO_URI,
                connectTimeoutMS=30000,
                socketTimeoutMS=30000,
                serverSelectionTimeoutMS=30000,
                retryWrites=True,
                w="majority",
                tls=True,  # 强制使用TLS
                tlsAllowInvalidCertificates=False,  # 严格证书验证
                tlsInsecure=False,  # 不跳过主机名验证
                ssl_cert_reqs=ssl.CERT_REQUIRED  # 要求证书验证
            )
            
            # 测试连接
            client.server_info()
            logger.info(f"MongoDB连接成功 (尝试 {attempt+1}/{retries})")
            return client
            
        except (AutoReconnect, ConnectionFailure, ServerSelectionTimeoutError) as e:
            if attempt < retries - 1:
                logger.warning(f"MongoDB连接失败 (尝试 {attempt+1}/{retries}): {str(e)}. 将在 {delay} 秒后重试...")
                time.sleep(delay)
                delay *= 2  # 指数退避策略
                continue
            logger.error(f"MongoDB连接失败: {str(e)} (尝试 {retries} 次后放弃)")
            return None
        except Exception as e:
            logger.error(f"意外的MongoDB连接错误: {str(e)}")
            return None

try:
    logger.info("正在初始化MongoDB连接...")
    client = create_mongo_client()
    
    if client:
        db = client[DB_NAME]
        logger.info(f"已连接到数据库: {DB_NAME}")
        
        # 测试数据库操作
        try:
            collection = db.test_connection
            collection.insert_one({"test": "connection", "timestamp": datetime.datetime.utcnow()})
            collection.delete_one({"test": "connection"})
            logger.info("数据库操作测试成功")
        except Exception as test_e:
            logger.error(f"数据库操作测试失败: {str(test_e)}")
    else:
        db = None
        logger.error("无法连接到MongoDB")
except Exception as e:
    logger.error(f"初始化MongoDB连接时出错: {str(e)}")
    db = None

# === 数据操作函数 ===
def get_driver_logs():
    """获取所有司机打卡记录"""
    logs = {}
    if not db:
        logger.warning("无法获取打卡记录 - 数据库未连接")
        return logs
        
    try:
        collection = db.driver_logs
        cursor = collection.find()
        
        for doc in cursor:
            user_id = doc["user_id"]
            date = doc["date"]
            if user_id not in logs:
                logs[user_id] = {}
            logs[user_id][date] = {
                "in": doc.get("clock_in", "N/A"),
                "out": doc.get("clock_out", "N/A")
            }
        logger.info(f"成功获取 {len(logs)} 位司机的打卡记录")
    except Exception as e:
        logger.error(f"获取打卡记录失败: {str(e)}")
    return logs

def save_driver_logs(logs):
    """保存司机打卡记录"""
    if not db:
        logger.warning("无法保存打卡记录 - 数据库未连接")
        return
        
    try:
        collection = db.driver_logs
        count = 0
        
        for user_id, dates in logs.items():
            for date, log_data in dates.items():
                result = collection.update_one(
                    {"user_id": user_id, "date": date},
                    {"$set": {
                        "clock_in": log_data.get("in", "N/A"),
                        "clock_out": log_data.get("out", "N/A")
                    }},
                    upsert=True
                )
                if result.upserted_id:
                    count += 1
        
        logger.info(f"成功保存打卡记录: 更新了 {count} 条记录")
    except Exception as e:
        logger.error(f"保存打卡记录失败: {str(e)}")

def get_driver_salaries():
    """获取所有司机薪资信息"""
    salaries = {}
    if not db:
        logger.warning("无法获取薪资信息 - 数据库未连接")
        return salaries
        
    try:
        collection = db.driver_salaries
        cursor = collection.find()
        
        for doc in cursor:
            user_id = doc["user_id"]
            salaries[user_id] = {
                "monthly_salary": doc.get("monthly_salary", 3500.00),
                "total_hours": doc.get("total_hours", 0),
                "last_updated": doc.get("last_updated", "")
            }
        logger.info(f"成功获取 {len(salaries)} 位司机的薪资信息")
    except Exception as e:
        logger.error(f"获取薪资信息失败: {str(e)}")
    return salaries

def save_driver_salaries(salaries):
    """保存司机薪资信息"""
    if not db:
        logger.warning("无法保存薪资信息 - 数据库未连接")
        return
        
    try:
        collection = db.driver_salaries
        count = 0
        
        for user_id, salary_data in salaries.items():
            result = collection.update_one(
                {"user_id": user_id},
                {"$set": salary_data},
                upsert=True
            )
            if result.modified_count > 0 or result.upserted_id:
                count += 1
        
        logger.info(f"成功保存薪资信息: 更新了 {count} 条记录")
    except Exception as e:
        logger.error(f"保存薪资信息失败: {str(e)}")

def get_driver_accounts():
    """获取所有司机账户信息"""
    accounts = {}
    if not db:
        logger.warning("无法获取账户信息 - 数据库未连接")
        return accounts
        
    try:
        collection = db.driver_accounts
        cursor = collection.find()
        
        for doc in cursor:
            user_id = doc["user_id"]
            accounts[user_id] = {
                "balance": doc.get("balance", 0.0),
                "claims": doc.get("claims", []),
                "topup_history": doc.get("topup_history", [])
            }
        logger.info(f"成功获取 {len(accounts)} 位司机的账户信息")
    except Exception as e:
        logger.error(f"获取账户信息失败: {str(e)}")
    return accounts

def save_driver_accounts(accounts):
    """保存所有司机账户信息"""
    if not db:
        logger.warning("无法保存账户信息 - 数据库未连接")
        return
        
    try:
        collection = db.driver_accounts
        count = 0
        
        for user_id, account_data in accounts.items():
            result = collection.update_one(
                {"user_id": user_id},
                {"$set": account_data},
                upsert=True
            )
            if result.modified_count > 0 or result.upserted_id:
                count += 1
        
        logger.info(f"成功保存账户信息: 更新了 {count} 条记录")
    except Exception as e:
        logger.error(f"保存账户信息失败: {str(e)}")

def add_claim(user_id, claim_data):
    """添加报销记录"""
    if not db:
        logger.warning("无法添加报销记录 - 数据库未连接")
        return False
        
    try:
        collection = db.driver_accounts
        result = collection.update_one(
            {"user_id": user_id},
            {
                "$push": {"claims": claim_data},
                "$inc": {"balance": -claim_data["amount"]}
            },
            upsert=True
        )
        
        if result.modified_count > 0 or result.upserted_id:
            logger.info(f"成功为用户 {user_id} 添加报销记录")
            return True
        return False
    except Exception as e:
        logger.error(f"添加报销记录失败: {str(e)}")
        return False

def add_topup(user_id, topup_data):
    """添加充值记录"""
    if not db:
        logger.warning("无法添加充值记录 - 数据库未连接")
        return False
        
    try:
        collection = db.driver_accounts
        result = collection.update_one(
            {"user_id": user_id},
            {
                "$push": {"topup_history": topup_data},
                "$inc": {"balance": topup_data["amount"]}
            },
            upsert=True
        )
        
        if result.modified_count > 0 or result.upserted_id:
            logger.info(f"成功为用户 {user_id} 添加充值记录")
            return True
        return False
    except Exception as e:
        logger.error(f"添加充值记录失败: {str(e)}")
        return False
