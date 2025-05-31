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
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

# ✅ 引入 pymongo，用于连接 MongoDB Atlas
from pymongo import MongoClient

# 在 clock_bot.py 中修改连接代码
mongo_uri = os.environ.get("MONGO_URI", "")
if not mongo_uri.startswith("mongodb"):
    mongo_uri = "mongodb+srv://fiofai:kienfeilowfio@cluster0.fy6uhn1.mongodb.net/?retryWrites=true&w=majority&appName=Cluster0"
client = MongoClient(mongo_uri)


# ✅ 定义数据库和集合
db = client["clockin_bot"]
drivers_collection = db["drivers"]

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

TOKEN = os.environ.get("TOKEN")
ADMIN_IDS = [1165249082]
DEFAULT_HOURLY_RATE = 20.00  # 默认时薪，RM20/小时
DEFAULT_MONTHLY_SALARY = 3500.00  # 默认月薪，RM3500
WORKING_DAYS_PER_MONTH = 22  # 默认每月工作天数
WORKING_HOURS_PER_DAY = 8  # 默认每天工作小时数

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
pdf_state = {}  # 用于存储PDF生成状态
salary_state = {}  # 新增：用于存储薪资设置状态

tz = pytz.timezone("Asia/Kuala_Lumpur")

# === conversation 状态 ===
TOPUP_USER, TOPUP_AMOUNT = range(2)
CLAIM_TYPE, CLAIM_OTHER_TYPE, CLAIM_AMOUNT, CLAIM_PROOF = range(4)
PDF_SELECT_DRIVER = range(1)  # PDF司机选择状态
SALARY_SELECT_DRIVER, SALARY_ENTER_AMOUNT = range(2)  # 新增：薪资设置状态

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

# === /start ===
def start(update, context):
    user = update.effective_user
    user_id = user.id
    username = user.username or str(user_id)

    driver_logs.setdefault(user_id, {})
    driver_salaries.setdefault(user_id, {"total_hours": 0.0, "daily_log": {}})
    driver_accounts.setdefault(user_id, {"balance": 0.0, "claims": [], "topup_history": []})

    msg = (
        f"👋 Hello {user.first_name}!\n"
        "Welcome to Driver ClockIn Bot.\n\n"
        "Available Commands:\n"
        "🕑 /clockin\n"
        "🏁 /clockout\n"
        "📅 /offday\n"
        "💸 /claim"
    )
    if user_id in ADMIN_IDS:
        msg += (
            "\n\n🔐 Admin Commands:\n"
            "📊 /balance\n"
            "📄 /check\n"
            "🧾 /PDF\n"
            "💵 /topup\n"
            "📷 /viewclaims\n"
            "💰 /salary"  # 新增薪资设置命令
        )

    update.message.reply_text(msg)
    logger.info(f"User {username} started the bot")

# === /clockin ===
def clockin(update, context):
    user_id = update.effective_user.id
    username = update.effective_user.username or str(user_id)
    now = datetime.datetime.now(tz)
    today = now.strftime("%Y-%m-%d")
    clock_time = now.strftime("%Y-%m-%d %H:%M:%S")

    driver_logs.setdefault(user_id, {}).setdefault(today, {})['in'] = clock_time
    
    # 修复：使用format_local_time确保显示本地时间格式
    local_time = format_local_time(clock_time)
    update.message.reply_text(f"✅ Clocked in at {local_time}")
    
    logger.info(f"User {username} clocked in at {clock_time}")

# === /clockout ===
def clockout(update, context):
    user_id = update.effective_user.id
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
            time_str = f"{hours}Hour {minutes}Min"
        elif hours:
            time_str = f"{hours}Hour"
        else:
            time_str = f"{minutes}Min"

        # 确保薪资记录存在
        if user_id not in driver_salaries:
            driver_salaries[user_id] = {'total_hours': 0.0, 'daily_log': {}}
        
        # 更新工时
        hours_worked = total_seconds / 3600
        driver_salaries[user_id]['total_hours'] += hours_worked
        driver_salaries[user_id]['daily_log'][today] = hours_worked

        # 修复：使用format_local_time确保显示本地时间格式
        local_time = format_local_time(clock_time)
        update.message.reply_text(f"🏁 Clocked out at {local_time}. Worked {time_str}.")
        
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
    username = update.effective_user.username or str(user_id)
    today = datetime.datetime.now(tz).strftime("%Y-%m-%d")
    driver_logs.setdefault(user_id, {})[today] = {"in": "OFF", "out": "OFF"}
    update.message.reply_text(f"📅 Marked {today} as off day.")
    logger.info(f"User {username} marked {today} as off day")

