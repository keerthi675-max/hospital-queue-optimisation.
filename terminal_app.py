"""
terminal_app.py
----------------
A pure-terminal version of the Smart Hospital Queue Agent - no browser
needed. Everything the web dashboard shows (doctors on duty, the live
queue board, AI advisory text, patient notifications, and the agent's
OBSERVE -> THINK -> ACT reasoning log) is printed straight to the
console instead, through a simple numbered menu.

It uses the exact same agent.py / database.py logic as the web app
(app.py) - this is just a different "view" on top of the same brain,
so anything you do here (register a patient, toggle a doctor, set an
AI API key) behaves identically to doing it through the website.

Run it with:
    python terminal_app.py
"""

import os
import sys
import time

from database import init_db
import agent

DB_PATH = os.path.join(os.path.dirname(__file__), "hospital.db")

LINE = "=" * 70
THIN = "-" * 70


def ensure_db():
    if not os.path.exists(DB_PATH):
        import seed_data
        seed_data.main()
    else:
        init_db(reset=False)


def pause():
    input("\nPress Enter to return to the menu...")


def print_header(title):
    print("\n" + LINE)
    print(f"  {title}")
    print(LINE)


# ---------------------------------------------------------------------------
# 1. Register a new patient
# ---------------------------------------------------------------------------
def action_register():
    print_header("REGISTER NEW PATIENT")
    name = input("Patient name: ").strip()
    phone = input("Phone number: ").strip()

    departments = agent.queue_tool_department_list()
    print("\nDepartments available:")
    for i, d in enumerate(departments, 1):
        print(f"  {i}. {d}")
    choice = input("Choose department (number or type name): ").strip()

    department = None
    if choice.isdigit() and 1 <= int(choice) <= len(departments):
        department = departments[int(choice) - 1]
    elif choice in departments:
        department = choice

    if not all([name, phone, department]):
        print("\n[Error] Name, phone, and a valid department are all required.")
        pause()
        return

    queue_id, token = agent.patient_tool_register(name, phone, department)
    result = agent.recalc_department(department)

    row = next((r for r in agent.queue_tool_get_waiting(department) if r["id"] == queue_id), None)
    expected_display = agent._fmt_time(row["expected_time"]) if row and row["expected_time"] else "Delayed - no doctor available"

    print(THIN)
    print(f"  TOKEN: {token}")
    print(f"  Department: {department}")
    print(f"  Expected time: {expected_display}")
    print(f"  Currently waiting in this department: {result['metrics']['waiting_count']}")
    print(THIN)
    pause()


# ---------------------------------------------------------------------------
# 2. Live queue board (same table as the website)
# ---------------------------------------------------------------------------
def action_view_queue():
    print_header("LIVE DEPARTURES BOARD")
    departments = agent.queue_tool_department_list()
    print("Departments: " + (", ".join(departments) if departments else "(none yet)"))
    dept_filter = input("Filter by department (Enter for all): ").strip() or None

    rows = agent.queue_tool_get_all(dept_filter)
    now = time.time()

    if not rows:
        print("\nNo one currently in the queue.")
        pause()
        return

    print(f"\n{'TOKEN':<8}{'PATIENT':<18}{'DEPARTMENT':<18}{'EXPECTED':<14}{'STATUS':<14}")
    print(THIN)
    for r in rows:
        expected = agent._fmt_time(r["expected_time"]) if r["expected_time"] else "Delayed"
        mins = round((r["expected_time"] - now) / 60, 1) if r["expected_time"] else None
        expected_str = expected + (f" (~{max(int(mins),0)}m)" if mins is not None and mins > 0 else " (now)" if mins is not None else "")
        print(f"{r['token']:<8}{r['name']:<18}{r['department']:<18}{expected_str:<14}{r['status']:<14}")
    print(THIN)
    print(f"updated {time.strftime('%I:%M:%S %p').lstrip('0')}")
    pause()


# ---------------------------------------------------------------------------
# 3. Doctors on duty
# ---------------------------------------------------------------------------
def action_view_doctors():
    print_header("DOCTORS ON DUTY")
    doctors = agent.doctor_tool_get_all()
    print(f"{'ID':<5}{'NAME':<20}{'DEPARTMENT':<20}{'STATUS':<12}{'AVG MIN':<8}")
    print(THIN)
    for d in doctors:
        status = "ON DUTY" if d["available"] else "OFF DUTY"
        print(f"{d['id']:<5}{d['name']:<20}{d['department']:<20}{status:<12}{d['avg_consultation_minutes']:<8}")
    pause()


# ---------------------------------------------------------------------------
# 4. Toggle a doctor's availability
# ---------------------------------------------------------------------------
def action_toggle_doctor():
    print_header("TOGGLE DOCTOR AVAILABILITY")
    action_view_doctors_inline()
    doc_id = input("\nEnter doctor ID to toggle: ").strip()
    if not doc_id.isdigit():
        print("[Error] Please enter a valid numeric ID.")
        pause()
        return
    department = agent.doctor_tool_toggle(int(doc_id))
    if not department:
        print("[Error] Doctor not found.")
        pause()
        return
    result = agent.recalc_department(department)
    print(f"\nDoctor updated. {len(result['changes'])} patient time(s) recalculated in {department}.")
    for msg in result["changes"]:
        print(f"  - {msg}")
    pause()


def action_view_doctors_inline():
    doctors = agent.doctor_tool_get_all()
    print(f"{'ID':<5}{'NAME':<20}{'DEPARTMENT':<20}{'STATUS':<12}")
    print(THIN)
    for d in doctors:
        status = "ON DUTY" if d["available"] else "OFF DUTY"
        print(f"{d['id']:<5}{d['name']:<20}{d['department']:<20}{status:<12}")


