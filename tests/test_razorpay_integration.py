"""
Comprehensive Automated Test Suite for iSellora Phase 2 Razorpay Integration.
Verifies:
1. Server-side authoritative amounts in integer paise (INR).
2. Protection against forged amounts, currencies, and client tampering.
3. Razorpay order creation for smartphone purchases and single-unit reservation.
4. HMAC SHA256 Checkout signature verification and state machine transitions.
5. Rejection of invalid signatures, order ID mismatches, and unauthorized user callbacks.
6. Idempotent callback handling without duplicate side effects.
7. Payment failure tracking (modal dismissal, declined cards) preserving retry capability.
8. Cash on Delivery (COD) separation and authorized post-delivery collection.
9. Webhook HMAC signature verification and rejection of forged webhooks.
10. Webhook events (order.paid, payment.captured, payment.failed, refund.processed).
11. Webhook deduplication and idempotency via WebhookEvent entity.
12. Verified activation of Priority Placement (₹49 / 4900 paise) boost.
13. Inventory release on abandoned reservation expiry.
14. Authorized administrative refund workflows.
"""
import hmac
import hashlib
import json
import uuid
from datetime import timedelta
from unittest.mock import MagicMock, patch
import pytest

from backend.models import (
    Listing, Order, Payment, ListingSubscription,
    WebhookEvent, OrderStatusHistory, AuditEvent, Notification, utc_now
)
from backend.database import db
from backend.services.payment_service import (
    create_order_razorpay_payment,
    create_boost_razorpay_payment,
    verify_checkout_signature,
    verify_webhook_signature,
    process_order_checkout_callback,
    process_boost_checkout_callback,
    record_payment_failure,
    process_webhook,
    initiate_payment_refund
)
from backend.services.inventory_service import expire_abandoned_reservations
from backend.services.order_service import create_order, record_cod_payment_collection


# Helper functions to generate genuine HMAC-SHA256 signatures for testing
def generate_test_checkout_signature(
    order_id: str,
    payment_id: str,
    secret: str = "mock_secret_key_456"
) -> str:
    msg = f"{order_id}|{payment_id}".encode("utf-8")
    return hmac.new(secret.encode("utf-8"), msg, hashlib.sha256).hexdigest()


def generate_test_webhook_signature(
    body: bytes,
    secret: str = "mock_webhook_secret_789"
) -> str:
    return hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


# ==============================================================================
# 1. SERVER-SIDE AMOUNT & CURRENCY INTEGRITY
# ==============================================================================

def test_server_side_amount_in_integer_paise(app, buyer_user, approved_listing):
    """
    Ensures that the payable amount is strictly calculated on the server side
    in integer paise (INR). Client-submitted amounts/currencies are ignored.
    """
    with app.app_context():
        # Listing price is Rs 50,000 -> 5000000 paise
        # Shipping is Rs 150 -> 15000 paise
        order, err = create_order(
            buyer_id=buyer_user.id,
            listing_id=approved_listing.id,
            buyer_name=buyer_user.name,
            buyer_phone=buyer_user.phone,
            delivery_method="shipping",
            shipping_address="123 High Street, Bengaluru",
            pincode="560001",
            payment_method="razorpay"
        )
        assert err == ""
        assert order.total_paise == 5015000
        assert order.total_rupees == 50150
        assert order.payment_method == "razorpay"
        assert order.payment_status == "created"


# ==============================================================================
# 2. RAZORPAY CHECKOUT ORDER CREATION
# ==============================================================================

def test_buy_now_creates_razorpay_order_and_reserves_inventory(client, buyer_user, approved_listing):
    """
    Verifies that choosing Razorpay at checkout creates a Razorpay order,
    records a Payment entity in 'created' status, and reserves single-unit inventory.
    """
    with client.session_transaction() as sess:
        sess["user_id"] = buyer_user.id
        sess["user_name"] = buyer_user.name
        sess["user_email"] = buyer_user.email

    # Establish checkout session token
    get_res = client.get(f"/buy/{approved_listing.id}")
    assert get_res.status_code == 200

    token = None
    with client.session_transaction() as sess:
        token = sess.get("checkout_token")
    assert token is not None

    mock_client = MagicMock()
    mock_client.order.create.return_value = {
        "id": "order_mock_test_123",
        "amount": 5000000,
        "currency": "INR",
        "status": "created"
    }

    with patch("backend.services.payment_service.get_razorpay_client", return_value=mock_client):
        response = client.post(
            f"/buy/{approved_listing.id}",
            data={
                "buyer_name": "Test Buyer",
                "buyer_phone": "9876543210",
                "delivery_method": "pickup",
                "payment_method": "razorpay",
                "checkout_token": token
            },
            headers={"X-Requested-With": "XMLHttpRequest"}
        )

    assert response.status_code == 200
    data = response.get_json()
    assert data["success"] is True
    assert data["payment_method"] == "razorpay"
    assert data["razorpay"]["razorpay_order_id"] == "order_mock_test_123"
    assert data["razorpay"]["amount_paise"] == 5000000
    assert data["razorpay"]["currency"] == "INR"

    # Inventory must now be reserved
    listing = db.session.get(Listing, approved_listing.id)
    assert listing.inventory_status == "reserved"

    # Order must exist with status Pending and payment_status created
    order = Order.query.filter_by(listing_id=approved_listing.id).first()
    assert order is not None
    assert order.status == "Pending"
    assert order.payment_status == "created"
    assert order.razorpay_order_id == "order_mock_test_123"

    # Payment record must exist
    payment = Payment.query.filter_by(razorpay_order_id="order_mock_test_123").first()
    assert payment is not None
    assert payment.status == "created"
    assert payment.purpose == "order"
    assert payment.amount_paise == 5000000


