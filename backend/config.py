"""
Application configuration environments for iSellora.
"""
import os
from datetime import timedelta
from dotenv import load_dotenv

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
ROOT_DIR = os.path.abspath(os.path.join(BASE_DIR, ".."))
INSTANCE_DIR = os.path.join(ROOT_DIR, "instance")

# Priority loading: load backend/.env first (authoritative), then root .env
backend_env_path = os.path.join(BASE_DIR, ".env")
root_env_path = os.path.join(ROOT_DIR, ".env")
if os.path.exists(backend_env_path):
    load_dotenv(backend_env_path, override=True)
if os.path.exists(root_env_path):
    load_dotenv(root_env_path, override=False)


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "isellora-development-fallback-secret-key-32-chars-min!!")
    
    # Database
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    
    # Uploads
    MAX_CONTENT_LENGTH = int(os.environ.get("MAX_CONTENT_LENGTH", 50 * 1024 * 1024))
    UPLOAD_FOLDER = os.path.join(BASE_DIR, "static", "uploads")
    ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "webp"}
    
    # Session & Security
    PERMANENT_SESSION_LIFETIME = timedelta(days=7)
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = os.environ.get("SESSION_COOKIE_SECURE", "False").lower() in ("true", "1", "t")
    
    # Admin
    ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "admin@isellora.local").strip().lower()

    # WTForms / CSRF
    WTF_CSRF_ENABLED = True
    WTF_CSRF_TIME_LIMIT = 3600 * 24  # 24 hours

    # Razorpay Payment Gateway Configuration (Test Mode Only)
    RAZORPAY_KEY_ID = (os.environ.get("RAZORPAY_KEY_ID") or "").strip()
    RAZORPAY_KEY_SECRET = (os.environ.get("RAZORPAY_KEY_SECRET") or "").strip()
    RAZORPAY_WEBHOOK_SECRET = (os.environ.get("RAZORPAY_WEBHOOK_SECRET") or "").strip()
    RAZORPAY_CURRENCY = "INR"

    # Email / SMTP Configuration
    MAIL_SERVER = (os.environ.get("MAIL_SERVER") or "").strip()
    MAIL_PORT = int(os.environ.get("MAIL_PORT", 587))
    MAIL_USE_TLS = os.environ.get("MAIL_USE_TLS", "True").lower() in ("true", "1", "t")
    MAIL_USE_SSL = os.environ.get("MAIL_USE_SSL", "False").lower() in ("true", "1", "t")
    MAIL_USERNAME = (os.environ.get("MAIL_USERNAME") or "").strip()
    MAIL_PASSWORD = (os.environ.get("MAIL_PASSWORD") or "").strip()
    MAIL_DEFAULT_SENDER = (os.environ.get("MAIL_DEFAULT_SENDER") or os.environ.get("MAIL_USERNAME") or "no-reply@isellora.local").strip()

    # Trusted Application Base URL (for email links, etc.)
    APP_BASE_URL = (os.environ.get("APP_BASE_URL") or "").strip().rstrip("/")

    # Password Reset Expiry (in minutes)
    PASSWORD_RESET_EXPIRY_MINUTES = int(os.environ.get("PASSWORD_RESET_EXPIRY_MINUTES", 15))

    @classmethod
    def validate_email_config(cls):
        """
        Validates email configuration status without exposing credentials.
        """
        mail_server = (os.environ.get("MAIL_SERVER") or "").strip()
        mail_username = (os.environ.get("MAIL_USERNAME") or "").strip()
        mail_password = (os.environ.get("MAIL_PASSWORD") or "").strip()

        external_domains = ("gmail", "google", "outlook", "office365", "sendgrid", "mailgun", "aws", "ses")
        is_external = any(domain in mail_server.lower() for domain in external_domains)

        has_auth = bool(mail_username and mail_password)
        is_placeholder = mail_server in ("smtp.example.com", "placeholder", "none", "")

        configured = bool(mail_server and not is_placeholder and (has_auth if (is_external or mail_username) else True))

        missing = []
        if is_placeholder:
            missing.append("MAIL_SERVER")
        elif (is_external or mail_username) and not has_auth:
            if not mail_username:
                missing.append("MAIL_USERNAME")
            if not mail_password:
                missing.append("MAIL_PASSWORD")

        return {
            "configured": configured,
            "mail_server": mail_server if not is_placeholder else "Not Set",
            "mail_port": int(os.environ.get("MAIL_PORT", 587)),
            "use_tls": os.environ.get("MAIL_USE_TLS", "True").lower() in ("true", "1", "t"),
            "has_auth": has_auth,
            "missing": missing,
            "default_sender": (os.environ.get("MAIL_DEFAULT_SENDER") or mail_username or "no-reply@isellora.local").strip()
        }

    @classmethod
    def validate_razorpay(cls):
        """
        Validates Razorpay Test Mode configuration at startup without printing secret values.
        Enforces Test Mode strictly and checks for missing required environment variables.
        """
        key_id = (os.environ.get("RAZORPAY_KEY_ID") or "").strip()
        key_secret = (os.environ.get("RAZORPAY_KEY_SECRET") or "").strip()
        webhook_secret = (os.environ.get("RAZORPAY_WEBHOOK_SECRET") or "").strip()

        # Strict constraint: Never allow live keys in this build
        if key_id.startswith("rzp_live"):
            raise RuntimeError(
                "CRITICAL SECURITY RESTRICTION: Live Razorpay key detected. "
                "Only Razorpay Test Mode ('rzp_test_...') is permitted. "
                "Please replace with a Test Mode key in backend/.env."
            )

        missing = []
        if not key_id or key_id == "rzp_test_placeholder_key_id":
            missing.append("RAZORPAY_KEY_ID")
        if not key_secret or key_secret == "placeholder_razorpay_secret_key":
            missing.append("RAZORPAY_KEY_SECRET")

        return {
            "configured": len(missing) == 0,
            "missing": missing,
            "has_webhook_secret": bool(webhook_secret and webhook_secret != "placeholder_razorpay_webhook_secret"),
            "masked_key_id": (key_id[:8] + "..." + key_id[-4:]) if len(key_id) > 12 else (key_id if key_id else "Not Set")
        }



