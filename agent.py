"""
agent.py
--------
The Smart Hospital Queue AI agent.

The core idea: don't make patients wait in the queue - make the queue wait
for the patient. A patient registers online, gets a token and a predicted
arrival time, and can stay home until close to that time. The agent's job
is to keep that prediction honest as real conditions change (more patients
join, a doctor goes unavailable, someone finishes early) - it continuously
re-observes the department and recalculates every waiting patient's slot.

Four tools:
    1. patient_tool  -> register_patient(name, phone, department)
    2. queue_tool    -> get_department_metrics(), get_queue(), mark_done()
    3. doctor_tool   -> get_available_doctors(), toggle_availability()
    4. ai_reasoning  -> advisory(department)  [natural-language recommendation]

Agent loop (OBSERVE -> THINK -> ACT), implemented in recalc_department():
    OBSERVE : how many patients are waiting, how many doctors are free,
              what's the average consultation time right now.
    THINK   : recompute every waiting patient's expected time given that.
    ACT     : write the new times back, and queue a notification for
              anyone whose time moved by a meaningful amount.

This loop is re-triggered automatically whenever something changes
(a new registration, a doctor going on/off duty, someone finishing up)
and can also be re-run manually to simulate the periodic monitoring a
real deployment would do on a timer - that repeated OBSERVE-AGAIN is what
makes this an agent rather than a one-shot token generator.
"""

import os
import json
import time

from database import get_conn

DEFAULT_AVG_CONSULT_MIN = 10  # used when a department currently has no doctors on duty
NOTIFY_THRESHOLD_SECONDS = 120  # only notify if predicted time moves by >2 min

DEPT_PREFIX = {
    "Cardiology": "C",
    "General Medicine": "G",
    "Pediatrics": "P",
    "Orthopedics": "O",
}


def _prefix_for(department):
    return DEPT_PREFIX.get(department, department[:1].upper() or "X")


def _log(department, step, detail):
    conn = get_conn()
    conn.execute(
        "INSERT INTO agent_log (department, step, detail, created_at) VALUES (?, ?, ?, ?)",
        (department, step, detail, time.time()),
    )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Tool 1: PATIENT TOOL
# ---------------------------------------------------------------------------
def _next_token(conn, department):
    prefix = _prefix_for(department)
    row = conn.execute(
        "SELECT token FROM queue WHERE token LIKE ? ORDER BY id DESC LIMIT 1",
        (prefix + "%",),
    ).fetchone()
    if row:
        try:
            n = int(row["token"][len(prefix):]) + 1
        except ValueError:
            n = 1
    else:
        n = 1
    return f"{prefix}{n:03d}"


def patient_tool_register(name, phone, department):
    """Registers a patient and gives them a place in the queue. Returns the
    queue_id; the caller should immediately run recalc_department() to get
    a real predicted time rather than a stale/blank one."""
    conn = get_conn()
    now = time.time()
    conn.execute(
        "INSERT INTO patients (name, phone, department, registration_time) VALUES (?, ?, ?, ?)",
        (name, phone, department, now),
    )
    patient_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]

    token = _next_token(conn, department)
    conn.execute(
        """INSERT INTO queue (patient_id, token, department, status, expected_time,
                               previous_expected_time, created_at)
           VALUES (?, ?, ?, 'waiting', NULL, NULL, ?)""",
        (patient_id, token, department, now),
    )
    conn.commit()
    queue_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
    conn.close()
    return queue_id, token


# ---------------------------------------------------------------------------
# Tool 2: QUEUE TOOL
# ---------------------------------------------------------------------------
def queue_tool_get_waiting(department):
    conn = get_conn()
    rows = [dict(r) for r in conn.execute(
        """SELECT queue.*, patients.name, patients.phone
           FROM queue JOIN patients ON patients.id = queue.patient_id
           WHERE queue.department = ? AND queue.status = 'waiting'
           ORDER BY queue.created_at ASC""",
        (department,),
    ).fetchall()]
    conn.close()
    return rows


def queue_tool_get_all(department=None):
    conn = get_conn()
    q = """SELECT queue.*, patients.name, patients.phone
           FROM queue JOIN patients ON patients.id = queue.patient_id
           WHERE queue.status IN ('waiting', 'in_consultation')"""
    params = []
    if department:
        q += " AND queue.department = ?"
        params.append(department)
    q += " ORDER BY queue.department, queue.created_at ASC"
    rows = [dict(r) for r in conn.execute(q, params).fetchall()]
    conn.close()
    return rows


