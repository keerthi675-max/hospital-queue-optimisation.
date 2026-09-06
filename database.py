"""
database.py
-----------
SQLite data-access layer for the Smart Hospital Queue (token + predicted
arrival time) system. SQLite is used instead of Postgres so the project
runs on any laptop with zero server setup - see README.md for the small,
mechanical change needed to swap in Postgres later.
"""

import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "hospital.db")


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(reset=False):
    if reset and os.path.exists(DB_PATH):
        os.remove(DB_PATH)

    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS doctors (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            department TEXT NOT NULL,
            available INTEGER NOT NULL DEFAULT 1,
            avg_consultation_minutes REAL NOT NULL DEFAULT 10
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS patients (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            phone TEXT NOT NULL,
            department TEXT NOT NULL,
            registration_time REAL NOT NULL
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            patient_id INTEGER NOT NULL REFERENCES patients(id),
            token TEXT UNIQUE NOT NULL,
            department TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'waiting',  -- waiting | in_consultation | done
            expected_time REAL,
            previous_expected_time REAL,
            created_at REAL NOT NULL
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            queue_id INTEGER NOT NULL REFERENCES queue(id),
            message TEXT NOT NULL,
            created_at REAL NOT NULL,
            delivered INTEGER NOT NULL DEFAULT 0
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS agent_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            department TEXT,
            step TEXT NOT NULL,   -- OBSERVE | THINK | ACT
            detail TEXT NOT NULL,
            created_at REAL NOT NULL
        )
    """)

    conn.commit()
    conn.close()
