"""
browser_booking.py
AI-powered browser automation for booking on ANY hotel website.
Supports OTP/email verification flows — pauses and asks user for OTP via WhatsApp.

Flow:
  Phase 1 — start_browser_session(): opens browser, logs in, detects OTP prompt
  Phase 2 — resume_with_otp(): enters OTP, completes login + booking
"""

import os
import json
import re
import base64
import logging
import requests
from dotenv import load_dotenv

load_dotenv()
log = logging.getLogger(__name__)

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_VISION_URL = (
    f"https://generativelanguage.googleapis.com/v1beta/models/"
    f"gemini-2.0-flash:generateContent?key={GEMINI_API_KEY}"
)

# Stores active Playwright sessions per user phone number
# Key: phone, Value: { pw, browser, page, details, otp_selector, otp_submit_selector }
_active_sessions: dict = {}


# ── Gemini Helpers ─────────────────────────────────
def ask_gemini_vision(screenshot_b64: str, prompt: str) -> str:
    payload = {
        "contents": [{
            "parts": [
                {"inline_data": {"mime_type": "image/png", "data": screenshot_b64}},
                {"text": prompt}
            ]
        }]
    }
    response = requests.post(
        GEMINI_VISION_URL,
        headers={"Content-Type": "application/json"},
        json=payload,
        timeout=30
    )
    if response.status_code != 200:
        raise Exception(f"Gemini Vision error [{response.status_code}]: {response.text}")
    return response.json()["candidates"][0]["content"]["parts"][0]["text"].strip()


def parse_json(text: str):
    text = re.sub(r'```json|```', '', text).strip()
    match = re.search(r'[\{\[].*[\}\]]', text, re.DOTALL)
    if match:
        return json.loads(match.group())
    raise ValueError(f"No JSON found in: {text[:200]}")


def get_screenshot(page) -> str:
    return base64.b64encode(page.screenshot(full_page=False)).decode("utf-8")


# ── Safe Playwright Actions ────────────────────────
def safe_fill(page, selector: str, value: str):
    if not selector or not value:
        return
    try:
        page.wait_for_selector(selector, timeout=5000)
        page.fill(selector, str(value))
        log.info(f"Filled '{selector}'")
    except Exception as e:
        log.warning(f"Could not fill '{selector}': {e}")


def safe_click(page, selector: str):
    if not selector:
        return
    try:
        page.wait_for_selector(selector, timeout=5000)
        page.click(selector)
        log.info(f"Clicked '{selector}'")
    except Exception as e:
        log.warning(f"Could not click '{selector}': {e}")


def safe_select(page, selector: str, value: str):
    if not selector or not value:
        return
    try:
        page.wait_for_selector(selector, timeout=5000)
        try:
            page.select_option(selector, label=value)
        except:
            page.select_option(selector, value=value)
    except Exception as e:
        log.warning(f"Could not select '{value}' in '{selector}': {e}")


# ══════════════════════════════════════════════════
# PHASE 1 — Start session, login, detect OTP
# ══════════════════════════════════════════════════
def start_browser_session(phone: str, website_url: str, details: dict,
                           email: str, password: str) -> dict:
    """
    Opens browser, navigates to site, attempts login.

    Returns one of:
      { "status": "otp_required", "message": "WhatsApp message to send user" }
      { "status": "completed",    "message": "...", "booking_id": "..." }
      { "status": "error",        "message": "..." }
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise Exception("Run: pip install playwright && playwright install chromium")

    log.info(f"[{phone}] Starting browser session on {website_url}")

    pw = sync_playwright().start()

    # These flags are required on Render/Linux cloud servers (no root access)
    launch_args = [
        "--no-sandbox",
        "--disable-setuid-sandbox",
        "--disable-dev-shm-usage",
        "--disable-gpu",
        "--disable-software-rasterizer",
        "--disable-blink-features=AutomationControlled",
        "--single-process",          # Important for Render free tier
        "--no-zygote",               # Required when --single-process is set
    ]

    try:
        # Try headless shell first (lighter, works better on Render)
        browser = pw.chromium.launch(
            channel="chromium",
            headless=True,
            args=launch_args
        )
    except Exception:
        # Fallback to standard chromium
        browser = pw.chromium.launch(
            headless=True,
            args=launch_args
        )

    page = browser.new_page(viewport={"width": 1280, "height": 900})

    # Save session so Phase 2 can reuse this browser
    _active_sessions[phone] = {
        "pw": pw,
        "browser": browser,
        "page": page,
        "details": details,
        "website_url": website_url,
        "otp_selector": "",
        "otp_submit_selector": "",
    }

    try:
        page.goto(website_url, wait_until="networkidle", timeout=30000)
        shot = get_screenshot(page)

        # ── Ask Gemini: is there a login form? ────
        login_check = ask_gemini_vision(shot, """
