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


RESERVED_USERNAMES = {
    "admin", "administrator", "root", "support", "help", "api", "auth",
    "login", "signup", "register", "logout", "account", "profile", "seller",
    "buyer", "orders", "wishlist", "checkout", "payment", "razorpay", "isellora"
}


def validate_username(username: str) -> tuple[bool, str]:
    """Validates public username format and checks against reserved keywords."""
    if not username:
        return True, ""
    clean = str(username).strip()
    if len(clean) < 3 or len(clean) > 30:
        return False, "Username must be between 3 and 30 characters."
    if not re.fullmatch(r"^[a-zA-Z0-9_]+$", clean):
        return False, "Username can only contain letters, numbers, and underscores."
    if clean.isdigit():
        return False, "Username cannot consist entirely of numbers."
    if clean.lower() in RESERVED_USERNAMES:
        return False, f"The username '{clean}' is reserved by the system."
    return True, ""


def is_safe_redirect_url(target: str) -> bool:
    r"""
    Validates that a post-login or post-action redirect target is a safe local application path.
    Strictly rejects:
    - Absolute URLs (http://, https://, etc.)
    - Protocol-relative URLs (//evil.example)
    - Backslash variations (/\evil, \evil)
    - URL-encoded backslash variations (%5c, %5C)
    - URLs with external netloc or scheme
    - None, empty, or whitespace strings
    """
    if not target or not isinstance(target, str):
        return False

    clean = target.strip()
    if not clean:
        return False

    # Disallow backslashes and encoded backslash variations
    if "\\" in clean or "%5c" in clean.lower():
        return False

    # Disallow protocol-relative and Windows-style paths
    if clean.startswith("//") or clean.startswith("/\\") or clean.startswith("\\"):
        return False

    # Must start with a single slash
    if not clean.startswith("/"):
        return False

    from urllib.parse import urlsplit, unquote
    try:
        parsed = urlsplit(clean)
        if parsed.scheme or parsed.netloc:
            return False

        # Validate decoded path to prevent double-encoding bypasses
        decoded_path = unquote(parsed.path)
        if decoded_path.startswith("//") or decoded_path.startswith("/\\") or "\\" in decoded_path:
            return False
    except Exception:
        return False

    return True


# =============================================================================
# SECURE PASSWORD RESET TOKEN MANAGEMENT & RATE LIMITING
# =============================================================================
import secrets
import hashlib
from backend.models import PasswordResetToken

RESET_REQUEST_ATTEMPTS = {}
MAX_RESET_REQUESTS = 3
RESET_LOCKOUT_MINUTES = 15

SUBMISSION_ATTEMPTS = {}
MAX_SUBMISSION_ATTEMPTS = 5
SUBMISSION_LOCKOUT_MINUTES = 15


