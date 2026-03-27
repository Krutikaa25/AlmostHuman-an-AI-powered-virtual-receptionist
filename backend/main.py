import socketio
import asyncio
import time
import json

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from groq import Groq
from dotenv import load_dotenv
import os

from almosthuman_brain import process_user_text
from meeting_scheduler import handle_meeting_request
from listen_and_transcribe_whisper import process_audio, flush_buffer
from brain_state import get_state, set_state, BrainState
from speak import speak
from database import (
    init_db, set_setting, get_setting,
    add_visitor, get_visitor_by_name,
    get_employee_by_name, get_employee_by_name_and_department,
    get_similar_employee, get_hr, get_department_manager,
    log_reception_entry, log_reception_checkout
)

load_dotenv()

# ─────────────────────────────────────────────
# GROQ CLIENT
# ─────────────────────────────────────────────

_groq_client = Groq(api_key=os.getenv("GROQ_API_KEY"))

# ─────────────────────────────────────────────
# EXTRACTION PROMPT
# ─────────────────────────────────────────────

EXTRACT_PROMPT = """You are processing speech captured at a corporate office reception desk in India.
Extract information from the message. Return ONLY valid JSON — no explanation, no markdown.

Fields:
- "name": the speaker's own name (string or null). Only their first name or full name.
- "intent": one of "VISITOR", "DELIVERY", "EMPLOYEE", "JOB_SEEKER", "UNKNOWN"
- "meeting_with": name or department they want to meet (string or null)
- "purpose": reason for visit e.g. "interview", "sales demo", "personal visit" (string or null)
- "department_hint": if they mention any team/department they belong to, extract it (string or null)

Intent rules:
- DELIVERY if they mention courier, parcel, package, shipment, dropping off, FedEx, Amazon, Swiggy, Zomato, BlueDart, Delhivery, or any delivery service
- JOB_SEEKER if they mention job, vacancy, apply, resume, career, opening, hiring
- EMPLOYEE only if they explicitly say they work here, say "I work here", "I'm from [dept]", "I'm an employee", or "I'm staff"
- VISITOR for any regular guest, client, interviewee, or visitor
- UNKNOWN only if truly none of the above

Rules:
- If a field cannot be determined, set it to null — never guess
- name is the SPEAKER's own name, not the person they're visiting
- Handle Hinglish naturally (e.g. "mera naam Aryan hai" -> name: "Aryan")
- department_hint: e.g. "I'm from DevOps" -> "DevOps", "I work in AI team" -> "AI"

Message: "{text}"
"""


async def extract_visitor_info(text: str) -> dict:
    """Call Groq to extract structured visitor info from raw speech."""
    try:
        response = _groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{
                "role": "user",
                "content": EXTRACT_PROMPT.format(text=text)
            }],
            max_tokens=150,
            temperature=0
        )
        raw = response.choices[0].message.content.strip()
        raw = raw.replace("```json", "").replace("```", "").strip()
        
        parsed = json.loads(raw)
        
        # FIX ERROR 1: Convert string "null" (and "None") to actual Python None
        for key, value in parsed.items():
            if isinstance(value, str) and value.strip().lower() in ["null", "none", ""]:
                parsed[key] = None
                
        return parsed
    except (json.JSONDecodeError, Exception) as e:
        print(f"Warning: extract_visitor_info failed: {e}")
        return {
            "name": None, "intent": "UNKNOWN",
            "meeting_with": None, "purpose": None,
            "department_hint": None
        }


# ─────────────────────────────────────────────
# SESSION MANAGEMENT
# ─────────────────────────────────────────────

sessions = {}


def get_session(sid: str) -> dict:
    if sid not in sessions:
        sessions[sid] = {
            "name": None,
            "intent": None,
            "meeting_with": None,
            "purpose": None,
            "identity": "UNKNOWN",
            "pending_employee": None,
            "checkin_done": False,
            "log_id": None,
            "meeting_state": "IDLE",
        }
    return sessions[sid]


def merge_extracted(session: dict, extracted: dict):
    for field in ["name", "intent", "meeting_with", "purpose"]:
        value = extracted.get(field)
        if value and session[field] is None:
            session[field] = value


# ─────────────────────────────────────────────
# EMPLOYEE TARGET RESOLUTION
# ─────────────────────────────────────────────

async def resolve_employee_target(meeting_with: str) -> dict | None:
    employee = get_employee_by_name(meeting_with)
    if employee:
        return dict(employee)
    if meeting_with.lower() in ["hr", "h.r", "human resources", "human resource"]:
        hr = get_hr()
        if hr:
            return dict(hr)
    dept = get_department_manager(meeting_with)
    if dept:
        return dict(dept)
    return None


# ─────────────────────────────────────────────
# CHECK-IN HANDLER
# ─────────────────────────────────────────────

