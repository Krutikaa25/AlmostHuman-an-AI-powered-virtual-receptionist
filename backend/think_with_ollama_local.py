import httpx
import json
import re

OLLAMA_URL = "http://localhost:11434/api/generate"

SYSTEM_PROMPT = """You are AlmostHuman, a professional AI virtual receptionist for a corporate office.

INTRODUCTION RULE:
- Introduce yourself as "AlmostHuman" only once at the very start of a new conversation.
- Never repeat your name unless the user directly asks for it.

YOUR RESPONSIBILITIES:
- Greet and assist visitors with check-in and appointments.
- Answer basic questions about the company and provide office directions.
- Help visitors connect with the right employees or departments.
- Escalate to human staff when needed.

COMMUNICATION STYLE:
- Speak professionally, confidently, and politely.
- Keep every response to 1-2 sentences, strictly 30-40 words maximum.
- Be direct and clear. No long explanations or filler phrases.

CONVERSATION RULES:
- Do not ask follow-up questions unless clarification is truly required.
- Never use phrases like "How may I help you today?" or "Is there anything else I can assist you with?"
- If a visitor thanks you, respond warmly and close the conversation naturally. Do not reopen it.
- When ending a conversation, give one brief closing statement only.

UNKNOWN INFORMATION:
- If you don't know something, say you will connect the visitor to the appropriate staff member. Do not guess."""

# Global conversation history — only "user" and "assistant" roles
conversation_history = []


def build_mistral_prompt(history: list, company_info: dict = None) -> str:
    """
    Builds proper Mistral [INST]...[/INST] formatted prompt.
    System prompt is injected into the FIRST user message only.
    """
    system = SYSTEM_PROMPT
    if company_info:
        system += "\n\nCOMPANY CONTEXT:\n"
        system += f"Company: {company_info.get('company_name', '')}\n"
        system += f"Location: {company_info.get('company_location', '')}\n"
        system += f"Office Hours: {company_info.get('office_hours', '')}\n"
        system += f"Departments: {company_info.get('departments', '')}"

    prompt = ""
    for i, msg in enumerate(history):
        if msg["role"] == "user":
            if i == 0:
                # Inject system prompt into very first user message
                prompt += f"[INST] {system}\n\nUser: {msg['content']} [/INST]"
            else:
                prompt += f"[INST] {msg['content']} [/INST]"
        elif msg["role"] == "assistant":
            prompt += f" {msg['content']}</s>"

    return prompt


def clean_reply(text: str) -> str:
    """
    Strip all prefixes Mistral sometimes adds to its own response.
    Handles: 'AI:', 'AI: AI:', 'Assistant:', 'AlmostHuman:' etc.
    """
    # Remove [INST] / [/INST] / </s> artifacts
    text = text.replace("[INST]", "").replace("[/INST]", "").replace("</s>", "")
    # Remove any leading role prefix like "AI:", "Assistant:", "AlmostHuman:"
    text = re.sub(r'^(AI|Assistant|AlmostHuman)\s*:\s*', '', text, flags=re.IGNORECASE)
    # In case it's doubled e.g. "AI: AI: ..."
    text = re.sub(r'^(AI|Assistant|AlmostHuman)\s*:\s*', '', text, flags=re.IGNORECASE)
    return text.strip()


async def think(user_text: str, company_info: dict = None) -> str:
    global conversation_history

    # Reset conversation on exit phrases
    if user_text.strip().lower() in ["bye", "goodbye", "thank you", "thanks"]:
        conversation_history = []

    # Only keep last 6 exchanges to prevent prompt bloat and slow inference
    if len(conversation_history) > 12:
        conversation_history = conversation_history[-12:]

    # Add user message
    conversation_history.append({
        "role": "user",
        "content": user_text
    })

    full_prompt = build_mistral_prompt(conversation_history, company_info)

    payload = {
        "model": "mistral:7b-instruct-q4_K_M",
        "prompt": full_prompt,
        "stream": True,
        "options": {
            "num_predict": 100,
            "temperature": 0.5,
            "stop": ["[INST]", "</s>", "\nUser:", "\nAI:", "\nAssistant:"]
        }
    }

    reply = ""

    async with httpx.AsyncClient(timeout=None) as client:
        async with client.stream("POST", OLLAMA_URL, json=payload) as response:
            async for line in response.aiter_lines():
                if not line:
                    continue
                data = json.loads(line)
                token = data.get("response", "")
                reply += token
                print(token, end="", flush=True)
                if data.get("done", False):
                    break

    print()  # newline after streaming tokens

    reply = clean_reply(reply)

    # Save to history
    conversation_history.append({
        "role": "assistant",
        "content": reply
    })

    return reply