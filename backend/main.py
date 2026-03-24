import socketio
import asyncio
import time
import re

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from almosthuman_brain import process_user_text
from listen_and_transcribe_whisper import process_audio
from brain_state import get_state, set_state, BrainState
from speak import speak
from database import (
    init_db, set_setting, get_setting,
    add_visitor, get_employee_by_name, get_visitor_by_name, get_hr, get_department_manager
)

# ─────────────────────────────────────────────
# SESSION MANAGEMENT
# ─────────────────────────────────────────────

sessions = {}

def get_session(sid):
    if sid not in sessions:
        sessions[sid] = {
            "state": "GET_NAME",
            "name": None,
            "meeting_with": None,
            "purpose": None
        }
    return sessions[sid]


# ─────────────────────────────────────────────
# EXTRACTION HELPERS
# ─────────────────────────────────────────────

def extract_name(text):
    text = text.lower()
    patterns = [
        r"my name is ([a-zA-Z]+)",
        r"i am ([a-zA-Z]+)",
        r"i'm ([a-zA-Z]+)"
    ]
    for p in patterns:
        match = re.search(p, text)
        if match:
            return match.group(1).capitalize()
    words = text.split()
    if len(words) == 1:
        return words[0].capitalize()
    return None


def extract_employee(text):
    FILLERS = {"the", "a", "an", "my", "our", "your", "their", "to", "for"}
    # Check for department keywords first (hr, engineering, devops, etc.)
    DEPARTMENTS = {"hr", "engineering", "devops", "product", "ai", "machine learning"}
    text_lower = text.lower()

    # Direct department mention like "meet the hr" or "meet hr"
    for dept in DEPARTMENTS:
        if dept in text_lower and any(w in text_lower for w in ["meet", "with", "appointment", "interview"]):
            return dept.upper() if len(dept) <= 3 else dept.title()

    match = re.search(
        r"(meet|with|appointment with|interview with)\s+((?:[a-zA-Z]+\s?){1,3})",
        text_lower
    )
    if match:
        words = match.group(2).strip().split()
        meaningful = [w for w in words if w not in FILLERS]
        if meaningful:
            return " ".join(meaningful).title()
    return None


def detect_intent(text):
    text = text.lower()
    if "interview" in text:
        return "INTERVIEW"
    if "meeting" in text or "meet" in text:
        return "MEETING"
    if "delivery" in text:
        return "DELIVERY"
    if "job" in text:
        return "JOB"
    return "UNKNOWN"


# ─────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────

async def speak_and_emit(sid, response: str, emotion: str = "neutral"):
    """Speak a response and emit it to the client."""
    set_state(BrainState.SPEAKING)
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, speak, response)
    set_state(BrainState.IDLE)

    await sio.emit(
        "ai_response",
        {
            "text": response,
            "emotion": emotion,
            "state": BrainState.IDLE.value,
            "response_time": 0,
            "audio_url": f"http://localhost:8000/static/output.wav?t={int(time.time())}"
        },
        to=sid
    )


# ─────────────────────────────────────────────
# SOCKET + FASTAPI
# ─────────────────────────────────────────────

sio = socketio.AsyncServer(async_mode="asgi", cors_allowed_origins="*")
app = FastAPI()
socket_app = socketio.ASGIApp(sio, app)

welcomed_sessions = set()

# ─────────────────────────────────────────────
# DATABASE INIT
# ─────────────────────────────────────────────

init_db()

set_setting("company_name", "Sharp Software Development India Pvt Ltd")
set_setting("company_location", "Bangalore, India")
set_setting("office_hours", "9 AM to 6 PM, Monday to Friday")
set_setting("departments", "HR, AI & Machine Learning, DevOps, Product Development")

# ─────────────────────────────────────────────
# STATIC + CORS
# ─────────────────────────────────────────────

app.mount("/static", StaticFiles(directory="static"), name="static")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─────────────────────────────────────────────
# SOCKET EVENTS
# ─────────────────────────────────────────────

@sio.event
async def connect(sid, environ):
    print("🤖 AlmostHuman Connected:", sid)