async def handle_checkin(sid: str, session: dict):
    name         = session["name"]
    intent       = session["intent"]
    meeting_with = session["meeting_with"]
    purpose      = session["purpose"]
    identity     = session["identity"]

    if intent == "DELIVERY":
        if not meeting_with:
            await speak_and_emit(sid, "Got it! Who is the delivery for?")
            return
        employee = await resolve_employee_target(meeting_with)
        if employee:
            log_reception_entry(
                person_name=name or "Delivery",
                person_type="DELIVERY",
                notes=f"Delivery for {employee['name']}",
                linked_employee_id=employee.get("id")
            )
            await speak_and_emit(
                sid,
                f"Thanks! The delivery for {employee['name']} can be taken to "
                f"{employee['floor']}. I'll let them know."
            )
        else:
            log_reception_entry(
                person_name=name or "Delivery",
                person_type="DELIVERY",
                notes=f"Delivery for {meeting_with} — not found in system"
            )
            await speak_and_emit(
                sid,
                f"Thanks! Please leave the delivery at reception and I'll notify {meeting_with}."
            )
        session["checkin_done"] = True
        return

    if intent == "JOB_SEEKER":
        hr = get_hr()
        if hr:
            log_reception_entry(
                person_name=name or "Job Seeker",
                person_type="JOB_SEEKER",
                notes="Enquired about job vacancies"
            )
            await speak_and_emit(
                sid,
                f"For job opportunities, please reach out to our HR team. "
                f"{hr['name']} is on {hr['floor']}, extension {hr['extension']}."
            )
        else:
            await speak_and_emit(sid, "Please contact our HR department for vacancy information.")
        session["checkin_done"] = True
        return

    if identity == "CONFIRMING":
        return

    if identity == "EMPLOYEE":
        return

    if not name:
        await speak_and_emit(sid, "Welcome! Could I get your name please?")
        return

    if identity == "UNKNOWN":
        similar = get_similar_employee(name)
        if similar:
            session["pending_employee"] = similar
            session["identity"] = "CONFIRMING"
            await speak_and_emit(
                sid,
                f"Hi {name}! Are you a visitor, or do you work here?"
            )
            return
        else:
            session["identity"] = "VISITOR"

    if not meeting_with:
        visitor = get_visitor_by_name(name)
        if visitor:
            await speak_and_emit(sid, f"Great to see you again, {name}! Who are you here to meet today?")
        else:
            await speak_and_emit(sid, f"Nice to meet you, {name}! Who are you here to see today?")
        return

    if not purpose:
        await speak_and_emit(sid, "And what's the purpose of your visit?")
        return

    badge_id, visitor_id = add_visitor(name, meeting_with, purpose)
    employee = await resolve_employee_target(meeting_with)

    log_id = log_reception_entry(
        person_name=name,
        person_type="VISITOR",
        notes=f"Meeting: {meeting_with} | Purpose: {purpose} | Badge: {badge_id}",
        linked_visitor_id=visitor_id
    )
    session["log_id"] = log_id
    session["checkin_done"] = True

    if employee:
        dept_name = employee.get("department") or meeting_with
        await speak_and_emit(
            sid,
            f"Perfect, {name}! You're all checked in. "
            f"{employee['name']} from {dept_name} is on {employee['floor']}. "
            f"I'll let them know you're here. Please have a seat!",
            emotion="happy"
        )
    else:
        await speak_and_emit(
            sid,
            f"You're all set, {name}! I've registered your visit with {meeting_with}. "
            f"Please have a seat and someone will be right with you."
        )

    asyncio.create_task(idle_prompt(sid))


# ─────────────────────────────────────────────
# IDENTITY CONFIRMATION HANDLER
# ─────────────────────────────────────────────

async def handle_identity_confirmation(sid: str, session: dict, text: str):
    text_lower = text.lower()
    pending = session["pending_employee"]

    EMPLOYEE_SIGNALS = [
        "work here", "i work", "employee", "staff", "i'm from",
        "i am from", "belong to", "part of", "my team", "our team",
        "i'm in", "i am in"
    ]
    VISITOR_SIGNALS = [
        "visitor", "visit", "guest", "no", "not an employee",
        "don't work", "here to meet", "here for", "appointment",
        "interview", "meeting", "client", "outside"
    ]

    is_employee_response = any(s in text_lower for s in EMPLOYEE_SIGNALS)
    is_visitor_response  = any(s in text_lower for s in VISITOR_SIGNALS)

    if is_employee_response:
        extracted = await extract_visitor_info(text)
        dept_hint = extracted.get("department_hint") or ""

        if dept_hint:
            verified = get_employee_by_name_and_department(session["name"], dept_hint)
            employee = verified if verified else pending
        else:
            employee = pending

        session["identity"] = "EMPLOYEE"
        session["name"] = employee["name"]
        session["pending_employee"] = None

        log_id = log_reception_entry(
            person_name=employee["name"],
            person_type="EMPLOYEE",
            notes="Employee checked in at reception",
            linked_employee_id=employee.get("id")
        )
        session["log_id"] = log_id
        session["checkin_done"] = True

        await speak_and_emit(
            sid,
            f"Welcome back, {employee['name']}! How can I help you today?"
        )
        return

    if is_visitor_response:
        session["identity"] = "VISITOR"
        session["pending_employee"] = None

        extracted = await extract_visitor_info(text)
        merge_extracted(session, extracted)

        await handle_checkin(sid, session)
        return

    await speak_and_emit(
        sid,
        f"Sorry, could you clarify — are you visiting us today, or are you one of our employees?"
    )


