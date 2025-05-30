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

# Import database utility functions
import db_utils

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
PHOTO_SERVER_BASE_URL = os.environ.get("PHOTO_SERVER_URL", "http://127.0.0.1:5001") # URL for the photo server

# === 初始化 ===
app = Flask(__name__)
bot = Bot(token=TOKEN)
dispatcher = Dispatcher(bot, None, use_context=True)

# 确保数据库已初始化
db_utils.initialize_database()

# === 时区 ===
tz = pytz.timezone("Asia/Kuala_Lumpur")

# === conversation 状态 ===
TOPUP_USER, TOPUP_AMOUNT = range(2)
CLAIM_TYPE, CLAIM_OTHER_TYPE, CLAIM_AMOUNT, CLAIM_PROOF = range(4)
PDF_SELECT_DRIVER = range(1)
SALARY_SELECT_DRIVER, SALARY_ENTER_AMOUNT = range(2)

# === 辅助函数 (保持不变) ===
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
    salary_info = db_utils.get_driver_salary_info(driver_id)
    if salary_info and salary_info["monthly_salary"] is not None:
        return calculate_hourly_rate(salary_info["monthly_salary"])
    return DEFAULT_HOURLY_RATE

# === PDF 生成功能 (需要修改以从数据库获取数据) ===
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
    driver_name = db_utils.get_driver_name(driver_id)
    driver_logs_db = db_utils.get_driver_clock_logs(driver_id)
    driver_salary_info = db_utils.get_driver_salary_info(driver_id)
    driver_claims = db_utils.get_driver_claims(driver_id)
    driver_topups = db_utils.get_driver_topups(driver_id)
    driver_balance = db_utils.get_account_balance(driver_id)

    doc = SimpleDocTemplate(
        output_path,
        pagesize=A4,
        rightMargin=72,
        leftMargin=72,
        topMargin=72,
        bottomMargin=72
    )

    # Styles (保持不变)
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
    total_hours_calculated = 0 # Calculate from logs for display consistency

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
            ("ALIGN", (0, 0), (-1, -1), "CENTER"), # Center align all
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
                        # os.remove(photo_path) # Clean up temp file
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
    period_text = f"Summary Period: {first_day.strftime("%Y-%m-%d")} to {last_day.strftime("%Y-%m-%d")}"
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
            topup_data.append([topup.get("date", "N/A"), f"RM{topup.get("amount", 0):.2f}"])
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
            claim_data.append([claim.get("date", "N/A"), claim.get("type", "N/A"), f"RM{claim.get("amount", 0):.2f}"])
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
    driver_ids = db_utils.get_all_driver_ids()
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

# === 错误处理函数 (保持不变) ===
def error_handler(update, context):
    logger.error("Exception while handling an update:", exc_info=context.error)
    try:
        if update and update.effective_message:
            update.effective_message.reply_text("⚠️ An unexpected error occurred. Please try again later.")
    except:
        logger.error("Failed to send error message to user")
    tb_list = traceback.format_exception(None, context.error, context.error.__traceback__)
    tb_string = ".join(tb_list)"
    logger.error(f"Full traceback:\n{tb_string}")

# === /start (使用数据库) ===
def start(update, context):
    user = update.effective_user
    user_id = user.id
    username = user.username or str(user_id)
    first_name = user.first_name

    # Ensure driver exists in DB
    db_utils.ensure_driver_exists(user_id, username, first_name)

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

# === /clockin (使用数据库) ===
def clockin(update, context):
    user = update.effective_user
    user_id = user.id
    username = user.username or str(user_id)
    first_name = user.first_name
    db_utils.ensure_driver_exists(user_id, username, first_name) # Ensure driver exists

    now = datetime.datetime.now(tz)
    today = now.strftime("%Y-%m-%d")
    clock_time = now.strftime("%Y-%m-%d %H:%M:%S") # Store with seconds for accuracy

    db_utils.save_clock_in(user_id, today, clock_time)

    local_time = format_local_time(clock_time)
    update.message.reply_text(f"✅ Clocked in at {local_time}")
    logger.info(f"User {username} clocked in at {clock_time}")

# === /clockout (使用数据库) ===
def clockout(update, context):
    user = update.effective_user
    user_id = user.id
    username = user.username or str(user_id)
    first_name = user.first_name
    db_utils.ensure_driver_exists(user_id, username, first_name) # Ensure driver exists

    now = datetime.datetime.now(tz)
    today = now.strftime("%Y-%m-%d")
    clock_time = now.strftime("%Y-%m-%d %H:%M:%S")

    # Get clock-in time from DB
    log = db_utils.get_clock_log(user_id, today)

    if not log or not log.get("in") or log.get("in") == "OFF":
        error_msg = "❌ You haven"t clocked in today or are marked as off."
        logger.warning(f"User {username}: {error_msg}")
        update.message.reply_text(error_msg)
        return

    if log.get("out") and log.get("out") != "N/A":
         update.message.reply_text("⚠️ You have already clocked out today.")
         return

    try:
        db_utils.save_clock_out(user_id, today, clock_time)

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

        # Update salary info in DB
        db_utils.update_driver_salary(user_id, hours_worked, today, hours_worked)

        local_time = format_local_time(clock_time)
        update.message.reply_text(f"🏁 Clocked out at {local_time}. Worked {time_str}.")
        logger.info(f"User {username} clocked out: worked {time_str}")
    except Exception as e:
        logger.error(f"Clockout error for user {username}: {str(e)}")
        logger.exception(e)
        update.message.reply_text("⚠️ An error occurred during clockout. Please try again.")

