# -*- coding: utf-8 -*-
"""
Telegram Bot for Driver Clock-in/out, Claims, and Balance Management

This version integrates MongoDB for data persistence.
"""

from flask import Flask, request
from telegram import (
    Bot, Update, ReplyKeyboardMarkup, ReplyKeyboardRemove, InlineKeyboardButton, InlineKeyboardMarkup
)
from telegram.ext import (
    Dispatcher, CommandHandler, MessageHandler, Filters, ConversationHandler, CallbackQueryHandler
)
import datetime
import pytz
import os
import logging
import traceback
import tempfile
import requests
import calendar
import json
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

# 导入MongoDB数据库操作模块
import db_mongo

# 修复字体注册，避免警告
try:
    # 尝试多个可能的字体路径
    font_paths = [
        '/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc',
        '/usr/share/fonts/noto/NotoSansCJK-Regular.ttc',
        '/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc'
    ]
    
    font_registered = False
    for path in font_paths:
        if os.path.exists(path):
            pdfmetrics.registerFont(TTFont('NotoSans', path))
            font_registered = True
            break
            
    if not font_registered:
        # 如果找不到NotoSans字体，使用reportlab内置字体，不显示警告
        logging.info("Using built-in fonts for PDF generation")
except:
    # 出错时静默处理，使用默认字体
    pass

app = Flask(__name__)

# 从环境变量获取Telegram Bot Token
TOKEN = os.environ.get("TOKEN")
# 管理员用户ID列表
ADMIN_IDS = [1165249082]
# 默认时薪，RM20/小时
DEFAULT_HOURLY_RATE = 20.00
# 默认月薪，RM3500
DEFAULT_MONTHLY_SALARY = 3500.00
# 默认每月工作天数
WORKING_DAYS_PER_MONTH = 22
# 默认每天工作小时数
WORKING_HOURS_PER_DAY = 8

# 初始化Telegram Bot
bot = Bot(token=TOKEN)
# 初始化Dispatcher
dispatcher = Dispatcher(bot, None, use_context=True)

# === 日志设置 ===
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# === 全局数据结构 (现在从数据库加载) ===
driver_logs = {}
driver_salaries = {}
driver_accounts = {}

# === 状态变量 (用于会话处理) ===
topup_state = {}
claim_state = {}
pdf_state = {}  # 用于存储PDF生成状态
salary_state = {}  # 新增：用于存储薪资设置状态

# 设置时区为亚洲/吉隆坡
tz = pytz.timezone("Asia/Kuala_Lumpur")

# === Conversation 状态定义 ===
TOPUP_USER, TOPUP_AMOUNT = range(2)
CLAIM_TYPE, CLAIM_OTHER_TYPE, CLAIM_AMOUNT, CLAIM_PROOF = range(4)
PDF_SELECT_DRIVER = range(1)  # PDF司机选择状态
SALARY_SELECT_DRIVER, SALARY_ENTER_AMOUNT = range(2)  # 新增：薪资设置状态

# === 从数据库加载数据 ===
try:
    # 尝试从MongoDB加载所有数据
    driver_logs = db_mongo.get_driver_logs()
    driver_salaries = db_mongo.get_driver_salaries()
    driver_accounts = db_mongo.get_driver_accounts()
    logger.info("从MongoDB数据库加载数据成功")
except Exception as e:
    # 如果加载失败，记录错误并使用空字典
    logger.error(f"从MongoDB数据库加载数据失败: {str(e)}")
    # 保持默认的空字典
    driver_logs = {}
    driver_salaries = {}
    driver_accounts = {}

# === 辅助函数 ===
def format_local_time(timestamp_str):
    """将时间戳字符串转换为本地时间格式，去除国际时间部分"""
    try:
        # 解析时间字符串
        dt = datetime.datetime.strptime(timestamp_str, "%Y-%m-%d %H:%M:%S")
        # 只返回日期和时间部分，不包含时区
        return dt.strftime("%Y-%m-%d %H:%M")
    except:
        return timestamp_str  # 如果解析失败，返回原始字符串

def format_duration(hours):
    """将小时数转换为更友好的时长格式"""
    try:
        total_minutes = int(float(hours) * 60)
        hours_part = total_minutes // 60
        minutes_part = total_minutes % 60
        
        if hours_part > 0 and minutes_part > 0:
            return f"{hours_part}Hour {minutes_part}Min"
        elif hours_part > 0:
            return f"{hours_part}Hour"
        else:
            return f"{minutes_part}Min"
    except:
        return str(hours)  # 如果转换失败，返回原始值

def get_month_date_range(date=None):
    """获取指定日期所在月份的起止日期"""
    if date is None:
        date = datetime.datetime.now(tz)
    
    year = date.year
    month = date.month
    
    # 获取月份第一天和最后一天
    first_day = datetime.date(year, month, 1)
    
    # 获取月份最后一天
    last_day = datetime.date(year, month, calendar.monthrange(year, month)[1])
    
    return first_day, last_day

def get_topup_history(user_id):
    """获取用户的充值历史记录"""
    if user_id not in driver_accounts:
        return []
    
    return driver_accounts[user_id].get("topup_history", [])

def calculate_hourly_rate(monthly_salary):
    """根据月薪计算时薪"""
    try:
        monthly_salary = float(monthly_salary)
        hourly_rate = monthly_salary / (WORKING_DAYS_PER_MONTH * WORKING_HOURS_PER_DAY)
        return round(hourly_rate, 2)
    except:
        return DEFAULT_HOURLY_RATE