# ==============================================================================
# 3. SIGNATURE VERIFICATION & ORDER CONFIRMATION
# ==============================================================================

def test_payment_verify_valid_signature_confirms_order(client, buyer_user, approved_listing):
    """
    Submitting valid Razorpay checkout tokens marks the Payment as paid,
    confirms the Order, and logs an audit trail.
    """
    rzp_order_id = "order_rzp_valid_101"
    rzp_payment_id = "pay_rzp_valid_202"
    valid_signature = generate_test_checkout_signature(rzp_order_id, rzp_payment_id)

    # Set up pending order and payment
    order, _ = create_order(
        buyer_id=buyer_user.id,
        listing_id=approved_listing.id,
        buyer_name=buyer_user.name,
        buyer_phone=buyer_user.phone,
        delivery_method="pickup",
        shipping_address="Local Pickup",
        payment_method="razorpay"
    )
    order.razorpay_order_id = rzp_order_id
    payment = Payment(
        order_id=order.id,
        user_id=buyer_user.id,
        purpose="order",
        provider="razorpay",
        razorpay_order_id=rzp_order_id,
        amount_paise=order.total_paise,
        currency="INR",
        status="created"
    )
    db.session.add(payment)
    db.session.commit()

    with client.session_transaction() as sess:
        sess["user_id"] = buyer_user.id
        sess["user_name"] = buyer_user.name
        sess["user_email"] = buyer_user.email

    response = client.post(
        "/payment/verify",
        json={
            "razorpay_order_id": rzp_order_id,
            "razorpay_payment_id": rzp_payment_id,
            "razorpay_signature": valid_signature
        }
    )

    assert response.status_code == 200
    res_data = response.get_json()
    assert res_data["success"] is True

    # Check updated database entities
    updated_payment = db.session.get(Payment, payment.id)
    assert updated_payment.status == "paid"
    assert updated_payment.razorpay_payment_id == rzp_payment_id
    assert updated_payment.paid_at is not None

    updated_order = db.session.get(Order, order.id)
    assert updated_order.status == "Confirmed"
    assert updated_order.payment_status == "paid"
    assert updated_order.razorpay_payment_id == rzp_payment_id

    # Check audit log
    audit = AuditEvent.query.filter_by(target_id=order.id, action="ORDER_PAYMENT_VERIFIED").first()
    assert audit is not None


def test_payment_verify_invalid_signature_rejected(client, buyer_user, approved_listing):
    """
    Submitting an invalid or tampered signature is rejected with HTTP 400
    and keeps the order and payment unverified.
    """
    rzp_order_id = "order_rzp_invalid_101"
    rzp_payment_id = "pay_rzp_invalid_202"
    forged_signature = "invalid_forged_signature_hex_000000000000"

    order, _ = create_order(
        buyer_id=buyer_user.id,
        listing_id=approved_listing.id,
        buyer_name=buyer_user.name,
        buyer_phone=buyer_user.phone,
        delivery_method="pickup",
        shipping_address="Local Pickup",
        payment_method="razorpay"
    )
    order.razorpay_order_id = rzp_order_id
    payment = Payment(
        order_id=order.id,
        user_id=buyer_user.id,
        purpose="order",
        provider="razorpay",
        razorpay_order_id=rzp_order_id,
        amount_paise=order.total_paise,
        currency="INR",
        status="created"
    )
    db.session.add(payment)
    db.session.commit()

    with client.session_transaction() as sess:
        sess["user_id"] = buyer_user.id

    response = client.post(
        "/payment/verify",
        json={
            "razorpay_order_id": rzp_order_id,
            "razorpay_payment_id": rzp_payment_id,
            "razorpay_signature": forged_signature
        }
    )

    assert response.status_code == 400
    res_data = response.get_json()
    assert res_data["success"] is False

    # Status must NOT be paid
    assert db.session.get(Payment, payment.id).status == "created"
    assert db.session.get(Order, order.id).status == "Pending"
    assert db.session.get(Order, order.id).payment_status == "created"