def validate_email_format(email: str) -> bool:
    """Validates standard email address format."""
    if not email or not isinstance(email, str):
        return False
    clean = email.strip()
    if len(clean) > 254:
        return False
    return bool(re.fullmatch(r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$", clean))


def is_reset_rate_limited(identifier: str) -> bool:
    """Checks if an IP or email is temporarily rate-limited for requesting password resets."""
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(minutes=RESET_LOCKOUT_MINUTES)
    attempts = RESET_REQUEST_ATTEMPTS.get(identifier, [])
    recent_attempts = [ts for ts in attempts if ts > cutoff]
    RESET_REQUEST_ATTEMPTS[identifier] = recent_attempts
    return len(recent_attempts) >= MAX_RESET_REQUESTS


def record_reset_request(identifier: str):
    """Records a password reset request attempt timestamp."""
    now = datetime.now(timezone.utc)
    if identifier not in RESET_REQUEST_ATTEMPTS:
        RESET_REQUEST_ATTEMPTS[identifier] = []
    RESET_REQUEST_ATTEMPTS[identifier].append(now)


def clear_reset_rate_limits(identifier: str | None = None):
    """Clears reset request rate limit history for tests or manual administrative reset."""
    if identifier:
        RESET_REQUEST_ATTEMPTS.pop(identifier, None)
        SUBMISSION_ATTEMPTS.pop(identifier, None)
    else:
        RESET_REQUEST_ATTEMPTS.clear()
        SUBMISSION_ATTEMPTS.clear()


def is_submission_rate_limited(identifier: str) -> bool:
    """Checks if an IP is rate-limited for password reset token submissions."""
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(minutes=SUBMISSION_LOCKOUT_MINUTES)
    attempts = SUBMISSION_ATTEMPTS.get(identifier, [])
    recent_attempts = [ts for ts in attempts if ts > cutoff]
    SUBMISSION_ATTEMPTS[identifier] = recent_attempts
    return len(recent_attempts) >= MAX_SUBMISSION_ATTEMPTS


def record_submission_attempt(identifier: str):
    """Records a token verification/submission attempt."""
    now = datetime.now(timezone.utc)
    if identifier not in SUBMISSION_ATTEMPTS:
        SUBMISSION_ATTEMPTS[identifier] = []
    SUBMISSION_ATTEMPTS[identifier].append(now)


def generate_reset_token() -> tuple[str, str]:
    """
    Generates a cryptographically secure, unpredictable 32-byte urlsafe reset token
    and returns (plaintext_token, sha256_hex_hash).
    """
    plaintext_token = secrets.token_urlsafe(32)
    token_hash = hash_reset_token(plaintext_token)
    return plaintext_token, token_hash


def hash_reset_token(token: str) -> str:
    """Hashes a plaintext reset token using SHA-256 for secure storage and comparison."""
    if not token or not isinstance(token, str):
        return ""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_password_reset_token(
    user: User,
    ip_address: str | None = None,
    user_agent: str | None = None,
    expires_in_minutes: int = 15
) -> tuple[PasswordResetToken, str]:
    """
    Creates a new single-use PasswordResetToken record for the given user.
    Stores only the SHA-256 hash in the database and returns (token_record, plaintext_token).
    """
    plaintext_token, token_hash = generate_reset_token()
    now = utc_now()
    expires_at = now + timedelta(minutes=expires_in_minutes)

    token_record = PasswordResetToken(
        user_id=user.id,
        token_hash=token_hash,
        created_at=now,
        expires_at=expires_at,
        is_revoked=False,
        ip_address=ip_address,
        user_agent=user_agent[:255] if user_agent else None
    )
    db.session.add(token_record)
    db.session.commit()

    return token_record, plaintext_token


def verify_password_reset_token(token_str: str) -> tuple[bool, str, PasswordResetToken | None]:
    """
    Verifies a plaintext reset token.
    Checks:
    - Token format
    - Presence in database by SHA-256 hash
    - Not revoked
    - Not already used
    - Not expired
    Returns (is_valid, error_message, token_record).
    """
    if not token_str or not isinstance(token_str, str) or len(token_str) < 16:
        return False, "This password reset link is invalid or malformed.", None

    token_hash = hash_reset_token(token_str)
    token_record = PasswordResetToken.query.filter_by(token_hash=token_hash).first()

    if not token_record:
        return False, "This password reset link is invalid or has expired.", None

    if token_record.is_revoked:
        return False, "This password reset link has been revoked. Please request a new one.", None

    if token_record.used_at is not None:
        return False, "This password reset link has already been used. Please request a new one.", None

    if token_record.is_expired:
        return False, "This password reset link has expired. Please request a new one.", None

    # Verify user exists and is active
    if not token_record.user or not token_record.user.is_active:
        return False, "The user account associated with this link is not active.", None

    return True, "", token_record


def consume_password_reset_token(token_record: PasswordResetToken, new_password: str) -> tuple[bool, str]:
    """
    Atomically validates password strength, sets the new password,
    marks the token as consumed, and revokes all other outstanding tokens for the user.
    """
    is_strong, err_msg = validate_password_strength(new_password)
    if not is_strong:
        return False, err_msg

    try:
        # Re-verify token state atomically
        token = db.session.get(PasswordResetToken, token_record.id)
        if not token or not token.is_valid:
            return False, "This password reset link is no longer valid or has already been used."

        user = db.session.get(User, token.user_id)
        if not user or not user.is_active:
            return False, "The user account associated with this token is not active."

        # Update password
        user.set_password(new_password)
        user.updated_at = utc_now()

        # Mark token as consumed
        token.used_at = utc_now()

        # Invalidate all other active reset tokens for this user
        PasswordResetToken.query.filter(
            PasswordResetToken.user_id == user.id,
            PasswordResetToken.id != token.id,
            PasswordResetToken.used_at == None,
            PasswordResetToken.is_revoked == False
        ).update({"is_revoked": True}, synchronize_session=False)

        db.session.commit()
        return True, "Your password has been successfully reset."
    except Exception as e:
        db.session.rollback()
        return False, f"An error occurred while resetting your password. Please try again."
