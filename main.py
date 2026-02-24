"""
Room Booking AI Agent — main.py
Denisson's Beach Resort
Supports: default site (API) + any external website (Playwright + AI Vision + OTP)
"""

import os
import json
import logging
import threading
import time
import requests as req
from dotenv import load_dotenv
from flask import Flask, request, Response
from twilio.rest import Client

from ai_extractor import extract_booking_details
from booking import create_booking
from browser_booking import start_browser_session, resume_with_otp, has_active_session
from session_manager import get_session, set_session, clear_session, get_state, get_data

load_dotenv()

# ── Config ─────────────────────────────────────────
DEFAULT_BOOKING_URL = "https://booking.heykoala.ai/"

# ── Logging ────────────────────────────────────────
os.makedirs("logs", exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("logs/agent.log"),
        logging.StreamHandler()
    ]
)
log = logging.getLogger(__name__)

# ── Twilio ─────────────────────────────────────────
twilio_client = Client(
    os.getenv("TWILIO_ACCOUNT_SID"),
    os.getenv("TWILIO_AUTH_TOKEN")
)
TWILIO_NUMBER = os.getenv("TWILIO_WHATSAPP_NUMBER")

# ── Flask ──────────────────────────────────────────
app = Flask(__name__)


# ── Keep Alive ─────────────────────────────────────
def keep_alive():
    url = os.getenv("RENDER_URL", "")
    if not url:
        return
    while True:
        time.sleep(600)
        try:
            req.get(f"{url}/", timeout=10)
            log.info("Keep-alive ping sent")
        except Exception as e:
            log.warning(f"Keep-alive failed: {e}")

def start_keep_alive():
    threading.Thread(target=keep_alive, daemon=True).start()


# ── Health Check ───────────────────────────────────
@app.route('/', methods=['GET'])
def health():
    return {"status": "running", "default_site": DEFAULT_BOOKING_URL}, 200


# ── Gemini Test ────────────────────────────────────
@app.route('/test-gemini', methods=['GET'])
def test_gemini():
    try:
        result = extract_booking_details(
            "Book a Deluxe Room for John Silva, john@gmail.com, "
            "check-in March 10 2026, check-out March 15 2026, 2 adults"
        )
        return {"status": "success", "extracted": result}, 200
    except Exception as e:
        return {"status": "error", "message": str(e)}, 500


# ══════════════════════════════════════════════════
# WhatsApp Webhook — handles ALL incoming messages
# ══════════════════════════════════════════════════
@app.route('/webhook/whatsapp', methods=['POST'])
def whatsapp_webhook():
    incoming_msg = request.values.get('Body', '').strip()
    sender = request.values.get('From', '')

    log.info(f"Message from {sender}: {incoming_msg}")

    state = get_state(sender)

    if state == "awaiting_otp":
        send_whatsapp(sender, "Got your OTP! Entering it now, please wait...")
        reply = handle_otp_reply(sender, incoming_msg)

    elif state == "booking_in_progress":
        reply = "Your booking is still being processed, please wait..."

    else:
        send_whatsapp(sender, "Processing your booking request, please wait...")
        reply = process_booking_request(incoming_msg, sender)

    send_whatsapp(sender, reply)
    return Response("<Response></Response>", mimetype='text/xml')


# ══════════════════════════════════════════════════
# OTP Reply Handler
# ══════════════════════════════════════════════════
def handle_otp_reply(sender: str, otp_code: str) -> str:
    digits = ''.join(filter(str.isdigit, otp_code))
    if not digits:
        return (
            "That doesn't look like a valid OTP code.\n\n"
            "Please reply with just the number from your email, e.g: 123456"
        )

    session_data = get_data(sender)
    set_session(sender, "booking_in_progress", session_data)

    try:
        result = resume_with_otp(sender, digits)
    except Exception as e:
        clear_session(sender)
        return f"Something went wrong: {str(e)[:200]}\n\nPlease try your booking again."

    clear_session(sender)

    if result.get("status") == "completed":
        details = session_data.get("details", {})
        booking_id = result.get("booking_id", "")
        booking_id_line = f"Booking ID:  {booking_id}\n" if booking_id else ""
        return (
            f"Booking Confirmed!\n\n"
            f"Website: {session_data.get('website_url', '')}\n"
            f"---------------------------\n"
            f"Guest:     {details.get('guest_name', '')}\n"
            f"Room:      {details.get('room_type', '')}\n"
            f"Check-in:  {details.get('check_in', '')}\n"
            f"Check-out: {details.get('check_out', '')}\n"
            f"Adults:    {details.get('adults', 1)}\n"
            f"Children:  {details.get('children', 0)}\n"
            f"{booking_id_line}"
            f"---------------------------\n"
            f"{result.get('message', '')}"
        )
    else:
        return result.get("message", "Booking failed. Please try again.")