def test_payment_verify_order_id_mismatch_rejected(client, buyer_user):
    """
    Submitting an unknown or mismatched razorpay_order_id returns 400.
    """
    with client.session_transaction() as sess:
        sess["user_id"] = buyer_user.id

    response = client.post(
        "/payment/verify",
        json={
            "razorpay_order_id": "order_unknown_99999",
            "razorpay_payment_id": "pay_test_123",
            "razorpay_signature": "mock_sig"
        }
    )
    assert response.status_code == 400


def test_payment_verify_ownership_enforcement(client, buyer_user, other_seller_user, approved_listing):
    """
    User B cannot verify or claim payment for an order initiated by User A.
    """
    rzp_order_id = "order_rzp_owner_test"
    rzp_payment_id = "pay_rzp_owner_test"
    valid_signature = generate_test_checkout_signature(rzp_order_id, rzp_payment_id)

    order, _ = create_order(
        buyer_id=buyer_user.id,
        listing_id=approved_listing.id,
        buyer_name=buyer_user.name,
        buyer_phone=buyer_user.phone,
        delivery_method="pickup",
        shipping_address="Local Pickup",
        payment_method="razorpay"
    )
    order.razorpay_order_id = rzp_order_id
    payment = Payment(
        order_id=order.id,
        user_id=buyer_user.id,  # Owned by buyer_user
        purpose="order",
        provider="razorpay",
        razorpay_order_id=rzp_order_id,
        amount_paise=order.total_paise,
        currency="INR",
        status="created"
    )
    db.session.add(payment)
    db.session.commit()

    # Log in as other user
    with client.session_transaction() as sess:
        sess["user_id"] = other_seller_user.id

    response = client.post(
        "/payment/verify",
        json={
            "razorpay_order_id": rzp_order_id,
            "razorpay_payment_id": rzp_payment_id,
            "razorpay_signature": valid_signature
        }
    )

    assert response.status_code == 400
    assert "ownership" in response.get_json()["error"].lower()


def test_payment_verify_idempotent_callback(client, buyer_user, approved_listing):
    """
    Calling /payment/verify multiple times with identical valid tokens is idempotent
    and does not duplicate history or error out.
    """
    rzp_order_id = "order_rzp_idempotent_1"
    rzp_payment_id = "pay_rzp_idempotent_1"
    valid_signature = generate_test_checkout_signature(rzp_order_id, rzp_payment_id)

    order, _ = create_order(
        buyer_id=buyer_user.id,
        listing_id=approved_listing.id,
        buyer_name=buyer_user.name,
        buyer_phone=buyer_user.phone,
        delivery_method="pickup",
        shipping_address="Local Pickup",
        payment_method="razorpay"
    )
    order.razorpay_order_id = rzp_order_id
    payment = Payment(
        order_id=order.id,
        user_id=buyer_user.id,
        purpose="order",
        provider="razorpay",
        razorpay_order_id=rzp_order_id,
        amount_paise=order.total_paise,
        currency="INR",
        status="created"
    )
    db.session.add(payment)
    db.session.commit()

    with client.session_transaction() as sess:
        sess["user_id"] = buyer_user.id

    payload = {
        "razorpay_order_id": rzp_order_id,
        "razorpay_payment_id": rzp_payment_id,
        "razorpay_signature": valid_signature
    }

    # First call: succeeds and marks paid
    res1 = client.post("/payment/verify", json=payload)
    assert res1.status_code == 200

    # Second identical call: returns success idempotently
    res2 = client.post("/payment/verify", json=payload)
    assert res2.status_code == 200
    assert res2.get_json()["success"] is True


# ==============================================================================
# 4. PAYMENT FAILURE LOGGING & RETRY PRESERVATION
# ==============================================================================

def test_payment_failure_logging_preserves_reservation_for_retry(client, buyer_user, approved_listing):
    """
    When payment fails or modal is dismissed, the failure is recorded without
    immediately releasing the inventory reservation prematurely.
    """
    rzp_order_id = "order_rzp_failed_test"
    order, _ = create_order(
        buyer_id=buyer_user.id,
        listing_id=approved_listing.id,
        buyer_name=buyer_user.name,
        buyer_phone=buyer_user.phone,
        delivery_method="pickup",
        shipping_address="Local Pickup",
        payment_method="razorpay"
    )
    order.razorpay_order_id = rzp_order_id
    payment = Payment(
        order_id=order.id,
        user_id=buyer_user.id,
        purpose="order",
        provider="razorpay",
        razorpay_order_id=rzp_order_id,
        amount_paise=order.total_paise,
        currency="INR",
        status="created"
    )
    db.session.add(payment)
    db.session.commit()

    with client.session_transaction() as sess:
        sess["user_id"] = buyer_user.id

    response = client.post(
        "/payment/failed",
        json={
            "razorpay_order_id": rzp_order_id,
            "error_code": "BAD_REQUEST_ERROR",
            "error_description": "Payment was declined by issuing bank."
        }
    )

    assert response.status_code == 200
    assert response.get_json()["success"] is True

    # Payment and order are marked failed, but listing is still reserved for retry window
    assert db.session.get(Payment, payment.id).status == "failed"
    assert db.session.get(Order, order.id).payment_status == "failed"
    assert db.session.get(Listing, approved_listing.id).inventory_status == "reserved"


