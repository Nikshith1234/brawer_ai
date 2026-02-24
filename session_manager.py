"""
session_manager.py
Stores per-user booking sessions in memory.
Handles multi-step flows: password → OTP → booking

Session states:
  - "awaiting_password"  : Bot asked user for their website password
  - "awaiting_otp"       : Bot asked user for OTP sent to their email
  - "booking_in_progress": Actively processing a booking
  - None                 : No active session
"""

import time
import logging

log = logging.getLogger(__name__)

# In-memory store: { phone_number: session_dict }
_sessions = {}

SESSION_TIMEOUT = 600  # 10 minutes


def get_session(phone: str) -> dict | None:
    """Get active session for a user, returns None if expired or missing."""
    session = _sessions.get(phone)
    if not session:
        return None

    if time.time() - session.get("created_at", 0) > SESSION_TIMEOUT:
        log.info(f"Session expired for {phone}")
        clear_session(phone)
        return None

    return session


def set_session(phone: str, state: str, data: dict):
    """Create or update a session for a user."""
    _sessions[phone] = {
        "state":      state,
        "data":       data,
        "created_at": time.time(),
    }
    log.info(f"Session set for {phone}: state={state}")


def clear_session(phone: str):
    """Remove session after booking completes or times out."""
    if phone in _sessions:
        del _sessions[phone]
        log.info(f"Session cleared for {phone}")


def get_state(phone: str) -> str | None:
    session = get_session(phone)
    return session["state"] if session else None


def get_data(phone: str) -> dict:
    session = get_session(phone)
    return session["data"] if session else {}