def get_driver_hourly_rate(driver_id):
    """获取司机的时薪"""
    if driver_id in driver_salaries and "monthly_salary" in driver_salaries[driver_id]:
        monthly_salary = driver_salaries[driver_id]["monthly_salary"]
        return calculate_hourly_rate(monthly_salary)
    return DEFAULT_HOURLY_RATE

# === PDF 生成功能 ===
def download_telegram_photo(file_id, bot):
    """Download a photo from Telegram by file_id and save to a temporary file"""
    try:
        file = bot.get_file(file_id)
        temp_file = tempfile.NamedTemporaryFile(delete=False, suffix='.jpg')
        file.download(temp_file.name)
        return temp_file.name
    except Exception as e:
        logger.error(f"Error downloading photo: {str(e)}")
        return None

def generate_driver_pdf(driver_id, driver_name, driver_logs, driver_salaries, driver_accounts, bot, output_path):
    """Generate a PDF report for a single driver"""
    
    doc = SimpleDocTemplate(
        output_path,
        pagesize=A4,
        rightMargin=72,
        leftMargin=72,
        topMargin=72,
        bottomMargin=72
    )
    
    # 获取样式表但不添加重复样式
    styles = getSampleStyleSheet()
    
    # 定义自定义样式，使用不同的名称避免冲突
    custom_title_style = ParagraphStyle(
        name='CustomTitle',
        fontName='Helvetica-Bold',
        fontSize=16,
        alignment=1,  # Center
        spaceAfter=12
    )
    
    custom_heading_style = ParagraphStyle(
        name='CustomHeading',
        fontName='Helvetica-Bold',
        fontSize=14,
        spaceAfter=6
    )
    
    custom_normal_style = ParagraphStyle(
        name='CustomNormal',
        fontName='Helvetica',
        fontSize=10,
        spaceAfter=6
    )
    
    # Content elements
    elements = []
    
    # Title
    title = Paragraph(f"Driver Report: {driver_name}", custom_title_style)
    elements.append(title)
    elements.append(Spacer(1, 12))
    
    # Clock-in/out Table
    elements.append(Paragraph("Daily Clock Records", custom_heading_style))
    elements.append(Spacer(1, 6))
    
    # Prepare clock data
    clock_data = [['Date', 'Clock In', 'Clock Out', 'Hours']]
    total_hours = 0
    
    if driver_id in driver_logs:
        for date, log in sorted(driver_logs[driver_id].items(), reverse=True):
            in_time = log.get('in', 'N/A')
            out_time = log.get('out', 'N/A')
            
            # 格式化时间，去除国际时间部分
            if in_time != 'N/A' and in_time != 'OFF':
                in_time = format_local_time(in_time)
            if out_time != 'N/A' and out_time != 'OFF':
                out_time = format_local_time(out_time)
            
            # Calculate hours if both in and out times exist
            hours = 'N/A'
            if in_time != 'N/A' and out_time != 'N/A' and in_time != 'OFF':
                try:
                    # 解析时间
                    in_dt = datetime.datetime.strptime(in_time, "%Y-%m-%d %H:%M")
                    out_dt = datetime.datetime.strptime(out_time, "%Y-%m-%d %H:%M")
                    duration = out_dt - in_dt
                    hours_float = duration.total_seconds() / 3600
                    # 使用新的格式化函数
                    hours = format_duration(hours_float)
                except:
                    hours = 'Error'
            elif in_time == 'OFF':
                hours = 'OFF'
                
            clock_data.append([date, in_time, out_time, hours])
    
    # Get total hours from salary data
    if driver_id in driver_salaries:
        total_hours = driver_salaries[driver_id].get('total_hours', 0)
    
    # Create clock table
    if len(clock_data) > 1:
        clock_table = Table(clock_data, colWidths=[80, 120, 120, 60])
        clock_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.grey),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (-1, 0), 'CENTER'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 12),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
            ('BACKGROUND', (0, 1), (-1, -1), colors.beige),
            ('GRID', (0, 0), (-1, -1), 1, colors.black),
            ('ALIGN', (0, 1), (-1, -1), 'CENTER'),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ]))
        elements.append(clock_table)
    else:
        elements.append(Paragraph("No clock records found.", custom_normal_style))
    
    elements.append(Spacer(1, 20))
    
    # Claims Section
    elements.append(Paragraph("Expense Claims", custom_heading_style))
    elements.append(Spacer(1, 6))
    
    # Calculate total claims amount
    total_claims = 0
    claims = []
    
    if driver_id in driver_accounts:
        claims = driver_accounts[driver_id].get('claims', [])
        for claim in claims:
            total_claims += claim.get('amount', 0)
    
    if claims:
        # Create a table for each claim with its photo
        for i, claim in enumerate(claims):
            claim_date = claim.get('date', 'N/A')
            claim_type = claim.get('type', 'N/A')
            claim_amount = claim.get('amount', 0)
            
            claim_data = [
                [f"Date: {claim_date}", f"Type: {claim_type}", f"Amount: RM{claim_amount:.2f}"]
            ]
            
            claim_table = Table(claim_data, colWidths=[120, 120, 120])
            claim_table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.lightblue),
                ('GRID', (0, 0), (-1, -1), 1, colors.black),
                ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
                ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                ('FONTNAME', (0, 0), (-1, -1), 'Helvetica'),
                ('FONTSIZE', (0, 0), (-1, -1), 10),
            ]))
            elements.append(claim_table)
            
            # Add photo if available
            if 'photo' in claim and claim['photo']:
                try:
                    photo_path = download_telegram_photo(claim['photo'], bot)
                    if photo_path:
                        img = Image(photo_path, width=300, height=200)
                        elements.append(img)
                        elements.append(Spacer(1, 6))
                        # Clean up temporary photo file
                        os.unlink(photo_path)
                except Exception as e:
                    elements.append(Paragraph(f"Error loading photo: {str(e)}", custom_normal_style))
            
            elements.append(Spacer(1, 10))
    else:
        elements.append(Paragraph("No claims found.", custom_normal_style))
    
    elements.append(Spacer(1, 20))
    
    # 增强的Summary Section
    elements.append(Paragraph("Summary", custom_heading_style))
    elements.append(Spacer(1, 6))
    
    # 获取月份日期范围
    first_day, last_day = get_month_date_range()
    period_text = f"Summary Period: {first_day.strftime('%Y-%m-%d')} to {last_day.strftime('%Y-%m-%d')}"
    elements.append(Paragraph(period_text, custom_normal_style))
    elements.append(Spacer(1, 6))
    
    # 获取司机个性化时薪
    hourly_rate = get_driver_hourly_rate(driver_id)
    
    # 获取月薪（如果有设置）
    monthly_salary = "N/A"
    if driver_id in driver_salaries and "monthly_salary" in driver_salaries[driver_id]:
        monthly_salary = f"RM{driver_salaries[driver_id]['monthly_salary']:.2f}"
    
    # 工资计算
    gross_pay = total_hours * hourly_rate
    pay_text = f"Monthly Salary: {monthly_salary}\nHourly Rate: RM{hourly_rate:.2f}\nTotal Hours: {format_duration(total_hours)}\nGross Pay: RM{gross_pay:.2f}"
    elements.append(Paragraph(pay_text, custom_normal_style))
    elements.append(Spacer(1, 12))
    
    # Get balance and account flow
    balance = 0
    if driver_id in driver_accounts:
        balance = driver_accounts[driver_id].get('balance', 0)
    
    # 账户流动明细
    elements.append(Paragraph("Account Transactions:", custom_normal_style))
    
    # 充值记录
    topup_history = get_topup_history(driver_id)
    if topup_history:
        elements.append(Paragraph("Topups:", custom_normal_style))
        topup_data = [['Date', 'Amount']]
        for topup in topup_history:
            topup_data.append([topup.get('date', 'N/A'), f"RM{topup.get('amount', 0):.2f}"])
        
        topup_table = Table(topup_data, colWidths=[120, 120])
        topup_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.lightgreen),
            ('GRID', (0, 0), (-1, -1), 1, colors.black),
            ('ALIGN', (0, 0), (-1, 0), 'CENTER'),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ]))
        elements.append(topup_table)
        elements.append(Spacer(1, 6))
    
    # 报销扣除记录
    if claims:
        elements.append(Paragraph("Claim Deductions:", custom_normal_style))
        claim_data = [['Date', 'Type', 'Amount']]
        for claim in claims:
            claim_data.append([
                claim.get('date', 'N/A'), 
                claim.get('type', 'N/A'), 
                f"RM{claim.get('amount', 0):.2f}"
            ])
        
        claim_deduct_table = Table(claim_data, colWidths=[80, 120, 80])
        claim_deduct_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.salmon),
            ('GRID', (0, 0), (-1, -1), 1, colors.black),
            ('ALIGN', (0, 0), (-1, 0), 'CENTER'),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ]))
        elements.append(claim_deduct_table)
        elements.append(Spacer(1, 12))
    
    # 最终余额表格
    summary_data = [
        ['Total Hours', 'Total Claims', 'Account Balance'],
        [format_duration(total_hours), f"RM{total_claims:.2f}", f"RM{balance:.2f}"]
    ]
    
    summary_table = Table(summary_data, colWidths=[120, 120, 120])
    summary_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.darkblue),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (0, 0), (-1, 0), 'CENTER'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 12),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
        ('BACKGROUND', (0, 1), (-1, -1), colors.lightgrey),
        ('GRID', (0, 0), (-1, -1), 1, colors.black),
        ('ALIGN', (0, 1), (-1, -1), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
        ('FONTSIZE', (0, 1), (-1, -1), 10),
    ]))
    elements.append(summary_table)
    
    # Build the PDF
    doc.build(elements)
    
    return output_path