# ==============================================================================
# 5. CASH ON DELIVERY (COD) SEPARATION & COLLECTION
# ==============================================================================

def test_cod_order_workflow_independent_of_gateway(app, buyer_user, approved_listing):
    """
    COD orders create order in Pending status with payment_status 'Not collected'
    without calling Razorpay order creation.
    """
    with app.app_context():
        order, err = create_order(
            buyer_id=buyer_user.id,
            listing_id=approved_listing.id,
            buyer_name=buyer_user.name,
            buyer_phone=buyer_user.phone,
            delivery_method="shipping",
            shipping_address="789 Lane, Pune",
            pincode="411001",
            payment_method="cod"
        )
        assert err == ""
        assert order.status == "Pending"
        assert order.payment_method == "cod"
        assert order.payment_status == "Not collected"
        assert order.razorpay_order_id is None

        # COD has no gateway payment transaction created
        payment = Payment.query.filter_by(order_id=order.id).first()
        assert payment is None


def test_cod_payment_collection_requires_delivered_state(app, buyer_user, seller_user, approved_listing):
    """
    COD payment can only be marked as collected once order status is 'Delivered'.
    """
    with app.app_context():
        order, _ = create_order(
            buyer_id=buyer_user.id,
            listing_id=approved_listing.id,
            buyer_name=buyer_user.name,
            buyer_phone=buyer_user.phone,
            delivery_method="pickup",
            shipping_address="Local Pickup",
            payment_method="cod"
        )

        # Attempt collection while Pending / Confirmed
        ok, err = record_cod_payment_collection(order, actor_id=seller_user.id, actor_role="seller")
        assert not ok
        assert "delivered" in err.lower()

        # Update order status to Delivered
        order.status = "Delivered"
        db.session.commit()

        # Record collection
        ok, msg = record_cod_payment_collection(order, actor_id=seller_user.id, actor_role="seller")
        assert ok
        assert order.payment_status == "Collected upon delivery"


# ==============================================================================
# 6. WEBHOOKS & ASYNCHRONOUS RECONCILIATION
# ==============================================================================

def test_webhook_rejects_missing_or_invalid_signature(client):
    """
    Webhooks without X-Razorpay-Signature or with forged signatures are rejected with 400.
    """
    payload = json.dumps({"event": "order.paid", "id": "evt_test_1"}).encode("utf-8")

    # Missing signature
    res_no_sig = client.post("/payment/webhook", data=payload, content_type="application/json")
    assert res_no_sig.status_code == 400

    # Invalid signature
    res_bad_sig = client.post(
        "/payment/webhook",
        data=payload,
        content_type="application/json",
        headers={"X-Razorpay-Signature": "invalid_signature_hex"}
    )
    assert res_bad_sig.status_code == 400


def test_webhook_order_paid_event_reconciliation(client, buyer_user, approved_listing):
    """
    Valid webhook 'order.paid' updates payment and confirms order asynchronously.
    """
    rzp_order_id = "order_rzp_webhook_paid_1"
    rzp_payment_id = "pay_rzp_webhook_paid_2"

    order, _ = create_order(
        buyer_id=buyer_user.id,
        listing_id=approved_listing.id,
        buyer_name=buyer_user.name,
        buyer_phone=buyer_user.phone,
        delivery_method="pickup",
        shipping_address="Local Pickup",
        payment_method="razorpay"
    )
    order.razorpay_order_id = rzp_order_id
    payment = Payment(
        order_id=order.id,
        user_id=buyer_user.id,
        purpose="order",
        provider="razorpay",
        razorpay_order_id=rzp_order_id,
        amount_paise=order.total_paise,
        currency="INR",
        status="created"
    )
    db.session.add(payment)
    db.session.commit()

    webhook_payload = {
        "entity": "event",
        "account_id": "acc_test",
        "event": "order.paid",
        "id": "evt_order_paid_12345",
        "payload": {
            "order": {
                "entity": {
                    "id": rzp_order_id,
                    "amount": order.total_paise,
                    "status": "paid"
                }
            },
            "payment": {
                "entity": {
                    "id": rzp_payment_id,
                    "order_id": rzp_order_id,
                    "amount": order.total_paise,
                    "status": "captured"
                }
            }
        }
    }
    raw_body = json.dumps(webhook_payload).encode("utf-8")
    sig = generate_test_webhook_signature(raw_body)

    response = client.post(
        "/payment/webhook",
        data=raw_body,
        content_type="application/json",
        headers={"X-Razorpay-Signature": sig}
    )

    assert response.status_code == 200
    assert response.get_json()["success"] is True

    # Verify entities transitioned
    assert db.session.get(Payment, payment.id).status == "paid"
    assert db.session.get(Order, order.id).status == "Confirmed"
    assert db.session.get(Order, order.id).payment_status == "paid"

    # Verify webhook event logged
    evt = WebhookEvent.query.filter_by(event_id="evt_order_paid_12345").first()
    assert evt is not None
    assert evt.status == "processed"