def queue_tool_get_by_token(token):
    conn = get_conn()
    row = conn.execute(
        """SELECT queue.*, patients.name, patients.phone
           FROM queue JOIN patients ON patients.id = queue.patient_id
           WHERE queue.token = ?""",
        (token,),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def queue_tool_mark_done(queue_id):
    conn = get_conn()
    row = conn.execute("SELECT department FROM queue WHERE id = ?", (queue_id,)).fetchone()
    if not row:
        conn.close()
        return None
    conn.execute("UPDATE queue SET status = 'done' WHERE id = ?", (queue_id,))
    conn.commit()
    conn.close()
    return row["department"]


def queue_tool_department_list():
    conn = get_conn()
    rows = conn.execute(
        "SELECT DISTINCT department FROM doctors "
        "UNION SELECT DISTINCT department FROM queue WHERE status='waiting'"
    ).fetchall()
    conn.close()
    return sorted({r["department"] for r in rows})


# ---------------------------------------------------------------------------
# Tool 3: DOCTOR TOOL
# ---------------------------------------------------------------------------
def doctor_tool_get_available(department):
    conn = get_conn()
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM doctors WHERE department = ? AND available = 1", (department,)
    ).fetchall()]
    conn.close()
    return rows


def doctor_tool_get_all():
    conn = get_conn()
    rows = [dict(r) for r in conn.execute("SELECT * FROM doctors ORDER BY department, name").fetchall()]
    conn.close()
    return rows


def doctor_tool_toggle(doctor_id):
    conn = get_conn()
    row = conn.execute("SELECT * FROM doctors WHERE id = ?", (doctor_id,)).fetchone()
    if not row:
        conn.close()
        return None
    new_val = 0 if row["available"] else 1
    conn.execute("UPDATE doctors SET available = ? WHERE id = ?", (new_val, doctor_id))
    conn.commit()
    conn.close()
    return row["department"]


# ---------------------------------------------------------------------------
# THE AGENT LOOP: OBSERVE -> THINK -> ACT
# ---------------------------------------------------------------------------
def get_department_metrics(department):
    doctors = doctor_tool_get_available(department)
    waiting = queue_tool_get_waiting(department)
    doctors_count = len(doctors)
    avg_consult = (
        sum(d["avg_consultation_minutes"] for d in doctors) / doctors_count
        if doctors_count else DEFAULT_AVG_CONSULT_MIN
    )
    now = time.time()
    waits = [
        (r["expected_time"] - now) / 60
        for r in waiting if r["expected_time"] is not None
    ]
    current_avg_wait = round(sum(waits) / len(waits), 1) if waits else 0.0
    people_per_doctor = round(len(waiting) / doctors_count, 1) if doctors_count else None

    return {
        "department": department,
        "waiting_count": len(waiting),
        "doctors_available": doctors_count,
        "avg_consultation_minutes": round(avg_consult, 1),
        "current_avg_wait_minutes": current_avg_wait,
        "people_per_doctor": people_per_doctor,
    }


def recalc_department(department):
    """The OBSERVE -> THINK -> ACT loop for one department. Re-run this any
    time conditions change (new registration, doctor availability flips,
    someone marked done) or periodically to simulate continuous monitoring."""
    doctors = doctor_tool_get_available(department)
    doctors_count = len(doctors)
    avg_consult = (
        sum(d["avg_consultation_minutes"] for d in doctors) / doctors_count
        if doctors_count else DEFAULT_AVG_CONSULT_MIN
    )
    waiting = queue_tool_get_waiting(department)

    _log(department, "OBSERVE",
         f"{len(waiting)} waiting, {doctors_count} doctor(s) available, "
         f"avg consultation {round(avg_consult,1)} min")

    now = time.time()
    conn = get_conn()
    changes = []

    for i, row in enumerate(waiting):
        if doctors_count == 0:
            new_expected = None
        else:
            slot = i // doctors_count
            new_expected = now + (slot + 1) * avg_consult * 60

        old_expected = row["expected_time"]
        conn.execute(
            "UPDATE queue SET previous_expected_time = ?, expected_time = ? WHERE id = ?",
            (old_expected, new_expected, row["id"]),
        )

        moved = False
        if old_expected is None and new_expected is not None:
            moved = True
            msg = f"Token {row['token']}: appointment time set to " + _fmt_time(new_expected)
        elif old_expected is not None and new_expected is None:
            moved = True
            msg = f"Token {row['token']}: delayed - no doctor currently available in {department}"
        elif old_expected is not None and new_expected is not None and abs(new_expected - old_expected) >= NOTIFY_THRESHOLD_SECONDS:
            moved = True
            direction = "later" if new_expected > old_expected else "earlier"
            msg = (f"Token {row['token']}: estimated time changed from "
                   f"{_fmt_time(old_expected)} to {_fmt_time(new_expected)} ({direction})")

        if moved:
            conn.execute(
                "INSERT INTO notifications (queue_id, message, created_at, delivered) VALUES (?, ?, ?, 0)",
                (row["id"], msg, now),
            )
            changes.append(msg)

    conn.commit()
    conn.close()

    _log(department, "THINK",
         f"recalculated schedule for {len(waiting)} waiting token(s); "
         f"{len(changes)} time change(s) detected")
    _log(department, "ACT",
         f"queue times updated" + (f"; {len(changes)} patient(s) notified" if changes else ""))

    return {"metrics": get_department_metrics(department), "changes": changes}


