import sqlite3
import difflib
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

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS meetings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        organizer_name TEXT NOT NULL,
        organizer_type TEXT NOT NULL,
        employee_name TEXT NOT NULL,
        employee_email TEXT,
        meeting_date TEXT NOT NULL,
        meeting_time TEXT NOT NULL,
        purpose TEXT,
        status TEXT DEFAULT 'scheduled',
        created_at TEXT
    )
    """)

    # Reception log — unified record of every person who interacted with reception
    # person_type: VISITOR, EMPLOYEE, DELIVERY, JOB_SEEKER
    # linked_visitor_id: FK to visitors.id (set for VISITOR and DELIVERY)
    # linked_employee_id: FK to employees.id (set for EMPLOYEE)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS reception_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        person_name TEXT NOT NULL,
        person_type TEXT NOT NULL,
        linked_visitor_id INTEGER,
        linked_employee_id INTEGER,
        check_in_time TEXT NOT NULL,
        check_out_time TEXT,
        notes TEXT,
        FOREIGN KEY (linked_visitor_id) REFERENCES visitors(id),
        FOREIGN KEY (linked_employee_id) REFERENCES employees(id)
    )
    """)

    conn.commit()
    conn.close()


# ─────────────────────────────────────────────
# RECEPTION LOG
# ─────────────────────────────────────────────

def log_reception_entry(
    person_name: str,
    person_type: str,
    notes: str = "",
    linked_visitor_id: int = None,
    linked_employee_id: int = None
) -> int:
    """Add an entry to the reception log. Returns the log entry id."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
    INSERT INTO reception_log
        (person_name, person_type, linked_visitor_id, linked_employee_id, check_in_time, notes)
    VALUES (?, ?, ?, ?, ?, ?)
    """, (
        person_name,
        person_type,
        linked_visitor_id,
        linked_employee_id,
        datetime.utcnow().isoformat(),
        notes
    ))
    log_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return log_id


def log_reception_checkout(log_id: int):
    """Mark a reception log entry as checked out."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
    UPDATE reception_log SET check_out_time = ? WHERE id = ?
    """, (datetime.utcnow().isoformat(), log_id))
    conn.commit()
    conn.close()


# ─────────────────────────────────────────────
# EMPLOYEE NAME SIMILARITY
# ─────────────────────────────────────────────

def get_similar_employee(name: str, cutoff: float = 0.55) -> dict | None:
    """
    Check if a given name is similar to any employee name using difflib.
    Used to decide whether to ask 'are you a visitor or employee?'

    Uses a lower cutoff (0.55) than get_employee_by_name (0.6) so it catches
    partial matches like 'Rahul' being similar to 'Rahul Sharma'.

    Returns the best matching employee as a dict, or None if no close match.
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
    SELECT id, name, department, role, floor, extension
    FROM employees
    WHERE is_public = 1
    """)
    all_employees = cursor.fetchall()
    conn.close()

    if not all_employees:
        return None

    name_lower = name.lower().strip()

    # Check if the given name appears as a substring of any employee name
    # e.g. "Rahul" is a substring of "Rahul Sharma"
    for emp in all_employees:
        emp_name_lower = emp["name"].lower()
        emp_parts = emp_name_lower.split()

        # Exact first name match or exact last name match
        if name_lower in emp_parts:
            return dict(emp)

    # Fallback: difflib similarity on full name
    emp_names = [emp["name"] for emp in all_employees]
    matches = difflib.get_close_matches(name, emp_names, n=1, cutoff=cutoff)
    if matches:
        for emp in all_employees:
            if emp["name"] == matches[0]:
                return dict(emp)

    return None


# ─────────────────────────────────────────────
# CONVERSATIONS
# ─────────────────────────────────────────────

def save_conversation(user_text: str, ai_response: str):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
    INSERT INTO conversations (user_text, ai_response, timestamp)
    VALUES (?, ?, ?)
    """, (user_text, ai_response, datetime.utcnow().isoformat()))
    conn.commit()
    conn.close()


def get_all_conversations():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM conversations")
    rows = cursor.fetchall()
    conn.close()
    return rows


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


# ─────────────────────────────────────────────
# VISITORS
# ─────────────────────────────────────────────

def generate_badge_id(visitor_id: int):
    year = datetime.utcnow().year
    return f"VIS-{year}-{visitor_id:04d}"


def add_visitor(name: str, meeting_with: str, purpose: str) -> tuple[str, int]:
    """
    Insert visitor record. Returns (badge_id, visitor_id).
    Caller is responsible for logging to reception_log.
    """
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
    return badge_id, visitor_id


def checkout_visitor(badge_id: str):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
    UPDATE visitors SET check_out_time = ? WHERE badge_id = ?
    """, (datetime.utcnow().isoformat(), badge_id))
    conn.commit()
    conn.close()


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


