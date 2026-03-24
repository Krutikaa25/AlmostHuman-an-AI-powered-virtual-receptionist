from database import get_connection

conn = get_connection()
cursor = conn.cursor()

# Clear existing employees to avoid duplicates on re-run
cursor.execute("DELETE FROM employees")

employees = [
    # HR Department
    ("Anita Verma",     "HR",                   "HR Manager",               "2nd Floor", "101"),
    ("Deepak Joshi",    "HR",                   "HR Executive",             "2nd Floor", "102"),

    # AI & Machine Learning
    ("Priya Nair",      "AI & Machine Learning","Engineering Manager",       "3rd Floor", "201"),
    ("Rahul Sharma",    "AI & Machine Learning","Senior ML Engineer",        "3rd Floor", "202"),
    ("Sneha Iyer",      "AI & Machine Learning","ML Engineer",               "3rd Floor", "203"),

    # DevOps
    ("Karan Mehta",     "DevOps",               "DevOps Manager",           "4th Floor", "301"),
    ("Rohan Das",       "DevOps",               "DevOps Engineer",          "4th Floor", "302"),

    # Product Development
    ("Neha Kapoor",     "Product Development",  "Product Manager",          "5th Floor", "401"),
    ("Arjun Reddy",     "Product Development",  "Senior Developer",         "5th Floor", "402"),
    ("Pooja Singh",     "Product Development",  "UI/UX Designer",           "5th Floor", "403"),

    # Management
    ("Vikram Malhotra", "Management",           "CEO",                      "6th Floor", "501"),
    ("Sunita Rao",      "Management",           "CTO",                      "6th Floor", "502"),
]

for emp in employees:
    cursor.execute("""
    INSERT INTO employees (name, department, role, floor, extension, is_public)
    VALUES (?, ?, ?, ?, ?, 1)
    """, emp)

conn.commit()
conn.close()

print(f"✅ {len(employees)} employees seeded successfully!")