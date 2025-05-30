from flask import Flask, request, send_file, render_template_string
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
import sqlite3
import json
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

# === 日志设置 ===
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# === 配置 ===
TOKEN = os.environ.get("TOKEN")
ADMIN_IDS = [1165249082] # Replace with actual admin IDs
DEFAULT_HOURLY_RATE = 20.00
DEFAULT_MONTHLY_SALARY = 3500.00
WORKING_DAYS_PER_MONTH = 22
WORKING_HOURS_PER_DAY = 8

# === 初始化 ===
app = Flask(__name__)
bot = Bot(token=TOKEN)
dispatcher = Dispatcher(bot, None, use_context=True)

# 确保数据库已初始化
initialize_database()  # 直接调用初始化函数

# === 时区 ===
tz = pytz.timezone("Asia/Kuala_Lumpur")

# === conversation 状态 ===
TOPUP_USER, TOPUP_AMOUNT = range(2)
CLAIM_TYPE, CLAIM_OTHER_TYPE, CLAIM_AMOUNT, CLAIM_PROOF = range(4)
PDF_SELECT_DRIVER = range(1)
SALARY_SELECT_DRIVER, SALARY_ENTER_AMOUNT = range(2)

# === 数据库初始化函数 ===
def initialize_database():
    """Initialize the SQLite database and create tables if they don't exist."""
    try:
        conn = sqlite3.connect("bot_data.db")
        cursor = conn.cursor()

        # Create drivers table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS drivers (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT
            )
        """)

        # Create clock_logs table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS clock_logs (
                log_id INTEGER PRIMARY KEY AUTOINCREMENT,
                driver_id INTEGER NOT NULL,
                date TEXT NOT NULL,
                clock_in_time TEXT,
                clock_out_time TEXT,
                FOREIGN KEY (driver_id) REFERENCES drivers (user_id)
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_clock_logs_driver_date ON clock_logs (driver_id, date)")

        # Create salaries table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS salaries (
                driver_id INTEGER PRIMARY KEY,
                monthly_salary REAL,
                total_hours REAL DEFAULT 0.0,
                daily_log_json TEXT,
                FOREIGN KEY (driver_id) REFERENCES drivers (user_id)
            )
        """)

        # Create accounts table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS accounts (
                driver_id INTEGER PRIMARY KEY,
                balance REAL DEFAULT 0.0,
                FOREIGN KEY (driver_id) REFERENCES drivers (user_id)
            )
        """)

        # Create claims table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS claims (
                claim_id INTEGER PRIMARY KEY AUTOINCREMENT,
                driver_id INTEGER NOT NULL,
                date TEXT NOT NULL,
                type TEXT NOT NULL,
                amount REAL NOT NULL,
                photo_file_id TEXT,
                FOREIGN KEY (driver_id) REFERENCES drivers (user_id)
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_claims_driver_date ON claims (driver_id, date)")

        # Create topups table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS topups (
                topup_id INTEGER PRIMARY KEY AUTOINCREMENT,
                driver_id INTEGER NOT NULL,
                date TEXT NOT NULL,
                amount REAL NOT NULL,
                admin_id INTEGER NOT NULL,
                FOREIGN KEY (driver_id) REFERENCES drivers (user_id)
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_topups_driver_date ON topups (driver_id, date)")

        conn.commit()
        conn.close()
        logger.info("Database initialized successfully.")
    except sqlite3.Error as e:
        logger.error(f"Database initialization error: {e}")
        raise

# === 数据库辅助函数 ===
def get_db_connection():
    """Establish and return a database connection."""
    conn = sqlite3.connect("bot_data.db")
    conn.row_factory = sqlite3.Row
    return conn

def ensure_driver_exists(user_id, username, first_name):
    """Ensure a driver exists in the database."""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("INSERT OR IGNORE INTO drivers (user_id, username, first_name) VALUES (?, ?, ?)",
                       (user_id, username, first_name))
        cursor.execute("UPDATE drivers SET username = ?, first_name = ? WHERE user_id = ?",
                       (username, first_name, user_id))
        conn.commit()
    except sqlite3.Error as e:
        logger.error(f"DB Error (ensure_driver_exists): {e}")
    finally:
        conn.close()

def get_driver_name(user_id):
    """Get driver's name from the database."""
    conn = get_db_connection()
    name = f"User {user_id}"
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT username, first_name FROM drivers WHERE user_id = ?", (user_id,))
        row = cursor.fetchone()
        if row:
            name = f"@{row['username']}" if row['username'] else row['first_name']
    except sqlite3.Error as e:
        logger.error(f"DB Error (get_driver_name): {e}")
    finally:
        conn.close()
    return name

def get_all_driver_ids():
    """Get a list of all driver user IDs."""
    conn = get_db_connection()
    ids = []
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT user_id FROM drivers")
        rows = cursor.fetchall()
        ids = [row['user_id'] for row in rows]
    except sqlite3.Error as e:
        logger.error(f"DB Error (get_all_driver_ids): {e}")
    finally:
        conn.close()
    return ids

def save_clock_in(driver_id, date, clock_in_time):
    """Save clock-in time."""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT log_id FROM clock_logs WHERE driver_id = ? AND date = ?", (driver_id, date))
        existing_log = cursor.fetchone()
        if existing_log:
            cursor.execute("UPDATE clock_logs SET clock_in_time = ?, clock_out_time = NULL WHERE log_id = ?",
                           (clock_in_time, existing_log['log_id']))
        else:
            cursor.execute("INSERT INTO clock_logs (driver_id, date, clock_in_time) VALUES (?, ?, ?)",
                           (driver_id, date, clock_in_time))
        conn.commit()
    except sqlite3.Error as e:
        logger.error(f"DB Error (save_clock_in): {e}")
    finally:
        conn.close()

def save_clock_out(driver_id, date, clock_out_time):
    """Save clock-out time."""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("UPDATE clock_logs SET clock_out_time = ? WHERE driver_id = ? AND date = ?",
                       (clock_out_time, driver_id, date))
        conn.commit()
    except sqlite3.Error as e:
        logger.error(f"DB Error (save_clock_out): {e}")
    finally:
        conn.close()

