"""
iSellora Administrator Setup Tool
Use this script to create a new administrator account or promote an existing user.

Usage:
    python create_admin.py
    python create_admin.py --email admin@isellora.com --password YourPassword123 --name "Admin Name"
"""
import sys
import os
import argparse
import getpass

# Ensure project root is in sys.path
PROJECT_ROOT = os.path.abspath(os.path.dirname(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from backend.app import create_app
from backend.database import db
from backend.models import User, AuditEvent, utc_now
from backend.services.auth_service import normalize_email, validate_password_strength, validate_phone


def main(app=None):
    parser = argparse.ArgumentParser(description="Create or promote an administrator for iSellora.")
    parser.add_argument("--email", help="Administrator email address")
    parser.add_argument("--password", help="Administrator password")
    parser.add_argument("--name", default=None, help="Administrator display name")
    parser.add_argument("--phone", default=None, help="Administrator contact phone number (10 digits)")
    args = parser.parse_args()

    email = args.email
    if not email:
        try:
            email = input("Enter administrator email: ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nAborted.")
            sys.exit(1)

    email = normalize_email(email)
    if not email or "@" not in email:
        print("[ERROR] Invalid email address format.")
        sys.exit(1)

    password = args.password
    if not password:
        try:
            password = getpass.getpass("Enter administrator password (min 8 chars): ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nAborted.")
            sys.exit(1)

    valid, err_msg = validate_password_strength(password)
    if not valid:
        print(f"[ERROR] Password does not meet security requirements: {err_msg}")
        sys.exit(1)

    name = args.name
    if not name:
        try:
            custom_name = input("Enter administrator display name [Administrator]: ").strip()
            name = custom_name if custom_name else "Administrator"
        except (KeyboardInterrupt, EOFError):
            name = "Administrator"

    phone = args.phone
    if phone:
        phone = str(phone).strip()
        if not validate_phone(phone):
            print("[WARN] Provided phone does not match standard 10-digit Indian format. Using anyway.")

    if app is None:
        config_name = os.environ.get("FLASK_CONFIG") or os.environ.get("FLASK_ENV") or "development"
        app = create_app(config_name)
    with app.app_context():
        user = User.query.filter_by(email=email).first()
        if user:
            user.role = "admin"
            user.set_password(password)
            user.is_active = True
            if name and name != "Administrator":
                user.name = name
            if phone:
                user.phone = phone
            audit = AuditEvent(
                user_id=user.id,
                action="ADMIN_ROLE_PROVISIONED",
                target_type="user",
                target_id=user.id,
                details=f"User role updated to admin via CLI tool.",
                created_at=utc_now()
            )
            db.session.add(audit)
            db.session.commit()
            print(f"\n[SUCCESS] Existing user '{email}' has been promoted to administrator!")
        else:
            # For new accounts, phone number is mandatory in the database schema
            if not phone:
                try:
                    custom_phone = input("Enter administrator phone number [9876543210]: ").strip()
                    phone = custom_phone if custom_phone else "9876543210"
                except (KeyboardInterrupt, EOFError):
                    phone = "9876543210"

            if not validate_phone(phone):
                # Fallback to safe default if user provided invalid string
                phone = "9876543210"

            user = User(
                name=name,
                email=email,
                phone=phone,
                role="admin",
                is_active=True
            )
            user.set_password(password)
            db.session.add(user)
            db.session.flush()
            audit = AuditEvent(
                user_id=user.id,
                action="ADMIN_ACCOUNT_CREATED",
                target_type="user",
                target_id=user.id,
                details=f"New administrator account created via CLI tool.",
                created_at=utc_now()
            )
            db.session.add(audit)
            db.session.commit()
            print(f"\n[SUCCESS] New administrator account '{email}' created successfully!")

        print("------------------------------------------------------------")
        print("You can now sign in to the Admin Console:")
        print("  URL:      http://127.0.0.1:5000/admin/login")
        print(f"  Email:    {email}")
        print("------------------------------------------------------------")


if __name__ == "__main__":
    main()