# === /offday (使用数据库) ===
def offday(update, context):
    user = update.effective_user
    user_id = user.id
    username = user.username or str(user_id)
    first_name = user.first_name
    db_utils.ensure_driver_exists(user_id, username, first_name) # Ensure driver exists

    today = datetime.datetime.now(tz).strftime("%Y-%m-%d")
    db_utils.save_off_day(user_id, today)
    update.message.reply_text(f"📅 Marked {today} as off day.")
    logger.info(f"User {username} marked {today} as off day")

# === /balance（管理员, 使用数据库）===
def balance(update, context):
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        return
    logger.info(f"Admin {user_id} requested balance")

    balances = db_utils.get_all_balances()
    msg = "📊 Driver Balances:\n"
    if not balances:
        msg += "No driver data found."
    else:
        for uid, bal in balances.items():
            name = db_utils.get_driver_name(uid)
            msg += f"• {name}: RM{bal:.2f}\n"
    update.message.reply_text(msg)

# === /check（管理员, 使用数据库）===
def check(update, context):
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        return
    logger.info(f"Admin {user_id} requested check")

    today = datetime.datetime.now(tz).strftime("%Y-%m-%d")
    msg = f"📄 Status for {today}:\n"
    driver_ids = db_utils.get_all_driver_ids()

    if not driver_ids:
        msg += "No drivers found."
    else:
        for uid in driver_ids:
            log = db_utils.get_clock_log(uid, today)
            name = db_utils.get_driver_name(uid)
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

# === /viewclaims（管理员, 使用Web界面）===
def viewclaims(update, context):
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        return update.message.reply_text("❌ You are not an admin.")

    logger.info(f"Admin {user_id} requested viewclaims")
    # Send a link to the web interface
    claims_url = f"{PHOTO_SERVER_BASE_URL}/"
    keyboard = [[InlineKeyboardButton("View Claims Online", url=claims_url)]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    update.message.reply_text(
        "📷 Click the button below to view claims with photos in your browser:",
        reply_markup=reply_markup
    )

# === /salary (管理员, 使用数据库) ===
def salary_start(update, context):
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        return update.message.reply_text("❌ You are not an admin.")
    logger.info(f"Admin {user_id} started salary setting process")

    keyboard = []
    context.user_data["salary_driver_map"] = {}
    driver_ids = db_utils.get_all_driver_ids()

    if not driver_ids:
        update.message.reply_text("❌ No drivers found.")
        return ConversationHandler.END

    for uid in driver_ids:
        name = db_utils.get_driver_name(uid)
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

    salary_info = db_utils.get_driver_salary_info(driver_id)
    current_salary = "Not Set"
    if salary_info and salary_info["monthly_salary"] is not None:
        current_salary = f"RM{salary_info["monthly_salary"]:.2f}"

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

        db_utils.set_monthly_salary(driver_id, monthly_salary)
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
        # Clean up user_data
        context.user_data.pop("salary_driver_id", None)
        context.user_data.pop("salary_driver_name", None)
        context.user_data.pop("salary_driver_map", None)
    return ConversationHandler.END

# === /PDF (管理员, 使用数据库) ===
def pdf_start(update, context):
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        return update.message.reply_text("❌ You are not an admin.")
    logger.info(f"Admin {user_id} started PDF generation process")

    keyboard = [[InlineKeyboardButton("📊 All Drivers", callback_data="pdf_all")]]
    context.user_data["pdf_driver_map"] = {}
    driver_ids = db_utils.get_all_driver_ids()

    if not driver_ids:
        update.message.reply_text("❌ No drivers found.")
        return

    for uid in driver_ids:
        name = db_utils.get_driver_name(uid)
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
                driver_name = db_utils.get_driver_name(driver_id)
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
        # Clean up temp dir?
        pass
        # Clean up user_data
        context.user_data.pop("pdf_driver_map", None)

# === /topup (交互流程管理员专用, 使用数据库) ===
def topup_start(update, context):
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        return
    logger.info(f"Admin {user_id} started topup process")

    keyboard = []
    context.user_data["topup_driver_map"] = {}
    driver_ids = db_utils.get_all_driver_ids()

    if not driver_ids:
        update.message.reply_text("❌ No drivers found.")
        return ConversationHandler.END

    for uid in driver_ids:
        name = db_utils.get_driver_name(uid)
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
        db_utils.save_topup(uid, today, amount, admin_id)

        name = db_utils.get_driver_name(uid)
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

# === /claim 分阶段 (使用数据库) ===
def claim_start(update, context):
    user = update.effective_user
    user_id = user.id
    username = user.username or str(user_id)
    first_name = user.first_name
    db_utils.ensure_driver_exists(user_id, username, first_name) # Ensure driver exists

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

        # Save claim to DB
        db_utils.save_claim(user_id, date, claim_type, amount, file_id)

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
    # Clean up user_data for all conversations
    keys_to_pop = [k for k in context.user_data if k.startswith(("claim_", "topup_", "pdf_", "salary_"))]
    for key in keys_to_pop:
        context.user_data.pop(key, None)
    logger.info(f"User {username} cancelled operation")
    return ConversationHandler.END

# === Webhook (保持不变) ===
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

# === Run (保持不变) ===
if __name__ == "__main__":
    logger.info("Bot server started.")
    # Note: This Flask app is only for the webhook.
    # The photo server runs separately.
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