# ══════════════════════════════════════════════════
# Core Booking Logic
# ══════════════════════════════════════════════════
def process_booking_request(message: str, sender: str) -> str:

    try:
        details = extract_booking_details(message)
        log.info(f"Extracted: {json.dumps(details, indent=2)}")
    except Exception as e:
        log.error(f"Extraction failed: {e}")
        return (
            "Booking Failed\n\n"
            "Sorry, I could not understand your booking request.\n\n"
            "Please try again like this:\n"
            "Hi, I'd like to book a Deluxe Room. My name is John Silva, "
            "email john@gmail.com. Check-in March 10 2026, check-out March 15 2026. 2 adults."
        )

    required = {
        "guest_name": "your full name",
        "email":      "your email address",
        "check_in":   "check-in date",
        "check_out":  "check-out date",
        "room_type":  "room type",
        "adults":     "number of adults",
    }
    missing = [label for field, label in required.items() if not details.get(field)]
    if missing:
        missing_list = "\n".join(f"  - {m}" for m in missing)
        return (
            f"Booking Incomplete\n\n"
            f"I could not find:\n{missing_list}\n\n"
            f"Please resend with all details. Thank you!"
        )

    website_url = details.get("website_url", "").strip()
    is_external = bool(website_url) and website_url.lower().rstrip("/") not in [
        DEFAULT_BOOKING_URL.rstrip("/"),
        "booking.heykoala.ai",
        "https://booking.heykoala.ai",
    ]

    if is_external:
        return _handle_external_booking(details, website_url, sender)
    else:
        return _handle_default_booking(details)


def _handle_default_booking(details: dict) -> str:
    try:
        result = create_booking(details)
        return (
            f"Booking Confirmed!\n\n"
            f"Denisson's Beach Resort\n"
            f"---------------------------\n"
            f"Guest:     {details['guest_name']}\n"
            f"Room:      {details['room_type']}\n"
            f"Check-in:  {details['check_in']}\n"
            f"Check-out: {details['check_out']}\n"
            f"Adults:    {details['adults']}\n"
            f"Children:  {details.get('children', 0)}\n"
            f"---------------------------\n"
            f"Confirmation sent to {details['email']}.\n\n"
            f"We look forward to welcoming you!"
        )
    except Exception as e:
        log.error(f"Default booking failed: {e}")
        return (
            f"Booking Failed\n\n"
            f"Sorry {details.get('guest_name', 'there')}, we could not complete your booking.\n\n"
            f"Please contact us:\n"
            f"Phone: +1 (555) 123-4567\n"
            f"Email: reservations@denissonsbeach.com"
        )


def _handle_external_booking(details: dict, website_url: str, sender: str) -> str:
    log.info(f"External booking for {sender} on {website_url}")

    email    = details.get("email", "")
    password = os.getenv("EXTERNAL_PASSWORD", "")

    if not email:
        return (
            "I need your email address to log in to the booking website.\n\n"
            "Please resend your request including your email."
        )

    try:
        result = start_browser_session(sender, website_url, details, email, password)
    except Exception as e:
        log.error(f"External booking crashed: {e}")
        return (
            f"Could not open {website_url}\n\n"
            f"Error: {str(e)[:200]}\n\n"
            f"Please try booking directly on the website."
        )

    status = result.get("status")

    if status == "otp_required":
        # Save state — user's next WhatsApp message will be their OTP
        set_session(sender, "awaiting_otp", {
            "details": details,
            "website_url": website_url,
        })
        return result["message"]

    elif status == "completed":
        booking_id = result.get("booking_id", "")
        booking_id_line = f"Booking ID:  {booking_id}\n" if booking_id else ""
        return (
            f"Booking Confirmed!\n\n"
            f"Website: {website_url}\n"
            f"---------------------------\n"
            f"Guest:     {details['guest_name']}\n"
            f"Room:      {details['room_type']}\n"
            f"Check-in:  {details['check_in']}\n"
            f"Check-out: {details['check_out']}\n"
            f"Adults:    {details['adults']}\n"
            f"Children:  {details.get('children', 0)}\n"
            f"{booking_id_line}"
            f"---------------------------\n"
            f"{result.get('message', 'Booking completed successfully.')}"
        )
    else:
        return result.get("message", "Booking failed. Please try again.")


# ── Send WhatsApp ──────────────────────────────────
def send_whatsapp(to: str, message: str):
    try:
        sender_num = str(TWILIO_NUMBER).strip()
        if not sender_num.startswith("whatsapp:"):
            sender_num = f"whatsapp:{sender_num}"
        twilio_client.messages.create(body=message, from_=sender_num, to=to.strip())
        log.info(f"WhatsApp sent to {to}")
    except Exception as e:
        log.error(f"Failed to send WhatsApp: {e}")


# ── Run ────────────────────────────────────────────
if __name__ == '__main__':
    start_keep_alive()
    port = int(os.environ.get("PORT", 5000))
    log.info(f"Starting server on port {port}...")
    app.run(host="0.0.0.0", port=port, debug=False)
