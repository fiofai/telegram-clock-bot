# telegram-clock-bot

Telegram Clock-In Bot with MongoDB Integration  
打卡 + 报销 + 工资记录管理 Bot，基于 Python Flask + Telegram Bot API + MongoDB Atlas

---

## 🚀 功能介绍

- `/clockin` / `/clockout` 上班与下班打卡
- `/offday` 记录休假日
- `/balance` 查看余额
- `/claim` 报销 + 上传凭证
- `/viewclaims` 查看报销记录
- `/PDF` 生成工时报销 PDF（带图片）
- `/topup` 管理员为司机充值
- `/salary` 设置司机月薪
- `/export` 管理员导出 JSON 数据
- `/migrate` 管理员将内存迁移到数据库

---

## 📁 项目结构

```bash
.
├── clock_bot.py        # 主 Flask 应用 + Telegram bot 路由
├── db_mongo.py         # MongoDB 操作封装
├── requirements.txt    # 依赖库列表
├── README.md           # 当前文件
└── .env                # 环境变量配置（不应上传）
# telegram-clock-bot
