"""
Application configuration environments for iSellora.
"""
import os
from datetime import timedelta
from dotenv import load_dotenv

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
# Load environment from backend/.env or root .env
load_dotenv(os.path.join(BASE_DIR, ".env"))
load_dotenv(os.path.join(os.path.dirname(BASE_DIR), ".env"))


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
    ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "akashbaddur2007@gmail.com").strip().lower()

    # WTForms / CSRF
    WTF_CSRF_ENABLED = True
    WTF_CSRF_TIME_LIMIT = 3600 * 24  # 24 hours


class DevelopmentConfig(Config):
    DEBUG = True
    db_path = os.path.join(BASE_DIR, "isellora.db")
    SQLALCHEMY_DATABASE_URI = os.environ.get("DATABASE_URL", f"sqlite:///{db_path}")


class TestingConfig(Config):
    TESTING = True
    DEBUG = False
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    WTF_CSRF_ENABLED = False  # Enabled selectively in CSRF-specific tests
    SECRET_KEY = "test-secret-key-for-unit-and-integration-tests"


class ProductionConfig(Config):
    DEBUG = False
    SQLALCHEMY_DATABASE_URI = os.environ.get("DATABASE_URL")
    SESSION_COOKIE_SECURE = True


config_by_name = {
    "development": DevelopmentConfig,
    "testing": TestingConfig,
    "production": ProductionConfig,
    "default": DevelopmentConfig
}