# ─────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────

async def speak_and_emit(sid: str, response: str, emotion: str = "neutral"):
    print(f"🤖 Response : {response}")
    set_state(BrainState.SPEAKING)
    tts_start = time.time()
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, speak, response)
    tts_time = int((time.time() - tts_start) * 1000)
    print(f"⏱  TTS      : {tts_time}ms")
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
# DATABASE INIT + SETTINGS
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
    print(f"🔌 Connected: {sid}")


@sio.event
async def audio_chunk(sid, data):
    session = get_session(sid)

    if sid not in welcomed_sessions:
        welcomed_sessions.add(sid)
        company_name = get_setting("company_name")
        welcome_message = (
            f"Welcome to {company_name}! "
            "I'm AlmostHuman, your virtual receptionist. "
            "How can I help you today?"
        )
        print(f"🤖 AI: {welcome_message}")
        await speak_and_emit(sid, welcome_message)

    if get_state() in [BrainState.THINKING, BrainState.SPEAKING]:
        return

    set_state(BrainState.THINKING)

    stt_start = time.time()
    text = await process_audio(data)
    stt_time = int((time.time() - stt_start) * 1000)

    if not text:
        set_state(BrainState.IDLE)
        return

    # Valid speech confirmed — tell frontend to close mic gate immediately,
    # and flush any audio that snuck into the buffer during processing
    await sio.emit("ai_thinking", {}, to=sid)
    flush_buffer()

    print(f"\n{'─'*50}")
    print(f"👤 Visitor  : {text}")
    print(f"⏱  STT      : {stt_time}ms")

    if session["identity"] == "EMPLOYEE":
        handled = await handle_meeting_request(session, text, speak_and_emit, sid)
        if handled:
            set_state(BrainState.IDLE)
            return
        set_state(BrainState.IDLE)
        llm_start = time.time()
        response = await process_user_text(text)
        llm_time = int((time.time() - llm_start) * 1000)
        print(f"⏱  LLM      : {llm_time}ms")
        print(f"🤖 Response : {response['text']}")
        await sio.emit("ai_response", response, to=sid)
        return

    if session["checkin_done"]:
        handled = await handle_meeting_request(session, text, speak_and_emit, sid)
        if handled:
            set_state(BrainState.IDLE)
            return
        set_state(BrainState.IDLE)
        llm_start = time.time()
        response = await process_user_text(text)
        llm_time = int((time.time() - llm_start) * 1000)
        print(f"⏱  LLM      : {llm_time}ms")
        print(f"🤖 Response : {response['text']}")
        await sio.emit("ai_response", response, to=sid)
        return

    if session["identity"] == "CONFIRMING":
        set_state(BrainState.IDLE)
        await handle_identity_confirmation(sid, session, text)
        return

    extracted, _ = await asyncio.gather(
        extract_visitor_info(text),
        asyncio.to_thread(get_visitor_by_name, text)
    )

    print(f"🧠 Extracted  : {extracted}")
    merge_extracted(session, extracted)

    set_state(BrainState.IDLE)
    await handle_checkin(sid, session)


# ─────────────────────────────────────────────
# IDLE PROMPT
# ─────────────────────────────────────────────

async def idle_prompt(sid: str):
    await asyncio.sleep(10)
    if sid in sessions:
        await speak_and_emit(sid, "Feel free to ask if you need anything while you wait!")


# ─────────────────────────────────────────────
# DISCONNECT
# ─────────────────────────────────────────────

@sio.event
async def speech_ended(sid: str):
    """Frontend fires this when audio playback finishes.
    Flush stale buffer and re-enable mic."""
    flush_buffer()
    if get_state() == BrainState.SPEAKING:
        set_state(BrainState.IDLE)
        print(f"🔈 Playback ended for {sid} — buffer flushed, mic re-enabled")


@sio.event
async def disconnect(sid: str):
    print(f"🔌 Disconnected: {sid}")
    welcomed_sessions.discard(sid)

    session = sessions.get(sid)
    if session and session.get("log_id"):
        log_reception_checkout(session["log_id"])

    sessions.pop(sid, None)

    from think_with_groq import conversation_history
    conversation_history.clear()