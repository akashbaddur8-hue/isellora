"""
Order lifecycle, state transitions, validation, and audit logging service.
"""
import uuid
from backend.models import Order, OrderStatusHistory, Notification, Listing, utc_now
from backend.database import db
from backend.services.inventory_service import claim_listing_for_checkout, release_listing_reservation, finalize_listing_sold

ALLOWED_STATUS_TRANSITIONS = {
    "Pending": {"Confirmed", "Cancelled"},
    "Confirmed": {"Shipped", "Delivered", "Cancelled"},
    "Shipped": {"Delivered"},
    "Delivered": set(),  # Terminal state
    "Cancelled": set()   # Terminal state
}

STANDARD_SHIPPING_CHARGE_PAISE = 15000  # Rs 150 in paise for insured shipping


def create_order(
    buyer_id: str,
    listing_id: str,
    buyer_name: str,
    buyer_phone: str,
    delivery_method: str,
    shipping_address: str,
    pincode: str = None,
    checkout_token: str = None
) -> tuple[Order | None, str]:
    """
    Creates an order with server-side price calculation and atomic reservation.
    Returns (order, error_message).
    """
    if not checkout_token:
        checkout_token = str(uuid.uuid4())

    # Check for duplicate order with this checkout token
    existing_order = Order.query.filter_by(checkout_token=checkout_token).first()
    if existing_order:
        return existing_order, ""

    # Attempt atomic claim on the listing
    listing = claim_listing_for_checkout(listing_id)
    if not listing:
        return None, "This smartphone is no longer available or was just reserved by another buyer."

    # Cannot buy your own listing
    if listing.seller_id == buyer_id:
        release_listing_reservation(listing_id)
        return None, "You cannot purchase your own listing."

    # Server-side price calculation (NEVER trust client price)
    price_paise = listing.price_paise
    delivery_charge_paise = 0 if delivery_method == "pickup" else STANDARD_SHIPPING_CHARGE_PAISE
    total_paise = price_paise + delivery_charge_paise

    order_id = str(uuid.uuid4())
    order = Order(
        id=order_id,
        checkout_token=checkout_token,
        buyer_id=buyer_id,
        seller_id=listing.seller_id,
        listing_id=listing.id,
        brand=listing.brand,
        model=listing.model,
        storage=listing.storage,
        price_paise=price_paise,
        delivery_charge_paise=delivery_charge_paise,
        total_paise=total_paise,
        delivery_method=delivery_method,
        buyer_name=buyer_name,
        buyer_phone=buyer_phone,
        shipping_address=shipping_address,
        pincode=pincode,
        seller_name=listing.seller_name or (listing.seller.name if listing.seller else "Seller"),
        seller_phone=listing.seller_phone or (listing.seller.phone if listing.seller else ""),
        status="Pending",
        payment_method="cod",
        payment_status="Not collected",
        created_at=utc_now(),
        updated_at=utc_now()
    )

    db.session.add(order)

    # Status history entry
    history = OrderStatusHistory(
        order_id=order_id,
        from_status=None,
        to_status="Pending",
        actor_id=buyer_id,
        actor_role="buyer",
        notes="Order placed by buyer via checkout",
        created_at=utc_now()
    )
    db.session.add(history)

    # Notify seller
    if listing.seller_id:
        notice = Notification(
            user_id=listing.seller_id,
            title="New Order Received",
            message=f"{buyer_name} placed an order for {listing.brand} {listing.model}.",
            listing_id=listing.id,
            order_id=order_id,
            created_at=utc_now()
        )
        db.session.add(notice)

    db.session.commit()
    return order, ""


def transition_order_status(
    order: Order,
    new_status: str,
    actor_id: str,
    actor_role: str,
    notes: str = None
) -> tuple[bool, str]:
    """
    Validates and performs an order status transition with audit logging.
    """
    old_status = order.status
    allowed = ALLOWED_STATUS_TRANSITIONS.get(old_status, set())

    if new_status not in allowed:
        return False, f"Cannot transition order status from {old_status} to {new_status}."

    # Shipping requirements
    if new_status == "Shipped":
        if order.delivery_method == "pickup":
            return False, "Local pickup orders cannot be marked as Shipped. Mark them as Delivered upon handover."
        if not order.courier_name or not order.tracking_number:
            return False, "Courier name and tracking number are required before marking the order as Shipped."

    order.status = new_status
    order.updated_at = utc_now()

    # If completed/delivered, mark listing as sold
    if new_status == "Delivered":
        finalize_listing_sold(order.listing_id)
        # Note: Payment status can be updated when confirmed collected
        if order.payment_status == "Not collected":
            order.payment_status = "Collected upon delivery"

    # If cancelled, release listing reservation
    elif new_status == "Cancelled":
        release_listing_reservation(order.listing_id)
        order.cancelled_by = actor_role
        order.cancellation_reason = notes or "Order cancelled"

    # Status history entry
    history = OrderStatusHistory(
        order_id=order.id,
        from_status=old_status,
        to_status=new_status,
        actor_id=actor_id,
        actor_role=actor_role,
        notes=notes,
        created_at=utc_now()
    )
    db.session.add(history)

    # Send Notification to buyer or seller
    target_user_id = order.buyer_id if actor_role in ("seller", "admin") else order.seller_id
    if target_user_id:
        notice = Notification(
            user_id=target_user_id,
            title=f"Order {new_status}",
            message=f"Order for {order.brand} {order.model} has been marked as {new_status}." + (f" Note: {notes}" if notes else ""),
            listing_id=order.listing_id,
            order_id=order.id,
            created_at=utc_now()
        )
        db.session.add(notice)

    db.session.commit()
    return True, ""