def _fmt_time(ts):
    import datetime
    return datetime.datetime.fromtimestamp(ts).strftime("%I:%M %p").lstrip("0")


# ---------------------------------------------------------------------------
# Tool 4: AI REASONING (advisory text for the staff dashboard)
# ---------------------------------------------------------------------------
def rule_based_advisory(metrics):
    dept = metrics["department"]
    if metrics["doctors_available"] == 0:
        return {
            "level": "bad",
            "text": (f"No doctors currently available in {dept}. New arrivals will be "
                     f"delayed until a doctor comes back on duty."),
        }

    ppd = metrics["people_per_doctor"]
    avg_wait = metrics["current_avg_wait_minutes"]

    if ppd is not None and ppd > 5:
        predicted = round(avg_wait * metrics["doctors_available"] / (metrics["doctors_available"] + 1), 1)
        return {
            "level": "warn",
            "text": (f"Queue is increasing in {dept}. Recommended action: open more online "
                     f"time slots or bring in another doctor. Current average waiting: "
                     f"{avg_wait} min. Predicted waiting after redistribution: {predicted} min."),
        }

    return {
        "level": "good",
        "text": (f"{dept} queue is steady. Current average waiting: {avg_wait} min "
                 f"across {metrics['waiting_count']} patient(s). No action needed."),
    }


def openai_advisory(metrics, api_key=None):
    from openai import OpenAI
    client = OpenAI(api_key=api_key) if api_key else OpenAI()
    prompt = (
        "You are a hospital operations assistant. Given these EXACT computed queue "
        "metrics (do not invent or change any numbers), write ONE short recommendation "
        "(2-3 sentences, plain text, no markdown) for hospital staff, in the same style "
        "as: 'Queue is increasing. Recommended action: ... Current average waiting: X "
        "min. Predicted waiting after redistribution: Y min.'\n\n"
        f"Metrics: {json.dumps(metrics)}"
    )
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=120,
    )
    text = response.choices[0].message.content.strip()
    level = "bad" if metrics["doctors_available"] == 0 else (
        "warn" if (metrics["people_per_doctor"] or 0) > 5 else "good"
    )
    return {"level": level, "text": text}


# ---------------------------------------------------------------------------
# Runtime-entered API key
# ---------------------------------------------------------------------------
# Lets a user paste their own OpenAI API key into the dashboard instead of
# (or in addition to) setting the OPENAI_API_KEY environment variable. Kept
# in memory only (never written to disk) and lost on server restart - this
# is meant for demo/dev convenience, not production secret storage.
_runtime_api_key = None


def set_runtime_api_key(key):
    global _runtime_api_key
    _runtime_api_key = (key or "").strip() or None
    return _runtime_api_key is not None


def clear_runtime_api_key():
    global _runtime_api_key
    _runtime_api_key = None


def get_active_api_key():
    """Whatever key should actually be used right now: a key entered in the
    dashboard takes priority over the environment variable."""
    return _runtime_api_key or os.environ.get("OPENAI_API_KEY")


def api_key_status():
    if _runtime_api_key:
        return {"configured": True, "source": "dashboard", "masked": _mask_key(_runtime_api_key)}
    env_key = os.environ.get("OPENAI_API_KEY")
    if env_key:
        return {"configured": True, "source": "environment", "masked": _mask_key(env_key)}
    return {"configured": False, "source": None, "masked": None}


def _mask_key(key):
    if len(key) <= 8:
        return "*" * len(key)
    return key[:4] + "…" + key[-4:]


def ai_reasoning_advisory(department):
    metrics = get_department_metrics(department)
    active_key = get_active_api_key()
    if active_key:
        try:
            return {"metrics": metrics, **openai_advisory(metrics, api_key=active_key)}
        except Exception as e:
            fallback = rule_based_advisory(metrics)
            fallback["text"] = f"[AI call failed ({e}), used fallback] " + fallback["text"]
            return {"metrics": metrics, **fallback}
    return {"metrics": metrics, **rule_based_advisory(metrics)}
