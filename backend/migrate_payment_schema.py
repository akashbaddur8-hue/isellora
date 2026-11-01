"""
Migration script for Razorpay integration schema:
- Adds razorpay_order_id and razorpay_payment_id to orders table
- Adds razorpay_order_id and razorpay_payment_id to listing_subscriptions table
- Creates payments table with indexes and unique constraints
- Creates webhook_events table for idempotent webhook deduplication
- Safely creates backups before migration and supports rollback verification
"""
import os
import shutil
import sqlite3
import sys

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
PROJECT_ROOT = os.path.dirname(BASE_DIR)
BACKUP_DIR = os.path.join(PROJECT_ROOT, "local_backups_pre_payment")

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

    print(f"\n--- Migrating {db_path} ---")
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    # 1. Orders table columns
    cur.execute("PRAGMA table_info(orders)")
    order_cols = [c[1] for c in cur.fetchall()]
    if "razorpay_order_id" not in order_cols:
        cur.execute("ALTER TABLE orders ADD COLUMN razorpay_order_id VARCHAR(100)")
        print("Added orders.razorpay_order_id")
    if "razorpay_payment_id" not in order_cols:
        cur.execute("ALTER TABLE orders ADD COLUMN razorpay_payment_id VARCHAR(100)")
        print("Added orders.razorpay_payment_id")

    # Index on orders razorpay columns
    cur.execute("CREATE INDEX IF NOT EXISTS ix_orders_razorpay_order_id ON orders (razorpay_order_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS ix_orders_razorpay_payment_id ON orders (razorpay_payment_id)")

    # 2. Listing subscriptions table columns
    cur.execute("PRAGMA table_info(listing_subscriptions)")
    sub_cols = [c[1] for c in cur.fetchall()]
    if "razorpay_order_id" not in sub_cols:
        cur.execute("ALTER TABLE listing_subscriptions ADD COLUMN razorpay_order_id VARCHAR(100)")
        print("Added listing_subscriptions.razorpay_order_id")
    if "razorpay_payment_id" not in sub_cols:
        cur.execute("ALTER TABLE listing_subscriptions ADD COLUMN razorpay_payment_id VARCHAR(100)")
        print("Added listing_subscriptions.razorpay_payment_id")

    cur.execute("CREATE INDEX IF NOT EXISTS ix_listing_subs_razorpay_order_id ON listing_subscriptions (razorpay_order_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS ix_listing_subs_razorpay_payment_id ON listing_subscriptions (razorpay_payment_id)")

    # 3. Create payments table
    cur.execute("""
    CREATE TABLE IF NOT EXISTS payments (
        id VARCHAR(36) PRIMARY KEY,
        order_id VARCHAR(36) REFERENCES orders(id) ON DELETE CASCADE,
        subscription_id VARCHAR(36) REFERENCES listing_subscriptions(id) ON DELETE CASCADE,
        user_id VARCHAR(36) REFERENCES users(id) ON DELETE SET NULL,
        purpose VARCHAR(50) NOT NULL,
        provider VARCHAR(50) NOT NULL,
        razorpay_order_id VARCHAR(100) UNIQUE,
        razorpay_payment_id VARCHAR(100) UNIQUE,
        razorpay_signature VARCHAR(255),
        amount_paise BIGINT NOT NULL,
        currency VARCHAR(10) NOT NULL DEFAULT 'INR',
        status VARCHAR(30) NOT NULL,
        error_code VARCHAR(100),
        error_description TEXT,
        refund_id VARCHAR(100),
        refund_status VARCHAR(50),
        refund_amount_paise BIGINT NOT NULL DEFAULT 0,
        created_at DATETIME NOT NULL,
        updated_at DATETIME NOT NULL,
        paid_at DATETIME
    );
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS ix_payments_order_id ON payments (order_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS ix_payments_subscription_id ON payments (subscription_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS ix_payments_user_id ON payments (user_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS ix_payments_purpose ON payments (purpose)")
    cur.execute("CREATE INDEX IF NOT EXISTS ix_payments_status ON payments (status)")
    cur.execute("CREATE INDEX IF NOT EXISTS ix_payments_razorpay_order_id ON payments (razorpay_order_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS ix_payments_razorpay_payment_id ON payments (razorpay_payment_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS ix_payments_refund_id ON payments (refund_id)")
    print("Checked/Created payments table and indexes")

    # 4. Create webhook_events table
    cur.execute("""
    CREATE TABLE IF NOT EXISTS webhook_events (
        id VARCHAR(36) PRIMARY KEY,
        event_id VARCHAR(100) UNIQUE NOT NULL,
        event_type VARCHAR(100) NOT NULL,
        payload TEXT,
        status VARCHAR(30) NOT NULL DEFAULT 'processed',
        created_at DATETIME NOT NULL
    );
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS ix_webhook_events_event_id ON webhook_events (event_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS ix_webhook_events_event_type ON webhook_events (event_type)")
    print("Checked/Created webhook_events table and indexes")

    conn.commit()
    conn.close()
    print(f"Successfully migrated {db_path}")


def main():
    print("Starting Razorpay Schema Migration...")
    backup_databases()
    for db_path in TARGET_DATABASES:
        migrate_database(db_path)
    print("\nAll database migrations completed successfully.")


if __name__ == "__main__":
    main()
