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
import psycopg2
from psycopg2 import pool
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from dotenv import load_dotenv
from pathlib import Path

# 加载环境变量
env_path = Path('.') / '.env'
load_dotenv(dotenv_path=env_path)

# === 初始化设置 ===
app = Flask(__name__)

TOKEN = os.getenv("TOKEN")
ADMIN_IDS = list(map(int, os.getenv("ADMIN_IDS", "1165249082").split(",")))
DEFAULT_HOURLY_RATE = float(os.getenv("DEFAULT_HOURLY_RATE", "20.00"))
DEFAULT_MONTHLY_SALARY = float(os.getenv("DEFAULT_MONTHLY_SALARY", "3500.00"))
WORKING_DAYS_PER_MONTH = int(os.getenv("WORKING_DAYS_PER_MONTH", "22"))
WORKING_HOURS_PER_DAY = int(os.getenv("WORKING_HOURS_PER_DAY", "8"))

# 设置时区
os.environ['TZ'] = os.getenv('TZ', 'Asia/Kuala_Lumpur')

# === 日志设置 ===
logging.basicConfig(
    level=os.getenv('LOG_LEVEL', 'INFO'),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# === 状态常量 ===
SALARY_SELECT_DRIVER = 0
SALARY_ENTER_AMOUNT = 1
TOPUP_USER = 0
TOPUP_AMOUNT = 1
CLAIM_TYPE = 0
CLAIM_OTHER_TYPE = 1
CLAIM_AMOUNT = 2
CLAIM_PROOF = 3

# === 数据库连接池 ===
db_pool = None

def init_db():
    """初始化数据库连接池和表结构"""
    global db_pool
    try:
        db_pool = psycopg2.pool.SimpleConnectionPool(
            minconn=1,
            maxconn=10,
            dsn=os.environ.get("DATABASE_URL")
        )
        
        with db_pool.getconn() as conn:
            with conn.cursor() as cur:
                # 创建司机表
                cur.execute("""
                CREATE TABLE IF NOT EXISTS drivers (
                    user_id BIGINT PRIMARY KEY,
                    username TEXT,
                    first_name TEXT,
                    balance FLOAT DEFAULT 0.0,
                    monthly_salary FLOAT DEFAULT 3500.0,
                    total_hours FLOAT DEFAULT 0.0
                )
                """)
                
                # 打卡记录表
                cur.execute("""
                CREATE TABLE IF NOT EXISTS clock_logs (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT REFERENCES drivers(user_id),
                    date DATE NOT NULL,
                    clock_in TEXT,
                    clock_out TEXT,
                    is_off BOOLEAN DEFAULT FALSE,
                    UNIQUE(user_id, date)
                )
                """)
                
                # 充值记录表
                cur.execute("""
                CREATE TABLE IF NOT EXISTS topups (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT REFERENCES drivers(user_id),
                    amount FLOAT NOT NULL,
                    date TEXT NOT NULL,
                    admin_id BIGINT
                )
                """)
                
                # 报销记录表
                cur.execute("""
                CREATE TABLE IF NOT EXISTS claims (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT REFERENCES drivers(user_id),
                    type TEXT NOT NULL,
                    amount FLOAT NOT NULL,
                    date TEXT NOT NULL,
                    photo_file_id TEXT
                )
                """)
                conn.commit()
        logger.info("Database initialized successfully")
    except Exception as e:
        logger.error(f"Database initialization failed: {e}")
        raise

# === 辅助函数 ===
def get_driver(user_id):
    """获取司机信息"""
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM drivers WHERE user_id = %s", (user_id,))
            return cur.fetchone()
    finally:
        release_db_connection(conn)

def update_driver(user_id, username=None, first_name=None, balance=None, monthly_salary=None, total_hours=None):
    """更新司机信息"""
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            # 检查司机是否存在
            cur.execute("SELECT 1 FROM drivers WHERE user_id = %s", (user_id,))
            if not cur.fetchone():
                # 插入新司机
                cur.execute(
                    "INSERT INTO drivers (user_id, username, first_name) VALUES (%s, %s, %s)",
                    (user_id, username, first_name)
                )
            
            updates = []
            params = []
            
            if username is not None:
                updates.append("username = %s")
                params.append(username)
            if first_name is not None:
                updates.append("first_name = %s")
                params.append(first_name)
            if balance is not None:
                updates.append("balance = %s")
                params.append(balance)
            if monthly_salary is not None:
                updates.append("monthly_salary = %s")
                params.append(monthly_salary)
            if total_hours is not None:
                updates.append("total_hours = %s")
                params.append(total_hours)
            
            if updates:
                query = "UPDATE drivers SET " + ", ".join(updates) + " WHERE user_id = %s"
                params.append(user_id)
                cur.execute(query, params)
            
            conn.commit()
    finally:
        release_db_connection(conn)

def format_local_time(timestamp_str):
    try:
        dt = datetime.datetime.strptime(timestamp_str, "%Y-%m-%d %H:%M:%S")
        return dt.strftime("%Y-%m-%d %H:%M")
    except:
        return timestamp_str

def format_duration(hours):
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
        return str(hours)

def get_month_date_range(date=None):
    if date is None:
        date = datetime.datetime.now(pytz.timezone("Asia/Kuala_Lumpur"))
    
    year = date.year
    month = date.month
    first_day = datetime.date(year, month, 1)
    last_day = datetime.date(year, month, calendar.monthrange(year, month)[1])
    return first_day, last_day

def calculate_hourly_rate(monthly_salary):
    try:
        return round(float(monthly_salary) / (WORKING_DAYS_PER_MONTH * WORKING_HOURS_PER_DAY), 2)
    except:
        return DEFAULT_HOURLY_RATE

# === PDF 生成功能 ===
def download_telegram_photo(file_id, bot):
    try:
        file = bot.get_file(file_id)
        temp_file = tempfile.NamedTemporaryFile(delete=False, suffix='.jpg')
        file.download(temp_file.name)
        return temp_file.name
    except Exception as e:
        logger.error(f"Error downloading photo: {str(e)}")
        return None

def generate_driver_pdf(driver_id, driver_name, bot, output_path):
    """生成司机PDF报告"""
    doc = SimpleDocTemplate(
        output_path,
        pagesize=A4,
        rightMargin=72,
        leftMargin=72,
        topMargin=72,
        bottomMargin=72
    )
    
    styles = getSampleStyleSheet()
    elements = []
    
    # 获取司机数据
    with db_pool.getconn() as conn:
        with conn.cursor() as cur:
            # 基本信息
            cur.execute("SELECT * FROM drivers WHERE user_id = %s", (driver_id,))
            driver = cur.fetchone()
            
            # 打卡记录
            cur.execute("""
            SELECT date, clock_in, clock_out, is_off 
            FROM clock_logs 
            WHERE user_id = %s 
            ORDER BY date DESC
            """, (driver_id,))
            clock_logs = cur.fetchall()
            
            # 报销记录
            cur.execute("""
            SELECT type, amount, date, photo_file_id 
            FROM claims 
            WHERE user_id = %s 
            ORDER BY date DESC
            """, (driver_id,))
            claims = cur.fetchall()
            
            # 充值记录
            cur.execute("""
            SELECT amount, date 
            FROM topups 
            WHERE user_id = %s 
            ORDER BY date DESC
            """, (driver_id,))
            topups = cur.fetchall()
    
    # 标题
    title = Paragraph(f"Driver Report: {driver_name}", styles['Title'])
    elements.append(title)
    elements.append(Spacer(1, 12))
    
    # 打卡记录表格
    elements.append(Paragraph("Daily Clock Records", styles['Heading2']))
    clock_data = [['Date', 'Clock In', 'Clock Out', 'Hours']]
    total_hours = driver[5] if driver else 0.0
    
    for log in clock_logs:
        date, in_time, out_time, is_off = log
        date_str = date.strftime("%Y-%m-%d")
        
        if is_off:
            clock_data.append([date_str, "OFF", "OFF", "OFF"])
            continue
            
        in_time_str = format_local_time(in_time) if in_time else "N/A"
        out_time_str = format_local_time(out_time) if out_time else "N/A"
        
        hours = "N/A"
        if in_time and out_time:
            try:
                in_dt = datetime.datetime.strptime(in_time, "%Y-%m-%d %H:%M:%S")
                out_dt = datetime.datetime.strptime(out_time, "%Y-%m-%d %H:%M:%S")
                duration = out_dt - in_dt
                hours_float = duration.total_seconds() / 3600
                hours = format_duration(hours_float)
            except:
                hours = "Error"
                
        clock_data.append([date_str, in_time_str, out_time_str, hours])
    
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
        elements.append(Paragraph("No clock records found.", styles['Normal']))
    
    elements.append(Spacer(1, 20))
    
    # 报销记录
    elements.append(Paragraph("Expense Claims", styles['Heading2']))
    
    if claims:
        for claim in claims:
            claim_type, amount, date, photo_id = claim
            claim_data = [
                [f"Date: {date}", f"Type: {claim_type}", f"Amount: RM{amount:.2f}"]
            ]
            
            claim_table = Table(claim_data, colWidths=[120, 120, 120])
            claim_table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.lightblue),
                ('GRID', (0, 0), (-1, -1), 1, colors.black),
                ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
                ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ]))
            elements.append(claim_table)
            
            if photo_id:
                try:
                    photo_path = download_telegram_photo(photo_id, bot)
                    if photo_path:
                        img = Image(photo_path, width=300, height=200)
                        elements.append(img)
                        elements.append(Spacer(1, 6))
                except Exception as e:
                    elements.append(Paragraph(f"Error loading photo: {str(e)}", styles['Normal']))
            
            elements.append(Spacer(1, 10))
    else:
        elements.append(Paragraph("No claims found.", styles['Normal']))
    
    elements.append(Spacer(1, 20))
    
    # 摘要部分
    elements.append(Paragraph("Summary", styles['Heading2']))
    
    first_day, last_day = get_month_date_range()
    elements.append(Paragraph(
        f"Summary Period: {first_day.strftime('%Y-%m-%d')} to {last_day.strftime('%Y-%m-%d')}",
        styles['Normal']
    ))
    
    hourly_rate = calculate_hourly_rate(driver[4]) if driver else DEFAULT_HOURLY_RATE
    monthly_salary = f"RM{driver[4]:.2f}" if driver else "N/A"
    gross_pay = total_hours * hourly_rate
    
    elements.append(Paragraph(
        f"Monthly Salary: {monthly_salary}\n"
        f"Hourly Rate: RM{hourly_rate:.2f}\n"
        f"Total Hours: {format_duration(total_hours)}\n"
        f"Gross Pay: RM{gross_pay:.2f}",
        styles['Normal']
    ))
    
    # 账户摘要
    total_claims = sum(claim[1] for claim in claims)
    balance = driver[3] if driver else 0.0
    
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
        ('BACKGROUND', (0, 1), (-1, -1), colors.lightgrey),
        ('GRID', (0, 0), (-1, -1), 1, colors.black),
    ]))
    elements.append(summary_table)
    
    doc.build(elements)
    return output_path

