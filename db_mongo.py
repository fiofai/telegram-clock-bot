"""
MongoDB数据库连接和操作模块

此模块负责与MongoDB Atlas数据库的连接和所有数据操作，
包括读取和保存打卡记录、薪资信息和账户信息等。
"""

from pymongo import MongoClient
import os
import logging
from pymongo.errors import ConnectionFailure, OperationFailure

# 设置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 从环境变量获取MongoDB连接字符串
# 注意：实际部署时需要在Render上设置此环境变量
MONGO_URI = os.environ.get("MONGO_URI", "mongodb+srv://yesclub0802:OWMjMxjzMPHGfPoA@cluster0.fy6uhn1.mongodb.net/?retryWrites=true&w=majority&appName=Cluster0")
DB_NAME = "clock_bot_db"  # 数据库名称

# 创建MongoDB客户端连接
try:
    # 连接到MongoDB Atlas
    client = MongoClient(MONGO_URI)
    # 选择数据库
    db = client[DB_NAME]
    
    # 创建集合（如果不存在）
    if "driver_logs" not in db.list_collection_names():
        db.create_collection("driver_logs")
    if "driver_salaries" not in db.list_collection_names():
        db.create_collection("driver_salaries")
    if "driver_accounts" not in db.list_collection_names():
        db.create_collection("driver_accounts")
    
    # 创建索引以提高查询效率
    # 为driver_logs创建复合索引，确保user_id和date的组合是唯一的
    db.driver_logs.create_index([("user_id", 1), ("date", 1)], unique=True)
    # 为driver_salaries创建唯一索引，确保每个用户只有一条薪资记录
    db.driver_salaries.create_index([("user_id", 1)], unique=True)
    # 为driver_accounts创建唯一索引，确保每个用户只有一个账户
    db.driver_accounts.create_index([("user_id", 1)], unique=True)
    
    logger.info("MongoDB连接成功")
except (ConnectionFailure, OperationFailure) as e:
    # 记录连接错误
    logger.error(f"MongoDB连接失败: {str(e)}")
    raise

# ===== 数据操作函数 =====

def get_driver_logs():
    """
    获取所有司机打卡记录
    
    返回:
        dict: 以用户ID为键，包含日期和打卡时间的嵌套字典
    """
    logs = {}
    # 查询所有打卡记录
    for doc in db.driver_logs.find():
        user_id = doc["user_id"]
        date = doc["date"]
        # 初始化用户记录（如果不存在）
        if user_id not in logs:
            logs[user_id] = {}
        # 添加日期记录
        logs[user_id][date] = {
            "in": doc.get("clock_in", "N/A"),  # 上班打卡时间
            "out": doc.get("clock_out", "N/A")  # 下班打卡时间
        }
    return logs

def save_driver_logs(logs):
    """
    保存司机打卡记录
    
    参数:
        logs (dict): 以用户ID为键，包含日期和打卡时间的嵌套字典
    """
    # 遍历所有用户
    for user_id, dates in logs.items():
        # 遍历用户的所有日期记录
        for date, log_data in dates.items():
            # 使用update_one方法，如果记录存在则更新，不存在则创建
            db.driver_logs.update_one(
                {"user_id": user_id, "date": date},  # 查询条件
                {"$set": {
                    "clock_in": log_data.get("in", "N/A"),  # 设置上班时间
                    "clock_out": log_data.get("out", "N/A")  # 设置下班时间
                }},
                upsert=True  # 如果记录不存在，则创建新记录
            )

def get_driver_salaries():
    """
    获取所有司机薪资信息
    
    返回:
        dict: 以用户ID为键，包含薪资信息的字典
    """
    salaries = {}
    # 查询所有薪资记录
    for doc in db.driver_salaries.find():
        user_id = doc["user_id"]
        # 构建薪资信息字典
        salaries[user_id] = {
            "monthly_salary": doc.get("monthly_salary", 3500.00),  # 月薪，默认3500
            "total_hours": doc.get("total_hours", 0),  # 总工作时长
            "last_updated": doc.get("last_updated", "")  # 最后更新时间
        }
    return salaries

def save_driver_salaries(salaries):
    """
    保存司机薪资信息
    
    参数:
        salaries (dict): 以用户ID为键，包含薪资信息的字典
    """
    # 遍历所有用户
    for user_id, salary_data in salaries.items():
        # 使用update_one方法，如果记录存在则更新，不存在则创建
        db.driver_salaries.update_one(
            {"user_id": user_id},  # 查询条件
            {"$set": salary_data},  # 设置整个薪资数据对象
            upsert=True  # 如果记录不存在，则创建新记录
        )

def get_driver_accounts():
    """
    获取所有司机账户信息
    
    返回:
        dict: 以用户ID为键，包含账户信息的字典
    """
    accounts = {}
    # 查询所有账户记录
    for doc in db.driver_accounts.find():
        user_id = doc["user_id"]
        # 构建账户信息字典
        accounts[user_id] = {
            "balance": doc.get("balance", 0.0),  # 账户余额
            "claims": doc.get("claims", []),  # 报销记录列表
            "topup_history": doc.get("topup_history", [])  # 充值历史列表
        }
    return accounts

def save_driver_accounts(accounts):
    """
    保存所有司机账户信息
    
    参数:
        accounts (dict): 以用户ID为键，包含账户信息的字典
    """
    # 遍历所有用户
    for user_id, account_data in accounts.items():
        # 使用update_one方法，如果记录存在则更新，不存在则创建
        db.driver_accounts.update_one(
            {"user_id": user_id},  # 查询条件
            {"$set": account_data},  # 设置整个账户数据对象
            upsert=True  # 如果记录不存在，则创建新记录
        )

def save_single_driver_account(user_id, account_data):
    """
    保存单个司机账户信息
    
    参数:
        user_id (int): 用户ID
        account_data (dict): 账户信息字典
    """
    # 使用update_one方法，如果记录存在则更新，不存在则创建
    db.driver_accounts.update_one(
        {"user_id": user_id},  # 查询条件
        {"$set": account_data},  # 设置整个账户数据对象
        upsert=True  # 如果记录不存在，则创建新记录
    )

def add_claim(user_id, claim_data):
    """
    添加报销记录
    
    参数:
        user_id (int): 用户ID
        claim_data (dict): 报销记录信息
    """
    # 使用update_one方法，同时添加报销记录并减少余额
    db.driver_accounts.update_one(
        {"user_id": user_id},  # 查询条件
        {
            "$push": {"claims": claim_data},  # 添加报销记录到claims数组
            "$inc": {"balance": -claim_data["amount"]}  # 减少账户余额
        },
        upsert=True  # 如果记录不存在，则创建新记录
    )

def add_topup(user_id, topup_data):
    """
    添加充值记录
    
    参数:
        user_id (int): 用户ID
        topup_data (dict): 充值记录信息
    """
    # 使用update_one方法，同时添加充值记录并增加余额
    db.driver_accounts.update_one(
        {"user_id": user_id},  # 查询条件
        {
            "$push": {"topup_history": topup_data},  # 添加充值记录到topup_history数组
            "$inc": {"balance": topup_data["amount"]}  # 增加账户余额
        },
        upsert=True  # 如果记录不存在，则创建新记录
    )