Look at this webpage. Is there a login or sign-in form visible?
Reply ONLY with JSON (no explanation):
{
  "has_login": true or false,
  "email_selector": "css selector for email/username input, or empty string",
  "password_selector": "css selector for password input, or empty string",
  "submit_selector": "css selector for login/continue button, or empty string",
  "email_only_first": true or false
}
Set email_only_first=true if site shows ONLY the email field first then password on next screen.
""")
        login_info = parse_json(login_check)
        log.info(f"[{phone}] Login form info: {login_info}")

        if not login_info.get("has_login"):
            # No login — go directly to booking form
            return _do_booking(phone, page, details)

        # ── Fill email ────────────────────────────
        safe_fill(page, login_info.get("email_selector", ""), email)

        if login_info.get("email_only_first"):
            # Submit email first, then fill password on next screen
            safe_click(page, login_info.get("submit_selector", ""))
            page.wait_for_load_state("networkidle", timeout=10000)
            shot = get_screenshot(page)

            pass_info = parse_json(ask_gemini_vision(shot, """
Find the password input and submit button now visible.
Reply ONLY with JSON:
{"password_selector": "css selector", "submit_selector": "css selector"}
"""))
            safe_fill(page, pass_info.get("password_selector", ""), password)
            safe_click(page, pass_info.get("submit_selector", ""))
        else:
            safe_fill(page, login_info.get("password_selector", ""), password)
            safe_click(page, login_info.get("submit_selector", ""))

        page.wait_for_load_state("networkidle", timeout=15000)
        shot = get_screenshot(page)

        # ── Ask Gemini: what happened after login? ─
        after_login = parse_json(ask_gemini_vision(shot, """
What happened after the login attempt? Check the page carefully.
Reply ONLY with JSON:
{
  "status": "logged_in" or "otp_required" or "captcha" or "error",
  "otp_input_selector": "css selector for OTP/verification code input field if visible, else empty string",
  "otp_submit_selector": "css selector for OTP submit/verify button if visible, else empty string",
  "message": "brief description of what you see on screen"
}
"""))
        log.info(f"[{phone}] After login result: {after_login}")

        status = after_login.get("status")

        if status == "otp_required":
            # Save OTP selectors for Phase 2
            _active_sessions[phone]["otp_selector"] = after_login.get("otp_input_selector", "")
            _active_sessions[phone]["otp_submit_selector"] = after_login.get("otp_submit_selector", "")

            return {
                "status": "otp_required",
                "message": (
                    f"🔐 *Verification Required*\n\n"
                    f"The website sent a One-Time Password (OTP) to:\n"
                    f"📧 *{email}*\n\n"
                    f"Please check your email inbox and reply here with the OTP code to continue your booking.\n\n"
                    f"_(OTP expires in 10 minutes)_"
                )
            }

        elif status == "logged_in":
            return _do_booking(phone, page, details)

        elif status == "captcha":
            _cleanup(phone)
            return {
                "status": "error",
                "message": (
                    f"⚠️ This website requires CAPTCHA verification which I cannot solve automatically.\n\n"
                    f"Please book directly at: {website_url}"
                )
            }

        else:
            _cleanup(phone)
            return {
                "status": "error",
                "message": f"❌ Login failed: {after_login.get('message', 'Unknown error')}"
            }

    except Exception as e:
        log.error(f"[{phone}] Phase 1 error: {e}")
        _cleanup(phone)
        raise


# ══════════════════════════════════════════════════
# PHASE 2 — User sent OTP, enter it and complete booking
# ══════════════════════════════════════════════════
def resume_with_otp(phone: str, otp_code: str) -> dict:
    """
    Called when user replies with their OTP code on WhatsApp.
    Enters OTP into the browser and completes the booking.

    Returns:
      { "status": "completed", "message": "...", "booking_id": "..." }
      { "status": "error",     "message": "..." }
    """
    session = _active_sessions.get(phone)
    if not session:
        return {
            "status": "error",
            "message": "⏰ Your session has expired. Please start your booking again."
        }

    page    = session["page"]
    details = session["details"]
    otp_sel = session.get("otp_selector", "")
    otp_sub = session.get("otp_submit_selector", "")

    log.info(f"[{phone}] Entering OTP: {otp_code.strip()}")

    try:
        if otp_sel:
            safe_fill(page, otp_sel, otp_code.strip())
            if otp_sub:
                safe_click(page, otp_sub)
        else:
            # Gemini finds OTP field from screenshot
            shot = get_screenshot(page)
            otp_info = parse_json(ask_gemini_vision(shot, f"""