def generate_all_drivers_pdf(driver_logs, driver_salaries, driver_accounts, bot, output_dir):
    """Generate PDF reports for all drivers and return a list of file paths"""
    
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)
    
    pdf_files = []
    
    # Process each driver
    for driver_id in driver_accounts.keys():
        try:
            # Get driver name
            try:
                chat = bot.get_chat(driver_id)
                driver_name = f"@{chat.username}" if chat.username else chat.first_name
            except:
                driver_name = f"Driver {driver_id}"
            
            # Generate PDF
            output_path = os.path.join(output_dir, f"driver_{driver_id}.pdf")
            generate_driver_pdf(
                driver_id, 
                driver_name, 
                driver_logs, 
                driver_salaries, 
                driver_accounts, 
                bot, 
                output_path
            )
            
            pdf_files.append(output_path)
            
        except Exception as e:
            logger.error(f"Error generating PDF for driver {driver_id}: {str(e)}")
    
    return pdf_files

def generate_single_driver_pdf(driver_id, driver_logs, driver_salaries, driver_accounts, bot, output_dir):
    """Generate PDF report for a single driver and return the file path"""
    
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)
    
    try:
        # Get driver name
        try:
            chat = bot.get_chat(driver_id)
            driver_name = f"@{chat.username}" if chat.username else chat.first_name
        except:
            driver_name = f"Driver {driver_id}"
        
        # Generate PDF
        output_path = os.path.join(output_dir, f"driver_{driver_id}.pdf")
        generate_driver_pdf(
            driver_id, 
            driver_name, 
            driver_logs, 
            driver_salaries, 
            driver_accounts, 
            bot, 
            output_path
        )
        
        return output_path
        
    except Exception as e:
        logger.error(f"Error generating PDF for driver {driver_id}: {str(e)}")
        return None

