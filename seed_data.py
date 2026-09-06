"""
seed_data.py
------------
Populates a fresh database with sample doctors. Run with: python seed_data.py
"""

from database import init_db, get_conn

DOCTORS = [
    ("Dr. Kumar", "Cardiology", 15),
    ("Dr. Rao", "Cardiology", 15),
    ("Dr. Fernandes", "General Medicine", 8),
    ("Dr. Menon", "General Medicine", 8),
    ("Dr. Iyer", "Pediatrics", 10),
    ("Dr. Sheikh", "Orthopedics", 12),
]

def main():
    init_db(reset=True)
    conn = get_conn()
    conn.executemany(
        "INSERT INTO doctors (name, department, available, avg_consultation_minutes) VALUES (?, ?, 1, ?)",
        DOCTORS,
    )
    conn.commit()
    conn.close()
    print(f"Database initialized at hospital.db with {len(DOCTORS)} doctors.")

if __name__ == "__main__":
    main()