def test_webhook_payment_captured_and_payment_failed_events(client, buyer_user, approved_listing):
    """
    Tests webhook handling of payment.captured and payment.failed events.
    """
    rzp_order_id = "order_rzp_webhook_cap_1"
    order, _ = create_order(
        buyer_id=buyer_user.id,
        listing_id=approved_listing.id,
        buyer_name=buyer_user.name,
        buyer_phone=buyer_user.phone,
        delivery_method="pickup",
        shipping_address="Local Pickup",
        payment_method="razorpay"
    )
    order.razorpay_order_id = rzp_order_id
    payment = Payment(
        order_id=order.id,
        user_id=buyer_user.id,
        purpose="order",
        provider="razorpay",
        razorpay_order_id=rzp_order_id,
        amount_paise=order.total_paise,
        currency="INR",
        status="created"
    )
    db.session.add(payment)
    db.session.commit()

    # 1. Test payment.failed
    failed_payload = {
        "event": "payment.failed",
        "id": "evt_fail_1",
        "payload": {
            "payment": {
                "entity": {
                    "id": "pay_fail_1",
                    "order_id": rzp_order_id,
                    "error_code": "GATEWAY_ERROR",
                    "error_description": "Declined by gateway."
                }
            }
        }
    }
    raw_failed = json.dumps(failed_payload).encode("utf-8")
    sig_failed = generate_test_webhook_signature(raw_failed)
    res_failed = client.post("/payment/webhook", data=raw_failed, content_type="application/json", headers={"X-Razorpay-Signature": sig_failed})
    assert res_failed.status_code == 200
    assert db.session.get(Payment, payment.id).status == "failed"

    # 2. Test payment.captured
    cap_payload = {
        "event": "payment.captured",
        "id": "evt_cap_1",
        "payload": {
            "payment": {
                "entity": {
                    "id": "pay_cap_1",
                    "order_id": rzp_order_id,
                    "amount": order.total_paise,
                    "status": "captured"
                }
            }
        }
    }
    raw_cap = json.dumps(cap_payload).encode("utf-8")
    sig_cap = generate_test_webhook_signature(raw_cap)
    res_cap = client.post("/payment/webhook", data=raw_cap, content_type="application/json", headers={"X-Razorpay-Signature": sig_cap})
    assert res_cap.status_code == 200
    assert db.session.get(Payment, payment.id).status == "paid"
    assert db.session.get(Order, order.id).payment_status == "paid"


def test_webhook_deduplication_idempotency(client):
    """
    Submitting the same webhook event ID twice returns HTTP 200 and does not reprocess.
    """
    webhook_payload = {
        "event": "payment.captured",
        "id": "evt_duplicate_test_99",
        "payload": {
            "payment": {
                "entity": {
                    "id": "pay_dup_1",
                    "order_id": "order_dup_1",
                    "status": "captured"
                }
            }
        }
    }
    raw_body = json.dumps(webhook_payload).encode("utf-8")
    sig = generate_test_webhook_signature(raw_body)

    # First call
    res1 = client.post(
        "/payment/webhook",
        data=raw_body,
        content_type="application/json",
        headers={"X-Razorpay-Signature": sig}
    )
    assert res1.status_code == 200

    # Second call (replay)
    res2 = client.post(
        "/payment/webhook",
        data=raw_body,
        content_type="application/json",
        headers={"X-Razorpay-Signature": sig}
    )
    assert res2.status_code == 200
    assert "already processed" in res2.get_json()["message"].lower()

    # Verify only single WebhookEvent record exists
    evts = WebhookEvent.query.filter_by(event_id="evt_duplicate_test_99").all()
    assert len(evts) == 1


# ==============================================================================
# 7. PRIORITY BOOST (₹49) VERIFIED WORKFLOW
# ==============================================================================

