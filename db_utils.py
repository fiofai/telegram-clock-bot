import sqlite3
import logging
import os
import json

logger = logging.getLogger(__name__)

DATABASE_FILE = "bot_data.db"

def initialize_database():
    """Initialize the SQLite database and create tables if they don't exist."""
    try:
        conn = sqlite3.connect(DATABASE_FILE)
        cursor = conn.cursor()

        # Create drivers table (optional but good practice)
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
        # Add index for faster lookups
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_clock_logs_driver_date ON clock_logs (driver_id, date)")

        # Create salaries table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS salaries (
                driver_id INTEGER PRIMARY KEY,
                monthly_salary REAL,
                total_hours REAL DEFAULT 0.0,
                daily_log_json TEXT,  -- Store daily log as JSON
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
        # Add index
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
        # Add index
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_topups_driver_date ON topups (driver_id, date)")

        conn.commit()
        conn.close()
        logger.info("Database initialized successfully.")
    except sqlite3.Error as e:
        logger.error(f"Database initialization error: {e}")
        raise

# --- Data Access Functions ---

def get_db_connection():
    """Establish and return a database connection."""
    conn = sqlite3.connect(DATABASE_FILE)
    conn.row_factory = sqlite3.Row # Return rows as dictionary-like objects
    return conn

# == Driver Functions ==
def ensure_driver_exists(user_id, username, first_name):
    """Ensure a driver exists in the database."""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("INSERT OR IGNORE INTO drivers (user_id, username, first_name) VALUES (?, ?, ?)",
                       (user_id, username, first_name))
        # Update names if they changed
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

# == Clock Log Functions ==
def save_clock_in(driver_id, date, clock_in_time):
    """Save clock-in time."""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        # Check if a log for this driver and date already exists
        cursor.execute("SELECT log_id FROM clock_logs WHERE driver_id = ? AND date = ?", (driver_id, date))
        existing_log = cursor.fetchone()
        if existing_log:
            # Update existing log
            cursor.execute("UPDATE clock_logs SET clock_in_time = ?, clock_out_time = NULL WHERE log_id = ?",
                           (clock_in_time, existing_log['log_id']))
        else:
            # Insert new log
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

# == Salary Functions ==
def update_driver_salary(driver_id, total_hours_increment, date, hours_worked_today):
    """Update driver's total hours and daily log."""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        # Ensure salary record exists
        cursor.execute("INSERT OR IGNORE INTO salaries (driver_id, total_hours, daily_log_json) VALUES (?, 0.0, '{}')", (driver_id,))

        # Get current total hours and daily log
        cursor.execute("SELECT total_hours, daily_log_json FROM salaries WHERE driver_id = ?", (driver_id,))
        row = cursor.fetchone()
        current_total_hours = row['total_hours'] if row else 0.0
        daily_log = json.loads(row['daily_log_json']) if row and row['daily_log_json'] else {}

        # Update values
        new_total_hours = current_total_hours + total_hours_increment
        daily_log[date] = hours_worked_today
        new_daily_log_json = json.dumps(daily_log)

        # Save back to DB
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

# == Account Functions ==
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

# == Claim Functions ==
def save_claim(driver_id, date, claim_type, amount, photo_file_id):
    """Save a new claim."""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("INSERT INTO claims (driver_id, date, type, amount, photo_file_id) VALUES (?, ?, ?, ?, ?)",
                       (driver_id, date, claim_type, amount, photo_file_id))
        conn.commit()
        # Also update balance
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

def get_claims_paginated(driver_id=None, page=1, per_page=5):
    """Get claims with pagination, optionally filtered by driver."""
    conn = get_db_connection()
    claims_list = []
    total_claims = 0
    offset = (page - 1) * per_page
    try:
        cursor = conn.cursor()
        count_query = "SELECT COUNT(*) FROM claims"
        data_query = "SELECT claim_id, driver_id, date, type, amount, photo_file_id FROM claims"
        params = []

        if driver_id:
            count_query += " WHERE driver_id = ?"
            data_query += " WHERE driver_id = ?"
            params.append(driver_id)

        cursor.execute(count_query, params)
        total_claims = cursor.fetchone()[0]

        data_query += " ORDER BY date DESC, claim_id DESC LIMIT ? OFFSET ?"
        params.extend([per_page, offset])
        cursor.execute(data_query, params)
        rows = cursor.fetchall()
        for row in rows:
            claims_list.append(dict(row))

    except sqlite3.Error as e:
        logger.error(f"DB Error (get_claims_paginated): {e}")
    finally:
        conn.close()
    total_pages = (total_claims + per_page - 1) // per_page
    return claims_list, total_claims, total_pages

# == Topup Functions ==
def save_topup(driver_id, date, amount, admin_id):
    """Save a new topup record."""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("INSERT INTO topups (driver_id, date, amount, admin_id) VALUES (?, ?, ?, ?)",
                       (driver_id, date, amount, admin_id))
        conn.commit()
        # Also update balance
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

# Initialize the database when this module is imported
if __name__ != "__main__":
    initialize_database()