# === 命令处理函数 ===
def start(update, context):
    user = update.effective_user
    update_driver(
        user.id,
        username=user.username,
        first_name=user.first_name
    )
    
    msg = (
        f"👋 Hello {user.first_name}!\n"
        "Welcome to Driver ClockIn Bot.\n\n"
        "Available Commands:\n"
        "🕑 /clockin\n"
        "🏁 /clockout\n"
        "📅 /offday\n"
        "💸 /claim"
    )
    if user.id in ADMIN_IDS:
        msg += (
            "\n\n🔐 Admin Commands:\n"
            "📊 /balance\n"
            "📄 /check\n"
            "🧾 /PDF\n"
            "💵 /topup\n"
            "📷 /viewclaims\n"
            "💰 /salary"
        )

    update.message.reply_text(msg)

def clockin(update, context):
    user = update.effective_user
    now = datetime.datetime.now(pytz.timezone("Asia/Kuala_Lumpur"))
    today = now.date()
    clock_time = now.strftime("%Y-%m-%d %H:%M:%S")
    
    with db_pool.getconn() as conn:
        with conn.cursor() as cur:
            # 检查是否已有记录
            cur.execute(
                "SELECT 1 FROM clock_logs WHERE user_id = %s AND date = %s",
                (user.id, today)
            )
            if cur.fetchone():
                # 更新记录
                cur.execute(
                    "UPDATE clock_logs SET clock_in = %s, is_off = FALSE WHERE user_id = %s AND date = %s",
                    (clock_time, user.id, today)
                )
            else:
                # 插入新记录
                cur.execute(
                    "INSERT INTO clock_logs (user_id, date, clock_in) VALUES (%s, %s, %s)",
                    (user.id, today, clock_time)
                )
            conn.commit()
    
    update.message.reply_text(f"✅ Clocked in at {format_local_time(clock_time)}")

