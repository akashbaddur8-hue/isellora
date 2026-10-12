"""
Safe and idempotent migration script from legacy JSON files into the SQLite/PostgreSQL relational database.
Preserves all legacy data, detects malformed records, normalizes prices to integer paise,
and builds relational integrity.
"""
import os
import sys
import json
import uuid
from datetime import datetime, timezone

# Ensure project root is in sys.path
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
PROJECT_ROOT = os.path.dirname(BASE_DIR)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from backend.database import db
from backend.models import (
    User, Listing, ListingImage, Order, OrderStatusHistory,
    Notification, utc_now
)
from backend.config import config_by_name
from flask import Flask


def parse_price_to_paise(price_val):
    """Parses a string or numeric price in Rupees into integer Paise."""
    if not price_val:
        return 0
    clean = str(price_val).replace(",", "").replace("₹", "").strip()
    try:
        rupees = float(clean)
        return int(round(rupees * 100))
    except (ValueError, TypeError):
        return 0


def parse_timestamp(ts_str):
    """Parses ISO timestamp string or returns current UTC time."""
    if not ts_str:
        return utc_now()
    try:
        # Handle trailing Z or offsets
        clean_ts = ts_str.replace("Z", "+00:00")
        return datetime.fromisoformat(clean_ts)
    except Exception:
        return utc_now()


def load_json_file(file_path):
    if not os.path.exists(file_path):
        return []
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except Exception as e:
        print(f"[WARN] Error loading {file_path}: {e}")
        return []


