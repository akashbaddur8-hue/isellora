"""
Schema migration script for Secure Password Reset feature in iSellora:
- Creates password_reset_tokens table with user_id, token_hash, created_at, expires_at, used_at, is_revoked, ip_address, user_agent.
- Adds unique index on token_hash and non-unique index on user_id and expires_at.
- Safe, non-destructive, and idempotent: inspects sqlite_master before applying DDL.
"""
import os
import shutil
import sqlite3
import sys

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
PROJECT_ROOT = os.path.dirname(BASE_DIR)
BACKUP_DIR = os.path.join(PROJECT_ROOT, "local_backups_pre_reset_migration")

TARGET_DATABASES = [
    os.path.join(PROJECT_ROOT, "instance", "isellora.db"),
    os.path.join(BASE_DIR, "isellora.db")
]


def backup_databases():
    os.makedirs(BACKUP_DIR, exist_ok=True)
    for db_path in TARGET_DATABASES:
        if os.path.exists(db_path):
            filename = os.path.basename(db_path)
            parent = os.path.basename(os.path.dirname(db_path))
            backup_filename = f"{parent}_{filename}.bak"
            dest = os.path.join(BACKUP_DIR, backup_filename)
            shutil.copy2(db_path, dest)
            print(f"Backed up {db_path} -> {dest}")


def migrate_database(db_path: str):
    if not os.path.exists(db_path):
        print(f"Skipping {db_path} (does not exist).")
        return

    print(f"\n--- Checking/Migrating Password Reset Schema for {db_path} ---")
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    # Check if password_reset_tokens table exists
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='password_reset_tokens'")
    table_exists = cur.fetchone() is not None

    if not table_exists:
        cur.execute("""
            CREATE TABLE password_reset_tokens (
                id VARCHAR(36) PRIMARY KEY,
                user_id VARCHAR(36) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                token_hash VARCHAR(64) NOT NULL,
                created_at DATETIME NOT NULL,
                expires_at DATETIME NOT NULL,
                used_at DATETIME,
                is_revoked BOOLEAN NOT NULL DEFAULT 0,
                ip_address VARCHAR(50),
                user_agent VARCHAR(255)
            )
        """)
        print("  + Created table 'password_reset_tokens'")
    else:
        print("  * Table 'password_reset_tokens' already exists")

    # Indexes
    cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS ix_password_reset_tokens_token_hash ON password_reset_tokens (token_hash)")
    cur.execute("CREATE INDEX IF NOT EXISTS ix_password_reset_tokens_user_id ON password_reset_tokens (user_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS ix_password_reset_tokens_expires_at ON password_reset_tokens (expires_at)")
    print("  * Verified password_reset_tokens indexes")

    conn.commit()
    conn.close()
    print("Migration finished for:", db_path)


def main():
    print("=" * 60)
    print("iSellora Schema Migration: Secure Password Reset")
    print("=" * 60)
    backup_databases()
    for db_path in TARGET_DATABASES:
        migrate_database(db_path)
    print("\nAll database migrations completed successfully.")


if __name__ == "__main__":
    main()