# === Telegram 命令处理函数 ===
def start(update, context):
    """处理 /start 命令"""
    user_id = update.effective_user.id
    username = update.effective_user.username or str(user_id)
    logger.info(f"User {username} started the bot")
    
    # 检查是否是管理员
    if user_id in ADMIN_IDS:
        update.message.reply_text(
            "Welcome Admin! Available commands:\n" 
            "/topup - Top up driver account\n" 
            "/PDF - Generate PDF reports\n" 
            "/salary - Set driver monthly salary\n" 
            "/migrate - Migrate data to DB (run once)\n" 
            "/export - Export data as JSON"
        )
    else:
        update.message.reply_text(
            "Welcome! Available commands:\n" 
            "/clockin - Clock in\n" 
            "/clockout - Clock out\n" 
            "/offday - Mark today as day off\n" 
            "/balance - Check account balance\n" 
            "/check - Check clock-in records\n" 
            "/claim - Submit expense claim\n" 
            "/viewclaims - View claim history"
        )

def clockin(update, context):
    """处理 /clockin 命令"""
    user_id = update.effective_user.id
    username = update.effective_user.username or str(user_id)
    today = datetime.datetime.now(tz).strftime("%Y-%m-%d")
    now = datetime.datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S")
    
    # 初始化用户记录
    if user_id not in driver_logs:
        driver_logs[user_id] = {}
    
    # 检查今天是否已经打卡
    if today in driver_logs[user_id] and driver_logs[user_id][today].get('in') not in ['N/A', 'OFF']:
        update.message.reply_text(f"⚠️ 您今天已经打卡了: {driver_logs[user_id][today]['in']}")
        return
    
    # 记录打卡时间
    driver_logs[user_id][today] = {'in': now, 'out': 'N/A'}
    
    # 保存到数据库 - 新增代码
    try:
        db_mongo.save_driver_logs(driver_logs)
        logger.info(f"用户 {username} 的打卡记录已保存到数据库")
    except Exception as e:
        logger.error(f"保存打卡记录到数据库失败: {str(e)}")
    
    update.message.reply_text(f"✅ 打卡成功: {now}")
    logger.info(f"User {username} clocked in at {now}")

def clockout(update, context):
    """处理 /clockout 命令"""
    user_id = update.effective_user.id
    username = update.effective_user.username or str(user_id)
    today = datetime.datetime.now(tz).strftime("%Y-%m-%d")
    now = datetime.datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S")
    
    # 检查是否有打卡记录
    if user_id not in driver_logs or today not in driver_logs[user_id]:
        update.message.reply_text("⚠️ 您今天还没有打卡")
        return
    
    # 检查是否已经下班打卡
    if driver_logs[user_id][today].get('out') not in ['N/A', 'OFF']:
        update.message.reply_text(f"⚠️ 您今天已经下班打卡了: {driver_logs[user_id][today]['out']}")
        return
    
    # 检查是否是休息日
    if driver_logs[user_id][today].get('in') == 'OFF':
        update.message.reply_text("⚠️ 今天是您的休息日")
        return
    
    # 记录下班时间
    driver_logs[user_id][today]['out'] = now
    
    # 计算工作时长
    try:
        in_time = datetime.datetime.strptime(driver_logs[user_id][today]['in'], "%Y-%m-%d %H:%M:%S")
        out_time = datetime.datetime.strptime(now, "%Y-%m-%d %H:%M:%S")
        duration = out_time - in_time
        hours = duration.total_seconds() / 3600
        
        # 更新总工作时长
        driver_salaries.setdefault(user_id, {"monthly_salary": DEFAULT_MONTHLY_SALARY, "total_hours": 0})
        driver_salaries[user_id]["total_hours"] += hours
        driver_salaries[user_id]["last_updated"] = now
        
        # 保存到数据库 - 新增代码
        try:
            db_mongo.save_driver_logs(driver_logs)
            db_mongo.save_driver_salaries(driver_salaries)
            logger.info(f"用户 {username} 的打卡和薪资记录已保存到数据库")
        except Exception as e:
            logger.error(f"保存记录到数据库失败: {str(e)}")
        
        # 格式化时长显示
        hours_str = format_duration(hours)
        update.message.reply_text(f"✅ 下班打卡成功: {now}\n⏱ 今日工作时长: {hours_str}")
    except Exception as e:
        logger.error(f"计算工作时长错误: {str(e)}")
        update.message.reply_text(f"✅ 下班打卡成功: {now}")
    
    logger.info(f"User {username} clocked out at {now}")

