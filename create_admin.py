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
from backend.models import User
from backend.services.auth_service import normalize_email, validate_password_strength


def main():
    parser = argparse.ArgumentParser(description="Create or promote an administrator for iSellora.")
    parser.add_argument("--email", help="Administrator email address")
    parser.add_argument("--password", help="Administrator password")
    parser.add_argument("--name", default="Administrator", help="Administrator display name")
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
    if not args.email and not args.name:
        try:
            custom_name = input(f"Enter administrator display name [{name}]: ").strip()
            if custom_name:
                name = custom_name
        except (KeyboardInterrupt, EOFError):
            pass

    app = create_app("development")
    with app.app_context():
        user = User.query.filter_by(email=email).first()
        if user:
            user.role = "admin"
            user.set_password(password)
            user.is_active = True
            if name and name != "Administrator":
                user.name = name
            db.session.commit()
            print(f"\n[SUCCESS] Existing user '{email}' has been promoted to administrator!")
        else:
            user = User(
                name=name,
                email=email,
                role="admin",
                is_active=True
            )
            user.set_password(password)
            db.session.add(user)
            db.session.commit()
            print(f"\n[SUCCESS] New administrator account '{email}' created successfully!")

        print("------------------------------------------------------------")
        print("You can now sign in to the Admin Console:")
        print("  URL:      http://127.0.0.1:5000/admin/login")
        print(f"  Email:    {email}")
        print("------------------------------------------------------------")


if __name__ == "__main__":
    main()
