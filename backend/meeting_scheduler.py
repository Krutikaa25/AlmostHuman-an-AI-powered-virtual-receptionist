import os
import re
import json
import asyncio
from datetime import datetime, date
from groq import Groq
from dotenv import load_dotenv

from database import (
    get_employee_by_name, get_available_slots,
    schedule_meeting, get_hr, get_department_manager
)

# Import the notification functions we created
from notify_teams import notify_teams_now
from notify_email import send_calendar_invite

load_dotenv()
_groq_client = Groq(api_key=os.getenv("GROQ_API_KEY"))


# ─────────────────────────────────────────────
# GROQ MEETING EXTRACTOR
# ─────────────────────────────────────────────

async def extract_meeting_info(text: str) -> dict:
    """Use Groq to extract structured meeting parameters from natural speech."""
    today_date = date.today().strftime("%Y-%m-%d")
    today_day = date.today().strftime("%A")

    prompt = f"""
    Extract meeting scheduling details from the text.
    Today's date is {today_date} ({today_day}).

    Return ONLY valid JSON. No markdown or explanations.
    Fields:
    - "employee_name": name or title of the person they want to meet (string or null).
    - "date": Date in YYYY-MM-DD format (string or null). Resolve words like "today", "tomorrow", "next monday".
    - "time": Time in HH:MM format using 24-hour clock (string or null). e.g., "4 pm" -> "16:00", "half past two" -> "14:30".
    - "purpose": The reason for the meeting (string or null).
    
    Text: "{text}"
    """

    try:
        response = _groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=150,
            temperature=0
        )
        raw = response.choices[0].message.content.strip()
        raw = raw.replace("```json", "").replace("```", "").strip()
        parsed = json.loads(raw)

        # Convert string "null" to actual Python None
        for k, v in parsed.items():
            if isinstance(v, str) and v.lower() in ["null", "none", ""]:
                parsed[k] = None
                
        return parsed
    except Exception as e:
        print(f"Warning: extract_meeting_info failed: {e}")
        return {}


# ─────────────────────────────────────────────
# FORMATTING HELPERS
# ─────────────────────────────────────────────

def format_slots(slots: list) -> str:
    """Format available slots nicely for speech."""
    if not slots:
        return "no available slots"
    formatted = []
    for s in slots:
        h, m = map(int, s.split(":"))
        meridiem = "AM" if h < 12 else "PM"
        h12 = h if h <= 12 else h - 12
        if h12 == 0:
            h12 = 12
        formatted.append(f"{h12}{':{:02d}'.format(m) if m else ''} {meridiem}")
    return ", ".join(formatted)


def format_date_for_speech(date_str: str) -> str:
    """Convert YYYY-MM-DD to human readable like 'Monday, March 25'."""
    try:
        d = datetime.strptime(date_str, "%Y-%m-%d")
        return d.strftime("%A, %B %d").replace(" 0", " ")
    except:
        return date_str


# ─────────────────────────────────────────────
# MAIN SCHEDULING FLOW (SLOT FILLING)
# ─────────────────────────────────────────────

