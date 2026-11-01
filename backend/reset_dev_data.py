"""
Development-Only Data Reset Script for iSellora
Safely purges development database records, uploaded user media, and demo data,
while preserving table schemas, indexes, static branding, and application code.

Usage:
    python backend/reset_dev_data.py --dry-run
    python backend/reset_dev_data.py --confirm
"""
import os
import sys
import shutil
import sqlite3

# Ensure project root is in sys.path
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
PROJECT_ROOT = os.path.dirname(BASE_DIR)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from backend.app import create_app
from backend.database import db
from backend.models import (
    User, Listing, ListingImage, ListingSubscription,
    Order, OrderStatusHistory, Payment, WebhookEvent,
    Notification, AuditEvent, Wishlist, Review,
    SupportTicket, ReportDispute
)

TABLE_ORDER = [
    WebhookEvent,
    ReportDispute,
    Review,
    SupportTicket,
    Wishlist,
    Notification,
    AuditEvent,
    Payment,
    OrderStatusHistory,
    Order,
    ListingSubscription,
    ListingImage,
    Listing,
    User,
]


def reset_development_data(confirm=False):
    app = create_app()
    env = os.environ.get("FLASK_ENV", "development").lower()
    if env == "production":
        print("[ABORT] Refusing to run reset in PRODUCTION environment!", file=sys.stderr)
        sys.exit(1)

    print("=" * 60)
    print("iSellora Development Data Reset")
    print(f"Target Database: {app.config['SQLALCHEMY_DATABASE_URI']}")
    print(f"Mode: {'EXECUTE RESET' if confirm else 'DRY RUN (Preview)'}")
    print("=" * 60)

    with app.app_context():
        # 1. Inventory of database records
        print("\n[1] Database Records Inventory:")
        deletion_counts = {}
        for model in reversed(TABLE_ORDER):
            count = model.query.count()
            table_name = model.__tablename__
            deletion_counts[table_name] = count
            print(f"  - {table_name:<26}: {count:>4} rows")

        # 2. Uploads Inventory
        uploads_dir = os.path.join(BASE_DIR, "static", "uploads")
        upload_files = []
        if os.path.exists(uploads_dir):
            for root, dirs, files in os.walk(uploads_dir):
                for f in files:
                    if f == ".gitkeep":
                        continue
                    upload_files.append(os.path.join(root, f))
        print(f"\n[2] Uploaded Media Files: {len(upload_files)} files found")

        # 3. Demo JSON Files Inventory
        demo_files = [
            os.path.join(BASE_DIR, "listings.json"),
            os.path.join(BASE_DIR, "notifications.json"),
            os.path.join(BASE_DIR, "orders.json"),
            os.path.join(BASE_DIR, "users.json"),
        ]
        print(f"\n[3] Demo JSON Files: {len(demo_files)} files found")

        if not confirm:
            print("\n" + "-" * 60)
            print("DRY RUN COMPLETE. No data was modified.")
            print("To execute this reset, pass the --confirm flag:")
            print("    python backend/reset_dev_data.py --confirm")
            print("-" * 60)
            return

        # --- EXECUTE RESET ---
        print("\nExecuting Reset Operations...")

        # Purge Database Records in proper foreign key order
        for model in TABLE_ORDER:
            deleted = model.query.delete()
            print(f"  Deleted {deleted} rows from {model.__tablename__}")
        db.session.commit()

        # Vacuum database to reclaim space
        try:
            with db.engine.connect() as conn:
                conn.execute(db.text("VACUUM;"))
            print("  Reclaimed database storage via VACUUM.")
        except Exception as ve:
            print(f"  Note on VACUUM: {ve}")

        # Purge Uploaded User Media (preserve .gitkeep and folders)
        purged_uploads = 0
        for fpath in upload_files:
            try:
                os.remove(fpath)
                purged_uploads += 1
            except Exception as e:
                print(f"  Warning removing {fpath}: {e}")
        print(f"  Removed {purged_uploads} uploaded media files.")

        # Ensure upload directories still exist with .gitkeep
        os.makedirs(uploads_dir, exist_ok=True)
        avatars_dir = os.path.join(uploads_dir, "avatars")
        os.makedirs(avatars_dir, exist_ok=True)
        for keep_dir in [uploads_dir, avatars_dir]:
            keep_file = os.path.join(keep_dir, ".gitkeep")
            if not os.path.exists(keep_file):
                with open(keep_file, "w") as kf:
                    kf.write("")

        # Neutralize demo JSON files to empty arrays []
        for dpath in demo_files:
            if os.path.exists(dpath):
                with open(dpath, "w", encoding="utf-8") as df:
                    df.write("[]\n")
                print(f"  Neutralized {os.path.basename(dpath)} to empty list [].")

        # Remove redundant legacy DBs if present to avoid confusion
        legacy_db = os.path.join(BASE_DIR, "isellora.db")
        if os.path.exists(legacy_db):
            try:
                os.remove(legacy_db)
                print(f"  Removed obsolete legacy DB: {legacy_db}")
            except Exception as le:
                print(f"  Note: {le}")

        backend_instance_db = os.path.join(BASE_DIR, "instance", "isellora.db")
        if os.path.exists(backend_instance_db):
            try:
                os.remove(backend_instance_db)
                print(f"  Removed obsolete duplicate DB: {backend_instance_db}")
            except Exception as bie:
                print(f"  Note: {bie}")

        print("\n" + "=" * 60)
        print("RESET COMPLETE: Development environment is now in a pristine state.")
        print("=" * 60)


if __name__ == "__main__":
    confirm_flag = "--confirm" in sys.argv
    reset_development_data(confirm=confirm_flag)
