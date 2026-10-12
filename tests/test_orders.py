"""
Order workflow, server-side price calculation, inventory concurrency, and status state machine tests.
"""
from backend.models import Order, Listing, OrderStatusHistory
from backend.database import db
from backend.services.order_service import (
    create_order, transition_order_status
)


def test_server_side_price_calculation(app, buyer_user, seller_user, approved_listing):
    with app.app_context():
        # Listing price is Rs 50,000 (5,000,000 paise)
        # Attempt to create order with shipping
        order, err = create_order(
            buyer_id=buyer_user.id,
            listing_id=approved_listing.id,
            buyer_name=buyer_user.name,
            buyer_phone=buyer_user.phone,
            delivery_method="shipping",
            shipping_address="123 Test Street, Bangalore",
            pincode="560001"
        )
        assert err == ""
        assert order is not None
        # Must be exactly listing price (50,000) + 150 shipping = 50,150
        assert order.price_rupees == 50000
        assert order.delivery_charge_rupees == 150
        assert order.total_rupees == 50150
        assert order.status == "Pending"
        assert order.payment_status == "Not collected"


def test_single_unit_inventory_concurrency_race_condition(app, buyer_user, other_seller_user, approved_listing):
    with app.app_context():
        # First buyer claims the listing
        order1, err1 = create_order(
            buyer_id=buyer_user.id,
            listing_id=approved_listing.id,
            buyer_name="First Buyer",
            buyer_phone="9876543210",
            delivery_method="pickup",
            shipping_address="Local"
        )
        assert err1 == ""
        assert order1 is not None

        # Verify listing status transitioned to reserved
        fresh_listing = db.session.get(Listing, approved_listing.id)
        assert fresh_listing.inventory_status == "reserved"

        # Second concurrent buyer attempts to purchase the exact same listing
        order2, err2 = create_order(
            buyer_id=other_seller_user.id,
            listing_id=approved_listing.id,
            buyer_name="Second Buyer",
            buyer_phone="9876543212",
            delivery_method="pickup",
            shipping_address="Local"
        )
        # Second order MUST be rejected
        assert order2 is None
        assert "no longer available" in err2.lower() or "reserved" in err2.lower()


def test_duplicate_submission_idempotency(app, buyer_user, approved_listing):
    with app.app_context():
        token = "fixed-unique-checkout-token-123"
        order1, _ = create_order(
            buyer_id=buyer_user.id,
            listing_id=approved_listing.id,
            buyer_name="Buyer",
            buyer_phone="9876543210",
            delivery_method="pickup",
            shipping_address="Local",
            checkout_token=token
        )

        # Resubmit with same checkout_token
        order2, _ = create_order(
            buyer_id=buyer_user.id,
            listing_id=approved_listing.id,
            buyer_name="Buyer",
            buyer_phone="9876543210",
            delivery_method="pickup",
            shipping_address="Local",
            checkout_token=token
        )

        assert order1.id == order2.id
        assert Order.query.count() == 1


def test_order_status_transitions_and_audit_history(app, buyer_user, approved_listing):
    with app.app_context():
        order, _ = create_order(
            buyer_id=buyer_user.id,
            listing_id=approved_listing.id,
            buyer_name="Buyer",
            buyer_phone="9876543210",
            delivery_method="shipping",
            shipping_address="123 Test Street",
            pincode="560001"
        )

        # 1. Pending -> Confirmed
        ok, _ = transition_order_status(order, "Confirmed", actor_id="seller1", actor_role="seller")
        assert ok is True
        assert order.status == "Confirmed"

        # 2. Cannot mark Shipped without courier details
        ok, err = transition_order_status(order, "Shipped", actor_id="seller1", actor_role="seller")
        assert ok is False

        # Add courier
        order.courier_name = "Blue Dart"
        order.tracking_number = "BD12345678"
        db.session.commit()

        # Now Confirmed -> Shipped succeeds
        ok, _ = transition_order_status(order, "Shipped", actor_id="seller1", actor_role="seller")
        assert ok is True
        assert order.status == "Shipped"

        # 3. Shipped -> Delivered
        ok, _ = transition_order_status(order, "Delivered", actor_id="seller1", actor_role="seller")
        assert ok is True
        assert order.status == "Delivered"

        # Verify listing marked sold
        fresh_listing = db.session.get(Listing, approved_listing.id)
        assert fresh_listing.inventory_status == "sold"

        # 4. Terminal state: Delivered cannot transition back to Pending or Cancelled
        ok, _ = transition_order_status(order, "Pending", actor_id="seller1", actor_role="seller")
        assert ok is False

        # Verify audit history logged
        history = OrderStatusHistory.query.filter_by(order_id=order.id).all()
        assert len(history) >= 4  # Pending, Confirmed, Shipped, Delivered


def test_order_cancellation_releases_inventory(app, buyer_user, approved_listing):
    with app.app_context():
        order, _ = create_order(
            buyer_id=buyer_user.id,
            listing_id=approved_listing.id,
            buyer_name="Buyer",
            buyer_phone="9876543210",
            delivery_method="pickup",
            shipping_address="Local"
        )
        fresh_listing = db.session.get(Listing, approved_listing.id)
        assert fresh_listing.inventory_status == "reserved"

        # Cancel order
        ok, _ = transition_order_status(order, "Cancelled", actor_id=buyer_user.id, actor_role="buyer", notes="Changed mind")
        assert ok is True
        assert order.status == "Cancelled"

        # Verify listing is released back to available
        fresh_listing_after = db.session.get(Listing, approved_listing.id)
        assert fresh_listing_after.inventory_status == "available"
