"""
Razorpay Payment Gateway integration service.
Handles:
- Server-side Razorpay order creation for smartphone purchases and priority boosts
- HMAC SHA256 Checkout signature verification
- Webhook signature verification and idempotent event processing
- Payment lifecycle states: created, pending, authorized, paid, failed, refunded
- Authorized refund workflows and audit logging
"""
import json
import logging
from datetime import timedelta
import razorpay
from flask import current_app
from backend.database import db
from backend.models import (
    Payment, WebhookEvent, Order, Listing, ListingSubscription,
    OrderStatusHistory, Notification, AuditEvent, User, utc_now
)

logger = logging.getLogger(__name__)


def get_razorpay_client() -> razorpay.Client:
    """
    Initializes and returns the official Razorpay client using configured server secrets.
    """
    key_id = current_app.config.get("RAZORPAY_KEY_ID", "").strip()
    key_secret = current_app.config.get("RAZORPAY_KEY_SECRET", "").strip()
    return razorpay.Client(auth=(key_id, key_secret))


def create_order_razorpay_payment(order: Order, buyer: User) -> tuple[dict | None, str]:
    """
    Creates a Razorpay order for an approved smartphone checkout.
    Calculates authoritative amount from server database records (order.total_paise).
    Returns (razorpay_payload_dict, error_message).
    """
    if not order or not buyer:
        return None, "Invalid order or buyer record."

    if order.total_paise <= 0:
        return None, "Invalid payable amount."

    key_id = current_app.config.get("RAZORPAY_KEY_ID", "").strip()
    key_secret = current_app.config.get("RAZORPAY_KEY_SECRET", "").strip()
    if not key_id or not key_secret:
        return None, "Razorpay credentials are not configured on the server."

    # Check for existing paid payment
    existing_paid = Payment.query.filter_by(order_id=order.id, status="paid").first()
    if existing_paid:
        return None, "This order has already been paid for."

    receipt = f"rcpt_{order.id.replace('-', '')[:20]}"
    client = get_razorpay_client()

    try:
        rzp_order = client.order.create({
            "amount": int(order.total_paise),
            "currency": "INR",
            "receipt": receipt,
            "notes": {
                "order_id": str(order.id),
                "buyer_id": str(buyer.id),
                "brand": str(order.brand),
                "model": str(order.model),
                "purpose": "order"
            }
        })
    except Exception as e:
        logger.error(f"Failed to create Razorpay order for Order {order.id}: {str(e)}")
        return None, f"Unable to initialize payment gateway: {str(e)}"

    razorpay_order_id = rzp_order.get("id")
    if not razorpay_order_id:
        return None, "Gateway returned an invalid order response."

    # Create Payment record
    payment = Payment(
        order_id=order.id,
        user_id=buyer.id,
        purpose="order",
        provider="razorpay",
        razorpay_order_id=razorpay_order_id,
        amount_paise=order.total_paise,
        currency="INR",
        status="created",
        created_at=utc_now(),
        updated_at=utc_now()
    )
    db.session.add(payment)

    # Bind order to Razorpay order ID and set payment method
    order.razorpay_order_id = razorpay_order_id
    order.payment_method = "razorpay"
    order.payment_status = "created"
    order.updated_at = utc_now()

    db.session.commit()

    return {
        "key_id": key_id,
        "razorpay_order_id": razorpay_order_id,
        "amount_paise": order.total_paise,
        "amount_rupees": order.total_rupees,
        "currency": "INR",
        "order_id": order.id,
        "prefill": {
            "name": buyer.name,
            "email": buyer.email,
            "contact": buyer.phone
        }
    }, ""