# === /balance（管理员）===
def balance(update, context):
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        return
    
    logger.info(f"Admin {user_id} requested balance")
    
    msg = "📊 Driver Balances:\n"
    for uid, acc in driver_accounts.items():
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
        return
    
    logger.info(f"Admin {user_id} requested check")
    
    today = datetime.datetime.now(tz).strftime("%Y-%m-%d")
    msg = "📄 Today's Status:\n"
    for uid, log in driver_logs.items():
        day = log.get(today, {})
        in_time = day.get("in", "❌")
        if in_time != "❌" and in_time != "OFF":
            in_time = format_local_time(in_time)
            
        out_time = day.get("out", "❌")
        if out_time != "❌" and out_time != "OFF":
            out_time = format_local_time(out_time)
            
        try:
            chat = bot.get_chat(uid)
            name = f"@{chat.username}" if chat.username else chat.first_name
        except Exception as e:
            logger.error(f"Error getting chat for user {uid}: {str(e)}")
            name = f"User {uid}"
        msg += f"• {name}: IN: {in_time}, OUT: {out_time}\n"
    update.message.reply_text(msg)

# === /viewclaims（管理员）===
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
        for c in claims[-5:]:  # 显示最多 5 条
            msg += f"• {c['date']} - RM{c['amount']} ({c['type']})\n"

    update.message.reply_text(msg)

# === /salary (管理员) - 新增薪资设置功能 ===
def salary_start(update, context):
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        return update.message.reply_text("❌ You are not an admin.")
    
    logger.info(f"Admin {user_id} started salary setting process")
    
    keyboard = []
    salary_state[user_id] = {}
    
    # 添加司机选项
    for uid in driver_accounts.keys():
        try:
            chat = bot.get_chat(uid)
            name = f"@{chat.username}" if chat.username else chat.first_name
            keyboard.append([name])
            salary_state[user_id][name] = uid
        except Exception as e:
            logger.error(f"Error getting chat for user {uid}: {str(e)}")
            name = f"User {uid}"
            keyboard.append([name])
            salary_state[user_id][name] = uid

    if not keyboard:
        update.message.reply_text("❌ No drivers found.")
        return ConversationHandler.END

    update.message.reply_text(
        "👤 Select driver to set salary:",
        reply_markup=ReplyKeyboardMarkup(keyboard, one_time_keyboard=True)
    )
    return SALARY_SELECT_DRIVER

def salary_select_driver(update, context):
    admin_id = update.effective_user.id
    selected = update.message.text.strip()
    
    logger.info(f"Admin {admin_id} selected driver: {selected}")

    if admin_id not in salary_state or selected not in salary_state[admin_id]:
        update.message.reply_text("❌ Invalid selection.")
        return ConversationHandler.END

    driver_id = salary_state[admin_id][selected]
    context.user_data["salary_driver_id"] = driver_id
    context.user_data["salary_driver_name"] = selected
    
    # 获取当前薪资（如果有）
    current_salary = "not set"
    if driver_id in driver_salaries and "monthly_salary" in driver_salaries[driver_id]:
        current_salary = f"RM{driver_salaries[driver_id]['monthly_salary']:.2f}"
    
    update.message.reply_text(
        f"💰 Enter monthly salary for {selected}:\n"
        f"Current salary: {current_salary}",
        reply_markup=ReplyKeyboardRemove()
    )
    return SALARY_ENTER_AMOUNT

def salary_enter_amount(update, context):
    admin_id = update.effective_user.id
    try:
        monthly_salary = float(update.message.text.strip())
        driver_id = context.user_data.get("salary_driver_id")
        driver_name = context.user_data.get("salary_driver_name")
        
        if not driver_id:
            update.message.reply_text("❌ Error: No driver selected.")
            return ConversationHandler.END
            
        # 确保司机薪资记录存在
        driver_salaries.setdefault(driver_id, {
            "total_hours": 0.0, 
            "daily_log": {}
        })
        
        # 设置月薪
        driver_salaries[driver_id]["monthly_salary"] = monthly_salary
        
        # 计算时薪
        hourly_rate = calculate_hourly_rate(monthly_salary)
        
        update.message.reply_text(
            f"✅ Set monthly salary for {driver_name}:\n"
            f"Monthly: RM{monthly_salary:.2f}\n"
            f"Hourly: RM{hourly_rate:.2f}\n"
            f"(Based on {WORKING_DAYS_PER_MONTH} days/month, {WORKING_HOURS_PER_DAY} hours/day)"
        )
        
        logger.info(f"Admin {admin_id} set salary for {driver_name}: RM{monthly_salary:.2f}/month")
    except ValueError:
        update.message.reply_text("❌ Invalid amount. Please enter a number.")
        return SALARY_ENTER_AMOUNT
    except Exception as e:
        logger.error(f"Salary setting error: {str(e)}")
        update.message.reply_text("❌ An error occurred during salary setting.")
    
    return ConversationHandler.END

