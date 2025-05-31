from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
import datetime

# ✅ 替换为你的 Neon 连接字符串
DATABASE_URL = "postgresql://neondb_owner:<password>@ep-xxx.eastus2.azure.neon.tech/neondb?sslmode=require"

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(bind=engine)

Base = declarative_base()

# 表1：Driver，保存用户余额
class Driver(Base):
    __tablename__ = "drivers"
    id = Column(Integer, primary_key=True, index=True)
    telegram_id = Column(String, unique=True, index=True)
    username = Column(String)
    balance = Column(Float, default=0.0)

# 表2：ClockIn，保存打卡时间
class ClockIn(Base):
    __tablename__ = "clockins"
    id = Column(Integer, primary_key=True)
    telegram_id = Column(String)
    timestamp = Column(DateTime, default=datetime.datetime.utcnow)

# 创建数据库表
if __name__ == "__main__":
    Base.metadata.create_all(bind=engine)
    print("✅ Neon 数据表创建成功！")