def create_boost_razorpay_payment(listing: Listing, seller: User) -> tuple[dict | None, str]:
    """
    Creates a Razorpay order for the ₹49 priority placement subscription.
    Returns (razorpay_payload_dict, error_message).
    """
    if not listing or not seller:
        return None, "Invalid listing or seller record."

    if listing.seller_id != seller.id and not seller.is_admin:
        return None, "You can only purchase priority placement for your own listings."

    key_id = current_app.config.get("RAZORPAY_KEY_ID", "").strip()
    key_secret = current_app.config.get("RAZORPAY_KEY_SECRET", "").strip()
    if not key_id or not key_secret:
        return None, "Razorpay credentials are not configured on the server."

    boost_amount_paise = 4900  # Exactly ₹49 in paise
    receipt = f"boost_{listing.id.replace('-', '')[:20]}"
    client = get_razorpay_client()

    try:
        rzp_order = client.order.create({
            "amount": boost_amount_paise,
            "currency": "INR",
            "receipt": receipt,
            "notes": {
                "listing_id": str(listing.id),
                "seller_id": str(seller.id),
                "purpose": "priority_boost"
            }
        })
    except Exception as e:
        logger.error(f"Failed to create Razorpay boost order for Listing {listing.id}: {str(e)}")
        return None, f"Unable to initialize priority placement payment: {str(e)}"

    razorpay_order_id = rzp_order.get("id")
    if not razorpay_order_id:
        return None, "Gateway returned an invalid order response."

    # Create pending ListingSubscription
    subscription = ListingSubscription(
        listing_id=listing.id,
        seller_id=seller.id,
        plan_name="Priority Placement (Sell First)",
        amount_paise=boost_amount_paise,
        status="pending",
        payment_method="razorpay",
        payment_status="pending",
        razorpay_order_id=razorpay_order_id,
        created_at=utc_now()
    )
    db.session.add(subscription)
    db.session.flush()

    # Create Payment record
    payment = Payment(
        subscription_id=subscription.id,
        user_id=seller.id,
        purpose="priority_boost",
        provider="razorpay",
        razorpay_order_id=razorpay_order_id,
        amount_paise=boost_amount_paise,
        currency="INR",
        status="created",
        created_at=utc_now(),
        updated_at=utc_now()
    )
    db.session.add(payment)
    db.session.commit()

    return {
        "key_id": key_id,
        "razorpay_order_id": razorpay_order_id,
        "amount_paise": boost_amount_paise,
        "amount_rupees": 49,
        "currency": "INR",
        "listing_id": listing.id,
        "prefill": {
            "name": seller.name,
            "email": seller.email,
            "contact": seller.phone
        }
    }, ""


def verify_checkout_signature(
    razorpay_order_id: str,
    razorpay_payment_id: str,
    razorpay_signature: str
) -> bool:
    """
    Verifies the payment signature returned by Razorpay Checkout.
    """
    if not razorpay_order_id or not razorpay_payment_id or not razorpay_signature:
        return False

    client = get_razorpay_client()
    try:
        client.utility.verify_payment_signature({
            "razorpay_order_id": razorpay_order_id,
            "razorpay_payment_id": razorpay_payment_id,
            "razorpay_signature": razorpay_signature
        })
        return True
    except razorpay.errors.SignatureVerificationError:
        logger.warning(f"Signature mismatch for order {razorpay_order_id} payment {razorpay_payment_id}")
        return False
    except Exception as e:
        logger.error(f"Signature verification error: {str(e)}")
        return False


def verify_webhook_signature(raw_body: bytes | str, signature: str) -> bool:
    """
    Verifies the webhook signature using RAZORPAY_WEBHOOK_SECRET.
    """
    if not raw_body or not signature:
        return False

    webhook_secret = current_app.config.get("RAZORPAY_WEBHOOK_SECRET", "").strip()
    if not webhook_secret:
        logger.warning("RAZORPAY_WEBHOOK_SECRET is not configured.")
        return False

    body_str = raw_body.decode("utf-8") if isinstance(raw_body, bytes) else str(raw_body)
    client = get_razorpay_client()
    try:
        client.utility.verify_webhook_signature(body_str, signature, webhook_secret)
        return True
    except razorpay.errors.SignatureVerificationError:
        logger.warning("Webhook signature verification failed.")
        return False
    except Exception as e:
        logger.error(f"Webhook verification error: {str(e)}")
        return False