# ---------------------------------------------------------------------------
# 5. Mark a token done
# ---------------------------------------------------------------------------
def action_mark_done():
    print_header("MARK TOKEN AS DONE")
    token = input("Enter token (e.g. G001): ").strip().upper()
    row = agent.queue_tool_get_by_token(token)
    if not row:
        print("[Error] Token not found.")
        pause()
        return
    department = agent.queue_tool_mark_done(row["id"])
    result = agent.recalc_department(department)
    print(f"\nToken {token} marked done. {len(result['changes'])} patient time(s) recalculated.")
    for msg in result["changes"]:
        print(f"  - {msg}")
    pause()


# ---------------------------------------------------------------------------
# 6. Recalculate now (manually re-run the agent loop)
# ---------------------------------------------------------------------------
def action_recalculate():
    print_header("RECALCULATE NOW")
    departments = agent.queue_tool_department_list()
    for d in departments:
        result = agent.recalc_department(d)
        print(f"[{d}] {len(result['changes'])} change(s):")
        for msg in result["changes"]:
            print(f"  - {msg}")
        if not result["changes"]:
            print("  - no changes")
    pause()


# ---------------------------------------------------------------------------
# 7. AI advisory (rule-based, or real AI if a key is set)
# ---------------------------------------------------------------------------
def action_advisory():
    print_header("AI QUEUE AGENT - ADVISORY")
    departments = agent.queue_tool_department_list()
    for i, d in enumerate(departments, 1):
        print(f"  {i}. {d}")
    choice = input("Choose department (number or type name): ").strip()

    department = None
    if choice.isdigit() and 1 <= int(choice) <= len(departments):
        department = departments[int(choice) - 1]
    elif choice in departments:
        department = choice

    if not department:
        print("[Error] Invalid department.")
        pause()
        return

    data = agent.ai_reasoning_advisory(department)
    print(THIN)
    print(f"[{data['level'].upper()}] {department}")
    print(data["text"])
    print(THIN)
    pause()


# ---------------------------------------------------------------------------
# 8. Notifications
# ---------------------------------------------------------------------------
def action_notifications():
    print_header("NOTIFICATIONS SENT TO PATIENTS")
    from database import get_conn
    conn = get_conn()
    rows = [dict(r) for r in conn.execute(
        """SELECT notifications.*, queue.token, queue.department
           FROM notifications JOIN queue ON queue.id = notifications.queue_id
           ORDER BY notifications.created_at DESC LIMIT 30"""
    ).fetchall()]
    conn.close()

    if not rows:
        print("No notifications yet.")
    for r in rows:
        print(f"  [{r['token']}] {r['message']}")
    pause()


# ---------------------------------------------------------------------------
# 9. Agent reasoning log (OBSERVE -> THINK -> ACT)
# ---------------------------------------------------------------------------
def action_log():
    print_header("AGENT REASONING LOG (OBSERVE -> THINK -> ACT)")
    from database import get_conn
    conn = get_conn()
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM agent_log ORDER BY created_at DESC LIMIT 40"
    ).fetchall()]
    conn.close()

    if not rows:
        print("No log entries yet.")
    for r in rows:
        print(f"  [{r['step']:<8}] [{r['department']}] {r['detail']}")
    pause()


# ---------------------------------------------------------------------------
# 10. Set / clear / check the AI API key
# ---------------------------------------------------------------------------
def action_api_key():
    print_header("AI SETTINGS - API KEY")
    status = agent.api_key_status()
    if status["configured"]:
        print(f"Current key: {status['masked']}  (source: {status['source']})")
    else:
        print("No API key set - advisory will use the rule-based fallback.")

    print("\n1. Enter / replace API key")
    print("2. Clear dashboard-entered key")
    print("0. Back to menu")
    choice = input("Choose an option: ").strip()

    if choice == "1":
        key = input("Paste your OpenAI API key (sk-...): ").strip()
        if key:
            agent.set_runtime_api_key(key)
            new_status = agent.api_key_status()
            print(f"\nSaved. Active key: {new_status['masked']} (source: {new_status['source']})")
            print("Try option 7 (AI advisory) now to test it - if the key doesn't")
            print("work, it will automatically fall back to rule-based text and")
            print("show you the error instead of crashing.")
        else:
            print("No key entered, nothing changed.")
    elif choice == "2":
        agent.clear_runtime_api_key()
        print("\nCleared. Back to rule-based advisory (or environment key, if set).")

    pause()


# ---------------------------------------------------------------------------
# Main menu
# ---------------------------------------------------------------------------
MENU = """
1.  Register new patient
2.  View live queue board
3.  View doctors on duty
4.  Toggle a doctor's availability
5.  Mark a token as done
6.  Recalculate now (run agent loop)
7.  AI advisory for a department
8.  View notifications sent to patients
9.  View agent reasoning log
10. AI Settings (set / clear / check API key)
0.  Exit
"""

ACTIONS = {
    "1": action_register,
    "2": action_view_queue,
    "3": action_view_doctors,
    "4": action_toggle_doctor,
    "5": action_mark_done,
    "6": action_recalculate,
    "7": action_advisory,
    "8": action_notifications,
    "9": action_log,
    "10": action_api_key,
}


def main():
    ensure_db()
    print(LINE)
    print("  SMART HOSPITAL QUEUE AGENT  -  Terminal Edition")
    print("  (same app.py / agent.py logic as the web dashboard)")
    print(LINE)

    while True:
        print(MENU)
        choice = input("Choose an option: ").strip()
        if choice == "0":
            print("\nGoodbye.")
            sys.exit(0)
        action = ACTIONS.get(choice)
        if action:
            action()
        else:
            print("\n[Error] Invalid option, try again.")


if __name__ == "__main__":
    main()