def offday(update, context):
    """处理 /offday 命令"""
    user_id = update.effective_user.id
    username = update.effective_user.username or str(user_id)
    today = datetime.datetime.now(tz).strftime("%Y-%m-%d")
    
    # 初始化用户记录
    if user_id not in driver_logs:
        driver_logs[user_id] = {}
    
    # 检查今天是否已经有记录
    if today in driver_logs[user_id] and driver_logs[user_id][today].get('in') not in ['N/A', 'OFF']:
        update.message.reply_text(f"⚠️ 您今天已经打卡了: {driver_logs[user_id][today]['in']}")
        return
    
    # 标记为休息日
    driver_logs[user_id][today] = {'in': 'OFF', 'out': 'OFF'}
    
    # 保存到数据库 - 新增代码
    try:
        db_mongo.save_driver_logs(driver_logs)
        logger.info(f"用户 {username} 的休息日记录已保存到数据库")
    except Exception as e:
        logger.error(f"保存休息日记录到数据库失败: {str(e)}")
    
    update.message.reply_text(f"✅ 已标记 {today} 为休息日")
    logger.info(f"User {username} marked {today} as day off")

def balance(update, context):
    """处理 /balance 命令"""
    user_id = update.effective_user.id
    username = update.effective_user.username or str(user_id)
    
    if user_id in driver_accounts:
        balance = driver_accounts[user_id].get("balance", 0.0)
        update.message.reply_text(f"💰 Your current balance: RM{balance:.2f}")
    else:
        update.message.reply_text("💰 Your current balance: RM0.00")
    
    logger.info(f"User {username} checked balance")

def check(update, context):
    """处理 /check 命令"""
    user_id = update.effective_user.id
    username = update.effective_user.username or str(user_id)
    
    if user_id not in driver_logs:
        update.message.reply_text("📅 No clock-in records found.")
        return
    
    response = "📅 Your recent clock-in records:\n"
    # 只显示最近7天的记录
    count = 0
    for date, log in sorted(driver_logs[user_id].items(), reverse=True):
        if count >= 7:
            break
        in_time = log.get("in", "N/A")
        out_time = log.get("out", "N/A")
        
        # 格式化时间
        if in_time != 'N/A' and in_time != 'OFF':
            in_time = format_local_time(in_time)
        if out_time != 'N/A' and out_time != 'OFF':
            out_time = format_local_time(out_time)
            
        response += f"\n*{date}*\n  In: {in_time}\n  Out: {out_time}\n"
        count += 1
        
    update.message.reply_text(response, parse_mode="Markdown")
    logger.info(f"User {username} checked clock-in records")

def viewclaims(update, context):
    """处理 /viewclaims 命令"""
    user_id = update.effective_user.id
    username = update.effective_user.username or str(user_id)
    
    if user_id not in driver_accounts or not driver_accounts[user_id].get("claims"):
        update.message.reply_text("🧾 No claims found.")
        return
    
    response = "🧾 Your recent claims:\n"
    claims = driver_accounts[user_id]["claims"]
    # 只显示最近5条记录
    for claim in sorted(claims, key=lambda x: x.get('date', ''), reverse=True)[:5]:
        date = claim.get("date", "N/A")
        type = claim.get("type", "N/A")
        amount = claim.get("amount", 0)
        response += f"\n*{date}* - {type}: RM{amount:.2f}\n"
        
    update.message.reply_text(response, parse_mode="Markdown")
    logger.info(f"User {username} viewed claims")