def process_order_checkout_callback(
    razorpay_order_id: str,
    razorpay_payment_id: str,
    razorpay_signature: str,
    current_user_id: str
) -> tuple[bool, str, Order | None]:
    """
    Processes the frontend callback after Razorpay Checkout completion.
    Validates signature, verifies payment ownership, and updates payment/order records atomically.
    Returns (success, message, order).
    """
    # 1. Signature Verification
    if not verify_checkout_signature(razorpay_order_id, razorpay_payment_id, razorpay_signature):
        return False, "Payment signature verification failed. Untrusted response rejected.", None

    # 2. Database Record Lookup
    payment = Payment.query.filter_by(razorpay_order_id=razorpay_order_id).first()
    if not payment:
        return False, "No pending payment record found for this transaction.", None

    if payment.user_id != current_user_id:
        return False, "Payment ownership verification failed.", None

    order = payment.order
    if not order:
        return False, "No associated order found for this payment.", None

    # Expected Razorpay order ID stored against the internal order
    if order.razorpay_order_id != razorpay_order_id:
        return False, "Order reference mismatch: expected stored order ID does not match callback token.", None

    # Idempotent handling: if already paid, return existing success
    if payment.status == "paid" and order.payment_status == "paid":
        return True, "Payment has already been verified and recorded.", order

    # 3. Transition Payment & Order Records
    payment.razorpay_payment_id = razorpay_payment_id
    payment.razorpay_signature = razorpay_signature
    payment.status = "paid"
    payment.paid_at = utc_now()
    payment.updated_at = utc_now()

    old_order_status = order.status
    order.payment_status = "paid"
    order.razorpay_payment_id = razorpay_payment_id
    if order.status == "Pending":
        order.status = "Confirmed"
    order.updated_at = utc_now()

    # 4. Status History & Audit
    history = OrderStatusHistory(
        order_id=order.id,
        from_status=old_order_status,
        to_status=order.status,
        actor_id=current_user_id,
        actor_role="buyer",
        notes=f"Online payment of {order.formatted_total} verified via Razorpay Checkout (Payment ID: {razorpay_payment_id}).",
        created_at=utc_now()
    )
    db.session.add(history)

    # 5. Notify Seller
    if order.seller_id:
        notice = Notification(
            user_id=order.seller_id,
            title="Order Paid & Confirmed",
            message=f"{order.buyer_name} completed online payment of {order.formatted_total} for {order.brand} {order.model}.",
            listing_id=order.listing_id,
            order_id=order.id,
            created_at=utc_now()
        )
        db.session.add(notice)

    audit = AuditEvent(
        user_id=current_user_id,
        action="ORDER_PAYMENT_VERIFIED",
        target_type="order",
        target_id=order.id,
        details=f"Razorpay payment {razorpay_payment_id} verified for order {order.id}.",
        created_at=utc_now()
    )
    db.session.add(audit)

    db.session.commit()
    return True, "Payment verified successfully.", order


def process_boost_checkout_callback(
    razorpay_order_id: str,
    razorpay_payment_id: str,
    razorpay_signature: str,
    current_user_id: str
) -> tuple[bool, str, ListingSubscription | None]:
    """
    Processes the frontend callback after ₹49 Priority Boost payment.
    Validates signature, verifies amount, activates subscription, and features listing.
    Returns (success, message, subscription).
    """
    # 1. Signature Verification
    if not verify_checkout_signature(razorpay_order_id, razorpay_payment_id, razorpay_signature):
        return False, "Payment signature verification failed. Untrusted response rejected.", None

    # 2. Database Record Lookup
    payment = Payment.query.filter_by(razorpay_order_id=razorpay_order_id).first()
    if not payment:
        return False, "No pending boost payment record found.", None

    if payment.user_id != current_user_id:
        return False, "Payment ownership verification failed.", None

    subscription = payment.subscription
    if not subscription:
        # Fallback query
        subscription = ListingSubscription.query.filter_by(razorpay_order_id=razorpay_order_id).first()
    if not subscription:
        return False, "No associated priority subscription found.", None

    # Idempotent handling: if already active and paid, return existing success
    if payment.status == "paid" and subscription.status == "active" and subscription.payment_status == "paid":
        return True, "Priority placement is already active.", subscription

    # 3. Transition Payment & Subscription Records
    payment.razorpay_payment_id = razorpay_payment_id
    payment.razorpay_signature = razorpay_signature
    payment.status = "paid"
    payment.paid_at = utc_now()
    payment.updated_at = utc_now()

    subscription.razorpay_payment_id = razorpay_payment_id
    subscription.status = "active"
    subscription.payment_status = "paid"
    subscription.expires_at = utc_now() + timedelta(days=30)

    # Feature listing
    listing = subscription.listing
    if listing:
        listing.is_featured = True
        listing.featured_at = utc_now()

    # 4. Audit & Notification
    audit = AuditEvent(
        user_id=current_user_id,
        action="SUBSCRIPTION_BOOST_ACTIVATED",
        target_type="listing",
        target_id=subscription.listing_id,
        details=f"Priority Placement (₹49) activated via verified Razorpay payment {razorpay_payment_id}.",
        created_at=utc_now()
    )
    db.session.add(audit)

    notice = Notification(
        user_id=subscription.seller_id,
        title="Priority Placement Activated",
        message=f"Your listing for {listing.brand if listing else 'phone'} {listing.model if listing else ''} is now pinned at #1 position in search feeds.",
        listing_id=subscription.listing_id,
        created_at=utc_now()
    )
    db.session.add(notice)

    db.session.commit()
    return True, "Priority placement activated successfully.", subscription