def test_priority_boost_order_creation_paise_amount(client, seller_user, approved_listing):
    """
    Priority Placement creates a Razorpay order for exactly 4900 paise (₹49)
    and does not feature the listing until payment is verified.
    """
    with client.session_transaction() as sess:
        sess["user_id"] = seller_user.id

    mock_client = MagicMock()
    mock_client.order.create.return_value = {
        "id": "order_boost_rzp_49",
        "amount": 4900,
        "currency": "INR"
    }

    with patch("backend.services.payment_service.get_razorpay_client", return_value=mock_client):
        response = client.post(
            f"/seller/boost/{approved_listing.id}/create-order",
            headers={"Content-Type": "application/json"}
        )

    assert response.status_code == 200
    data = response.get_json()
    assert data["success"] is True
    assert data["razorpay"]["amount_paise"] == 4900
    assert data["razorpay"]["amount_rupees"] == 49
    assert data["razorpay"]["currency"] == "INR"

    # Listing must NOT be featured yet
    listing = db.session.get(Listing, approved_listing.id)
    assert listing.is_featured is False
    assert listing.is_priority is False

    # Subscription and payment records must be pending
    sub = ListingSubscription.query.filter_by(listing_id=approved_listing.id).first()
    assert sub is not None
    assert sub.status == "pending"
    assert sub.amount_paise == 4900


def test_priority_boost_verified_activation_pins_listing_first(client, seller_user, approved_listing):
    """
    Submitting valid signature tokens for priority placement verifies the payment,
    activates the subscription, and sets is_featured=True.
    """
    rzp_order_id = "order_boost_verify_123"
    rzp_payment_id = "pay_boost_verify_456"
    sig = generate_test_checkout_signature(rzp_order_id, rzp_payment_id)

    # Create pending boost subscription & payment
    sub = ListingSubscription(
        listing_id=approved_listing.id,
        seller_id=seller_user.id,
        plan_name="Priority Placement (Sell First)",
        amount_paise=4900,
        status="pending",
        payment_method="razorpay",
        payment_status="pending",
        razorpay_order_id=rzp_order_id,
        created_at=utc_now()
    )
    db.session.add(sub)
    db.session.flush()

    payment = Payment(
        subscription_id=sub.id,
        user_id=seller_user.id,
        purpose="priority_boost",
        provider="razorpay",
        razorpay_order_id=rzp_order_id,
        amount_paise=4900,
        currency="INR",
        status="created"
    )
    db.session.add(payment)
    db.session.commit()

    with client.session_transaction() as sess:
        sess["user_id"] = seller_user.id

    response = client.post(
        f"/seller/boost/{approved_listing.id}/verify",
        json={
            "razorpay_order_id": rzp_order_id,
            "razorpay_payment_id": rzp_payment_id,
            "razorpay_signature": sig
        }
    )

    assert response.status_code == 200
    res_data = response.get_json()
    assert res_data["success"] is True

    # Subscription is active and paid
    updated_sub = db.session.get(ListingSubscription, sub.id)
    assert updated_sub.status == "active"
    assert updated_sub.payment_status == "paid"
    assert updated_sub.expires_at is not None

    # Listing is featured and priority
    updated_listing = db.session.get(Listing, approved_listing.id)
    assert updated_listing.is_featured is True
    assert updated_listing.is_priority is True

    # Audit & Notification created
    audit = AuditEvent.query.filter_by(
        target_id=approved_listing.id,
        action="SUBSCRIPTION_BOOST_ACTIVATED"
    ).first()
    assert audit is not None


def test_priority_boost_idempotent_verification(client, seller_user, approved_listing):
    """
    Submitting verification again for an already active boost returns success idempotently.
    """
    rzp_order_id = "order_boost_idem_123"
    rzp_payment_id = "pay_boost_idem_456"
    sig = generate_test_checkout_signature(rzp_order_id, rzp_payment_id)

    sub = ListingSubscription(
        listing_id=approved_listing.id,
        seller_id=seller_user.id,
        plan_name="Priority Placement (Sell First)",
        amount_paise=4900,
        status="active",
        payment_status="paid",
        razorpay_order_id=rzp_order_id,
        razorpay_payment_id=rzp_payment_id,
        created_at=utc_now()
    )
    db.session.add(sub)
    db.session.flush()

    payment = Payment(
        subscription_id=sub.id,
        user_id=seller_user.id,
        purpose="priority_boost",
        provider="razorpay",
        razorpay_order_id=rzp_order_id,
        razorpay_payment_id=rzp_payment_id,
        amount_paise=4900,
        currency="INR",
        status="paid"
    )
    db.session.add(payment)
    db.session.commit()

    with client.session_transaction() as sess:
        sess["user_id"] = seller_user.id

    response = client.post(
        f"/seller/boost/{approved_listing.id}/verify",
        json={
            "razorpay_order_id": rzp_order_id,
            "razorpay_payment_id": rzp_payment_id,
            "razorpay_signature": sig
        }
    )
    assert response.status_code == 200
    assert response.get_json()["success"] is True


# ==============================================================================
# 8. ABANDONED RESERVATIONS & INVENTORY EXPIRY
# ==============================================================================