def save_off_day(driver_id, date):
    """Mark a day as off."""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("INSERT OR REPLACE INTO clock_logs (driver_id, date, clock_in_time, clock_out_time) VALUES (?, ?, ?, ?)",
                       (driver_id, date, 'OFF', 'OFF'))
        conn.commit()
    except sqlite3.Error as e:
        logger.error(f"DB Error (save_off_day): {e}")
    finally:
        conn.close()

def get_clock_log(driver_id, date):
    """Get clock log for a specific driver and date."""
    conn = get_db_connection()
    log = None
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT clock_in_time, clock_out_time FROM clock_logs WHERE driver_id = ? AND date = ?",
                       (driver_id, date))
        row = cursor.fetchone()
        if row:
            log = {'in': row['clock_in_time'], 'out': row['clock_out_time']}
    except sqlite3.Error as e:
        logger.error(f"DB Error (get_clock_log): {e}")
    finally:
        conn.close()
    return log

def get_driver_clock_logs(driver_id):
    """Get all clock logs for a driver, ordered by date descending."""
    conn = get_db_connection()
    logs = {}
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT date, clock_in_time, clock_out_time FROM clock_logs WHERE driver_id = ? ORDER BY date DESC",
                       (driver_id,))
        rows = cursor.fetchall()
        for row in rows:
            logs[row['date']] = {'in': row['clock_in_time'], 'out': row['clock_out_time']}
    except sqlite3.Error as e:
        logger.error(f"DB Error (get_driver_clock_logs): {e}")
    finally:
        conn.close()
    return logs

def update_driver_salary(driver_id, total_hours_increment, date, hours_worked_today):
    """Update driver's total hours and daily log."""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("INSERT OR IGNORE INTO salaries (driver_id, total_hours, daily_log_json) VALUES (?, 0.0, '{}')", (driver_id,))

        cursor.execute("SELECT total_hours, daily_log_json FROM salaries WHERE driver_id = ?", (driver_id,))
        row = cursor.fetchone()
        current_total_hours = row['total_hours'] if row else 0.0
        daily_log = json.loads(row['daily_log_json']) if row and row['daily_log_json'] else {}

        new_total_hours = current_total_hours + total_hours_increment
        daily_log[date] = hours_worked_today
        new_daily_log_json = json.dumps(daily_log)

        cursor.execute("UPDATE salaries SET total_hours = ?, daily_log_json = ? WHERE driver_id = ?",
                       (new_total_hours, new_daily_log_json, driver_id))
        conn.commit()
    except sqlite3.Error as e:
        logger.error(f"DB Error (update_driver_salary): {e}")
    except json.JSONDecodeError as e:
        logger.error(f"JSON Error (update_driver_salary): {e}")
    finally:
        conn.close()

def set_monthly_salary(driver_id, monthly_salary):
    """Set the monthly salary for a driver."""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("INSERT OR IGNORE INTO salaries (driver_id) VALUES (?)", (driver_id,))
        cursor.execute("UPDATE salaries SET monthly_salary = ? WHERE driver_id = ?", (monthly_salary, driver_id))
        conn.commit()
    except sqlite3.Error as e:
        logger.error(f"DB Error (set_monthly_salary): {e}")
    finally:
        conn.close()

def get_driver_salary_info(driver_id):
    """Get salary info (total hours, monthly salary) for a driver."""
    conn = get_db_connection()
    info = {'total_hours': 0.0, 'monthly_salary': None}
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT total_hours, monthly_salary FROM salaries WHERE driver_id = ?", (driver_id,))
        row = cursor.fetchone()
        if row:
            info['total_hours'] = row['total_hours'] if row['total_hours'] is not None else 0.0
            info['monthly_salary'] = row['monthly_salary']
    except sqlite3.Error as e:
        logger.error(f"DB Error (get_driver_salary_info): {e}")
    finally:
        conn.close()
    return info

def update_account_balance(driver_id, amount_change):
    """Update driver's account balance."""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("INSERT OR IGNORE INTO accounts (driver_id, balance) VALUES (?, 0.0)", (driver_id,))
        cursor.execute("UPDATE accounts SET balance = balance + ? WHERE driver_id = ?", (amount_change, driver_id))
        conn.commit()
    except sqlite3.Error as e:
        logger.error(f"DB Error (update_account_balance): {e}")
    finally:
        conn.close()

def get_account_balance(driver_id):
    """Get driver's account balance."""
    conn = get_db_connection()
    balance = 0.0
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT balance FROM accounts WHERE driver_id = ?", (driver_id,))
        row = cursor.fetchone()
        if row:
            balance = row['balance'] if row['balance'] is not None else 0.0
    except sqlite3.Error as e:
        logger.error(f"DB Error (get_account_balance): {e}")
    finally:
        conn.close()
    return balance

def get_all_balances():
    """Get balances for all drivers."""
    conn = get_db_connection()
    balances = {}
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT driver_id, balance FROM accounts")
        rows = cursor.fetchall()
        for row in rows:
            balances[row['driver_id']] = row['balance'] if row['balance'] is not None else 0.0
    except sqlite3.Error as e:
        logger.error(f"DB Error (get_all_balances): {e}")
    finally:
        conn.close()
    return balances

def save_claim(driver_id, date, claim_type, amount, photo_file_id):
    """Save a new claim."""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("INSERT INTO claims (driver_id, date, type, amount, photo_file_id) VALUES (?, ?, ?, ?, ?)",
                       (driver_id, date, claim_type, amount, photo_file_id))
        conn.commit()
        update_account_balance(driver_id, -amount)
    except sqlite3.Error as e:
        logger.error(f"DB Error (save_claim): {e}")
    finally:
        conn.close()

def get_driver_claims(driver_id, limit=None):
    """Get claims for a specific driver, optionally limited."""
    conn = get_db_connection()
    claims_list = []
    try:
        cursor = conn.cursor()
        query = "SELECT claim_id, date, type, amount, photo_file_id FROM claims WHERE driver_id = ? ORDER BY date DESC, claim_id DESC"
        params = (driver_id,)
        if limit:
            query += " LIMIT ?"
            params += (limit,)
        cursor.execute(query, params)
        rows = cursor.fetchall()
        for row in rows:
            claims_list.append(dict(row))
    except sqlite3.Error as e:
        logger.error(f"DB Error (get_driver_claims): {e}")
    finally:
        conn.close()
    return claims_list