def clockout(update, context):
    user = update.effective_user
    now = datetime.datetime.now(pytz.timezone("Asia/Kuala_Lumpur"))
    today = now.date()
    clock_time = now.strftime("%Y-%m-%d %H:%M:%S")
    
    with db_pool.getconn() as conn:
        with conn.cursor() as cur:
            # 检查是否已打卡
            cur.execute(
                "SELECT clock_in FROM clock_logs WHERE user_id = %s AND date = %s",
                (user.id, today)
            )
            log = cur.fetchone()
            
            if not log or not log[0] or log[0] == "OFF":
                update.message.reply_text("❌ You haven't clocked in today.")
                return
            
            # 更新打卡时间 
            cur.execute(
                "UPDATE clock_logs SET clock_out = %s WHERE user_id = %s AND date = %s",
                (clock_time, user.id, today)
            )
            
            # 计算工时
            in_time = datetime.datetime.strptime(log[0], "%Y-%m-%d %H:%M:%S")
            out_time = datetime.datetime.strptime(clock_time, "%Y-%m-%d %H:%M:%S")
            hours_worked = (out_time - in_time).total_seconds() / 3600
            
            # 更新总工时
            cur.execute(
                "UPDATE drivers SET total_hours = total_hours + %s WHERE user_id = %s",
                (hours_worked, user.id)
            )
            conn.commit()
    
    time_str = format_duration(hours_worked)
    update.message.reply_text(
        f"🏁 Clocked out at {format_local_time(clock_time)}. Worked {time_str}."
    )

