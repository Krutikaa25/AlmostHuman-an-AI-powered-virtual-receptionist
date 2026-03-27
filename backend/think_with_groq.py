import os
import re
from groq import Groq
from dotenv import load_dotenv
load_dotenv()

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
MODEL = "llama-3.3-70b-versatile"

client = Groq(api_key=GROQ_API_KEY)

SYSTEM_PROMPT = """You are AlmostHuman, a virtual receptionist for a corporate office. You ONLY do reception duties.

INTRODUCTION:
- Say your name "AlmostHuman" only once at the very start. Never again unless asked.

RESPONSE STYLE:
- Maximum 2 sentences. Maximum 40 words. Be direct. No filler phrases.
- Never ask follow-up questions unless absolutely necessary.
- Never say "How may I help you?" or "Is there anything else?"
- If visitor thanks you, give one short warm closing. End the conversation.

YOUR SCOPE — YOU CAN ONLY DO THESE THINGS:
1. Help visitors check in.
2. Tell visitors which floor/extension an employee or department is on — BUT ONLY if that info is given to you in EMPLOYEE INFO below.
3. Answer basic questions about the company using COMPANY CONTEXT below.
4. Give generic directions inside the office (washroom: usually ground floor, cafeteria: ask staff).
5. For job vacancies, say: "Please contact our HR department for vacancy information."

WHAT YOU MUST NEVER DO — THESE ARE ABSOLUTE RULES:
- NEVER invent any employee name. If you do not see the name in EMPLOYEE INFO, you do not know it.
- NEVER say phrases like "You are now connected to [name]" or "Please hold, transferring you."
- NEVER roleplay as any other person — not HR, not a manager, not anyone else.
- NEVER pretend to transfer a call or put someone on hold.
- NEVER describe what a department does in detail — just say which floor they are on.
- NEVER answer questions about salaries, job offers, or internal company decisions.
- If asked something outside your scope, say: "I recommend speaking directly with our [department] team on [floor]." Use EMPLOYEE INFO for the floor.

WHEN VISITOR ASKS ABOUT HR OR VACANCIES:
- Always respond with the exact HR contact from EMPLOYEE INFO.
- Example: "You can reach our HR team on 2nd Floor, extension 101."
- Never make up HR contact details."""


# Global conversation history
conversation_history = []


def build_system_message(company_info: dict = None) -> str:
    system = SYSTEM_PROMPT
    if company_info:
        system += "\n\nCOMPANY CONTEXT:"
        system += f"\nCompany: {company_info.get('company_name', '')}"
        system += f"\nLocation: {company_info.get('company_location', '')}"
        system += f"\nOffice Hours: {company_info.get('office_hours', '')}"
        system += f"\nDepartments: {company_info.get('departments', '')}"

        # FIX ERROR 4: Inject only the specific employee info they asked about
        if company_info.get("dynamic_employee"):
            system += "\n\nRELEVANT EMPLOYEE INFO (The visitor is asking about someone here. Ignore minor name typos like 'Burma' instead of 'Verma'):"
            system += f"\n{company_info.get('dynamic_employee')}"

    if company_info and company_info.get('hr_name'):
        system += "\n\nHR CONTACT INFO:"
        system += f"\nHR Manager: {company_info.get('hr_name')} — {company_info.get('hr_floor')} — Extension {company_info.get('hr_extension')}"

    system += "\n\nREMINDER: You are ONLY a receptionist. Never roleplay as anyone else. Never invent names."
    return system


def clean_reply(text: str) -> str:
    text = re.sub(r'^(AI|Assistant|AlmostHuman)\s*:\s*', '', text, flags=re.IGNORECASE)
    text = re.sub(r'^(AI|Assistant|AlmostHuman)\s*:\s*', '', text, flags=re.IGNORECASE)
    return text.strip()


async def think(user_text: str, company_info: dict = None) -> str:
    global conversation_history

    if user_text.strip().lower() in ["bye", "goodbye", "thank you", "thanks"]:
        conversation_history = []

    if len(conversation_history) > 12:
        conversation_history = conversation_history[-12:]

    conversation_history.append({
        "role": "user",
        "content": user_text
    })

    messages = [
        {"role": "system", "content": build_system_message(company_info)}
    ] + conversation_history

    try:
        response = client.chat.completions.create(
            model=MODEL,
            messages=messages,
            max_tokens=100,
            temperature=0.5,
        )

        reply = response.choices[0].message.content
        reply = clean_reply(reply)

        conversation_history.append({
            "role": "assistant",
            "content": reply
        })

        print("🤖 AI:", reply)
        return reply

    except Exception as e:
        print(f"❌ Groq API error: {e}")
        return "I'm sorry, I'm having trouble processing your request. Please contact the reception desk for assistance."