async def handle_meeting_request(
    session: dict,
    text: str,
    speak_and_emit,
    sid: str
) -> bool:
    """
    Handle meeting scheduling using an intelligent Slot-Filling approach.
    Returns True if handled, False if not a meeting request.
    """
    text_lower = text.lower()
    meeting_state = session.get("meeting_state", "IDLE")

    # 1. Handle Cancellations & Exits early
    if meeting_state != "IDLE":
        exit_phrases = ["cancel", "stop", "nevermind", "abort", "exit", "thank you", "thanks"]
        if any(phrase in text_lower for phrase in exit_phrases):
            session["meeting_state"] = "IDLE"
            for k in ["meeting_employee", "meeting_date", "meeting_time", "meeting_purpose"]:
                session.pop(k, None)
            
            if "thank" in text_lower:
                await speak_and_emit(sid, "You're welcome! Let me know if you need anything else.")
            else:
                await speak_and_emit(sid, "Okay, I've cancelled the meeting request. How else can I help you?")
            return True

    # 2. Trigger check for initial requests
    MEETING_TRIGGERS = [
        "schedule", "book", "arrange", "set up", "fix a meeting",
        "meeting with", "appointment with", "want to meet"
    ]
    if meeting_state == "IDLE":
        if not any(t in text_lower for t in MEETING_TRIGGERS):
            return False
        session["meeting_state"] = "IN_PROGRESS"

    # 3. Handle strict states (Confirmation & Purpose)
    if meeting_state == "CONFIRM":
        if any(w in text_lower for w in ["yes", "yeah", "confirm", "sure", "okay", "ok", "yep", "please"]):
            emp = session["meeting_employee"]
            organizer = session.get("name", "Visitor")
            organizer_type = "employee" if session.get("identity") == "EMPLOYEE" else "visitor"
            m_date = session["meeting_date"]
            m_time = session["meeting_time"]
            m_purpose = session.get("meeting_purpose", "")

            # A. Save to Database
            schedule_meeting(
                organizer_name=organizer,
                organizer_type=organizer_type,
                employee_name=emp["name"],
                meeting_date=m_date,
                meeting_time=m_time,
                purpose=m_purpose
            )

            # B. Resolve organizer email — only available if they're an employee
            organizer_email = None
            if organizer_type == "employee":
                org_record = get_employee_by_name(organizer)
                if org_record:
                    organizer_email = dict(org_record).get("email")

            # C. SMART HYBRID NOTIFICATIONS
            today_str = date.today().strftime("%Y-%m-%d")

            # Always send the Calendar Invite Email (Runs async in background)
            if emp.get("email"):
                asyncio.create_task(asyncio.to_thread(
                    send_calendar_invite,
                    emp["name"], emp["email"], organizer, m_date, m_time, m_purpose, organizer_email
                ))

            # IF the meeting is TODAY, also send a Teams Alert (Runs async in background)
            if m_date == today_str:
                asyncio.create_task(asyncio.to_thread(
                    notify_teams_now,
                    emp["name"], organizer, m_time, m_purpose
                ))

            # C. Clean up and respond
            friendly_date = format_date_for_speech(m_date)
            friendly_time = format_slots([m_time])

            session["meeting_state"] = "IDLE"
            for k in ["meeting_employee", "meeting_date", "meeting_time", "meeting_purpose"]:
                session.pop(k, None)

            await speak_and_emit(sid, f"Done! Your meeting with {emp['name']} on {friendly_date} at {friendly_time} is confirmed. I have sent them the details.")
            return True

        elif any(w in text_lower for w in ["no", "nope", "don't"]):
            session["meeting_state"] = "IDLE"
            for k in ["meeting_employee", "meeting_date", "meeting_time", "meeting_purpose"]:
                session.pop(k, None)
            await speak_and_emit(sid, "No problem, the meeting request has been cancelled.")
            return True

        else:
            await speak_and_emit(sid, "Sorry, should I confirm the meeting? Please say yes or no.")
            return True

    if meeting_state == "GET_PURPOSE":
        session["meeting_purpose"] = text  # Accept verbatim
        session["meeting_state"] = "CONFIRM"
        emp = session["meeting_employee"]
        friendly_date = format_date_for_speech(session["meeting_date"])
        friendly_time = format_slots([session["meeting_time"]])
        await speak_and_emit(sid, f"Just to confirm — meeting with {emp['name']} on {friendly_date} at {friendly_time} for {text}. Shall I confirm this?")
        return True


    # 4. Extract data using Groq for dynamic slot filling
    extracted = await extract_meeting_info(text)
    
    # Save extracted details to session if we don't already have them
    if extracted.get("employee_name") and not session.get("meeting_employee"):
        emp_text = extracted["employee_name"]
        emp = get_employee_by_name(emp_text) or get_department_manager(emp_text)
        if not emp and "hr" in emp_text.lower(): 
            emp = get_hr()
            
        if emp:
            session["meeting_employee"] = dict(emp)
        else:
            await speak_and_emit(sid, f"I couldn't find anyone named {emp_text}. Could you check the name and try again?")
            return True

    if extracted.get("date") and not session.get("meeting_date"):
        session["meeting_date"] = extracted["date"]

    if extracted.get("time") and not session.get("meeting_time"):
        session["meeting_time"] = extracted["time"]

    if extracted.get("purpose") and not session.get("meeting_purpose"):
        session["meeting_purpose"] = extracted["purpose"]


    # 5. Cascading Evaluation (Fill missing slots one by one)
    
    # Check Employee
    if not session.get("meeting_employee"):
        await speak_and_emit(sid, "I'd be happy to schedule a meeting! Who would you like to meet with?")
        return True
        
    emp = session["meeting_employee"]

    # Check Date
    if not session.get("meeting_date"):
        await speak_and_emit(sid, f"What date would you like to meet {emp['name']}?")
        return True
        
    date_val = session["meeting_date"]
    friendly_date = format_date_for_speech(date_val)
    slots = get_available_slots(emp["name"], date_val)
    
    if not slots:
        session.pop("meeting_date", None)
        session.pop("meeting_time", None)  # Invalid date invalidates time
        await speak_and_emit(sid, f"{emp['name']} is fully booked on {friendly_date}. Please try another date.")
        return True

    # Check Time
    if not session.get("meeting_time"):
        slots_text = format_slots(slots)
        await speak_and_emit(sid, f"{emp['name']} is available on {friendly_date} at: {slots_text}. Which time works for you?")
        return True
        
    time_val = session["meeting_time"]
    friendly_time = format_slots([time_val])
    
    if time_val not in slots:
        session.pop("meeting_time", None)
        slots_text = format_slots(slots)
        await speak_and_emit(sid, f"Sorry, {emp['name']} isn't available at {friendly_time}. Available slots are: {slots_text}.")
        return True

    # Check Purpose
    if not session.get("meeting_purpose"):
        if session.get("purpose"):  # We already know the purpose from the initial check-in!
            session["meeting_purpose"] = session["purpose"]
        else:
            session["meeting_state"] = "GET_PURPOSE"
            await speak_and_emit(sid, f"Great! What is the purpose of the meeting with {emp['name']} at {friendly_time}?")
            return True

    # Everything is filled! Transition to confirm.
    session["meeting_state"] = "CONFIRM"
    await speak_and_emit(sid, f"Just to confirm — meeting with {emp['name']} on {friendly_date} at {friendly_time} for {session['meeting_purpose']}. Shall I confirm this?")
    return True