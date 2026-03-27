import os
import requests
import json
from dotenv import load_dotenv

load_dotenv()

TEAMS_WEBHOOK_URL = os.getenv("TEAMS_WEBHOOK_URL", "")

def notify_teams_now(employee_name: str, organizer_name: str, meeting_time: str, purpose: str):
    if not TEAMS_WEBHOOK_URL:
        print("⚠️ Teams Webhook URL not set. Skipping Teams notification.")
        return

    # Notice we don't include the Date, because this function only fires for TODAY'S meetings
    message = (
        f"🚨 **URGENT: Meeting Scheduled for TODAY** 🚨\n\n"
        f"**Employee:** {employee_name}\n\n"
        f"**Organizer:** {organizer_name}\n\n"
        f"**Time:** {meeting_time}\n\n"
        f"**Purpose:** {purpose if purpose else 'Not specified'}"
    )

    payload = {"text": message}

    try:
        response = requests.post(TEAMS_WEBHOOK_URL, data=json.dumps(payload), headers={"Content-Type": "application/json"})
        if response.status_code == 200:
            print(f"✅ Teams popup sent for {employee_name}")
    except Exception as e:
        print(f"❌ Teams error: {e}")