# === /PDF (管理员) - 支持选择司机 ===
def pdf_start(update, context):
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        return update.message.reply_text("❌ You are not an admin.")
    
    logger.info(f"Admin {user_id} started PDF generation process")
    
    # 创建司机选择键盘
    keyboard = []
    pdf_state[user_id] = {}
    
    # 添加"所有司机"选项
    keyboard.append([InlineKeyboardButton("📊 All Drivers", callback_data="pdf_all")])
    
    # 添加单个司机选项
    for uid in driver_accounts.keys():
        try:
            chat = bot.get_chat(uid)
            name = f"@{chat.username}" if chat.username else chat.first_name
            keyboard.append([InlineKeyboardButton(f"👤 {name}", callback_data=f"pdf_{uid}")])
            pdf_state[user_id][f"pdf_{uid}"] = uid
        except Exception as e:
            logger.error(f"Error getting chat for user {uid}: {str(e)}")
            name = f"User {uid}"
            keyboard.append([InlineKeyboardButton(f"👤 {name}", callback_data=f"pdf_{uid}")])
            pdf_state[user_id][f"pdf_{uid}"] = uid

    if len(keyboard) <= 1:  # 只有"所有司机"选项
        update.message.reply_text("❌ No drivers found.")
        return
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    update.message.reply_text(
        "🧾 Select driver for PDF report:",
        reply_markup=reply_markup
    )

def pdf_button_callback(update, context):
    query = update.callback_query
    user_id = query.from_user.id
    
    if user_id not in ADMIN_IDS:
        query.answer("❌ You are not an admin.")
        return
    
    query.answer()  # 通知Telegram已处理回调
    
    callback_data = query.data
    logger.info(f"Admin {user_id} selected: {callback_data}")
    
    # 处理"所有司机"选项
    if callback_data == "pdf_all":
        query.edit_message_text("🔄 Generating PDF reports for all drivers. This may take a moment...")
        generate_all_pdfs(query)
        return
    
    # 处理单个司机选项
    if user_id in pdf_state and callback_data in pdf_state[user_id]:
        driver_id = pdf_state[user_id][callback_data]
        query.edit_message_text(f"🔄 Generating PDF report. This may take a moment...")
        generate_single_pdf(query, driver_id)
    else:
        query.edit_message_text("❌ Invalid selection or session expired.")

def generate_all_pdfs(query):
    """生成所有司机的PDF报告"""
    try:
        # Create temp directory for PDFs
        temp_dir = tempfile.mkdtemp()
        
        # Generate PDFs
        pdf_files = generate_all_drivers_pdf(
            driver_logs, 
            driver_salaries, 
            driver_accounts, 
            bot, 
            temp_dir
        )
        
        if not pdf_files:
            query.edit_message_text("❌ No driver data available to generate PDFs.")
            return
        
        # Send each PDF
        for pdf_file in pdf_files:
            try:
                with open(pdf_file, 'rb') as f:
                    bot.send_document(
                        chat_id=query.message.chat_id,
                        document=f,
                        filename=os.path.basename(pdf_file),
                        caption="Driver Report"
                    )
            except Exception as e:
                logger.error(f"Error sending PDF: {str(e)}")
                bot.send_message(
                    chat_id=query.message.chat_id,
                    text=f"❌ Error sending PDF: {str(e)}"
                )
        
        query.edit_message_text(f"✅ Generated {len(pdf_files)} PDF reports.")
        
    except Exception as e:
        logger.error(f"PDF generation error: {str(e)}")
        logger.exception(e)
        query.edit_message_text(f"❌ Error generating PDFs: {str(e)}")