def run_migration(app=None, dry_run=False):
    """
    Executes the migration inside the Flask application context.
    Returns migration report dict.
    """
    if app is None:
        app = Flask(__name__)
        config_name = os.environ.get("FLASK_ENV", "development")
        app.config.from_object(config_by_name.get(config_name, config_by_name["development"]))
        db.init_app(app)

    report = {
        "users": {"imported": 0, "existing": 0, "errors": 0},
        "listings": {"imported": 0, "existing": 0, "errors": 0},
        "listing_images": {"imported": 0, "existing": 0},
        "orders": {"imported": 0, "existing": 0, "errors": 0},
        "notifications": {"imported": 0, "existing": 0, "errors": 0},
        "dry_run": dry_run
    }

    with app.app_context():
        # Ensure tables exist
        db.create_all()

        # Paths
        users_file = os.path.join(BASE_DIR, "users.json")
        backend_listings_file = os.path.join(BASE_DIR, "listings.json")
        root_listings_file = os.path.join(PROJECT_ROOT, "listings.json")
        orders_file = os.path.join(BASE_DIR, "orders.json")
        notifications_file = os.path.join(BASE_DIR, "notifications.json")

        admin_email = app.config.get("ADMIN_EMAIL", "").strip().lower()

        # ---------------------------------------------------------------------
        # 1. MIGRATE USERS
        # ---------------------------------------------------------------------
        raw_users = load_json_file(users_file)
        user_phone_map = {}
        for u in raw_users:
            u_id = u.get("id") or str(uuid.uuid4())
            email = str(u.get("email", "")).strip().lower()
            name = str(u.get("name", "Unknown User")).strip()
            phone = str(u.get("phone", "")).strip()
            password_hash = u.get("password", "")
            
            if not email or "@" not in email:
                report["users"]["errors"] += 1
                continue

            user_phone_map[phone] = u_id

            existing = User.query.filter((User.id == u_id) | (User.email == email)).first()
            if existing:
                report["users"]["existing"] += 1
                user_phone_map[phone] = existing.id
                # Ensure admin role if matches admin_email or legacy admin
                if (email == admin_email or email == "akashbaddur2007@gmail.com") and existing.role != "admin":
                    existing.role = "admin"
                continue

            role = "admin" if (email == admin_email or email == "akashbaddur2007@gmail.com") else "user"
            new_user = User(
                id=u_id,
                name=name,
                email=email,
                phone=phone,
                password_hash=password_hash,
                role=role,
                is_active=True,
                created_at=utc_now(),
                updated_at=utc_now()
            )
            db.session.add(new_user)
            report["users"]["imported"] += 1

        if not dry_run:
            db.session.commit()

        # ---------------------------------------------------------------------
        # 2. MIGRATE LISTINGS (Combining backend/listings.json and root listings.json)
        # ---------------------------------------------------------------------
        raw_listings = load_json_file(backend_listings_file)
        # Also check root listings.json for any missing items
        root_raw_listings = load_json_file(root_listings_file)
        seen_ids = set()
        combined_listings = []

        for item in raw_listings + root_raw_listings:
            lid = item.get("id")
            if lid and lid not in seen_ids:
                seen_ids.add(lid)
                combined_listings.append(item)

        for l in combined_listings:
            l_id = l.get("id") or str(uuid.uuid4())
            existing = db.session.get(Listing, l_id)
            if existing:
                report["listings"]["existing"] += 1
                continue

            seller_id = l.get("seller_id")
            seller_phone = str(l.get("seller_phone") or l.get("phone") or "").strip()
            seller_name = str(l.get("seller_name", "")).strip()

            # Correlate seller_id by phone if missing
            if not seller_id and seller_phone in user_phone_map:
                seller_id = user_phone_map[seller_phone]

            price_paise = parse_price_to_paise(l.get("price", "0"))
            battery_val = None
            if l.get("battery"):
                try:
                    b_int = int(str(l["battery"]).strip())
                    if 1 <= b_int <= 100:
                        battery_val = b_int
                except ValueError:
                    pass

            created_at = parse_timestamp(l.get("created_at"))
            reviewed_at = parse_timestamp(l.get("reviewed_at")) if l.get("reviewed_at") else None
            approval_status = str(l.get("approval_status", "approved")).strip().lower()

            listing = Listing(
                id=l_id,
                seller_id=seller_id,
                brand=str(l.get("brand", "Unknown")).strip(),
                model=str(l.get("model", "Phone")).strip(),
                price_paise=price_paise,
                storage=str(l.get("storage", "128GB")).strip(),
                ram=str(l.get("ram", "")).strip() or None,
                color=str(l.get("color", "")).strip() or None,
                battery_health=battery_val,
                condition=str(l.get("condition", "Good")).strip(),
                description=str(l.get("description", "")).strip(),
                screen_damage=str(l.get("screen_damage", "No Damage")).strip(),
                body_damage=str(l.get("body_damage", "No Damage")).strip(),
                repair_history=str(l.get("repair_history", "Never Repaired")).strip(),
                original_display=str(l.get("original_display", "Yes")).strip(),
                accessories=str(l.get("accessories", "Phone Only")).strip(),
                listing_type=str(l.get("listing_type", "free")).strip(),
                approval_status=approval_status,
                inventory_status="available",  # will adjust if ordered
                seller_name=seller_name,
                seller_phone=seller_phone,
                created_at=created_at,
                updated_at=parse_timestamp(l.get("updated_at")) if l.get("updated_at") else created_at,
                reviewed_at=reviewed_at
            )
            db.session.add(listing)
            report["listings"]["imported"] += 1

            # Migrate Images
            images = l.get("images") or l.get("photos") or []
            for idx, img in enumerate(images):
                img_path = str(img).strip()
                if not img_path.startswith("/static/"):
                    if not img_path.startswith("static/"):
                        img_path = f"/static/uploads/{img_path}"
                    else:
                        img_path = f"/{img_path}"
                img_rec = ListingImage(
                    listing_id=l_id,
                    image_path=img_path,
                    display_order=idx,
                    created_at=created_at
                )
                db.session.add(img_rec)
                report["listing_images"]["imported"] += 1

        if not dry_run:
            db.session.commit()

        # ---------------------------------------------------------------------
        # 3. MIGRATE ORDERS
        # ---------------------------------------------------------------------
        raw_orders = load_json_file(orders_file)
        for o in raw_orders:
            o_id = o.get("order_id") or str(uuid.uuid4())
            existing = db.session.get(Order, o_id)
            if existing:
                report["orders"]["existing"] += 1
                continue

            product_id = o.get("product_id")
            buyer_id = o.get("buyer_id")
            buyer_phone = str(o.get("buyer_phone", "")).strip()
            if not buyer_id and buyer_phone in user_phone_map:
                buyer_id = user_phone_map[buyer_phone]

            seller_id = o.get("seller_id")
            seller_phone = str(o.get("seller_phone", "")).strip()
            if not seller_id and seller_phone in user_phone_map:
                seller_id = user_phone_map[seller_phone]

            price_paise = parse_price_to_paise(o.get("price", "0"))
            delivery_charge_paise = parse_price_to_paise(o.get("delivery_charge", "0"))
            total_paise = price_paise + delivery_charge_paise

            created_at = parse_timestamp(o.get("created_at"))
            updated_at = parse_timestamp(o.get("updated_at")) if o.get("updated_at") else created_at
            status = str(o.get("status", "Pending")).strip()

            order = Order(
                id=o_id,
                checkout_token=o.get("checkout_token") or str(uuid.uuid4()),
                buyer_id=buyer_id,
                seller_id=seller_id,
                listing_id=product_id,
                brand=str(o.get("brand", "Unknown")).strip(),
                model=str(o.get("model", "Phone")).strip(),
                storage=str(o.get("storage", "")).strip() or None,
                price_paise=price_paise,
                delivery_charge_paise=delivery_charge_paise,
                total_paise=total_paise,
                delivery_method=str(o.get("delivery_method", "pickup")).strip().lower(),
                buyer_name=str(o.get("buyer_name", "Buyer")).strip(),
                buyer_phone=buyer_phone,
                shipping_address=str(o.get("address", "Local Pickup")).strip(),
                pincode=str(o.get("pincode", "")).strip() or None,
                courier_name=str(o.get("courier_name", "")).strip() or None,
                tracking_number=str(o.get("tracking_number", "")).strip() or None,
                seller_name=str(o.get("seller_name", "")).strip() or None,
                seller_phone=seller_phone or None,
                status=status,
                payment_method="cod",
                payment_status=str(o.get("payment_status", "Not collected")).strip(),
                created_at=created_at,
                updated_at=updated_at
            )
            db.session.add(order)
            report["orders"]["imported"] += 1

            # Update associated listing inventory status
            if product_id:
                target_listing = db.session.get(Listing, product_id)
                if target_listing:
                    if status in ("Delivered", "Confirmed", "Shipped"):
                        target_listing.inventory_status = "sold"
                    elif status == "Pending":
                        target_listing.inventory_status = "reserved"

            # Create initial audit history for this order
            history = OrderStatusHistory(
                order_id=o_id,
                from_status=None,
                to_status=status,
                actor_role="system",
                notes="Migrated from legacy order record",
                created_at=created_at
            )
            db.session.add(history)

        if not dry_run:
            db.session.commit()

        # ---------------------------------------------------------------------
        # 4. MIGRATE NOTIFICATIONS
        # ---------------------------------------------------------------------
        raw_notifications = load_json_file(notifications_file)
        for n in raw_notifications:
            n_id = n.get("id") or str(uuid.uuid4())
            existing = db.session.get(Notification, n_id)
            if existing:
                report["notifications"]["existing"] += 1
                continue

            user_id = n.get("user_id")
            if not user_id:
                report["notifications"]["errors"] += 1
                continue

            title = str(n.get("title", "Notification")).strip()
            # Clean any corrupted characters like "dYZ%"
            title = title.replace("dYZ%", "").strip() or "Order Update"

            notice = Notification(
                id=n_id,
                user_id=user_id,
                title=title,
                message=str(n.get("message", "")).strip(),
                listing_id=n.get("listing_id"),
                is_read=bool(n.get("read", False)),
                created_at=parse_timestamp(n.get("created_at"))
            )
            db.session.add(notice)
            report["notifications"]["imported"] += 1

        if not dry_run:
            db.session.commit()

    return report


if __name__ == "__main__":
    print("=" * 60)
    print("iSellora Legacy JSON -> Relational DB Migration")
    print("=" * 60)
    summary = run_migration(dry_run=False)
    print("\nMigration Completed Successfully:")
    print(json.dumps(summary, indent=2))