def offday(update, context):
    user = update.effective_user
    today = datetime.datetime.now(pytz.timezone("Asia/Kuala_Lumpur")).date()
    
    with db_pool.getconn() as conn:
        with conn.cursor() as cur:
            # 标记休息日
            cur.execute(
                "INSERT INTO clock_logs (user_id, date, is_off) VALUES (%s, %s, TRUE) "
                "ON CONFLICT (user_id, date) DO UPDATE SET is_off = TRUE, clock_in = NULL, clock_out = NULL",
                (user.id, today)
            )
            conn.commit()
    
    update.message.reply_text(f"📅 Marked {today} as off day.")

def balance(update, context):
    if update.effective_user.id not in ADMIN_IDS:
        return
    
    with db_pool.getconn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT user_id, first_name, username, balance FROM drivers")
            drivers = cur.fetchall()
    
    msg = "📊 Driver Balances:\n"
    for driver in drivers:
        name = f"@{driver[2]}" if driver[2] else driver[1]
        msg += f"• {name}: RM{driver[3]:.2f}\n"
    
    update.message.reply_text(msg)

def check(update, context):
    if update.effective_user.id not in ADMIN_IDS:
        return
    
    today = datetime.datetime.now(pytz.timezone("Asia/Kuala_Lumpur")).date()
    
    with db_pool.getconn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
            SELECT d.user_id, d.first_name, d.username, l.clock_in, l.clock_out, l.is_off
            FROM drivers d
            LEFT JOIN clock_logs l ON d.user_id = l.user_id AND l.date = %s
            """, (today,))
            logs = cur.fetchall()
    
    msg = "📄 Today's Status:\n"
    for log in logs:
        user_id, first_name, username, in_time, out_time, is_off = log
        name = f"@{username}" if username else first_name
        
        if is_off:
            msg += f"• {name}: OFF DAY\n"
        else:
            in_str = format_local_time(in_time) if in_time else "❌"
            out_str = format_local_time(out_time) if out_time else "❌"
            msg += f"• {name}: IN: {in_str}, OUT: {out_str}\n"
    
    update.message.reply_text(msg)

def viewclaims(update, context):
    if update.effective_user.id not in ADMIN_IDS:
        return
    
    with db_pool.getconn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
            SELECT d.user_id, d.first_name, d.username, c.type, c.amount, c.date
            FROM claims c
            JOIN drivers d ON c.user_id = d.user_id
            ORDER BY c.date DESC
            LIMIT 20
            """)
            claims = cur.fetchall()
    
    msg = "📷 Recent Claims:\n"
    for claim in claims:
        user_id, first_name, username, claim_type, amount, date = claim
        name = f"@{username}" if username else first_name
        msg += f"• {name}: RM{amount:.2f} ({claim_type}) on {date}\n"
    
    update.message.reply_text(msg)

