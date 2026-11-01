"""
Safe development cleanup utility for legacy demo/seed listings in iSellora.
Isolates legacy seed records (originating from legacy JSON migration) while
strictly preserving all genuine seller submissions (e.g. Rahul, Chetan, etc.).

Usage:
    # Dry run (default - inspects and reports without modifying DB):
    python backend/cleanup_demo_listings.py --dry-run

    # Execute safe removal:
    python backend/cleanup_demo_listings.py --force
"""
import os
import sys
import json
import argparse

# Ensure project root is in sys.path
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from backend.app import create_app
from backend.database import db
from backend.models import (
    Listing, ListingImage, Order, Wishlist,
    ListingSubscription, ReportDispute, Review,
    AuditEvent, utc_now
)


def load_legacy_seed_ids():
    """Extracts known seed listing IDs from backend/listings.json and root listings.json."""
    seed_ids = set()
    candidate_paths = [
        os.path.join(PROJECT_ROOT, "backend", "listings.json"),
        os.path.join(PROJECT_ROOT, "listings.json")
    ]
    for p in candidate_paths:
        if os.path.exists(p):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, list):
                        for item in data:
                            if isinstance(item, dict) and item.get("id"):
                                seed_ids.add(str(item["id"]).strip())
            except Exception as e:
                print(f"[WARN] Failed to read {p}: {e}")
    return seed_ids


def identify_listings(seed_ids):
    """
    Separates database listings into legacy seed listings and genuine seller submissions.
    """
    all_listings = Listing.query.all()
    legacy_listings = []
    genuine_listings = []

    for listing in all_listings:
        # Match legacy listing either by known seed ID or demo seller Akash Baddur
        is_seed = (
            listing.id in seed_ids or
            (listing.seller_name and listing.seller_name.strip().lower() == "akash baddur" and
             listing.brand.lower() in ("apple", "oneplus"))
        )
        if is_seed:
            legacy_listings.append(listing)
        else:
            genuine_listings.append(listing)

    return legacy_listings, genuine_listings


def run_cleanup(dry_run=True, mode="purge"):
    app = create_app("development")

    with app.app_context():
        seed_ids = load_legacy_seed_ids()
        legacy_listings, genuine_listings = identify_listings(seed_ids)

        print("\n" + "=" * 70)
        print(" iSellora - Legacy Seed Listings Cleanup Utility")
        print("=" * 70)
        print(f" Mode: {'DRY RUN (no database changes)' if dry_run else 'LIVE EXECUTION (--force)'}")
        print(f" Cleanup Strategy: {mode.upper()}")
        print(f" Authoritative Database: {app.config.get('SQLALCHEMY_DATABASE_URI')}")
        print("-" * 70)

        print(f"\n[+] Total Listings in Database: {len(legacy_listings) + len(genuine_listings)}")
        print(f"[!] Identified Legacy Seed Listings: {len(legacy_listings)}")
        print(f"[OK] Preserved Genuine Seller Listings: {len(genuine_listings)}")

        print("\n--- PRESERVED GENUINE LISTINGS (WILL NOT BE MODIFIED) ---")
        if genuine_listings:
            for g in genuine_listings:
                print(f"  * ID: {g.id[:12]}... | {g.brand} {g.model} | Seller: {g.seller_name} | Status: {g.approval_status} | Created: {g.created_at}")
        else:
            print("  (None found)")

        print("\n--- IDENTIFIED LEGACY SEED LISTINGS ---")
        if legacy_listings:
            for l in legacy_listings:
                print(f"  * ID: {l.id} | {l.brand} {l.model} | Seller: {l.seller_name} | Status: {l.approval_status}")
        else:
            print("  (None found - database is already free of legacy demo listings)")
            print("\n" + "=" * 70)
            return 0

        legacy_ids = [l.id for l in legacy_listings]

        # Audit relations
        images_count = ListingImage.query.filter(ListingImage.listing_id.in_(legacy_ids)).count()
        wishlists_count = Wishlist.query.filter(Wishlist.listing_id.in_(legacy_ids)).count()
        orders_affected = Order.query.filter(Order.listing_id.in_(legacy_ids)).all()
        subs_count = ListingSubscription.query.filter(ListingSubscription.listing_id.in_(legacy_ids)).count()

        print("\n--- DEPENDENT RELATIONS SUMMARY ---")
        print(f"  * Associated Images: {images_count}")
        print(f"  * Associated Wishlist Entries: {wishlists_count}")
        print(f"  * Associated Subscriptions: {subs_count}")
        print(f"  * Orders Referencing Listings: {len(orders_affected)}")
        if orders_affected:
            for o in orders_affected:
                print(f"    - Order {o.id[:8]} ({o.brand} {o.model}) -> listing_id will be safely SET NULL to preserve order history")

        if dry_run:
            print("\n" + "-" * 70)
            print(" [DRY RUN COMPLETE] No records were modified.")
            print(" To perform actual cleanup, re-run with: python backend/cleanup_demo_listings.py --force")
            print("=" * 70 + "\n")
            return 0

        # LIVE EXECUTION
        try:
            # 1. Nullify listing_id on orders referencing these listings so order history remains intact
            for order in orders_affected:
                order.listing_id = None
            db.session.flush()

            # 2. Delete wishlists
            Wishlist.query.filter(Wishlist.listing_id.in_(legacy_ids)).delete(synchronize_session=False)

            # 3. Delete subscriptions if any
            ListingSubscription.query.filter(ListingSubscription.listing_id.in_(legacy_ids)).delete(synchronize_session=False)

            # 4. Delete images
            ListingImage.query.filter(ListingImage.listing_id.in_(legacy_ids)).delete(synchronize_session=False)

            # 5. Delete listings
            for l in legacy_listings:
                db.session.delete(l)

            # 6. Record audit event
            audit = AuditEvent(
                user_id="system_cli",
                action="DEMO_LISTINGS_CLEANUP",
                target_type="listing",
                target_id=f"count:{len(legacy_ids)}",
                details=json.dumps({
                    "removed_count": len(legacy_ids),
                    "removed_ids": legacy_ids,
                    "preserved_count": len(genuine_listings),
                    "orders_disassociated": len(orders_affected)
                }),
                created_at=utc_now()
            )
            db.session.add(audit)
            db.session.commit()

            print("\n" + "-" * 70)
            print(f" [SUCCESS] Successfully cleaned {len(legacy_listings)} legacy seed listings.")
            print(f" [SUCCESS] {len(genuine_listings)} genuine seller listings remain active and untouched.")
            print("=" * 70 + "\n")
            return 0

        except Exception as e:
            db.session.rollback()
            print(f"\n[ERROR] Cleanup failed and was rolled back: {e}", file=sys.stderr)
            return 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Clean legacy seed listings from iSellora database.")
    parser.add_argument("--dry-run", action="store_true", default=False, help="Perform dry run without modifying database (default if --force omitted)")
    parser.add_argument("--force", action="store_true", default=False, help="Perform live deletion of legacy seed listings")
    parser.add_argument("--mode", choices=["purge", "delist"], default="purge", help="Action strategy (default: purge)")

    args = parser.parse_args()
    dry_run = not args.force

    sys.exit(run_cleanup(dry_run=dry_run, mode=args.mode))