# === PDF 生成命令 ===
def pdf_start(update, context):
    """处理 /PDF 命令，开始PDF生成流程"""
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        update.message.reply_text("⛔ You do not have permission for this command.")
        return ConversationHandler.END
    
    # 获取所有有账户记录的司机
    drivers = []
    for driver_id in driver_accounts.keys():
        try:
            chat = bot.get_chat(driver_id)
            name = f"@{chat.username}" if chat.username else chat.first_name
            drivers.append((driver_id, name))
        except:
            drivers.append((driver_id, f"User {driver_id}"))
    
    if not drivers:
        update.message.reply_text("❌ No driver data found to generate PDF.")
        return ConversationHandler.END
    
    # 创建按钮
    keyboard = [[InlineKeyboardButton("All Drivers", callback_data="pdf_all")]]
    for driver_id, name in drivers:
        keyboard.append([InlineKeyboardButton(name, callback_data=f"pdf_{driver_id}")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    update.message.reply_text("📄 Select driver(s) to generate PDF report:", reply_markup=reply_markup)
    
    return PDF_SELECT_DRIVER

def pdf_button_callback(update, context):
    """处理PDF选择按钮的回调"""
    query = update.callback_query
    query.answer()
    
    data = query.data
    admin_id = query.from_user.id
    
    query.edit_message_text(text="⏳ Generating PDF report(s)... Please wait.")
    
    temp_dir = tempfile.mkdtemp()
    pdf_files = []
    
    try:
        if data == "pdf_all":
            pdf_files = generate_all_drivers_pdf(
                driver_logs, driver_salaries, driver_accounts, bot, temp_dir
            )
        elif data.startswith("pdf_"):
            driver_id = int(data.split("_")[1])
            pdf_path = generate_single_driver_pdf(
                driver_id, driver_logs, driver_salaries, driver_accounts, bot, temp_dir
            )
            if pdf_path:
                pdf_files.append(pdf_path)
        
        if pdf_files:
            query.edit_message_text(text="📤 Sending PDF report(s)...")
            for pdf_file in pdf_files:
                try:
                    context.bot.send_document(chat_id=admin_id, document=open(pdf_file, 'rb'))
                except Exception as send_err:
                    logger.error(f"Error sending PDF {pdf_file}: {str(send_err)}")
                    query.message.reply_text(f"❌ Error sending PDF for {os.path.basename(pdf_file)}.")
                finally:
                    # Clean up individual PDF file
                    if os.path.exists(pdf_file):
                        os.unlink(pdf_file)
            query.edit_message_text(text="✅ PDF report(s) sent successfully.")
        else:
            query.edit_message_text(text="❌ Failed to generate PDF report(s).")
            
    except Exception as e:
        logger.error(f"PDF generation error: {str(e)}")
        query.edit_message_text(text="❌ An error occurred during PDF generation.")
    finally:
        # Clean up temporary directory
        if os.path.exists(temp_dir):
            import shutil
            shutil.rmtree(temp_dir)
            
    return ConversationHandler.END

# === /salary 命令处理函数 (新增) ===
def salary_start(update, context):
    """开始设置司机月薪的流程"""
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        update.message.reply_text("⛔ You do not have permission for this command.")
        return ConversationHandler.END
    
    # 获取所有司机列表
    drivers = []
    for driver_id in driver_accounts.keys():
        try:
            chat = bot.get_chat(driver_id)
            name = f"@{chat.username}" if chat.username else chat.first_name
            drivers.append(f"{name} ({driver_id})")
        except:
            drivers.append(f"User {driver_id}")
            
    if not drivers:
        update.message.reply_text("❌ No drivers found.")
        return ConversationHandler.END
        
    # 发送司机列表供选择
    keyboard = [[driver] for driver in drivers]
    update.message.reply_text(
        "💼 Select the driver to set monthly salary:",
        reply_markup=ReplyKeyboardMarkup(keyboard, one_time_keyboard=True)
    )
    return SALARY_SELECT_DRIVER

def salary_select_driver(update, context):
    """处理管理员选择的司机"""
    admin_id = update.effective_user.id
    selected_text = update.message.text
    
    try:
        # 从文本中提取司机ID
        driver_id = int(selected_text.split('(')[-1].split(')')[0])
        context.user_data["salary_driver_id"] = driver_id
        
        # 获取当前月薪
        current_salary = DEFAULT_MONTHLY_SALARY
        if driver_id in driver_salaries:
            current_salary = driver_salaries[driver_id].get("monthly_salary", DEFAULT_MONTHLY_SALARY)
            
        update.message.reply_text(f"💰 Enter the new monthly salary for this driver (Current: RM{current_salary:.2f}):")
        return SALARY_ENTER_AMOUNT
    except:
        update.message.reply_text("❌ Invalid selection. Please select a driver from the list.")
        return SALARY_SELECT_DRIVER

def salary_enter_amount(update, context):
    """处理管理员输入的月薪金额"""
    admin_id = update.effective_user.id
    try:
        new_salary = float(update.message.text.strip())
        driver_id = context.user_data.get("salary_driver_id")
        
        if not driver_id:
            update.message.reply_text("❌ Error: No driver selected.")
            return ConversationHandler.END
            
        # 更新或创建司机薪资记录
        driver_salaries.setdefault(driver_id, {"total_hours": 0})
        driver_salaries[driver_id]["monthly_salary"] = new_salary
        driver_salaries[driver_id]["last_updated"] = datetime.datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S")
        
        # 保存到数据库 - 新增代码
        try:
            db_mongo.save_driver_salaries(driver_salaries)
            logger.info(f"管理员 {admin_id} 为用户 {driver_id} 设置月薪为 {new_salary} 的记录已保存到数据库")
        except Exception as e:
            logger.error(f"保存薪资记录到数据库失败: {str(e)}")
        
        try:
            chat = bot.get_chat(driver_id)
            name = f"@{chat.username}" if chat.username else chat.first_name
        except:
            name = f"User {driver_id}"
            
        update.message.reply_text(f"✅ Monthly salary for {name} set to RM{new_salary:.2f}.")
        logger.info(f"Admin {admin_id} set monthly salary for {name} to RM{new_salary:.2f}")
        
    except ValueError:
        update.message.reply_text("❌ Invalid amount. Please enter a number.")
        return SALARY_ENTER_AMOUNT
    except Exception as e:
        logger.error(f"Salary setting error: {str(e)}")
        update.message.reply_text("❌ An error occurred during salary setting.")
        
    # 清理状态
    if "salary_driver_id" in context.user_data:
        del context.user_data["salary_driver_id"]
        
    return ConversationHandler.END

# === /topup 分阶段 ===
def topup_start(update, context):
    """处理 /topup 命令，开始充值流程"""
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        update.message.reply_text("⛔ You do not have permission for this command.")
        return ConversationHandler.END
    
    # 获取所有司机列表
    drivers = []
    for driver_id in driver_accounts.keys():
        try:
            chat = bot.get_chat(driver_id)
            name = f"@{chat.username}" if chat.username else chat.first_name
            drivers.append(f"{name} ({driver_id})")
        except:
            drivers.append(f"User {driver_id}")
            
    if not drivers:
        update.message.reply_text("❌ No drivers found.")
        return ConversationHandler.END
        
    # 发送司机列表供选择
    keyboard = [[driver] for driver in drivers]
    update.message.reply_text(
        "👤 Select the driver to top up:",
        reply_markup=ReplyKeyboardMarkup(keyboard, one_time_keyboard=True)
    )
    return TOPUP_USER

def topup_user(update, context):
    """处理管理员选择的司机"""
    admin_id = update.effective_user.id
    selected_text = update.message.text
    
    try:
        # 从文本中提取司机ID
        uid = int(selected_text.split('(')[-1].split(')')[0])
        context.user_data["topup_uid"] = uid
        update.message.reply_text("💰 Enter top-up amount:")
        return TOPUP_AMOUNT
    except:
        update.message.reply_text("❌ Invalid selection. Please select a driver from the list.")
        return TOPUP_USER

def topup_amount(update, context):
    """处理管理员输入的充值金额"""
    admin_id = update.effective_user.id
    try:
        amount = float(update.message.text.strip())
        uid = context.user_data.get("topup_uid")
        
        if not uid:
            update.message.reply_text("❌ Error: No user selected.")
            return ConversationHandler.END
            
        driver_accounts.setdefault(uid, {"balance": 0.0, "claims": [], "topup_history": []})
        # 注意：余额增加和历史记录添加现在由db_mongo.add_topup原子化处理
        # driver_accounts[uid]["balance"] += amount
        
        # 记录充值历史
        today = datetime.datetime.now(tz).strftime("%Y-%m-%d")
        topup_record = {
            "date": today,
            "amount": amount,
            "admin": admin_id
        }
        # 注意：历史记录添加现在由db_mongo.add_topup原子化处理
        # driver_accounts[uid]["topup_history"].append(topup_record)
        
        # 保存到数据库 - 使用更高效的单用户更新方法
        try:
            db_mongo.add_topup(uid, topup_record)
            logger.info(f"管理员 {admin_id} 为用户 {uid} 充值 {amount} 的记录已保存到数据库")
            # 更新内存中的余额，以便立即显示正确余额
            driver_accounts[uid]["balance"] += amount
            driver_accounts[uid]["topup_history"].append(topup_record)
        except Exception as e:
            logger.error(f"保存充值记录到数据库失败: {str(e)}")
            # 如果数据库保存失败，需要考虑回滚内存操作或通知管理员
            update.message.reply_text("❌ Database error during topup. Please check logs.")
            return ConversationHandler.END
        
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
        
    # 清理状态
    if "topup_uid" in context.user_data:
        del context.user_data["topup_uid"]
        
    return ConversationHandler.END

# === /claim 分阶段 ===
def claim_start(update, context):
    """处理 /claim 命令，开始报销流程"""
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
    """处理用户选择的报销类型"""
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
    """处理用户输入的自定义报销类型"""
    user_id = update.effective_user.id
    username = update.effective_user.username or str(user_id)
    claim_state[user_id]["type"] = update.message.text
    
    logger.info(f"User {username} entered custom claim type: {update.message.text}")
    
    update.message.reply_text("💰 Enter amount:")
    return CLAIM_AMOUNT

def claim_amount(update, context):
    """处理用户输入的报销金额"""
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
    """处理用户发送的报销凭证照片"""
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

    driver_accounts.setdefault(user_id, {"balance": 0.0, "claims": [], "topup_history": []})
    # 注意：余额减少和报销记录添加现在由db_mongo.add_claim原子化处理
    # driver_accounts[user_id]["claims"].append(entry)
    # driver_accounts[user_id]["balance"] -= entry["amount"]

    # 保存到数据库 - 使用更高效的单用户更新方法
    try:
        db_mongo.add_claim(user_id, entry)
        logger.info(f"用户 {username} 的报销记录已保存到数据库")
        # 更新内存中的余额和记录，以便立即显示正确信息
        driver_accounts[user_id]["claims"].append(entry)
        driver_accounts[user_id]["balance"] -= entry["amount"]
    except Exception as e:
        logger.error(f"保存报销记录到数据库失败: {str(e)}")
        # 如果数据库保存失败，需要考虑回滚内存操作或通知用户
        update.message.reply_text("❌ Database error during claim. Please check logs.")
        return ConversationHandler.END

    response = f"✅ RM{entry['amount']} claimed for {entry['type']} on {entry['date']}."
    update.message.reply_text(response)
    
    logger.info(f"User {username} completed claim: {response}")
    
    # 清理状态
    if user_id in claim_state:
        del claim_state[user_id]

    return ConversationHandler.END

def cancel(update, context):
    """处理取消操作"""
    user_id = update.effective_user.id
    username = update.effective_user.username or str(user_id)
    
    update.message.reply_text("❌ Operation cancelled.", reply_markup=ReplyKeyboardRemove())
    
    # 清理所有可能的状态
    if user_id in claim_state:
        del claim_state[user_id]
    if user_id in topup_state:
        del topup_state[user_id]
    if user_id in pdf_state:
        del pdf_state[user_id]
    if user_id in salary_state:
        del salary_state[user_id]
    if "topup_uid" in context.user_data:
        del context.user_data["topup_uid"]
    if "salary_driver_id" in context.user_data:
        del context.user_data["salary_driver_id"]
    
    logger.info(f"User {username} cancelled operation")
    
    return ConversationHandler.END

# === 数据迁移和导出命令 (管理员) ===
def migrate_data(update, context):
    """将内存数据迁移到数据库 (管理员命令)"""
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        update.message.reply_text("⛔ 您没有权限执行此操作")
        return
    
    try:
        # 保存所有数据到数据库
        db_mongo.save_driver_logs(driver_logs)
        db_mongo.save_driver_salaries(driver_salaries)
        db_mongo.save_driver_accounts(driver_accounts)
        update.message.reply_text("✅ 数据迁移成功")
        logger.info(f"管理员 {user_id} 执行了数据迁移")
    except Exception as e:
        update.message.reply_text(f"❌ 数据迁移失败: {str(e)}")
        logger.error(f"数据迁移失败: {str(e)}")

def export_data(update, context):
    """导出数据为JSON文件 (管理员命令)"""
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        update.message.reply_text("⛔ 您没有权限执行此操作")
        return
    
    try:
        # 创建临时文件保存数据
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False, encoding='utf-8') as logs_file:
            json.dump(driver_logs, logs_file, ensure_ascii=False, indent=2)
            logs_path = logs_file.name
            
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False, encoding='utf-8') as salaries_file:
            json.dump(driver_salaries, salaries_file, ensure_ascii=False, indent=2)
            salaries_path = salaries_file.name
            
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False, encoding='utf-8') as accounts_file:
            json.dump(driver_accounts, accounts_file, ensure_ascii=False, indent=2)
            accounts_path = accounts_file.name
        
        # 发送文件
        update.message.reply_text("📤 正在发送数据备份文件...")
        context.bot.send_document(chat_id=user_id, document=open(logs_path, 'rb'), filename='driver_logs.json')
        context.bot.send_document(chat_id=user_id, document=open(salaries_path, 'rb'), filename='driver_salaries.json')
        context.bot.send_document(chat_id=user_id, document=open(accounts_path, 'rb'), filename='driver_accounts.json')
        
        update.message.reply_text("✅ 数据导出成功")
        logger.info(f"管理员 {user_id} 导出了数据备份")
        
        # 清理临时文件
        os.unlink(logs_path)
        os.unlink(salaries_path)
        os.unlink(accounts_path)
    except Exception as e:
        update.message.reply_text(f"❌ 数据导出失败: {str(e)}")
        logger.error(f"数据导出失败: {str(e)}")