def save_topup(driver_id, date, amount, admin_id):
    """Save a new topup record."""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("INSERT INTO topups (driver_id, date, amount, admin_id) VALUES (?, ?, ?, ?)",
                       (driver_id, date, amount, admin_id))
        conn.commit()
        update_account_balance(driver_id, amount)
    except sqlite3.Error as e:
        logger.error(f"DB Error (save_topup): {e}")
    finally:
        conn.close()

def get_driver_topups(driver_id):
    """Get topup history for a driver."""
    conn = get_db_connection()
    topups_list = []
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT date, amount FROM topups WHERE driver_id = ? ORDER BY date DESC", (driver_id,))
        rows = cursor.fetchall()
        for row in rows:
            topups_list.append(dict(row))
    except sqlite3.Error as e:
        logger.error(f"DB Error (get_driver_topups): {e}")
    finally:
        conn.close()
    return topups_list

# === 辅助函数 ===
def format_local_time(timestamp_str):
    """将时间戳字符串转换为本地时间格式，去除国际时间部分"""
    try:
        dt = datetime.datetime.strptime(timestamp_str, "%Y-%m-%d %H:%M:%S")
        return dt.strftime("%Y-%m-%d %H:%M")
    except:
        return timestamp_str

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
        return str(hours)

def get_month_date_range(date=None):
    """获取指定日期所在月份的起止日期"""
    if date is None:
        date = datetime.datetime.now(tz)
    year = date.year
    month = date.month
    first_day = datetime.date(year, month, 1)
    last_day = datetime.date(year, month, calendar.monthrange(year, month)[1])
    return first_day, last_day

def calculate_hourly_rate(monthly_salary):
    """根据月薪计算时薪"""
    try:
        monthly_salary = float(monthly_salary)
        hourly_rate = monthly_salary / (WORKING_DAYS_PER_MONTH * WORKING_HOURS_PER_DAY)
        return round(hourly_rate, 2)
    except:
        return DEFAULT_HOURLY_RATE

def get_driver_hourly_rate(driver_id):
    """获取司机的时薪 (从数据库)"""
    salary_info = get_driver_salary_info(driver_id)
    if salary_info and salary_info["monthly_salary"] is not None:
        return calculate_hourly_rate(salary_info["monthly_salary"])
    return DEFAULT_HOURLY_RATE

# === PDF 生成功能 ===
def download_telegram_photo(file_id, bot):
    """Download a photo from Telegram by file_id and save to a temporary file"""
    try:
        file = bot.get_file(file_id)
        temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".jpg")
        file.download(temp_file.name)
        return temp_file.name
    except Exception as e:
        logger.error(f"Error downloading photo: {str(e)}")
        return None