@sio.event
async def audio_chunk(sid, data):
    session = get_session(sid)

    # ── Welcome message (first time only) ──
    if sid not in welcomed_sessions:
        welcomed_sessions.add(sid)
        company_name = get_setting("company_name")
        welcome_message = (
            f"Hello. Welcome to {company_name}. "
            "I am AlmostHuman, the virtual receptionist. "
            "May I have your name please?"
        )
        print("🤖 AI:", welcome_message)
        await speak_and_emit(sid, welcome_message)
        return

    # ── Block overlapping states ──
    if get_state() in [BrainState.THINKING, BrainState.SPEAKING]:
        return

    set_state(BrainState.THINKING)

    text = await process_audio(data)

    if not text:
        set_state(BrainState.IDLE)
        return

    print("🎤 USER:", text)

    # ── STEP 1: GET NAME ──
    if session["state"] == "GET_NAME":
        name = extract_name(text)

        if not name:
            response = "Sorry, I didn't catch your name. Could you please repeat it?"
            set_state(BrainState.IDLE)
            await speak_and_emit(sid, response)
            return

        session["name"] = name
        visitor = get_visitor_by_name(name)

        # FIX: use if/else so welcome-back message is not overwritten
        if visitor:
            response = f"Welcome back, {name}. Who are you here to meet today?"
        else:
            response = f"Nice to meet you, {name}. Who are you here to meet?"

        session["state"] = "GET_HOST"
        set_state(BrainState.IDLE)
        await speak_and_emit(sid, response)
        return

    # ── STEP 2: GET HOST ──
    if session["state"] == "GET_HOST":
        # If visitor says something like "i am here for an interview" without naming anyone,
        # ask again instead of saving the whole sentence as employee name
        PURPOSE_KEYWORDS = ["interview", "delivery", "job", "meeting", "appointment", "visit", "here for", "came for"]
        looks_like_purpose = any(kw in text.lower() for kw in PURPOSE_KEYWORDS) and extract_employee(text) is None

        if looks_like_purpose:
            response = "I see! But could you tell me who you are here to meet?"
            set_state(BrainState.IDLE)
            await speak_and_emit(sid, response)
            return

        employee_name = extract_employee(text)
        if not employee_name:
            employee_name = text.strip().capitalize()

        session["meeting_with"] = employee_name
        session["state"] = "GET_PURPOSE"

        response = "What is the purpose of your visit?"
        set_state(BrainState.IDLE)
        await speak_and_emit(sid, response)
        return

    # ── STEP 3: GET PURPOSE ──
    if session["state"] == "GET_PURPOSE":
        session["purpose"] = text
        session["state"] = "DONE"

        name = session["name"]
        meeting = session["meeting_with"]

        add_visitor(name, meeting, text)

        employee = get_employee_by_name(meeting)

        if not employee and meeting.lower() in ["hr", "h.r", "human resources"]:
            employee = get_hr()

        if not employee:
            employee = get_department_manager(meeting)

        if employee:
            response = (
                f"Thank you, {name}. "
                f"You are registered to meet {employee['name']} "
                f"from the {employee['department'] if employee['department'] else meeting} department, "
                f"located on {employee['floor']}. "
                f"Their extension is {employee['extension']}. "
                "Please take a seat and I will notify them."
            )
        else:
            response = (
                f"Thank you, {name}. "
                f"You are registered to meet {meeting}. "
                "Please take a seat and someone will be with you shortly."
            )

        set_state(BrainState.IDLE)
        await speak_and_emit(sid, response)

        asyncio.create_task(idle_prompt(sid))
        return

    # ── Employee lookup by name ──
    employee = get_employee_by_name(text.strip().capitalize())
    if employee:
        response = (
            f"{employee['name']} from {employee['department']} "
            f"is on {employee['floor']}, extension {employee['extension']}."
        )
        set_state(BrainState.IDLE)
        await speak_and_emit(sid, response)
        return

    # ── HR lookup by keyword ──
    HR_KEYWORDS = ["hr", "human resource", "human resources"]
    if any(kw in text.lower() for kw in HR_KEYWORDS):
        hr = get_hr()
        if hr:
            response = (
                f"Our HR Manager is {hr['name']}, "
                f"located on {hr['floor']}, extension {hr['extension']}."
            )
            set_state(BrainState.IDLE)
            await speak_and_emit(sid, response)
            return

    # ── Department lookup ──
    dept = get_department_manager(text.strip())
    if dept:
        response = (
            f"The manager for that department is {dept['name']}, "
            f"on {dept['floor']}."
        )
        set_state(BrainState.IDLE)
        await speak_and_emit(sid, response)
        return

    # ── Free LLM conversation ──
    set_state(BrainState.IDLE)
    response = await process_user_text(text)
    await sio.emit("ai_response", response, to=sid)


# ─────────────────────────────────────────────
# IDLE PROMPT
# ─────────────────────────────────────────────

async def idle_prompt(sid):
    await asyncio.sleep(10)
    text = "If you need directions or any help, feel free to ask."
    await speak_and_emit(sid, text)


# ─────────────────────────────────────────────
# DISCONNECT
# ─────────────────────────────────────────────

@sio.event
async def disconnect(sid):
    print("Client disconnected:", sid)
    welcomed_sessions.discard(sid)
    if sid in sessions:
        del sessions[sid]
    from think_with_groq import conversation_history
    conversation_history.clear()