"""
Order lifecycle, state transitions, validation, and audit logging service.
"""
import uuid
from backend.models import Order, OrderStatusHistory, Notification, Listing, utc_now
from backend.database import db
from backend.services.inventory_service import claim_listing_for_checkout, release_listing_reservation, finalize_listing_sold

def get_allowed_transitions(current_status: str, delivery_method: str = "shipping") -> set[str]:
    """
    Returns the set of permitted next states based on current order status and delivery method.
    Shipping orders MUST transition through 'Shipped' before 'Delivered'.
    Pickup orders transition directly from 'Confirmed' to 'Delivered' upon in-person handover.
    """
    if delivery_method == "shipping":
        transitions = {
            "Pending": {"Confirmed", "Cancelled"},
            "Confirmed": {"Shipped", "Cancelled"},  # Shipping orders CANNOT skip Shipped
            "Shipped": {"Delivered"},
            "Delivered": set(),  # Terminal state
            "Cancelled": set()   # Terminal state
        }
    else:  # pickup
        transitions = {
            "Pending": {"Confirmed", "Cancelled"},
            "Confirmed": {"Delivered", "Cancelled"},  # In-person physical handover
            "Shipped": set(),  # Pickup has no courier shipping step
            "Delivered": set(),  # Terminal state
            "Cancelled": set()   # Terminal state
        }
    return transitions.get(current_status, set())


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
    checkout_token: str = None,
    payment_method: str = "cod"
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

    if payment_method not in ("cod", "razorpay", "upi"):
        release_listing_reservation(listing_id)
        return None, "Invalid payment method."

    order_payment_method = payment_method
    if payment_method == "razorpay":
        initial_payment_status = "created"
    elif payment_method == "upi":
        initial_payment_status = "pending"
    else:
        initial_payment_status = "Not collected"

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
        payment_method=order_payment_method,
        payment_status=initial_payment_status,
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
    if old_status == new_status:
        return False, f"Order is already in '{new_status}' status."

    if old_status in ("Delivered", "Cancelled"):
        return False, f"Order is in terminal state '{old_status}' and cannot be reopened or modified."

    allowed = get_allowed_transitions(old_status, order.delivery_method)

    if new_status not in allowed:
        if order.delivery_method == "shipping" and old_status == "Confirmed" and new_status == "Delivered":
            return False, "Shipping orders must be marked as Shipped before they can be marked as Delivered."
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
        # Note: Payment status is kept decoupled from delivery status.
        # COD collection must be recorded through explicit collection workflow.

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


def record_cod_payment_collection(
    order: Order,
    actor_id: str,
    actor_role: str,
    notes: str = None
) -> tuple[bool, str]:
    """
    Explicit, authorized workflow to record COD payment collection upon verified physical delivery.
    Decoupled from order delivery transitions.
    """
    if order.payment_status == "Collected upon delivery":
        return False, "Payment has already been recorded as collected."
    if order.status != "Delivered":
        return False, "Payment can only be confirmed collected for delivered orders."

    order.payment_status = "Collected upon delivery"
    order.updated_at = utc_now()

    history = OrderStatusHistory(
        order_id=order.id,
        from_status=order.status,
        to_status=order.status,
        actor_id=actor_id,
        actor_role=actor_role,
        notes=f"Payment Collection Confirmed: {notes or 'Cash collected upon delivery.'}",
        created_at=utc_now()
    )
    db.session.add(history)
    db.session.commit()
    return True, "Payment collection recorded successfully."
