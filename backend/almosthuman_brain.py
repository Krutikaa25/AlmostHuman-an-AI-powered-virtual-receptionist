from speak import speak
from think_with_groq import think
from brain_state import set_state, get_state, BrainState
import time
import asyncio
from database import save_conversation, get_recent_conversations, get_setting


# Cache company settings once at startup to avoid repeated DB hits
_company_info_cache = None

def get_company_info() -> dict:
    global _company_info_cache
    if _company_info_cache is None:
        from database import get_hr
        hr = get_hr()
        _company_info_cache = {
            "company_name": get_setting("company_name"),
            "company_location": get_setting("company_location"),
            "office_hours": get_setting("office_hours"),
            "departments": get_setting("departments"),
            "hr_name": hr["name"] if hr else "",
            "hr_floor": hr["floor"] if hr else "",
            "hr_extension": hr["extension"] if hr else "",
        }
    return _company_info_cache


def detect_emotion(text: str) -> str:
    text = text.lower()
    if any(w in text for w in ["great", "awesome", "nice", "happy", "love"]):
        return "happy"
    if any(w in text for w in ["think", "hmm", "let me", "consider"]):
        return "thinking"
    return "neutral"


async def process_user_text(user_text: str) -> dict:
    total_start = time.time()

    print(f"\n[AlmostHuman] Visitor  : {user_text}")

    set_state(BrainState.THINKING)

    user_message = user_text
    company_info = get_company_info()

    think_start = time.time()
    response_text = await think(user_message, company_info=company_info)
    think_time = int((time.time() - think_start) * 1000)

    print(f"[AlmostHuman] Response : {response_text}")
    print(f"[AlmostHuman] LLM      : {think_time}ms")

    save_conversation(user_text, response_text)

    emotion = detect_emotion(response_text)

    set_state(BrainState.SPEAKING)

    speak_start = time.time()
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, speak, response_text)
    speak_time = int((time.time() - speak_start) * 1000)
    print(f"[AlmostHuman] TTS      : {speak_time}ms")

    set_state(BrainState.IDLE)

    total_time = int((time.time() - total_start) * 1000)
    print(f"[AlmostHuman] Total    : {total_time}ms")

    return {
        "text": response_text,
        "emotion": emotion,
        "state": get_state().value,
        "response_time": total_time,
        "audio_url": f"http://localhost:8000/static/output.wav?t={int(time.time())}"
    }