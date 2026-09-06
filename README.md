# Smart Hospital Queue Optimization — Token & Predicted Arrival Time

The core idea: **don't make patients wait in the queue — make the queue
wait for the patient.** A patient registers online, gets a token and a
predicted arrival time, and can stay home until close to that time. An AI
agent keeps that prediction honest as real conditions change.

```
OBSERVE  →  how many waiting, how many doctors on duty, avg consultation time
THINK    →  recompute every waiting token's predicted time
ACT      →  write new times back, notify anyone whose time moved
   ↑___________________________________________________________|
   (re-run whenever something changes = OBSERVE AGAIN)
```

This loop re-runs automatically whenever a new patient registers, a doctor
goes on/off duty, or someone is marked done — and can also be triggered
manually ("Recalculate now" on the dashboard) to simulate the periodic
monitoring a real deployment would do on a timer.

## What's in here

| File | Role |
|---|---|
| `agent.py` | The 4 tools + the OBSERVE→THINK→ACT scheduling loop. This is the agent. |
| `app.py` | Flask routes: patient registration/tracking API, staff dashboard API |
| `database.py` | SQLite schema + connection helper |
| `seed_data.py` | Creates sample doctors on first run |
| `templates/register.html` | Patient registration + boarding-pass style token + token tracker |
| `templates/dashboard.html` | Staff "departures board" live dashboard |
| `static/style.css`, `static/dashboard.js` | Front end |

**Database:** SQLite (a single `hospital.db` file), not Postgres — zero
setup on your laptop. `database.py`'s SQL is plain enough to swap for
`psycopg2` + Postgres later (change `AUTOINCREMENT` → `SERIAL` and the
connection function).

## How to run it

```bash
cd hospital-token-agent
pip install -r requirements.txt
python app.py
```

Then open:
- **http://127.0.0.1:5000/** — patient registration (get a token)
- **http://127.0.0.1:5000/dashboard** — staff departures board

The database and sample doctors are created automatically on first run.
To start fresh, delete `hospital.db` and restart.

## The 4 tools

| Tool | Function | Purpose |
|---|---|---|
| Patient Tool | `patient_tool_register()` | Register a patient, issue a token |
| Queue Tool | `queue_tool_get_waiting()`, `queue_tool_mark_done()` | Read/update the live queue |
| Doctor Tool | `doctor_tool_get_available()`, `doctor_tool_toggle()` | Check/change doctor availability |
| AI Reasoning | `ai_reasoning_advisory()` | Turns queue metrics into a staff recommendation |

**Important design choice:** the actual predicted times are computed with
plain, deterministic queueing math (position ÷ doctors available × average
consultation time) — never guessed by an LLM. The AI Reasoning tool only
*writes the recommendation sentence* for staff, from numbers it's handed;
it can't invent or change a wait-time figure. This is what "no medical
priority guessing" from the brief actually looks like in code.

## Two brains for the advisory text

- **No `OPENAI_API_KEY` set (default):** a rule-based template generates
  the staff recommendation ("Queue is increasing. Recommended action:
  ... Current average waiting: X min. Predicted waiting after
  redistribution: Y min."). Runs with zero cost or setup.
- **`OPENAI_API_KEY` set:** the same exact metrics are handed to GPT-4o-mini,
  which is instructed not to invent numbers, only to phrase the
  recommendation. Enable with:
  ```bash
  export OPENAI_API_KEY=sk-...
  python app.py
  ```

## Notification service

Real SMS/email would need a provider like Twilio or SendGrid — out of
scope for a student build. Instead, every time a token's predicted time
changes meaningfully, a row is written to the `notifications` table. The
staff dashboard shows them live under "Notifications sent to patients",
and a patient can see their own via the "Check its status" box on the
registration page (enter their token code) — this is the same mechanism
a real SMS webhook would read from.

## Demo script

1. Open the dashboard and registration page side by side.
2. Register 3 patients into **Cardiology** (2 doctors, ~15 min consult
   each). Watch the departures board fill in with tokens C001–C003 and
   predicted times.
3. On the dashboard sidebar, click **Dr. Kumar** to take him off duty.
   Watch the board recalculate immediately — remaining tokens move later,
   and the **Notifications** panel logs exactly what changed for whom.
4. Open the **Agent reasoning log** panel to see the OBSERVE / THINK / ACT
   entries behind that recalculation.
5. Go back to the registration page, paste one of the tokens into
   "Check its status" — see the same predicted time and any new
   notifications for that patient specifically.
6. Click **Mark done** on a token, then **Recalculate now** — see the
   queue behind them shift forward.

## Extending it

- Add `APScheduler` to call `agent.recalc_department()` for every
  department automatically every few minutes, instead of only reacting
  to registrations/toggles/manual clicks.
- Wire the notification rows to a real SMS/WhatsApp API (Twilio, etc.)
  instead of just logging them to the dashboard.
- Swap SQLite for Postgres per the note in `database.py`.