# === 薪资设置功能 ===
def salary_start(update, context):
    if update.effective_user.id not in ADMIN_IDS:
        return
    
    with db_pool.getconn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT user_id, first_name, username FROM drivers")
            drivers = cur.fetchall()
    
    keyboard = [[f"{driver[1]} (ID: {driver[0]})"] for driver in drivers]
    context.user_data['salary_drivers'] = {f"{driver[1]} (ID: {driver[0]})": driver[0] for driver in drivers}
    
    update.message.reply_text(
        "👤 Select driver to set salary:",
        reply_markup=ReplyKeyboardMarkup(keyboard, one_time_keyboard=True)
    )
    return SALARY_SELECT_DRIVER

def salary_select_driver(update, context):
    selected = update.message.text
    drivers = context.user_data.get('salary_drivers', {})
    
    if selected not in drivers:
        update.message.reply_text("❌ Invalid selection.")
        return ConversationHandler.END
    
    context.user_data['selected_driver'] = drivers[selected]
    update.message.reply_text(
        "💰 Enter monthly salary (RM):",
        reply_markup=ReplyKeyboardRemove()
    )
    return SALARY_ENTER_AMOUNT

def salary_enter_amount(update, context):
    try:
        amount = float(update.message.text)
        driver_id = context.user_data.get('selected_driver')
        
        with db_pool.getconn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE drivers SET monthly_salary = %s WHERE user_id = %s",
                    (amount, driver_id)
                )
                conn.commit()
        
        hourly_rate = calculate_hourly_rate(amount)
        update.message.reply_text(
            f"✅ Salary set to RM{amount:.2f}/month\n"
            f"Hourly rate: RM{hourly_rate:.2f}"
        )
    except ValueError:
        update.message.reply_text("❌ Please enter a valid number.")
        return SALARY_ENTER_AMOUNT
    
    return ConversationHandler.END

# === PDF 生成功能 ===
def pdf_start(update, context):
    if update.effective_user.id not in ADMIN_IDS:
        return
    
    with db_pool.getconn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT user_id, first_name, username FROM drivers")
            drivers = cur.fetchall()
    
    keyboard = [
        [InlineKeyboardButton("📊 All Drivers", callback_data="all")]
    ]
    
    # Add individual driver buttons
    for driver in drivers:
        keyboard.append([
            InlineKeyboardButton(
                f"@{driver[2]}" if driver[2] else driver[1],
                callback_data=str(driver[0])
            )
        ])
    
    update.message.reply_text(
        "🧾 Select driver for PDF report:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

def pdf_button_callback(update, context):
    query = update.callback_query
    query.answer()
    
    if query.data == "all":
        query.edit_message_text("🔄 Generating reports for all drivers...")
        generate_all_pdfs(query)
    else:
        query.edit_message_text("🔄 Generating report...")
        generate_single_pdf(query, int(query.data))

def generate_all_pdfs(query):
    try:
        temp_dir = tempfile.mkdtemp()
        
        with db_pool.getconn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT user_id, first_name, username FROM drivers")
                drivers = cur.fetchall()
        
        for driver in drivers:
            driver_id, first_name, username = driver
            name = f"@{username}" if username else first_name
            output_path = os.path.join(temp_dir, f"driver_{driver_id}.pdf")
            generate_driver_pdf(driver_id, name, bot, output_path)
            
            with open(output_path, 'rb') as f:
                bot.send_document(
                    chat_id=query.message.chat_id,
                    document=f,
                    caption=f"Report for {name}"
                )
        
        query.edit_message_text("✅ All reports generated")
    except Exception as e:
        logger.error(f"PDF generation error: {e}")
        query.edit_message_text(f"❌ Error: {str(e)}")

def generate_single_pdf(query, driver_id):
    try:
        with db_pool.getconn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT first_name, username FROM drivers WHERE user_id = %s",
                    (driver_id,)
                )
                driver = cur.fetchone()
        
        if not driver:
            query.edit_message_text("❌ Driver not found")
            return
        
        name = f"@{driver[1]}" if driver[1] else driver[0]
        temp_dir = tempfile.mkdtemp()
        output_path = os.path.join(temp_dir, f"driver_{driver_id}.pdf")
        
        generate_driver_pdf(driver_id, name, bot, output_path)
        
        with open(output_path, 'rb') as f:
            bot.send_document(
                chat_id=query.message.chat_id,
                document=f,
                caption=f"Report for {name}"
            )
        
        query.edit_message_text("✅ Report generated")
    except Exception as e:
        logger.error(f"PDF generation error: {e}")
        query.edit_message_text(f"❌ Error: {str(e)}")