AUTHORITATIVE_DB_PATH = os.path.join(INSTANCE_DIR, "isellora.db")
os.makedirs(INSTANCE_DIR, exist_ok=True)


class DevelopmentConfig(Config):
    DEBUG = True
    APP_BASE_URL = os.environ.get("APP_BASE_URL", "http://127.0.0.1:5000").rstrip("/")
    default_db_path = AUTHORITATIVE_DB_PATH
    _env_db = (os.environ.get("DATABASE_URL") or "").strip()
    if not _env_db or _env_db in ("sqlite:///isellora.db", "sqlite:///./isellora.db", "sqlite:///instance/isellora.db"):
        SQLALCHEMY_DATABASE_URI = f"sqlite:///{AUTHORITATIVE_DB_PATH}"
    else:
        SQLALCHEMY_DATABASE_URI = _env_db


class TestingConfig(Config):
    TESTING = True
    DEBUG = False
    APP_BASE_URL = os.environ.get("APP_BASE_URL", "http://localhost").rstrip("/")
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    WTF_CSRF_ENABLED = False  # Enabled selectively in CSRF-specific tests
    SECRET_KEY = "test-secret-key-for-unit-and-integration-tests"
    RAZORPAY_KEY_ID = "rzp_test_mock_key_id_123"
    RAZORPAY_KEY_SECRET = "mock_secret_key_456"
    RAZORPAY_WEBHOOK_SECRET = "mock_webhook_secret_789"


KNOWN_INSECURE_SECRETS = {
    "isellora-development-fallback-secret-key-32-chars-min!!",
    "change-this-to-a-secure-random-64-character-string-in-production",
    "test-secret-key-for-unit-and-integration-tests",
    "changeme",
    "secret",
    "password"
}


class ProductionConfig(Config):
    DEBUG = False
    SESSION_COOKIE_SECURE = True
    SECRET_KEY = os.environ.get("SECRET_KEY")
    SQLALCHEMY_DATABASE_URI = os.environ.get("DATABASE_URL")

    @classmethod
    def validate_production(cls):
        secret = (os.environ.get("SECRET_KEY") or "").strip()
        if not secret or secret in KNOWN_INSECURE_SECRETS or len(secret) < 32:
            raise RuntimeError(
                "CRITICAL SECURITY CONFIGURATION ERROR: Production requires a secure, non-placeholder "
                "SECRET_KEY environment variable of at least 32 characters."
            )
        db_url = (os.environ.get("DATABASE_URL") or "").strip()
        if not db_url:
            raise RuntimeError(
                "CRITICAL CONFIGURATION ERROR: Production requires the DATABASE_URL environment variable to be configured."
            )


config_by_name = {
    "development": DevelopmentConfig,
    "testing": TestingConfig,
    "production": ProductionConfig,
    "default": DevelopmentConfig
}
