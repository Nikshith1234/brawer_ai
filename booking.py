"""
booking.py
Handles hotel booking via direct HTTP API calls.
Endpoints discovered from api_discovery.py
"""

import os
import logging
import httpx
from dotenv import load_dotenv

load_dotenv()
log = logging.getLogger(__name__)

# ── Real API URLs ──────────────────────────────────
BASE_URL = "https://bookingbe.heykoala.ai"

# ── Room Type ID Map ───────────────────────────────
ROOM_TYPE_MAP = {
    "premium suite":        1,
    "deluxe room":          2,
    "deluxe":               2,
    "executive room":       3,
    "executive":            3,
    "family suite":         4,
    "family":               4,
    "deluxe sea view room": 5,
    "sea view":             5,
    "presidential suite":   6,
    "presidential":         6,
}

# ── Cached Token ───────────────────────────────────
_cached_token = None


def get_auth_token(force_refresh: bool = False) -> str:
    """Login and return JWT token."""
    global _cached_token

    if _cached_token and not force_refresh:
        return _cached_token

    log.info("Logging in to bookingbe.heykoala.ai...")

    response = httpx.post(
        f"{BASE_URL}/login",
        data={
            "username": os.getenv("ADMIN_USERNAME"),
            "password": os.getenv("ADMIN_PASSWORD"),
        },
        timeout=30
    )

    if response.status_code != 200:
        raise Exception(f"Login failed [{response.status_code}]: {response.text}")

    data = response.json()
    token = (
        data.get("token") or
        data.get("access_token") or
        data.get("jwt") or
        data.get("authToken")
    )

    if not token:
        raise Exception(f"No token found in login response: {data}")

    _cached_token = token
    log.info("Login successful.")
    return token


def create_booking(details: dict) -> dict:
    """Submit a booking to the hotel API."""
    token = get_auth_token()

    # Look up the room_type_id from the room name
    room_raw = details.get("room_type", "").lower().strip()
    room_type_id = ROOM_TYPE_MAP.get(room_raw)

    if not room_type_id:
        for key, val in ROOM_TYPE_MAP.items():
            if key in room_raw or room_raw in key:
                room_type_id = val
                break

    if not room_type_id:
        raise Exception(
            f"Unknown room type: '{details.get('room_type')}'. "
            f"Valid options: {list(ROOM_TYPE_MAP.keys())}"
        )

    payload = {
        "user_name":      details.get("guest_name", ""),
        "email":          details.get("email", ""),
        "room_type_id":   room_type_id,
        "check_in_date":  details.get("check_in", ""),
        "check_out_date": details.get("check_out", ""),
        "adults":         int(details.get("adults", 1)),
        "children":       int(details.get("children", 0)),
        "children_ages":  [],
    }

    log.info(f"Creating booking: {payload}")

    response = httpx.post(
        f"{BASE_URL}/bookings",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type":  "application/json",
        },
        json=payload,
        timeout=30
    )

    log.info(f"Response [{response.status_code}]: {response.text}")

    if response.status_code in (200, 201):
        return response.json()

    elif response.status_code == 401:
        log.warning("Token expired, retrying with fresh token...")
        token = get_auth_token(force_refresh=True)
        response = httpx.post(
            f"{BASE_URL}/bookings",
            headers={"Authorization": f"Bearer {token}",
                     "Content-Type": "application/json"},
            json=payload,
            timeout=30
        )
        if response.status_code in (200, 201):
            return response.json()

    raise Exception(f"Booking failed [{response.status_code}]: {response.text}")