def generate_single_pdf(query, driver_id):
    """生成单个司机的PDF报告"""
    try:
        # Create temp directory for PDF
        temp_dir = tempfile.mkdtemp()
        
        # Generate PDF
        pdf_file = generate_single_driver_pdf(
            driver_id, 
            driver_logs, 
            driver_salaries, 
            driver_accounts, 
            bot, 
            temp_dir
        )
        
        if not pdf_file:
            query.edit_message_text("❌ No data available to generate PDF.")
            return
        
        # Send PDF
        try:
            with open(pdf_file, 'rb') as f:
                bot.send_document(
                    chat_id=query.message.chat_id,
                    document=f,
                    filename=os.path.basename(pdf_file),
                    caption="Driver Report"
                )
            query.edit_message_text("✅ PDF report generated successfully.")
        except Exception as e:
            logger.error(f"Error sending PDF: {str(e)}")
            query.edit_message_text(f"❌ Error sending PDF: {str(e)}")
        
    except Exception as e:
        logger.error(f"PDF generation error: {str(e)}")
        logger.exception(e)
        query.edit_message_text(f"❌ Error generating PDF: {str(e)}")

# === /topup (交互流程管理员专用) ===
def topup_start(update, context):
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        return
    
    logger.info(f"Admin {user_id} started topup process")
    
    keyboard = []
    topup_state[user_id] = {}
    for uid in driver_accounts:
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
        update.message.reply_text("❌ No drivers found.")
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
            
        driver_accounts.setdefault(uid, {"balance": 0.0, "claims": [], "topup_history": []})
        driver_accounts[uid]["balance"] += amount
        
        # 记录充值历史
        today = datetime.datetime.now(tz).strftime("%Y-%m-%d")
        topup_record = {
            "date": today,
            "amount": amount,
            "admin": admin_id
        }
        driver_accounts[uid]["topup_history"].append(topup_record)
        
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
    username = update.effective_user.username or str(user_id)
    
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
    driver_accounts[user_id]["claims"].append(entry)
    driver_accounts[user_id]["balance"] -= entry["amount"]

    response = f"✅ RM{entry['amount']} claimed for {entry['type']} on {entry['date']}."
    update.message.reply_text(response)
    
    logger.info(f"User {username} completed claim: {response}")

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
    if user_id in pdf_state:
        del pdf_state[user_id]
    if user_id in salary_state:
        del salary_state[user_id]
    
    logger.info(f"User {username} cancelled operation")
    
    return ConversationHandler.END

@app.route("/webhook", methods=["POST"])
def webhook():
    print("🔥 收到 Telegram webhook 请求")
    update = Update.de_json(request.get_json(force=True), bot)
    dispatcher.process_update(update)
    return "ok", 200



# === Dispatcher 注册 ===
dispatcher.add_handler(CommandHandler("start", start))
dispatcher.add_handler(CommandHandler("clockin", clockin))
dispatcher.add_handler(CommandHandler("clockout", clockout))
dispatcher.add_handler(CommandHandler("offday", offday))
dispatcher.add_handler(CommandHandler("balance", balance))
dispatcher.add_handler(CommandHandler("check", check))
dispatcher.add_handler(CommandHandler("viewclaims", viewclaims))
dispatcher.add_handler(CommandHandler("PDF", pdf_start))
dispatcher.add_handler(CallbackQueryHandler(pdf_button_callback, pattern=r'^pdf_'))

# === salary handler - 新增薪资设置处理器 ===
dispatcher.add_handler(ConversationHandler(
    entry_points=[CommandHandler("salary", salary_start)],
    states={
        SALARY_SELECT_DRIVER: [MessageHandler(Filters.text & ~Filters.command, salary_select_driver)],
        SALARY_ENTER_AMOUNT: [MessageHandler(Filters.text & ~Filters.command, salary_enter_amount)],
    },
    fallbacks=[CommandHandler("cancel", cancel)],
))

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

# === 注册错误处理器 ===
dispatcher.add_error_handler(error_handler)

# === Run ===
if __name__ == "__main__":
    logger.info("Bot server started.")
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))


# === PingDB 测试命令，用于测试 MongoDB 写入是否成功 ===
def pingdb(update, context):
    try:
        test_data = {
            "test": True,
            "username": update.effective_user.username or "unknown",
            "timestamp": datetime.datetime.now(tz)
        }
        db.test_collection.insert_one(test_data)
        update.message.reply_text("✅ MongoDB insert success!")
    except Exception as e:
        update.message.reply_text(f"❌ MongoDB error: {str(e)}")

# === 注册 /pingdb 指令 ===
dispatcher.add_handler(CommandHandler("pingdb", pingdb))