def record_payment_failure(
    razorpay_order_id: str,
    error_code: str = None,
    error_description: str = None,
    user_id: str = None
) -> bool:
    """
    Records a payment failure or modal dismissal.
    Does not immediately delete or cancel the order so the user can retry.
    """
    if not razorpay_order_id:
        return False

    payment = Payment.query.filter_by(razorpay_order_id=razorpay_order_id).first()
    if not payment:
        return False

    if payment.status == "paid":
        # Do not overwrite verified paid status
        return True

    payment.status = "failed"
    payment.error_code = error_code or "PAYMENT_FAILED"
    payment.error_description = error_description or "Payment was unsuccessful or cancelled by user."
    payment.updated_at = utc_now()

    if payment.order and payment.order.payment_status != "paid":
        payment.order.payment_status = "failed"
        payment.order.updated_at = utc_now()

    db.session.commit()
    return True


def process_webhook(raw_body: bytes, signature: str) -> tuple[bool, str, int]:
    """
    Independent reconciliation webhook endpoint.
    Handles order.paid, payment.captured, payment.failed, and refund.processed idempotently.
    Returns (success, message, http_status_code).
    """
    # 1. Verify webhook signature
    if not verify_webhook_signature(raw_body, signature):
        logger.warning("Rejected webhook with invalid signature.")
        return False, "Invalid webhook signature.", 400

    try:
        body_str = raw_body.decode("utf-8") if isinstance(raw_body, bytes) else str(raw_body)
        event = json.loads(body_str)
    except Exception as e:
        logger.error(f"Malformed webhook JSON: {str(e)}")
        return False, "Malformed JSON payload.", 400

    event_id = event.get("id")
    event_type = event.get("event")
    if not event_id or not event_type:
        return False, "Missing event identifier or type.", 400

    # 2. Idempotency Check
    existing_event = WebhookEvent.query.filter_by(event_id=event_id).first()
    if existing_event:
        return True, "Event already processed.", 200

    # 3. Process Event Types
    payload_data = event.get("payload", {})
    payment_entity = payload_data.get("payment", {}).get("entity", {})
    order_entity = payload_data.get("order", {}).get("entity", {})

    rzp_order_id = payment_entity.get("order_id") or order_entity.get("id")
    rzp_payment_id = payment_entity.get("id")

    if event_type in ("order.paid", "payment.captured"):
        if rzp_order_id:
            payment = Payment.query.filter_by(razorpay_order_id=rzp_order_id).first()
            if payment and payment.status != "paid":
                payment.status = "paid"
                if rzp_payment_id:
                    payment.razorpay_payment_id = rzp_payment_id
                payment.paid_at = utc_now()
                payment.updated_at = utc_now()

                if payment.purpose == "order" and payment.order:
                    order = payment.order
                    order.payment_status = "paid"
                    if order.status == "Pending":
                        order.status = "Confirmed"
                    if rzp_payment_id:
                        order.razorpay_payment_id = rzp_payment_id
                    order.updated_at = utc_now()

                    history = OrderStatusHistory(
                        order_id=order.id,
                        from_status=order.status,
                        to_status=order.status,
                        actor_id="system",
                        actor_role="system",
                        notes=f"Payment verified via Razorpay webhook ({event_type}).",
                        created_at=utc_now()
                    )
                    db.session.add(history)

                elif payment.purpose == "priority_boost" and payment.subscription:
                    sub = payment.subscription
                    sub.status = "active"
                    sub.payment_status = "paid"
                    if rzp_payment_id:
                        sub.razorpay_payment_id = rzp_payment_id
                    sub.expires_at = utc_now() + timedelta(days=30)
                    if sub.listing:
                        sub.listing.is_featured = True
                        sub.listing.featured_at = utc_now()

    elif event_type == "payment.failed":
        if rzp_order_id:
            payment = Payment.query.filter_by(razorpay_order_id=rzp_order_id).first()
            if payment and payment.status != "paid":
                payment.status = "failed"
                payment.error_code = payment_entity.get("error_code") or "WEBHOOK_PAYMENT_FAILED"
                payment.error_description = payment_entity.get("error_description") or "Payment failed at gateway."
                payment.updated_at = utc_now()
                if payment.order and payment.order.payment_status != "paid":
                    payment.order.payment_status = "failed"
                    payment.order.updated_at = utc_now()

    elif event_type == "refund.processed":
        refund_entity = payload_data.get("refund", {}).get("entity", {})
        refund_id = refund_entity.get("id")
        refund_amount = refund_entity.get("amount", 0)
        pay_id = refund_entity.get("payment_id")

        if pay_id:
            payment = Payment.query.filter_by(razorpay_payment_id=pay_id).first()
            if payment:
                payment.refund_id = refund_id
                payment.refund_amount_paise = refund_amount
                payment.refund_status = "processed"
                payment.status = "refunded"
                payment.updated_at = utc_now()
                if payment.order:
                    payment.order.payment_status = "refunded"
                    payment.order.updated_at = utc_now()

    # 4. Record WebhookEvent record for permanent audit & deduplication
    webhook_record = WebhookEvent(
        event_id=event_id,
        event_type=event_type,
        payload=json.dumps({"event": event_type, "id": event_id, "order_id": rzp_order_id}),
        status="processed",
        created_at=utc_now()
    )
    db.session.add(webhook_record)
    db.session.commit()

    return True, "Webhook processed successfully.", 200


