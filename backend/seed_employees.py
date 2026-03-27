from database import get_connection

conn = get_connection()
cursor = conn.cursor()

# Clear existing employees to avoid duplicates on re-run
cursor.execute("DELETE FROM employees")

employees = [
    # name, department, role, floor, extension, email
    # HR Department
    ("Anita Verma",     "HR",                   "HR Manager",          "2nd Floor", "101", "krutikakanchani847@gmail.com"),
    ("Deepak Joshi",    "HR",                   "HR Executive",        "2nd Floor", "102", "deepak.joshi@sharpsoftware.in"),

    # AI & Machine Learning
    ("Priya Nair",      "AI & Machine Learning","Engineering Manager", "3rd Floor", "201", "krutikaak07@gmail.com"),
    ("Rahul Sharma",    "AI & Machine Learning","Senior ML Engineer",  "3rd Floor", "202", "rahul.sharma@sharpsoftware.in"),
    ("Sneha Iyer",      "AI & Machine Learning","ML Engineer",         "3rd Floor", "203", "sneha.iyer@sharpsoftware.in"),

    # DevOps
    ("Karan Mehta",     "DevOps",               "DevOps Manager",      "4th Floor", "301", "karan.mehta@sharpsoftware.in"),
    ("Rohan Das",       "DevOps",               "DevOps Engineer",     "4th Floor", "302", "rohan.das@sharpsoftware.in"),

    # Product Development
    ("Neha Kapoor",     "Product Development",  "Product Manager",     "5th Floor", "401", "neha.kapoor@sharpsoftware.in"),
    ("Arjun Reddy",     "Product Development",  "Senior Developer",    "5th Floor", "402", "arjun.reddy@sharpsoftware.in"),
    ("Pooja Singh",     "Product Development",  "UI/UX Designer",      "5th Floor", "403", "pooja.singh@sharpsoftware.in"),

    # Management
    ("Vikram Malhotra", "Management",           "CEO",                 "6th Floor", "501", "vikram.malhotra@sharpsoftware.in"),
    ("Sunita Rao",      "Management",           "CTO",                 "6th Floor", "502", "sunita.rao@sharpsoftware.in"),
]

for emp in employees:
    cursor.execute("""
    INSERT INTO employees (name, department, role, floor, extension, email, is_public)
    VALUES (?, ?, ?, ?, ?, ?, 1)
    """, emp)

conn.commit()
conn.close()

print(f"✅ {len(employees)} employees seeded successfully!")