"""
app.py
------
Flask backend for the Smart Hospital Queue Optimization system.

Patient-facing:
    GET  /                     -> registration page
    POST /api/register         -> register, get a token + predicted time
    GET  /api/token/<token>    -> look up a token's current status/time

Staff-facing:
    GET  /dashboard             -> departures-board style live dashboard
    GET  /api/queue             -> current queue (all or one department)
    GET  /api/doctors           -> doctor list + availability
    POST /api/doctors/<id>/toggle -> flip a doctor on/off duty (triggers recalculation)
    POST /api/mark-done         -> mark a token as done (frees their slot)
    POST /api/recalculate       -> manually re-run the agent loop (simulates periodic monitoring)
    GET  /api/advisory          -> AI recommendation text for a department
    GET  /api/notifications     -> recent notifications (the "notification service")
    GET  /api/log               -> agent OBSERVE/THINK/ACT log
"""

import os
import time

from flask import Flask, request, jsonify, render_template

from database import init_db
import agent

app = Flask(__name__)

if not os.path.exists(os.path.join(os.path.dirname(__file__), "hospital.db")):
    import seed_data
    seed_data.main()
else:
    init_db(reset=False)


@app.route("/")
def register_page():
    return render_template("register.html")


@app.route("/dashboard")
def dashboard_page():
    return render_template("dashboard.html")


# ---------------------------------------------------------------------------
# Patient-facing API
# ---------------------------------------------------------------------------
@app.route("/api/register", methods=["POST"])
def api_register():
    data = request.get_json(force=True)
    name = (data.get("name") or "").strip()
    phone = (data.get("phone") or "").strip()
    department = (data.get("department") or "").strip()

    if not all([name, phone, department]):
        return jsonify({"error": "name, phone and department are all required"}), 400

    queue_id, token = agent.patient_tool_register(name, phone, department)
    result = agent.recalc_department(department)

    row = next((r for r in agent.queue_tool_get_waiting(department) if r["id"] == queue_id), None)
    expected_time = row["expected_time"] if row else None
    position = None
    if row:
        waiting = agent.queue_tool_get_waiting(department)
        position = next((i + 1 for i, r in enumerate(waiting) if r["id"] == queue_id), None)

    return jsonify({
        "token": token,
        "department": department,
        "expected_time": expected_time,
        "expected_time_display": agent._fmt_time(expected_time) if expected_time else "Delayed - no doctor available",
        "position": position,
        "waiting_count": result["metrics"]["waiting_count"],
    })


@app.route("/api/token/<token>")
def api_token_lookup(token):
    row = agent.queue_tool_get_by_token(token)
    if not row:
        return jsonify({"error": "Token not found"}), 404

    position = None
    if row["status"] == "waiting":
        waiting = agent.queue_tool_get_waiting(row["department"])
        position = next((i + 1 for i, r in enumerate(waiting) if r["id"] == row["id"]), None)

    from database import get_conn
    conn = get_conn()
    unread = [dict(r) for r in conn.execute(
        "SELECT * FROM notifications WHERE queue_id = ? AND delivered = 0 ORDER BY created_at ASC",
        (row["id"],),
    ).fetchall()]
    if unread:
        conn.execute("UPDATE notifications SET delivered = 1 WHERE queue_id = ?", (row["id"],))
        conn.commit()
    conn.close()

    return jsonify({
        "token": row["token"],
        "department": row["department"],
        "status": row["status"],
        "position": position,
        "expected_time_display": agent._fmt_time(row["expected_time"]) if row["expected_time"] else "Delayed - no doctor available",
        "new_notifications": [n["message"] for n in unread],
    })


# ---------------------------------------------------------------------------
# Staff-facing API
# ---------------------------------------------------------------------------
@app.route("/api/queue")
def api_queue():
    department = request.args.get("department") or None
    rows = agent.queue_tool_get_all(department)
    now = time.time()
    for r in rows:
        r["expected_time_display"] = agent._fmt_time(r["expected_time"]) if r["expected_time"] else "Delayed"
        r["minutes_until"] = round((r["expected_time"] - now) / 60, 1) if r["expected_time"] else None
    return jsonify({"queue": rows, "departments": agent.queue_tool_department_list()})


@app.route("/api/doctors")
def api_doctors():
    return jsonify(agent.doctor_tool_get_all())


@app.route("/api/doctors/<int:doctor_id>/toggle", methods=["POST"])
def api_doctor_toggle(doctor_id):
    department = agent.doctor_tool_toggle(doctor_id)
    if not department:
        return jsonify({"error": "doctor not found"}), 404
    result = agent.recalc_department(department)
    return jsonify({"department": department, "changes": result["changes"]})


@app.route("/api/mark-done", methods=["POST"])
def api_mark_done():
    data = request.get_json(force=True)
    queue_id = data.get("queue_id")
    department = agent.queue_tool_mark_done(queue_id)
    if not department:
        return jsonify({"error": "queue entry not found"}), 404
    result = agent.recalc_department(department)
    return jsonify({"department": department, "changes": result["changes"]})


@app.route("/api/recalculate", methods=["POST"])
def api_recalculate():
    data = request.get_json(silent=True) or {}
    department = data.get("department")
    depts = [department] if department else agent.queue_tool_department_list()
    summary = {}
    for d in depts:
        result = agent.recalc_department(d)
        summary[d] = len(result["changes"])
    return jsonify({"recalculated": summary})


@app.route("/api/advisory")
def api_advisory():
    department = request.args.get("department")
    if not department:
        return jsonify({"error": "department query param required"}), 400
    return jsonify(agent.ai_reasoning_advisory(department))


@app.route("/api/settings", methods=["GET"])
def api_settings_get():
    return jsonify(agent.api_key_status())


@app.route("/api/settings", methods=["POST"])
def api_settings_set():
    data = request.get_json(force=True)
    api_key = (data.get("api_key") or "").strip()
    if not api_key:
        return jsonify({"error": "api_key is required"}), 400
    agent.set_runtime_api_key(api_key)
    return jsonify(agent.api_key_status())


@app.route("/api/settings", methods=["DELETE"])
def api_settings_clear():
    agent.clear_runtime_api_key()
    return jsonify(agent.api_key_status())


@app.route("/api/notifications")
def api_notifications():
    from database import get_conn
    conn = get_conn()
    rows = [dict(r) for r in conn.execute(
        """SELECT notifications.*, queue.token, queue.department
           FROM notifications JOIN queue ON queue.id = notifications.queue_id
           ORDER BY notifications.created_at DESC LIMIT 30"""
    ).fetchall()]
    conn.close()
    return jsonify(rows)


@app.route("/api/log")
def api_log():
    from database import get_conn
    conn = get_conn()
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM agent_log ORDER BY created_at DESC LIMIT 40"
    ).fetchall()]
    conn.close()
    return jsonify(rows)


if __name__ == "__main__":
    app.run(debug=True, port=5000, use_reloader=False)