def generate_driver_pdf(driver_id, output_path):
    """Generate a PDF report for a single driver using data from DB."""
    driver_name = get_driver_name(driver_id)
    driver_logs_db = get_driver_clock_logs(driver_id)
    driver_salary_info = get_driver_salary_info(driver_id)
    driver_claims = get_driver_claims(driver_id)
    driver_topups = get_driver_topups(driver_id)
    driver_balance = get_account_balance(driver_id)

    doc = SimpleDocTemplate(
        output_path,
        pagesize=A4,
        rightMargin=72,
        leftMargin=72,
        topMargin=72,
        bottomMargin=72
    )

    # Styles
    styles = getSampleStyleSheet()
    custom_title_style = ParagraphStyle(name="CustomTitle", fontName="Helvetica-Bold", fontSize=16, alignment=1, spaceAfter=12)
    custom_heading_style = ParagraphStyle(name="CustomHeading", fontName="Helvetica-Bold", fontSize=14, spaceAfter=6)
    custom_normal_style = ParagraphStyle(name="CustomNormal", fontName="Helvetica", fontSize=10, spaceAfter=6)

    elements = []

    # Title
    title = Paragraph(f"Driver Report: {driver_name}", custom_title_style)
    elements.append(title)
    elements.append(Spacer(1, 12))

    # Clock-in/out Table
    elements.append(Paragraph("Daily Clock Records", custom_heading_style))
    elements.append(Spacer(1, 6))
    clock_data = [["Date", "Clock In", "Clock Out", "Hours"]]
    total_hours_calculated = 0

    for date, log in sorted(driver_logs_db.items(), reverse=True):
        in_time = log.get("in", "N/A")
        out_time = log.get("out", "N/A")
        hours = "N/A"

        if in_time != "N/A" and in_time != "OFF":
            in_time_fmt = format_local_time(in_time)
        else:
            in_time_fmt = in_time

        if out_time != "N/A" and out_time != "OFF":
            out_time_fmt = format_local_time(out_time)
        else:
            out_time_fmt = out_time

        if in_time != "N/A" and out_time != "N/A" and in_time != "OFF":
            try:
                naive_in_time = datetime.datetime.strptime(in_time, "%Y-%m-%d %H:%M:%S")
                naive_out_time = datetime.datetime.strptime(out_time, "%Y-%m-%d %H:%M:%S")
                duration = naive_out_time - naive_in_time
                hours_float = duration.total_seconds() / 3600
                if hours_float >= 0:
                    hours = format_duration(hours_float)
                    total_hours_calculated += hours_float
                else:
                    hours = "Error"
            except:
                hours = "Error"
        elif in_time == "OFF":
            hours = "OFF"

        clock_data.append([date, in_time_fmt, out_time_fmt, hours])

    if len(clock_data) > 1:
        clock_table = Table(clock_data, colWidths=[80, 120, 120, 60])
        clock_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.grey),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke),
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, 0), 12),
            ("BOTTOMPADDING", (0, 0), (-1, 0), 12),
            ("BACKGROUND", (0, 1), (-1, -1), colors.beige),
            ("GRID", (0, 0), (-1, -1), 1, colors.black),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ]))
        elements.append(clock_table)
    else:
        elements.append(Paragraph("No clock records found.", custom_normal_style))
    elements.append(Spacer(1, 20))

    # Claims Section
    elements.append(Paragraph("Expense Claims", custom_heading_style))
    elements.append(Spacer(1, 6))
    total_claims_amount = 0

    if driver_claims:
        for claim in driver_claims:
            total_claims_amount += claim.get("amount", 0)
            claim_date = claim.get("date", "N/A")
            claim_type = claim.get("type", "N/A")
            claim_amount = claim.get("amount", 0)
            claim_data = [[f"Date: {claim_date}", f"Type: {claim_type}", f"Amount: RM{claim_amount:.2f}"]]
            claim_table = Table(claim_data, colWidths=[120, 120, 120])
            claim_table.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), colors.lightblue),
                ("GRID", (0, 0), (-1, -1), 1, colors.black),
                ("ALIGN", (0, 0), (-1, -1), "LEFT"),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
                ("FONTSIZE", (0, 0), (-1, -1), 10),
            ]))
            elements.append(claim_table)
            if claim.get("photo_file_id"):
                try:
                    photo_path = download_telegram_photo(claim["photo_file_id"], bot)
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

    # Summary Section
    elements.append(Paragraph("Summary", custom_heading_style))
    elements.append(Spacer(1, 6))
    first_day, last_day = get_month_date_range()
    period_text = f"Summary Period: {first_day.strftime('%Y-%m-%d')} to {last_day.strftime('%Y-%m-%d')}"
    elements.append(Paragraph(period_text, custom_normal_style))
    elements.append(Spacer(1, 6))

    hourly_rate = get_driver_hourly_rate(driver_id)
    monthly_salary_val = driver_salary_info.get("monthly_salary")
    monthly_salary_str = f"RM{monthly_salary_val:.2f}" if monthly_salary_val is not None else "Not Set"
    total_hours_db = driver_salary_info.get("total_hours", 0.0)
    gross_pay = total_hours_db * hourly_rate

    pay_text = f"Monthly Salary: {monthly_salary_str}<br/>Hourly Rate: RM{hourly_rate:.2f}<br/>Total Hours: {format_duration(total_hours_db)}<br/>Gross Pay: RM{gross_pay:.2f}"
    elements.append(Paragraph(pay_text, custom_normal_style))
    elements.append(Spacer(1, 12))

    elements.append(Paragraph("Account Transactions:", custom_normal_style))
    if driver_topups:
        elements.append(Paragraph("Topups:", custom_normal_style))
        topup_data = [["Date", "Amount"]]
        for topup in driver_topups:
            topup_data.append([topup.get("date", "N/A"), f"RM{topup.get('amount', 0):.2f}"])
        topup_table = Table(topup_data, colWidths=[120, 120])
        topup_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.lightgreen),
            ("GRID", (0, 0), (-1, -1), 1, colors.black),
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ]))
        elements.append(topup_table)
        elements.append(Spacer(1, 6))

    if driver_claims:
        elements.append(Paragraph("Claim Deductions:", custom_normal_style))
        claim_data = [["Date", "Type", "Amount"]]
        for claim in driver_claims:
            claim_data.append([claim.get("date", "N/A"), claim.get("type", "N/A"), f"RM{claim.get('amount', 0):.2f}"])
        claim_deduct_table = Table(claim_data, colWidths=[80, 120, 80])
        claim_deduct_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.salmon),
            ("GRID", (0, 0), (-1, -1), 1, colors.black),
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ]))
        elements.append(claim_deduct_table)
        elements.append(Spacer(1, 12))

    summary_data = [
        ["Total Hours", "Total Claims", "Account Balance"],
        [format_duration(total_hours_db), f"RM{total_claims_amount:.2f}", f"RM{driver_balance:.2f}"]
    ]
    summary_table = Table(summary_data, colWidths=[120, 120, 120])
    summary_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.darkblue),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, 0), 12),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 12),
        ("BACKGROUND", (0, 1), (-1, -1), colors.lightgrey),
        ("GRID", (0, 0), (-1, -1), 1, colors.black),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
        ("FONTSIZE", (0, 1), (-1, -1), 10),
    ]))
    elements.append(summary_table)

    doc.build(elements)
    return output_path

def generate_all_drivers_pdf(output_dir):
    """Generate PDF reports for all drivers using data from DB."""
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)
    pdf_files = []
    driver_ids = get_all_driver_ids()
    for driver_id in driver_ids:
        try:
            output_path = os.path.join(output_dir, f"driver_{driver_id}.pdf")
            generate_driver_pdf(driver_id, output_path)
            pdf_files.append(output_path)
        except Exception as e:
            logger.error(f"Error generating PDF for driver {driver_id}: {str(e)}")
    return pdf_files

def generate_single_driver_pdf_wrapper(driver_id, output_dir):
    """Wrapper to generate PDF for a single driver using data from DB."""
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)
    try:
        output_path = os.path.join(output_dir, f"driver_{driver_id}.pdf")
        generate_driver_pdf(driver_id, output_path)
        return output_path
    except Exception as e:
        logger.error(f"Error generating PDF for driver {driver_id}: {str(e)}")
        return None

# === 照片服务器功能 ===
TEMP_DIR = tempfile.mkdtemp()