# === 错误处理 ===
def error_handler(update, context):
    """处理发生的错误"""
    logger.error(f"Update {update} caused error {context.error}")
    traceback.print_exc()  # 打印完整的错误堆栈信息
    # 可以在这里添加向管理员发送错误通知的逻辑
    if update and update.effective_message:
        update.effective_message.reply_text("❌ An unexpected error occurred. Please try again later or contact admin.")

# === Webhook 设置 ===
@app.route(f"/{TOKEN}", methods=["POST"])
def webhook():
    """处理来自Telegram的Webhook请求"""
    update = Update.de_json(request.get_json(force=True), bot)
    dispatcher.process_update(update)
    return "ok"

# === Dispatcher 注册命令和会话处理器 ===
# 基本命令
dispatcher.add_handler(CommandHandler("start", start))
dispatcher.add_handler(CommandHandler("clockin", clockin))
dispatcher.add_handler(CommandHandler("clockout", clockout))
dispatcher.add_handler(CommandHandler("offday", offday))
dispatcher.add_handler(CommandHandler("balance", balance))
dispatcher.add_handler(CommandHandler("check", check))
dispatcher.add_handler(CommandHandler("viewclaims", viewclaims))

# PDF 生成会话
dispatcher.add_handler(ConversationHandler(
    entry_points=[CommandHandler("PDF", pdf_start)],
    states={
        PDF_SELECT_DRIVER: [CallbackQueryHandler(pdf_button_callback, pattern=r'^pdf_')]
    },
    fallbacks=[CommandHandler("cancel", cancel)],
    allow_reentry=True
))

