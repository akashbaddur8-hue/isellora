"""
Inventory claim and single-unit smartphone reservation service.
Provides database-enforced concurrency protection against overselling and race conditions.
"""
from backend.models import Listing
from backend.database import db


def claim_listing_for_checkout(listing_id: str) -> Listing | None:
    """
    Atomically attempts to reserve a single-unit listing for an incoming order.
    Returns the Listing if claimed, or None if already reserved/sold/unapproved.
    Guarantees concurrency protection at the database level.
    """
    # Execute atomic conditional update
    rows_updated = Listing.query.filter(
        Listing.id == listing_id,
        Listing.approval_status == "approved",
        Listing.inventory_status == "available"
    ).update({"inventory_status": "reserved"}, synchronize_session=False)

    if rows_updated == 0:
        return None

    db.session.flush()
    return db.session.get(Listing, listing_id)


def release_listing_reservation(listing_id: str) -> bool:
    """
    Safely releases a reserved listing back to available status (e.g. on order cancellation).
    """
    if not listing_id:
        return False
    
    rows_updated = Listing.query.filter(
        Listing.id == listing_id,
        Listing.inventory_status == "reserved"
    ).update({"inventory_status": "available"}, synchronize_session=False)

    db.session.flush()
    return rows_updated > 0


def finalize_listing_sold(listing_id: str) -> bool:
    """
    Marks a listing as permanently sold upon successful order completion or confirmation.
    """
    if not listing_id:
        return False

    rows_updated = Listing.query.filter(
        Listing.id == listing_id
    ).update({"inventory_status": "sold"}, synchronize_session=False)

    db.session.flush()
    return rows_updated > 0


def expire_abandoned_reservations(timeout_minutes: int = 30) -> int:
    """
    Safely releases inventory reservations that have exceeded the checkout window
    without a verified paid order or active COD confirmation.
    """
    from datetime import timedelta
    from backend.models import Order, utc_now

    cutoff = utc_now() - timedelta(minutes=timeout_minutes)
    
    # Find orders that are still pending with uncompleted payment beyond the cutoff
    stale_orders = Order.query.filter(
        Order.status == "Pending",
        Order.payment_status.in_(["created", "failed", "pending"]),
        Order.created_at < cutoff
    ).all()

    released_count = 0
    for order in stale_orders:
        order.status = "Cancelled"
        order.cancelled_by = "system"
        order.cancellation_reason = f"Checkout reservation expired after {timeout_minutes} minutes without completed payment."
        if release_listing_reservation(order.listing_id):
            released_count += 1

    db.session.commit()
    return released_count