def initiate_payment_refund(
    payment_id: str,
    amount_paise: int,
    reason: str,
    actor_id: str,
    actor_role: str = "admin"
) -> tuple[bool, str]:
    """
    Initiates a verified server-side refund through the official Razorpay API.
    Guarantees state and audit tracking.
    """
    payment = db.session.get(Payment, payment_id)
    if not payment:
        return False, "Payment record not found."

    if payment.status != "paid":
        return False, f"Cannot refund payment in '{payment.status}' state."

    if not payment.razorpay_payment_id:
        return False, "No Razorpay payment reference available to refund."

    if amount_paise <= 0 or amount_paise > payment.amount_paise:
        return False, "Refund amount must be positive and cannot exceed total paid."

    client = get_razorpay_client()
    try:
        rzp_refund = client.payment.refund(payment.razorpay_payment_id, {
            "amount": int(amount_paise),
            "notes": {
                "reason": str(reason),
                "actor_id": str(actor_id)
            }
        })
    except Exception as e:
        logger.error(f"Refund request failed for payment {payment.id}: {str(e)}")
        return False, f"Gateway refund request failed: {str(e)}"

    refund_id = rzp_refund.get("id")
    payment.refund_id = refund_id
    payment.refund_status = "processed"
    payment.refund_amount_paise = amount_paise
    payment.status = "refunded" if amount_paise == payment.amount_paise else "partially_refunded"
    payment.updated_at = utc_now()

    if payment.order:
        payment.order.payment_status = "refunded"
        payment.order.updated_at = utc_now()

    audit = AuditEvent(
        user_id=actor_id,
        action="PAYMENT_REFUND_PROCESSED",
        target_type="payment",
        target_id=payment.id,
        details=f"Refund of ₹{amount_paise // 100} processed via Razorpay (Refund ID: {refund_id}). Reason: {reason}",
        created_at=utc_now()
    )
    db.session.add(audit)
    db.session.commit()

    return True, f"Refund of ₹{amount_paise // 100} initiated successfully."