# ─────────────────────────────────────────────
# SETTINGS
# ─────────────────────────────────────────────

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


# ─────────────────────────────────────────────
# EMPLOYEES
# ─────────────────────────────────────────────

def get_hr(name: str = "HR"):
    conn = get_connection()
    cursor = conn.cursor()
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
    """
    Strict employee lookup. Used for directory lookups and meeting targets.
    Requires a reasonably strong match — not used for identity verification.
    """
    conn = get_connection()
    cursor = conn.cursor()

    name_clean = name.lower().strip()

    # 1. Exact full name match
    cursor.execute("""
    SELECT id, name, department, role, floor, extension, email
    FROM employees
    WHERE LOWER(name) = LOWER(?)
    AND is_public = 1
    """, (name_clean,))
    row = cursor.fetchone()

    # 2. Substring / partial name match
    if not row:
        cursor.execute("""
        SELECT id, name, department, role, floor, extension, email
        FROM employees
        WHERE (LOWER(name) LIKE LOWER(?) OR LOWER(name) LIKE LOWER(?))
        AND is_public = 1
        LIMIT 1
        """, (f"%{name_clean}%", f"{name_clean}%"))
        row = cursor.fetchone()

    # 3. Difflib fuzzy match (cutoff 0.6 — stricter than get_similar_employee)
    if not row:
        cursor.execute("""
        SELECT id, name, department, role, floor, extension, email
        FROM employees
        WHERE is_public = 1
        """)
        all_employees = cursor.fetchall()
        emp_names = [emp["name"] for emp in all_employees]
        matches = difflib.get_close_matches(name_clean, emp_names, n=1, cutoff=0.6)
        if matches:
            for emp in all_employees:
                if emp["name"] == matches[0]:
                    row = emp
                    break

    conn.close()
    return row


def get_employee_by_name_and_department(name: str, department: str):
    """
    Verify an employee by both name fragment AND department.
    Used during identity confirmation to avoid false positives.
    Returns employee dict or None.
    """
    conn = get_connection()
    cursor = conn.cursor()

    name_clean = name.lower().strip()
    dept_clean = department.lower().strip()

    cursor.execute("""
    SELECT id, name, department, role, floor, extension, email
    FROM employees
    WHERE LOWER(name) LIKE LOWER(?)
    AND (LOWER(department) LIKE LOWER(?) OR LOWER(role) LIKE LOWER(?))
    AND is_public = 1
    LIMIT 1
    """, (f"%{name_clean}%", f"%{dept_clean}%", f"%{dept_clean}%"))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


def get_department_manager(department: str):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
    SELECT name, role, floor, extension
    FROM employees
    WHERE LOWER(department) = LOWER(?)
    AND LOWER(role) LIKE '%manager%'
    AND is_public = 1
    LIMIT 1
    """, (department,))
    row = cursor.fetchone()
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


# ─────────────────────────────────────────────
# MEETINGS
# ─────────────────────────────────────────────

def schedule_meeting(organizer_name: str, organizer_type: str, employee_name: str,
                     meeting_date: str, meeting_time: str, purpose: str = "") -> int:
    """Save a meeting and return its ID."""
    conn = get_connection()
    cursor = conn.cursor()

    emp = get_employee_by_name(employee_name)
    employee_email = dict(emp).get("email", "") if emp else ""

    cursor.execute("""
    INSERT INTO meetings (organizer_name, organizer_type, employee_name, employee_email,
                         meeting_date, meeting_time, purpose, status, created_at)
    VALUES (?, ?, ?, ?, ?, ?, ?, 'scheduled', ?)
    """, (organizer_name, organizer_type, employee_name, employee_email,
          meeting_date, meeting_time, purpose, datetime.now().isoformat()))

    meeting_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return meeting_id


def get_employee_meetings(employee_name: str, meeting_date: str) -> list:
    """Get all meetings for an employee on a given date."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
    SELECT meeting_time, organizer_name, purpose
    FROM meetings
    WHERE LOWER(employee_name) = LOWER(?)
    AND meeting_date = ?
    AND status = 'scheduled'
    ORDER BY meeting_time
    """, (employee_name, meeting_date))
    rows = cursor.fetchall()
    conn.close()
    return rows


def get_available_slots(employee_name: str, meeting_date: str) -> list:
    """Return available 1-hour slots between 9AM-5PM for an employee on a date."""
    all_slots = [
        "09:00", "10:00", "11:00", "12:00",
        "13:00", "14:00", "15:00", "16:00", "17:00"
    ]
    booked = get_employee_meetings(employee_name, meeting_date)
    booked_times = [row["meeting_time"][:5] for row in booked]
    return [s for s in all_slots if s not in booked_times]


def cancel_meeting(meeting_id: int):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
    UPDATE meetings SET status = 'cancelled' WHERE id = ?
    """, (meeting_id,))
    conn.commit()
    conn.close()