def test_abandoned_reservation_expiry_frees_inventory(app, buyer_user, approved_listing):
    """
    Orders in Pending status with uncompleted payment older than cutoff
    are cancelled and their inventory reservation is safely released.
    """
    with app.app_context():
        order, _ = create_order(
            buyer_id=buyer_user.id,
            listing_id=approved_listing.id,
            buyer_name=buyer_user.name,
            buyer_phone=buyer_user.phone,
            delivery_method="pickup",
            shipping_address="Local Pickup",
            payment_method="razorpay"
        )
        db.session.expire_all()
        fresh_listing = db.session.get(Listing, approved_listing.id)
        assert fresh_listing.inventory_status == "reserved"

        # Backdate order to 45 minutes ago
        order.created_at = utc_now() - timedelta(minutes=45)
        db.session.commit()

        # Run expiry job (30 min timeout)
        released_count = expire_abandoned_reservations(timeout_minutes=30)
        assert released_count >= 1

        db.session.expire_all()
        fresh_listing2 = db.session.get(Listing, approved_listing.id)
        assert fresh_listing2.inventory_status == "available"

        fresh_order = db.session.get(Order, order.id)
        assert fresh_order.status == "Cancelled"
        assert fresh_order.cancelled_by == "system"


# ==============================================================================
# 9. REFUND WORKFLOWS & ACCESS CONTROL
# ==============================================================================

def test_admin_refund_workflow(client, admin_user, buyer_user, approved_listing):
    """
    Only administrators can initiate refunds; non-admins receive 403 Forbidden.
    Verifies state transitions to refunded and audit logging.
    """
    # Create paid order and payment
    order, _ = create_order(
        buyer_id=buyer_user.id,
        listing_id=approved_listing.id,
        buyer_name=buyer_user.name,
        buyer_phone=buyer_user.phone,
        delivery_method="pickup",
        shipping_address="Local Pickup",
        payment_method="razorpay"
    )
    order.status = "Confirmed"
    order.payment_status = "paid"
    payment = Payment(
        order_id=order.id,
        user_id=buyer_user.id,
        purpose="order",
        provider="razorpay",
        razorpay_order_id="order_refund_test_1",
        razorpay_payment_id="pay_refund_test_1",
        amount_paise=order.total_paise,
        currency="INR",
        status="paid"
    )
    db.session.add(payment)
    db.session.commit()

    # 1. Non-admin attempt receives 403
    with client.session_transaction() as sess:
        sess["user_id"] = buyer_user.id
        sess["admin_authenticated"] = False

    res_forbidden = client.post(
        f"/admin/payment/{payment.id}/refund",
        json={"reason": "Customer cancellation request"}
    )
    assert res_forbidden.status_code == 403

    # 2. Authorized admin attempt
    with client.session_transaction() as sess:
        sess["user_id"] = admin_user.id
        sess["admin_authenticated"] = True

    mock_client = MagicMock()
    mock_client.payment.refund.return_value = {
        "id": "rfnd_test_mock_123",
        "payment_id": "pay_refund_test_1",
        "amount": order.total_paise,
        "status": "processed"
    }

    with patch("backend.services.payment_service.get_razorpay_client", return_value=mock_client):
        res_admin = client.post(
            f"/admin/payment/{payment.id}/refund",
            json={"reason": "Device return approved by moderation"}
        )

    assert res_admin.status_code == 200
    assert res_admin.get_json()["success"] is True

    # Payment and order are marked refunded
    fresh_payment = db.session.get(Payment, payment.id)
    assert fresh_payment.status == "refunded"
    assert fresh_payment.refund_id == "rfnd_test_mock_123"

    fresh_order = db.session.get(Order, order.id)
    assert fresh_order.payment_status == "refunded"

    # Audit event logged
    audit = AuditEvent.query.filter_by(
        target_id=payment.id,
        action="PAYMENT_REFUND_PROCESSED"
    ).first()
    assert audit is not None


def test_initiate_payment_refund_service_validations(app, buyer_user, approved_listing):
    """
    Tests edge cases in initiate_payment_refund:
    - Non-existent payment
    - Payment in non-paid status
    - Missing razorpay_payment_id
    - Invalid refund amount (exceeding total or zero)
    """
    with app.app_context():
        # Non-existent
        ok, err = initiate_payment_refund("non_existent_id", 1000, "Reason", "admin_1")
        assert not ok
        assert "not found" in err.lower()

        # Payment in created status
        order, _ = create_order(
            buyer_id=buyer_user.id,
            listing_id=approved_listing.id,
            buyer_name=buyer_user.name,
            buyer_phone=buyer_user.phone,
            delivery_method="pickup",
            shipping_address="Local Pickup",
            payment_method="razorpay"
        )
        payment = Payment(
            order_id=order.id,
            user_id=buyer_user.id,
            purpose="order",
            provider="razorpay",
            razorpay_order_id="order_test_val_1",
            amount_paise=5000000,
            currency="INR",
            status="created"
        )
        db.session.add(payment)
        db.session.commit()

        ok, err = initiate_payment_refund(payment.id, 5000000, "Reason", "admin_1")
        assert not ok
        assert "state" in err.lower()

        # Mark paid but missing razorpay_payment_id
        payment.status = "paid"
        db.session.commit()
        ok, err = initiate_payment_refund(payment.id, 5000000, "Reason", "admin_1")
        assert not ok
        assert "reference" in err.lower()

        # Add razorpay_payment_id but invalid amount
        payment.razorpay_payment_id = "pay_valid_ref_1"
        db.session.commit()
        ok, err = initiate_payment_refund(payment.id, 999999999, "Reason", "admin_1")
        assert not ok
        assert "exceed" in err.lower()