# === 充值功能 ===
def topup_start(update, context):
    if update.effective_user.id not in ADMIN_IDS:
        return
    
    with db_pool.getconn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT user_id, first_name, username FROM drivers")
            drivers = cur.fetchall()
    
    keyboard = [[f"{driver[1]} (ID: {driver[0]})"] for driver in drivers]
    context.user_data['topup_drivers'] = {f"{driver[1]} (ID: {driver[0]})": driver[0] for driver in drivers}
    
    update.message.reply_text(
        "👤 Select driver to top up:",
        reply_markup=ReplyKeyboardMarkup(keyboard, one_time_keyboard=True)
    )
    return TOPUP_USER

def topup_user(update, context):
    selected = update.message.text
    drivers = context.user_data.get('topup_drivers', {})
    
    if selected not in drivers:
        update.message.reply_text("❌ Invalid selection.")
        return ConversationHandler.END
    
    context.user_data['selected_driver'] = drivers[selected]
    update.message.reply_text(
        "💰 Enter amount (RM):",
        reply_markup=ReplyKeyboardRemove()
    )
    return TOPUP_AMOUNT

def topup_amount(update, context):
    try:
        amount = float(update.message.text)
        driver_id = context.user_data.get('selected_driver')
        admin_id = update.effective_user.id
        date = datetime.datetime.now(pytz.timezone("Asia/Kuala_Lumpur")).strftime("%Y-%m-%d")
        
        with db_pool.getconn() as conn:
            with conn.cursor() as cur:
                # 更新余额
                cur.execute(
                    "UPDATE drivers SET balance = balance + %s WHERE user_id = %s",
                    (amount, driver_id)
                )
                
                # 记录充值
                cur.execute(
                    "INSERT INTO topups (user_id, amount, date, admin_id) VALUES (%s, %s, %s, %s)",
                    (driver_id, amount, date, admin_id)
                )
                conn.commit()
        
        update.message.reply_text(f"✅ Topped up RM{amount:.2f}")
    except ValueError:
        update.message.reply_text("❌ Please enter a valid number.")
        return TOPUP_AMOUNT
    
    return ConversationHandler.END

# === 报销功能 ===
def claim_start(update, context):
    user = update.effective_user
    keyboard = [["Toll", "Petrol"], ["Parking", "Other"]]
    update.message.reply_text(
        "🚗 Select claim type:",
        reply_markup=ReplyKeyboardMarkup(keyboard, one_time_keyboard=True)
    )
    return CLAIM_TYPE

def claim_type(update, context):
    claim_type = update.message.text
    context.user_data['claim_type'] = claim_type
    
    if claim_type.lower() == "other":
        update.message.reply_text("✍️ Please describe the claim type:")
        return CLAIM_OTHER_TYPE
    
    update.message.reply_text("💰 Enter amount (RM):")
    return CLAIM_AMOUNT