@app.route('/claims/')
def claims_index():
    """Main page showing claims list with pagination."""
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 5, type=int)
    driver_id = request.args.get('driver_id', None, type=int)
    
    # Get all drivers for the filter dropdown
    drivers = []
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT user_id, username, first_name FROM drivers")
        for row in cursor.fetchall():
            name = f"@{row['username']}" if row['username'] else row['first_name']
            drivers.append({'id': row['user_id'], 'name': name})
    except Exception as e:
        logger.error(f"Error fetching drivers: {e}")
    finally:
        conn.close()
    
    # Get claims with pagination
    claims = []
    total_claims = 0
    total_pages = 1
    
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        
        # Count query
        count_query = "SELECT COUNT(*) FROM claims"
        params = []
        if driver_id:
            count_query += " WHERE driver_id = ?"
            params.append(driver_id)
        
        cursor.execute(count_query, params)
        total_claims = cursor.fetchone()[0]
        total_pages = (total_claims + per_page - 1) // per_page if total_claims > 0 else 1
        
        # Data query
        offset = (page - 1) * per_page
        data_query = """
            SELECT c.claim_id, c.driver_id, c.date, c.type, c.amount, c.photo_file_id,
                   d.username, d.first_name
            FROM claims c
            LEFT JOIN drivers d ON c.driver_id = d.user_id
        """
        if driver_id:
            data_query += " WHERE c.driver_id = ?"
            params.append(driver_id)
        
        data_query += " ORDER BY c.date DESC, c.claim_id DESC LIMIT ? OFFSET ?"
        params.extend([per_page, offset])
        
        cursor.execute(data_query, params)
        for row in cursor.fetchall():
            driver_name = f"@{row['username']}" if row['username'] else row['first_name'] or f"User {row['driver_id']}"
            claims.append({
                'id': row['claim_id'],
                'driver_id': row['driver_id'],
                'driver_name': driver_name,
                'date': row['date'],
                'type': row['type'],
                'amount': row['amount'],
                'photo_file_id': row['photo_file_id']
            })
    except Exception as e:
        logger.error(f"Error fetching claims: {e}")
    finally:
        conn.close()
    
    # HTML template
    html_template = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Driver Claims</title>
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            body {
                font-family: Arial, sans-serif;
                margin: 0;
                padding: 20px;
                background-color: #f5f5f5;
            }
            .container {
                max-width: 1000px;
                margin: 0 auto;
                background-color: white;
                padding: 20px;
                border-radius: 8px;
                box-shadow: 0 2px 4px rgba(0,0,0,0.1);
            }
            h1 {
                color: #333;
                margin-top: 0;
            }
            .filters {
                margin-bottom: 20px;
                padding: 15px;
                background-color: #f9f9f9;
                border-radius: 5px;
            }
            .claim-card {
                border: 1px solid #ddd;
                border-radius: 5px;
                padding: 15px;
                margin-bottom: 15px;
                background-color: white;
            }
            .claim-header {
                display: flex;
                justify-content: space-between;
                margin-bottom: 10px;
            }
            .claim-details {
                margin-bottom: 10px;
            }
            .claim-photo {
                text-align: center;
            }
            .claim-photo img {
                max-width: 100%;
                max-height: 200px;
                border-radius: 5px;
            }
            .pagination {
                margin-top: 20px;
                text-align: center;
            }
            .pagination a, .pagination span {
                display: inline-block;
                padding: 8px 16px;
                text-decoration: none;
                color: black;
                border: 1px solid #ddd;
                margin: 0 4px;
            }
            .pagination a:hover {
                background-color: #ddd;
            }
            .pagination .active {
                background-color: #4CAF50;
                color: white;
                border: 1px solid #4CAF50;
            }
            .modal {
                display: none;
                position: fixed;
                z-index: 1;
                left: 0;
                top: 0;
                width: 100%;
                height: 100%;
                overflow: auto;
                background-color: rgba(0,0,0,0.9);
            }
            .modal-content {
                margin: auto;
                display: block;
                max-width: 90%;
                max-height: 90%;
            }
            .close {
                position: absolute;
                top: 15px;
                right: 35px;
                color: #f1f1f1;
                font-size: 40px;
                font-weight: bold;
                transition: 0.3s;
            }
            .close:hover, .close:focus {
                color: #bbb;
                text-decoration: none;
                cursor: pointer;
            }
            select, button {
                padding: 8px;
                border-radius: 4px;
                border: 1px solid #ddd;
            }
            button {
                background-color: #4CAF50;
                color: white;
                border: none;
                cursor: pointer;
            }
            button:hover {
                background-color: #45a049;
            }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>Driver Claims</h1>
            
            <div class="filters">
                <form action="/claims/" method="get">
                    <label for="driver_id">Filter by Driver:</label>
                    <select name="driver_id" id="driver_id">
                        <option value="">All Drivers</option>
                        {% for driver in drivers %}
                        <option value="{{ driver.id }}" {% if driver_id == driver.id %}selected{% endif %}>
                            {{ driver.name }}
                        </option>
                        {% endfor %}
                    </select>
                    <button type="submit">Apply Filter</button>
                    {% if driver_id %}
                    <a href="/claims/" style="margin-left: 10px;">Clear Filter</a>
                    {% endif %}
                </form>
            </div>
            
            {% if claims %}
                {% for claim in claims %}
                <div class="claim-card">
                    <div class="claim-header">
                        <div><strong>{{ claim.driver_name }}</strong></div>
                        <div>{{ claim.date }}</div>
                    </div>
                    <div class="claim-details">
                        <div><strong>Type:</strong> {{ claim.type }}</div>
                        <div><strong>Amount:</strong> RM{{ "%.2f"|format(claim.amount) }}</div>
                    </div>
                    {% if claim.photo_file_id %}
                    <div class="claim-photo">
                        <img src="/claims/photo/{{ claim.photo_file_id }}" 
                             alt="Claim photo" 
                             onclick="openModal('/claims/photo/{{ claim.photo_file_id }}')">
                        <div>
                            <a href="/claims/download/{{ claim.photo_file_id }}" target="_blank">Download Photo</a>
                        </div>
                    </div>
                    {% else %}
                    <div class="claim-photo">No photo available</div>
                    {% endif %}
                </div>
                {% endfor %}
                
                <div class="pagination">
                    {% if page > 1 %}
                    <a href="{{ url_for('claims_index', page=page-1, driver_id=driver_id) }}">&laquo; Previous</a>
                    {% endif %}
                    
                    {% for p in range(1, total_pages + 1) %}
                        {% if p == page %}
                        <span class="active">{{ p }}</span>
                        {% else %}
                        <a href="{{ url_for('claims_index', page=p, driver_id=driver_id) }}">{{ p }}</a>
                        {% endif %}
                    {% endfor %}
                    
                    {% if page < total_pages %}
                    <a href="{{ url_for('claims_index', page=page+1, driver_id=driver_id) }}">Next &raquo;</a>
                    {% endif %}
                </div>
            {% else %}
                <p>No claims found.</p>
            {% endif %}
        </div>
        
        <!-- Modal for image preview -->
        <div id="imageModal" class="modal">
            <span class="close" onclick="closeModal()">&times;</span>
            <img class="modal-content" id="modalImg">
        </div>
        
        <script>
            function openModal(imgSrc) {
                var modal = document.getElementById("imageModal");
                var modalImg = document.getElementById("modalImg");
                modal.style.display = "block";
                modalImg.src = imgSrc;
            }
            
            function closeModal() {
                document.getElementById("imageModal").style.display = "none";
            }
            
            // Close modal when clicking outside the image
            window.onclick = function(event) {
                var modal = document.getElementById("imageModal");
                if (event.target == modal) {
                    modal.style.display = "none";
                }
            }
        </script>
    </body>
    </html>
    """
    
    return render_template_string(
        html_template, 
        claims=claims, 
        drivers=drivers,
        page=page, 
        total_pages=total_pages,
        driver_id=driver_id
    )

@app.route('/claims/photo/<file_id>')
def claims_photo(file_id):
    """Serve a photo by its Telegram file_id."""
    try:
        # Check if we already have this photo cached
        photo_path = os.path.join(TEMP_DIR, f"{file_id}.jpg")
        
        if not os.path.exists(photo_path):
            # Download from Telegram
            file = bot.get_file(file_id)
            file.download(photo_path)
        
        return send_file(photo_path, mimetype='image/jpeg')
    except Exception as e:
        logger.error(f"Error serving photo {file_id}: {e}")
        return "Error loading photo", 500

@app.route('/claims/download/<file_id>')
def claims_download_photo(file_id):
    """Download a photo by its Telegram file_id."""
    try:
        # Check if we already have this photo cached
        photo_path = os.path.join(TEMP_DIR, f"{file_id}.jpg")
        
        if not os.path.exists(photo_path):
            # Download from Telegram
            file = bot.get_file(file_id)
            file.download(photo_path)
        
        return send_file(photo_path, mimetype='image/jpeg', 
                         download_name=f"claim_{file_id}.jpg", 
                         as_attachment=True)
    except Exception as e:
        logger.error(f"Error downloading photo {file_id}: {e}")
        return "Error downloading photo", 500

# === 错误处理函数 ===
def error_handler(update, context):
    logger.error("Exception while handling an update:", exc_info=context.error)
    try:
        if update and update.effective_message:
            update.effective_message.reply_text("⚠️ An unexpected error occurred. Please try again later.")
    except:
        logger.error("Failed to send error message to user")
    tb_list = traceback.format_exception(None, context.error, context.error.__traceback__)
    tb_string = ''.join(tb_list)
    logger.error(f"Full traceback:\n{tb_string}")

# === /start ===
def start(update, context):
    user = update.effective_user
    user_id = user.id
    username = user.username or str(user_id)
    first_name = user.first_name

    ensure_driver_exists(user_id, username, first_name)

    msg = (
        f"👋 Hello {first_name}!\n"
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
            "💰 /salary"
        )

    update.message.reply_text(msg)
    logger.info(f"User {username} (ID: {user_id}) started the bot")

# === /clockin ===
def clockin(update, context):
    user = update.effective_user
    user_id = user.id
    username = user.username or str(user_id)
    first_name = user.first_name
    ensure_driver_exists(user_id, username, first_name)

    now = datetime.datetime.now(tz)
    today = now.strftime("%Y-%m-%d")
    clock_time = now.strftime("%Y-%m-%d %H:%M:%S")

    save_clock_in(user_id, today, clock_time)

    local_time = format_local_time(clock_time)
    update.message.reply_text(f"✅ Clocked in at {local_time}")
    logger.info(f"User {username} clocked in at {clock_time}")

# === /clockout ===
def clockout(update, context):
    user = update.effective_user
    user_id = user.id
    username = user.username or str(user_id)
    first_name = user.first_name
    ensure_driver_exists(user_id, username, first_name)

    now = datetime.datetime.now(tz)
    today = now.strftime("%Y-%m-%d")
    clock_time = now.strftime("%Y-%m-%d %H:%M:%S")

    log = get_clock_log(user_id, today)

    if not log or not log.get("in") or log.get("in") == "OFF":
        error_msg = "❌ You haven't clocked in today or are marked as off."
        logger.warning(f"User {username}: {error_msg}")
        update.message.reply_text(error_msg)
        return

    if log.get("out") and log.get("out") != "N/A":
         update.message.reply_text("⚠️ You have already clocked out today.")
         return

    try:
        save_clock_out(user_id, today, clock_time)

        in_time_str = log["in"]
        naive_in_time = datetime.datetime.strptime(in_time_str, "%Y-%m-%d %H:%M:%S")
        now_naive = now.replace(tzinfo=None)
        duration = now_naive - naive_in_time
        total_seconds = duration.total_seconds()

        if total_seconds < 0:
            logger.warning(f"Negative time difference detected for {username}: {total_seconds} seconds. Using absolute value.")
            total_seconds = abs(total_seconds)

        hours_worked = total_seconds / 3600
        time_str = format_duration(hours_worked)

        update_driver_salary(user_id, hours_worked, today, hours_worked)

        local_time = format_local_time(clock_time)
        update.message.reply_text(f"🏁 Clocked out at {local_time}. Worked {time_str}.")
        logger.info(f"User {username} clocked out: worked {time_str}")
    except Exception as e:
        logger.error(f"Clockout error for user {username}: {str(e)}")
        logger.exception(e)
        update.message.reply_text("⚠️ An error occurred during clockout. Please try again.")

# === /offday ===
def offday(update, context):
    user = update.effective_user
    user_id = user.id
    username = user.username or str(user_id)
    first_name = user.first_name
    ensure_driver_exists(user_id, username, first_name)

    today = datetime.datetime.now(tz).strftime("%Y-%m-%d")
    save_off_day(user_id, today)
    update.message.reply_text(f"📅 Marked {today} as off day.")
    logger.info(f"User {username} marked {today} as off day")

# === /balance（管理员）===
def balance(update, context):
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        return
    logger.info(f"Admin {user_id} requested balance")

    balances = get_all_balances()
    msg = "📊 Driver Balances:\n"
    if not balances:
        msg += "No driver data found."
    else:
        for uid, bal in balances.items():
            name = get_driver_name(uid)
            msg += f"• {name}: RM{bal:.2f}\n"
    update.message.reply_text(msg)

# === /check（管理员）===
def check(update, context):
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        return
    logger.info(f"Admin {user_id} requested check")

    today = datetime.datetime.now(tz).strftime("%Y-%m-%d")
    msg = f"📄 Status for {today}:\n"
    driver_ids = get_all_driver_ids()

    if not driver_ids:
        msg += "No drivers found."
    else:
        for uid in driver_ids:
            log = get_clock_log(uid, today)
            name = get_driver_name(uid)
            in_time_str = "❌"
            out_time_str = "❌"
            if log:
                in_time = log.get("in")
                out_time = log.get("out")
                if in_time and in_time != "OFF":
                    in_time_str = format_local_time(in_time)
                elif in_time == "OFF":
                     in_time_str = "OFF"

                if out_time and out_time != "OFF":
                    out_time_str = format_local_time(out_time)
                elif out_time == "OFF":
                    out_time_str = "OFF"

            msg += f"• {name}: IN: {in_time_str}, OUT: {out_time_str}\n"
    update.message.reply_text(msg)

# === /viewclaims（管理员）===
def viewclaims(update, context):
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        return update.message.reply_text("❌ You are not an admin.")

    logger.info(f"Admin {user_id} requested viewclaims")
    # 使用主应用内的相对路径
    claims_url = "/claims/"
    keyboard = [[InlineKeyboardButton("View Claims Online", url=claims_url)]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    update.message.reply_text(
        "📷 Click the button below to view claims with photos in your browser:",
        reply_markup=reply_markup
    )

# === /salary (管理员) ===
def salary_start(update, context):
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        return update.message.reply_text("❌ You are not an admin.")
    logger.info(f"Admin {user_id} started salary setting process")

    keyboard = []
    context.user_data["salary_driver_map"] = {}
    driver_ids = get_all_driver_ids()

    if not driver_ids:
        update.message.reply_text("❌ No drivers found.")
        return ConversationHandler.END

    for uid in driver_ids:
        name = get_driver_name(uid)
        keyboard.append([name])
        context.user_data["salary_driver_map"][name] = uid

    update.message.reply_text(
        "👤 Select driver to set salary:",
        reply_markup=ReplyKeyboardMarkup(keyboard, one_time_keyboard=True)
    )
    return SALARY_SELECT_DRIVER

def salary_select_driver(update, context):
    admin_id = update.effective_user.id
    selected = update.message.text.strip()
    logger.info(f"Admin {admin_id} selected driver: {selected}")

    driver_map = context.user_data.get("salary_driver_map", {})
    if selected not in driver_map:
        update.message.reply_text("❌ Invalid selection.")
        return ConversationHandler.END

    driver_id = driver_map[selected]
    context.user_data["salary_driver_id"] = driver_id
    context.user_data["salary_driver_name"] = selected

    salary_info = get_driver_salary_info(driver_id)
    current_salary = "Not Set"
    if salary_info and salary_info["monthly_salary"] is not None:
        current_salary = f"RM{salary_info['monthly_salary']:.2f}"

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

        set_monthly_salary(driver_id, monthly_salary)
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
    finally:
        context.user_data.pop("salary_driver_id", None)
        context.user_data.pop("salary_driver_name", None)
        context.user_data.pop("salary_driver_map", None)
    return ConversationHandler.END

# === /PDF (管理员) ===
def pdf_start(update, context):
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        return update.message.reply_text("❌ You are not an admin.")
    logger.info(f"Admin {user_id} started PDF generation process")

    keyboard = [[InlineKeyboardButton("📊 All Drivers", callback_data="pdf_all")]]
    context.user_data["pdf_driver_map"] = {}
    driver_ids = get_all_driver_ids()

    if not driver_ids:
        update.message.reply_text("❌ No drivers found.")
        return

    for uid in driver_ids:
        name = get_driver_name(uid)
        keyboard.append([InlineKeyboardButton(f"👤 {name}", callback_data=f"pdf_{uid}")])
        context.user_data["pdf_driver_map"][f"pdf_{uid}"] = uid

    reply_markup = InlineKeyboardMarkup(keyboard)
    update.message.reply_text("🧾 Select driver for PDF report:", reply_markup=reply_markup)

def pdf_button_callback(update, context):
    query = update.callback_query
    user_id = query.from_user.id
    if user_id not in ADMIN_IDS:
        query.answer("❌ You are not an admin.")
        return
    query.answer()

    callback_data = query.data
    logger.info(f"Admin {user_id} selected PDF option: {callback_data}")

    temp_dir = tempfile.mkdtemp()
    try:
        if callback_data == "pdf_all":
            query.edit_message_text("🔄 Generating PDF reports for all drivers...")
            pdf_files = generate_all_drivers_pdf(temp_dir)
            if not pdf_files:
                query.edit_message_text("❌ No driver data available.")
                return
            for pdf_file in pdf_files:
                try:
                    with open(pdf_file, "rb") as f:
                        bot.send_document(chat_id=query.message.chat_id, document=f, filename=os.path.basename(pdf_file))
                except Exception as e:
                    logger.error(f"Error sending PDF {os.path.basename(pdf_file)}: {e}")
                    bot.send_message(chat_id=query.message.chat_id, text=f"❌ Error sending PDF for {os.path.basename(pdf_file)}")
            query.edit_message_text(f"✅ Generated {len(pdf_files)} PDF reports.")
        else:
            driver_map = context.user_data.get("pdf_driver_map", {})
            if callback_data in driver_map:
                driver_id = driver_map[callback_data]
                driver_name = get_driver_name(driver_id)
                query.edit_message_text(f"🔄 Generating PDF report for {driver_name}...")
                pdf_file = generate_single_driver_pdf_wrapper(driver_id, temp_dir)
                if not pdf_file:
                    query.edit_message_text("❌ No data available for this driver.")
                    return
                try:
                    with open(pdf_file, "rb") as f:
                        bot.send_document(chat_id=query.message.chat_id, document=f, filename=os.path.basename(pdf_file))
                    query.edit_message_text(f"✅ PDF report for {driver_name} generated.")
                except Exception as e:
                    logger.error(f"Error sending PDF for {driver_name}: {e}")
                    query.edit_message_text(f"❌ Error sending PDF for {driver_name}.")
            else:
                query.edit_message_text("❌ Invalid selection or session expired.")
    except Exception as e:
        logger.error(f"PDF generation error: {str(e)}")
        logger.exception(e)
        query.edit_message_text(f"❌ Error generating PDF(s): {str(e)}")
    finally:
        context.user_data.pop("pdf_driver_map", None)

# === /topup (管理员) ===
def topup_start(update, context):
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        return
    logger.info(f"Admin {user_id} started topup process")

    keyboard = []
    context.user_data["topup_driver_map"] = {}
    driver_ids = get_all_driver_ids()

    if not driver_ids:
        update.message.reply_text("❌ No drivers found.")
        return ConversationHandler.END

    for uid in driver_ids:
        name = get_driver_name(uid)
        keyboard.append([name])
        context.user_data["topup_driver_map"][name] = uid

    update.message.reply_text(
        "👤 Select driver to top up:",
        reply_markup=ReplyKeyboardMarkup(keyboard, one_time_keyboard=True)
    )
    return TOPUP_USER

def topup_user(update, context):
    admin_id = update.effective_user.id
    selected = update.message.text.strip()
    logger.info(f"Admin {admin_id} selected topup target: {selected}")

    driver_map = context.user_data.get("topup_driver_map", {})
    if selected not in driver_map:
        update.message.reply_text("❌ Invalid selection.")
        return ConversationHandler.END

    context.user_data["topup_uid"] = driver_map[selected]
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

        today = datetime.datetime.now(tz).strftime("%Y-%m-%d")
        save_topup(uid, today, amount, admin_id)

        name = get_driver_name(uid)
        update.message.reply_text(f"✅ Topped up RM{amount:.2f} to {name}.")
        logger.info(f"Admin {admin_id} topped up RM{amount:.2f} to {name} (ID: {uid})")
    except ValueError:
        update.message.reply_text("❌ Invalid amount. Please enter a number.")
        return TOPUP_AMOUNT
    except Exception as e:
        logger.error(f"Topup error: {str(e)}")
        update.message.reply_text("❌ An error occurred during topup.")
    finally:
        context.user_data.pop("topup_uid", None)
        context.user_data.pop("topup_driver_map", None)
    return ConversationHandler.END

# === /claim 分阶段 ===
def claim_start(update, context):
    user = update.effective_user
    user_id = user.id
    username = user.username or str(user_id)
    first_name = user.first_name
    ensure_driver_exists(user_id, username, first_name)

    logger.info(f"User {username} started claim process")
    context.user_data["claim_info"] = {}
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
    context.user_data["claim_info"]["type"] = text
    logger.info(f"User {username} selected claim type: {text}")

    if text == "other":
        update.message.reply_text("✍️ Please enter the type description:", reply_markup=ReplyKeyboardRemove())
        return CLAIM_OTHER_TYPE
    update.message.reply_text("💰 Enter amount:", reply_markup=ReplyKeyboardRemove())
    return CLAIM_AMOUNT

def claim_other_type(update, context):
    user_id = update.effective_user.id
    username = update.effective_user.username or str(user_id)
    context.user_data["claim_info"]["type"] = update.message.text
    logger.info(f"User {username} entered custom claim type: {update.message.text}")
    update.message.reply_text("💰 Enter amount:")
    return CLAIM_AMOUNT

def claim_amount(update, context):
    user_id = update.effective_user.id
    username = update.effective_user.username or str(user_id)
    try:
        amount = float(update.message.text)
        if amount <= 0:
             update.message.reply_text("❌ Amount must be positive.")
             return CLAIM_AMOUNT
        context.user_data["claim_info"]["amount"] = amount
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
    try:
        file_id = update.message.photo[-1].file_id
        date = datetime.datetime.now(tz).strftime("%Y-%m-%d")
        claim_info = context.user_data.get("claim_info", {})
        amount = claim_info.get("amount")
        claim_type = claim_info.get("type")

        if not amount or not claim_type:
            update.message.reply_text("❌ Error: Claim information missing. Please start again with /claim.")
            return ConversationHandler.END

        save_claim(user_id, date, claim_type, amount, file_id)

        response = f"✅ RM{amount:.2f} claimed for {claim_type} on {date}."
        update.message.reply_text(response)
        logger.info(f"User {username} completed claim: {response}")
    except Exception as e:
        logger.error(f"Claim proof error: {str(e)}")
        update.message.reply_text("❌ An error occurred while saving the claim.")
    finally:
        context.user_data.pop("claim_info", None)
    return ConversationHandler.END

def cancel(update, context):
    user_id = update.effective_user.id
    username = update.effective_user.username or str(user_id)
    update.message.reply_text("❌ Operation cancelled.", reply_markup=ReplyKeyboardRemove())
    keys_to_pop = [k for k in context.user_data if k.startswith(("claim_", "topup_", "pdf_", "salary_"))]
    for key in keys_to_pop:
        context.user_data.pop(key, None)
    logger.info(f"User {username} cancelled operation")
    return ConversationHandler.END

# === Webhook ===
@app.route(f"/{TOKEN}", methods=["POST"])
def webhook():
    update = Update.de_json(request.get_json(force=True), bot)
    dispatcher.process_update(update)
    return "ok"

# === Dispatcher 注册 ===
dispatcher.add_handler(CommandHandler("start", start))
dispatcher.add_handler(CommandHandler("clockin", clockin))
dispatcher.add_handler(CommandHandler("clockout", clockout))
dispatcher.add_handler(CommandHandler("offday", offday))
dispatcher.add_handler(CommandHandler("balance", balance))
dispatcher.add_handler(CommandHandler("check", check))
dispatcher.add_handler(CommandHandler("viewclaims", viewclaims))
dispatcher.add_handler(CommandHandler("PDF", pdf_start))
dispatcher.add_handler(CallbackQueryHandler(pdf_button_callback, pattern=r"^pdf_"))

# Salary handler
dispatcher.add_handler(ConversationHandler(
    entry_points=[CommandHandler("salary", salary_start)],
    states={
        SALARY_SELECT_DRIVER: [MessageHandler(Filters.text & ~Filters.command, salary_select_driver)],
        SALARY_ENTER_AMOUNT: [MessageHandler(Filters.text & ~Filters.command, salary_enter_amount)],
    },
    fallbacks=[CommandHandler("cancel", cancel)],
))

# Topup handler
dispatcher.add_handler(ConversationHandler(
    entry_points=[CommandHandler("topup", topup_start)],
    states={
        TOPUP_USER: [MessageHandler(Filters.text & ~Filters.command, topup_user)],
        TOPUP_AMOUNT: [MessageHandler(Filters.text & ~Filters.command, topup_amount)],
    },
    fallbacks=[CommandHandler("cancel", cancel)],
))

# Claim handler
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

# Error handler
dispatcher.add_error_handler(error_handler)

# === Run ===
if __name__ == "__main__":
    logger.info("Bot server started.")
    port = int(os.environ.get("PORT", 5000))
    logger.info(f"Starting on port: {port}")
    app.run(host="0.0.0.0", port=port)