# 薪资设置会话 (管理员)
dispatcher.add_handler(ConversationHandler(
    entry_points=[CommandHandler("salary", salary_start)],
    states={
        SALARY_SELECT_DRIVER: [MessageHandler(Filters.text & ~Filters.command, salary_select_driver)],
        SALARY_ENTER_AMOUNT: [MessageHandler(Filters.text & ~Filters.command, salary_enter_amount)],
    },
    fallbacks=[CommandHandler("cancel", cancel)],
))

# 充值会话 (管理员)
dispatcher.add_handler(ConversationHandler(
    entry_points=[CommandHandler("topup", topup_start)],
    states={
        TOPUP_USER: [MessageHandler(Filters.text & ~Filters.command, topup_user)],
        TOPUP_AMOUNT: [MessageHandler(Filters.text & ~Filters.command, topup_amount)],
    },
    fallbacks=[CommandHandler("cancel", cancel)],
))

# 报销会话
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

# 管理员命令
dispatcher.add_handler(CommandHandler("migrate", migrate_data))
dispatcher.add_handler(CommandHandler("export", export_data))

# 注册错误处理器
dispatcher.add_error_handler(error_handler)

# === 应用关闭时保存数据 (可选) ===
@app.teardown_appcontext
def save_data_on_shutdown(exception=None):
    """在应用关闭时尝试保存数据到数据库 (最佳实践)"""
    try:
        # 注意：频繁修改的数据已在操作时保存，这里可以作为最后保障
        # 如果需要确保所有最新状态都保存，可以再次调用保存函数
        # db_mongo.save_driver_logs(driver_logs)
        # db_mongo.save_driver_salaries(driver_salaries)
        # db_mongo.save_driver_accounts(driver_accounts)
        logger.info("应用关闭，数据已在操作时保存到MongoDB")
    except Exception as e:
        logger.error(f"应用关闭时尝试保存数据失败: {str(e)}")

# === 启动 Flask 应用 ===
if __name__ == "__main__":
    logger.info("Bot server started with MongoDB integration.")
    # 使用gunicorn等WSGI服务器部署时，不会执行这里的app.run
    # Render通常会使用Procfile或启动命令来运行gunicorn
    # 例如: gunicorn clock_bot_mongo:app
    # 本地测试时可以取消注释下一行
    # app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))