def claim_other_type(update, context):
    context.user_data['claim_type'] = update.message.text
    update.message.reply_text("💰 Enter amount (RM):")
    return CLAIM_AMOUNT

def claim_amount(update, context):
    try:
        amount = float(update.message.text)
        context.user_data['claim_amount'] = amount
        update.message.reply_text("📎 Please send a photo of the receipt:")
        return CLAIM_PROOF
    except ValueError:
        update.message.reply_text("❌ Please enter a valid number.")
        return CLAIM_AMOUNT

def claim_proof(update, context):
    user = update.effective_user
    photo_file = update.message.photo[-1].file_id
    date = datetime.datetime.now(pytz.timezone("Asia/Kuala_Lumpur")).strftime("%Y-%m-%d")
    
    with db_pool.getconn() as conn:
        with conn.cursor() as cur:
            # 记录报销
            cur.execute(
                "INSERT INTO claims (user_id, type, amount, date, photo_file_id) "
                "VALUES (%s, %s, %s, %s, %s)",
                (user.id, context.user_data['claim_type'], 
                 context.user_data['claim_amount'], date, photo_file)
            )
            
            # 扣除余额
            cur.execute(
                "UPDATE drivers SET balance = balance - %s WHERE user_id = %s",
                (context.user_data['claim_amount'], user.id)
            )
            conn.commit()
    
    update.message.reply_text(
        f"✅ Claim submitted for {context.user_data['claim_type']}: "
        f"RM{context.user_data['claim_amount']:.2f}"
    )
    return ConversationHandler.END

def cancel(update, context):
    update.message.reply_text(
        "❌ Operation cancelled",
        reply_markup=ReplyKeyboardRemove()
    )
    return ConversationHandler.END

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

# === Webhook ===
@app.route("/webhook", methods=["POST"])
def webhook():
    update = Update.de_json(request.get_json(force=True), bot)
    dispatcher.process_update(update)
    return "ok"

# === 初始化数据库和处理器 ===
init_db()

# 注册命令处理器
dispatcher.add_handler(CommandHandler("start", start))
dispatcher.add_handler(CommandHandler("clockin", clockin))
dispatcher.add_handler(CommandHandler("clockout", clockout))
dispatcher.add_handler(CommandHandler("offday", offday))
dispatcher.add_handler(CommandHandler("balance", balance))
dispatcher.add_handler(CommandHandler("check", check))
dispatcher.add_handler(CommandHandler("viewclaims", viewclaims))
dispatcher.add_handler(CommandHandler("PDF", pdf_start))
dispatcher.add_handler(CallbackQueryHandler(pdf_button_callback, pattern=r'^all|\d+$'))

# 注册对话处理器
dispatcher.add_handler(ConversationHandler(
    entry_points=[CommandHandler("salary", salary_start)],
    states={
        SALARY_SELECT_DRIVER: [MessageHandler(Filters.text & ~Filters.command, salary_select_driver)],
        SALARY_ENTER_AMOUNT: [MessageHandler(Filters.text & ~Filters.command, salary_enter_amount)],
    },
    fallbacks=[CommandHandler("cancel", cancel)],
))

dispatcher.add_handler(ConversationHandler(
    entry_points=[CommandHandler("topup", topup_start)],
    states={
        TOPUP_USER: [MessageHandler(Filters.text & ~Filters.command, topup_user)],
        TOPUP_AMOUNT: [MessageHandler(Filters.text & ~Filters.command, topup_amount)],
    },
    fallbacks=[CommandHandler("cancel", cancel)],
))

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

# 注册错误处理器
dispatcher.add_error_handler(error_handler)

# === 启动应用 ===
if __name__ == "__main__":
    logger.info("Starting bot...")
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))

# === 时间处理工具 ===
def get_current_time():
    """获取当前时间（马来西亚时区）"""
    return datetime.datetime.now(pytz.timezone("Asia/Kuala_Lumpur"))

def get_current_date():
    """获取当前日期（马来西亚时区）"""
    return get_current_time().date()

def format_datetime(dt):
    """格式化日期时间"""
    if isinstance(dt, str):
        try:
            dt = datetime.datetime.strptime(dt, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return dt
    return dt.strftime("%Y-%m-%d %H:%M")