I need to enter OTP code "{otp_code.strip()}" on this page.
Find the OTP input and submit button.
Reply ONLY with JSON:
{{"otp_selector": "css selector", "submit_selector": "css selector"}}
"""))
            safe_fill(page, otp_info.get("otp_selector", ""), otp_code.strip())
            safe_click(page, otp_info.get("submit_selector", ""))

        page.wait_for_load_state("networkidle", timeout=15000)
        shot = get_screenshot(page)

        # Verify OTP was accepted
        verify = parse_json(ask_gemini_vision(shot, """
Did the OTP verification succeed? Is the user now logged in?
Reply ONLY with JSON:
{"success": true or false, "message": "what you see on screen"}
"""))

        if not verify.get("success"):
            _cleanup(phone)
            return {
                "status": "error",
                "message": (
                    f"❌ OTP verification failed: {verify.get('message')}\n\n"
                    f"The code may have expired. Please start your booking again."
                )
            }

        log.info(f"[{phone}] OTP accepted — now filling booking form")
        return _do_booking(phone, page, details)

    except Exception as e:
        log.error(f"[{phone}] Phase 2 error: {e}")
        _cleanup(phone)
        return {"status": "error", "message": f"❌ Failed after OTP: {str(e)[:200]}"}


# ══════════════════════════════════════════════════
# BOOKING FORM — fills form after successful login
# ══════════════════════════════════════════════════
def _do_booking(phone: str, page, details: dict) -> dict:
    log.info(f"[{phone}] Filling booking form")

    try:
        shot = get_screenshot(page)

        # Navigate to booking form if needed
        nav = parse_json(ask_gemini_vision(shot, """
I need to create a new hotel room booking on this page.
What should I click to reach the booking creation form?
Reply ONLY with JSON:
{"action": "click" or "already_on_form", "selector": "css selector or empty", "description": "brief description"}
"""))

        if nav.get("action") == "click" and nav.get("selector"):
            safe_click(page, nav["selector"])
            page.wait_for_load_state("networkidle", timeout=15000)

        shot = get_screenshot(page)

        # Ask Gemini to fill the form
        form_actions = parse_json(ask_gemini_vision(shot, f"""
Fill this hotel booking form with:
- Guest Name: {details.get('guest_name', '')}
- Email: {details.get('email', '')}
- Phone: {details.get('phone', '')}
- Check-in Date: {details.get('check_in', '')}
- Check-out Date: {details.get('check_out', '')}
- Room Type: {details.get('room_type', '')}
- Adults: {details.get('adults', 1)}
- Children: {details.get('children', 0)}
- Special Requests: {details.get('special_requests', '')}

Return ONLY a JSON array of actions:
[
  {{"action": "fill",   "selector": "css selector", "value": "text to type"}},
  {{"action": "select", "selector": "css selector", "value": "option to pick"}},
  {{"action": "click",  "selector": "css selector"}}
]
End with clicking the submit/confirm/save button.
"""))

        for action in form_actions:
            act = action.get("action")
            sel = action.get("selector", "")
            val = action.get("value", "")
            if act == "fill":
                safe_fill(page, sel, val)
            elif act == "select":
                safe_select(page, sel, val)
            elif act == "click":
                safe_click(page, sel)
                page.wait_for_timeout(500)

        page.wait_for_load_state("networkidle", timeout=15000)
        shot = get_screenshot(page)

        # Check booking result
        result = parse_json(ask_gemini_vision(shot, """
Did the hotel booking succeed?
Look for: confirmation message, booking reference/ID, success banner.
Reply ONLY with JSON:
{"success": true or false, "booking_id": "ID if visible else empty string", "message": "what you see"}
"""))

        _cleanup(phone)
        log.info(f"[{phone}] Final booking result: {result}")

        return {
            "status": "completed" if result.get("success") else "error",
            "booking_id": result.get("booking_id", ""),
            "message": result.get("message", "")
        }

    except Exception as e:
        log.error(f"[{phone}] Booking form error: {e}")
        _cleanup(phone)
        raise


# ── Cleanup ────────────────────────────────────────
def _cleanup(phone: str):
    session = _active_sessions.pop(phone, None)
    if session:
        try:
            session["browser"].close()
            session["pw"].stop()
        except:
            pass
        log.info(f"[{phone}] Browser closed")


def has_active_session(phone: str) -> bool:
    """Check if user has an ongoing browser session (e.g. waiting for OTP)."""
    return phone in _active_sessions