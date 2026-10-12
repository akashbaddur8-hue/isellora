"""
Authentication, rate limiting, and password policy service.
"""
from datetime import datetime, timezone, timedelta
import re
from backend.models import User, utc_now
from backend.database import db

# In-memory rate limiting store: ip_or_email -> list of failed attempt timestamps
FAILED_ATTEMPTS = {}
MAX_FAILED_ATTEMPTS = 5
LOCKOUT_MINUTES = 15


def is_rate_limited(identifier: str) -> bool:
    """Checks if an IP or email is temporarily locked out due to excessive failed attempts."""
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(minutes=LOCKOUT_MINUTES)
    
    attempts = FAILED_ATTEMPTS.get(identifier, [])
    # Keep only recent attempts within the lockout window
    recent_attempts = [ts for ts in attempts if ts > cutoff]
    FAILED_ATTEMPTS[identifier] = recent_attempts
    
    return len(recent_attempts) >= MAX_FAILED_ATTEMPTS


def record_failed_attempt(identifier: str):
    """Records a failed authentication attempt."""
    now = datetime.now(timezone.utc)
    if identifier not in FAILED_ATTEMPTS:
        FAILED_ATTEMPTS[identifier] = []
    FAILED_ATTEMPTS[identifier].append(now)


def clear_failed_attempts(identifier: str):
    """Clears failed attempts upon successful login."""
    FAILED_ATTEMPTS.pop(identifier, None)


def validate_password_strength(password: str) -> tuple[bool, str]:
    """Validates password complexity requirements."""
    if len(password) < 8:
        return False, "Password must be at least 8 characters long."
    if not re.search(r"[A-Za-z]", password):
        return False, "Password must contain at least one letter."
    if not re.search(r"[0-9]", password):
        return False, "Password must contain at least one number."
    return True, ""


def normalize_email(email: str) -> str:
    """Normalizes email address to lower case and trimmed."""
    return str(email).strip().lower()


def validate_phone(phone: str) -> bool:
    """Validates 10-digit Indian phone number format."""
    clean = re.sub(r"[\s+-]", "", str(phone))
    if len(clean) == 12 and clean.startswith("91"):
        clean = clean[2:]
    return bool(re.fullmatch(r"[6-9][0-9]{9}", clean))
