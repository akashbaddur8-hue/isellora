"""
Schema migration script for User Profile enhancements in iSellora:
- Adds username (VARCHAR 50, unique index) to users table
- Adds avatar_path (VARCHAR 255) to users table
- Adds city (VARCHAR 100) to users table
- Adds state (VARCHAR 100) to users table
- Adds bio (TEXT) to users table
- Adds is_verified (BOOLEAN default 0) to users table
Safe and idempotent: inspects PRAGMA table_info before applying ALTER TABLE.
"""
import os
import sqlite3
import sys

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
PROJECT_ROOT = os.path.dirname(BASE_DIR)

TARGET_DATABASES = [
    os.path.join(PROJECT_ROOT, "instance", "isellora.db"),
    os.path.join(BASE_DIR, "isellora.db")
]


def migrate_database(db_path: str):
    if not os.path.exists(db_path):
        return

    print(f"\n--- Checking/Migrating User Profile Schema for {db_path} ---")
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    cur.execute("PRAGMA table_info(users)")
    existing_cols = {c[1] for c in cur.fetchall()}

    columns_to_add = [
        ("username", "VARCHAR(50)"),
        ("avatar_path", "VARCHAR(255)"),
        ("city", "VARCHAR(100)"),
        ("state", "VARCHAR(100)"),
        ("bio", "TEXT"),
        ("is_verified", "BOOLEAN DEFAULT 0 NOT NULL")
    ]

    for col_name, col_type in columns_to_add:
        if col_name not in existing_cols:
            cur.execute(f"ALTER TABLE users ADD COLUMN {col_name} {col_type}")
            print(f"  + Added column users.{col_name} ({col_type})")
        else:
            print(f"  * Column users.{col_name} already present")

    # Add partial unique index for username where username is not null
    try:
        cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS ix_users_username ON users (username) WHERE username IS NOT NULL")
        print("  * Verified ix_users_username partial index")
    except Exception as e:
        print(f"  [WARN] Index error: {e}")

    conn.commit()
    conn.close()
    print("Migration finished for:", db_path)


def run_all():
    for db_path in TARGET_DATABASES:
        migrate_database(db_path)


if __name__ == "__main__":
    run_all()
