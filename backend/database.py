import sqlite3
from pathlib import Path
from datetime import datetime

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "receptionist.db"


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS employees (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        department TEXT,
        role TEXT,
        email TEXT,
        floor TEXT,
        extension TEXT,
        reports_to INTEGER,
        is_public INTEGER DEFAULT 1,
        photo_path TEXT,
        FOREIGN KEY (reports_to) REFERENCES employees(id)
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS visitors (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        meeting_with TEXT,
        purpose TEXT,
        badge_id TEXT,
        check_in_time TEXT,
        check_out_time TEXT,
        id_photo_path TEXT
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS conversations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_text TEXT,
        ai_response TEXT,
        timestamp TEXT
    )
    """)

    conn.commit()
    conn.close()


def save_conversation(user_text: str, ai_response: str):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
    INSERT INTO conversations (user_text, ai_response, timestamp)
    VALUES (?, ?, ?)
    """, (user_text, ai_response, datetime.now().isoformat()))
    conn.commit()
    conn.close()
    

def get_all_conversations():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM conversations")
    rows = cursor.fetchall()
    conn.close()
    return rows


def generate_badge_id(visitor_id: int):
    year = datetime.utcnow().year
    return f"VIS-{year}-{visitor_id:04d}"


def add_visitor(name, meeting_with, purpose):
    conn = get_connection()
    cursor = conn.cursor()

    check_in_time = datetime.utcnow().isoformat()

    cursor.execute("""
    INSERT INTO visitors (name, meeting_with, purpose, check_in_time)
    VALUES (?, ?, ?, ?)
    """, (name, meeting_with, purpose, check_in_time))

    visitor_id = cursor.lastrowid
    badge_id = generate_badge_id(visitor_id)

    cursor.execute("""
    UPDATE visitors SET badge_id = ? WHERE id = ?
    """, (badge_id, visitor_id))

    conn.commit()
    conn.close()
    return badge_id


def checkout_visitor(badge_id: str):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
    UPDATE visitors SET check_out_time = ? WHERE badge_id = ?
    """, (datetime.utcnow().isoformat(), badge_id))
    conn.commit()
    conn.close()


def get_recent_conversations(limit: int = 5):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
    SELECT user_text, ai_response
    FROM conversations
    ORDER BY id DESC
    LIMIT ?
    """, (limit,))
    rows = cursor.fetchall()
    conn.close()
    return rows[::-1]  # oldest → newest


def set_setting(key: str, value: str):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
    INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)
    """, (key, value))
    conn.commit()
    conn.close()


def get_setting(key: str):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT value FROM settings WHERE key = ?", (key,))
    row = cursor.fetchone()
    conn.close()
    return row["value"] if row else ""


# FIX: get_hr() was missing the search parameter — now accepts a name argument
def get_hr(name: str = "HR"):
    conn = get_connection()
    cursor = conn.cursor()
    # Try to get HR Manager first, fallback to any HR employee
    cursor.execute("""
    SELECT name, department, floor, extension
    FROM employees
    WHERE LOWER(department) = 'hr'
    AND LOWER(role) LIKE '%manager%'
    AND is_public = 1
    LIMIT 1
    """)
    row = cursor.fetchone()
    if not row:
        cursor.execute("""
        SELECT name, department, floor, extension
        FROM employees
        WHERE LOWER(department) = 'hr'
        AND is_public = 1
        LIMIT 1
        """)
        row = cursor.fetchone()
    conn.close()
    return row


def get_employee_by_name(name: str):
    conn = get_connection()
    cursor = conn.cursor()
    # Try exact match first
    cursor.execute("""
    SELECT name, department, role, floor, extension
    FROM employees
    WHERE LOWER(name) = LOWER(?)
    AND is_public = 1
    """, (name,))
    row = cursor.fetchone()
    # Fallback: fuzzy LIKE match on first or last name
    if not row:
        cursor.execute("""
        SELECT name, department, role, floor, extension
        FROM employees
        WHERE (LOWER(name) LIKE LOWER(?) OR LOWER(name) LIKE LOWER(?))
        AND is_public = 1
        LIMIT 1
        """, (f"%{name}%", f"{name}%"))
        row = cursor.fetchone()
    conn.close()
    return row


def get_department_manager(department: str):
    conn = get_connection()
    cursor = conn.cursor()
    # Exact match first
    cursor.execute("""
    SELECT name, role, floor, extension
    FROM employees
    WHERE LOWER(department) = LOWER(?)
    AND LOWER(role) LIKE '%manager%'
    AND is_public = 1
    LIMIT 1
    """, (department,))
    row = cursor.fetchone()
    # Fuzzy match fallback
    if not row:
        cursor.execute("""
        SELECT name, role, floor, extension
        FROM employees
        WHERE LOWER(department) LIKE LOWER(?)
        AND LOWER(role) LIKE '%manager%'
        AND is_public = 1
        LIMIT 1
        """, (f"%{department}%",))
        row = cursor.fetchone()
    conn.close()
    return row


def get_visitor_by_name(name: str):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
    SELECT name, meeting_with, purpose, check_in_time
    FROM visitors
    WHERE LOWER(name) = LOWER(?)
    ORDER BY id DESC
    LIMIT 1
    """, (name,))
    row = cursor.fetchone()
    conn.close()
    return row