"""
ai_extractor.py
Calls Gemini api latest Flash directly via HTTP requests.
No special google package needed — works everywhere.
"""

import os
import json
import re
import logging
import requests
from dotenv import load_dotenv

load_dotenv()
log = logging.getLogger(__name__)

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_URL = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-flash-latest:generateContent?key={GEMINI_API_KEY}"


EXTRACTION_PROMPT = """
You are a hotel booking assistant.
Extract booking details from the message below.
Return ONLY valid JSON — no explanation, no markdown, no extra text.

Message:
{message}

Return this exact JSON structure:
{{
  "guest_name": "full name or empty string",
  "email": "email address or empty string",
  "phone": "phone number or empty string",
  "check_in": "YYYY-MM-DD or empty string",
  "check_out": "YYYY-MM-DD or empty string",
  "room_type": "room type or empty string",
  "adults": 1,
  "children": 0,
  "special_requests": "any special requests or empty string",
  "website_url": "full URL of booking website if mentioned, or empty string"
}}

Rules:
- Convert dates like "March 10 2025" to "2025-03-10"
- If adults not mentioned, default to 1
- If children not mentioned, default to 0
- room_type options for default site: Premium Suite, Deluxe Room, Executive Room, Family Suite, Deluxe Sea View Room, Presidential Suite
- For website_url: extract only if user mentions a specific website/URL (e.g. "book on booking.com", "use marriott.com"). Include https:// prefix.
- Return ONLY the JSON object, nothing else
"""


def extract_booking_details(message: str) -> dict:
    """Call Gemini API directly via HTTP and extract booking data."""
    log.info("Sending to Gemini for extraction...")

    payload = {
        "contents": [{
            "parts": [{
                "text": EXTRACTION_PROMPT.format(message=message)
            }]
        }]
    }

    response = requests.post(
        GEMINI_URL,
        headers={"Content-Type": "application/json"},
        json=payload,
        timeout=30
    )

    if response.status_code != 200:
        raise Exception(f"Gemini API error [{response.status_code}]: {response.text}")

    raw = response.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
    log.info(f"Gemini response: {raw}")

    # Strip markdown fences if present
    raw = re.sub(r'```json|```', '', raw).strip()

    # Extract JSON safely
    match = re.search(r'\{.*\}', raw, re.DOTALL)
    if not match:
        raise ValueError("No JSON found in Gemini response")

    data = json.loads(match.group())
    log.info(f"Extracted: {data}")
    return data