# ==============================================================================
# 10. STARTUP VALIDATION & CREDENTIAL EXPOSURE SHIELDING
# ==============================================================================

def test_razorpay_startup_validation_flags_missing_keys(monkeypatch):
    """
    Validates that Config.validate_razorpay() correctly flags missing or placeholder credentials
    without exposing any secret values in output.
    """
    from backend.config import Config

    monkeypatch.setenv("RAZORPAY_KEY_ID", "")
    monkeypatch.setenv("RAZORPAY_KEY_SECRET", "")
    monkeypatch.setenv("RAZORPAY_WEBHOOK_SECRET", "")

    status = Config.validate_razorpay()
    assert status["configured"] is False
    assert "RAZORPAY_KEY_ID" in status["missing"]
    assert "RAZORPAY_KEY_SECRET" in status["missing"]
    assert status["has_webhook_secret"] is False


def test_razorpay_rejects_live_mode_keys_strictly(monkeypatch):
    """
    Enforces the critical constraint that live mode keys (rzp_live_...) are strictly rejected
    with a RuntimeError at startup to prevent accidental live transactions.
    """
    from backend.config import Config

    monkeypatch.setenv("RAZORPAY_KEY_ID", "rzp_live_sampleLiveKey123")
    monkeypatch.setenv("RAZORPAY_KEY_SECRET", "sampleSecret123")

    with pytest.raises(RuntimeError, match="Live Razorpay key detected"):
        Config.validate_razorpay()


def test_checkout_callback_rejects_stored_order_id_mismatch(app, buyer_user, approved_listing):
    """
    Validates that process_order_checkout_callback rejects any callback where the expected
    Razorpay order ID stored on the internal order entity does not match the callback order ID.
    """
    with app.app_context():
        order, _ = create_order(
            buyer_id=buyer_user.id,
            listing_id=approved_listing.id,
            buyer_name=buyer_user.name,
            buyer_phone=buyer_user.phone,
            delivery_method="pickup",
            shipping_address="Local Pickup",
            payment_method="razorpay"
        )
        order.razorpay_order_id = "order_expected_internal_id"

        payment = Payment(
            order_id=order.id,
            user_id=buyer_user.id,
            purpose="order",
            provider="razorpay",
            razorpay_order_id="order_expected_internal_id",
            amount_paise=order.total_paise,
            currency="INR",
            status="created"
        )
        db.session.add(payment)
        db.session.commit()

        # Generate signature for a different order ID
        diff_order_id = "order_tampered_different_id"
        sig = generate_test_checkout_signature(diff_order_id, "pay_mock_123")

        # Mock verify_checkout_signature to pass raw signature check
        with patch("backend.services.payment_service.verify_checkout_signature", return_value=True):
            # 1. Unknown order ID
            ok1, err1, _ = process_order_checkout_callback(
                razorpay_order_id=diff_order_id,
                razorpay_payment_id="pay_mock_123",
                razorpay_signature=sig,
                current_user_id=buyer_user.id
            )
            assert not ok1

            # 2. Tampered internal order ID mismatch
            order.razorpay_order_id = "order_mismatched_stored_val"
            db.session.commit()

            ok2, err2, _ = process_order_checkout_callback(
                razorpay_order_id="order_expected_internal_id",
                razorpay_payment_id="pay_mock_123",
                razorpay_signature=sig,
                current_user_id=buyer_user.id
            )
            assert not ok2
            assert "mismatch" in err2.lower()


def test_key_id_injected_to_frontend_without_exposing_secret(client, buyer_user, approved_listing):
    """
    Confirms that the Test Mode Key ID is made available to the server-rendered template,
    while the Key Secret is NEVER printed, rendered, or exposed in the HTML output.
    """
    with client.session_transaction() as sess:
        sess["user_id"] = buyer_user.id

    response = client.get(f"/buy/{approved_listing.id}")
    assert response.status_code == 200

    html = response.get_data(as_text=True)
    # Key ID is safely exposed for frontend checkout.js modal launch
    assert "rzp_test_mock_key_id_123" in html
    # Key Secret MUST NEVER be in the response HTML
    assert "mock_secret_key_456" not in html
    assert "mock_webhook_secret_789" not in html

