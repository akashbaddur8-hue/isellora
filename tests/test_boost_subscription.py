"""
Tests for Priority Placement (₹49) Subscription Feature:
- Access control and ownership verification for boost subscription
- ₹49 (4900 paise) subscription record creation and status updates
- Listing ranking priority (boosted listing ranks #1 at top of catalog)
- Admin dashboard subscription metrics
"""
import uuid
from backend.models import Listing, ListingSubscription, Notification, AuditEvent
from backend.database import db
from backend.services.listing_service import query_catalog


def test_boost_page_access_control(client, seller_user, other_seller_user, approved_listing):
    # Unauthenticated visitor redirected to login
    response = client.get(f"/seller/boost/{approved_listing.id}")
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]

    # Other seller receives 403 Forbidden
    with client.session_transaction() as sess:
        sess["user_id"] = other_seller_user.id
        sess["user_name"] = other_seller_user.name
        sess["user_email"] = other_seller_user.email

    response = client.get(f"/seller/boost/{approved_listing.id}")
    assert response.status_code == 403

    # Legitimate owner receives 200 OK with ₹49 subscription information
    with client.session_transaction() as sess:
        sess["user_id"] = seller_user.id
        sess["user_name"] = seller_user.name
        sess["user_email"] = seller_user.email

    response = client.get(f"/seller/boost/{approved_listing.id}")
    assert response.status_code == 200
    assert b"Priority Placement" in response.data
    assert b"49" in response.data


def test_boost_activation_success(client, seller_user, approved_listing):
    with client.session_transaction() as sess:
        sess["user_id"] = seller_user.id
        sess["user_name"] = seller_user.name
        sess["user_email"] = seller_user.email

    assert not approved_listing.is_featured
    assert not approved_listing.is_priority

    # Activate Priority Placement subscription
    response = client.post(f"/seller/boost/{approved_listing.id}", follow_redirects=True)
    assert response.status_code == 200

    # Verify listing updated
    updated_listing = db.session.get(Listing, approved_listing.id)
    assert updated_listing.is_featured is True
    assert updated_listing.is_priority is True
    assert updated_listing.featured_at is not None

    # Verify ListingSubscription record created with exact ₹49 (4900 paise)
    sub = ListingSubscription.query.filter_by(listing_id=approved_listing.id).first()
    assert sub is not None
    assert sub.seller_id == seller_user.id
    assert sub.amount_paise == 4900
    assert sub.amount_rupees == 49
    assert sub.status == "active"
    assert "Priority Placement" in sub.plan_name

    # Verify notification sent to seller
    notif = Notification.query.filter_by(user_id=seller_user.id).order_by(Notification.created_at.desc()).first()
    assert notif is not None
    assert "Priority Placement Activated" in notif.title

    # Verify audit event logged
    audit = AuditEvent.query.filter_by(target_id=approved_listing.id, action="SUBSCRIPTION_BOOST_ACTIVATED").first()
    assert audit is not None


def test_boost_idempotency_when_already_boosted(client, seller_user, approved_listing):
    with client.session_transaction() as sess:
        sess["user_id"] = seller_user.id
        sess["user_name"] = seller_user.name
        sess["user_email"] = seller_user.email

    # First activation
    client.post(f"/seller/boost/{approved_listing.id}")
    assert ListingSubscription.query.filter_by(listing_id=approved_listing.id).count() == 1

    # Second attempt should not duplicate subscription
    response = client.post(f"/seller/boost/{approved_listing.id}", follow_redirects=True)
    assert response.status_code == 200
    assert b"already active" in response.data
    assert ListingSubscription.query.filter_by(listing_id=approved_listing.id).count() == 1


def test_boosted_listing_ranks_first_in_catalog(app, seller_user):
    # Create non-boosted listing
    regular_listing = Listing(
        id=str(uuid.uuid4()),
        seller_id=seller_user.id,
        brand="Apple",
        model="iPhone 13",
        price_paise=3500000,
        storage="128GB",
        condition="Good",
        description="Regular listing",
        approval_status="approved",
        inventory_status="available",
        is_featured=False
    )
    db.session.add(regular_listing)

    # Create boosted listing (Sell First subscription)
    boosted_listing = Listing(
        id=str(uuid.uuid4()),
        seller_id=seller_user.id,
        brand="Apple",
        model="iPhone 14",
        price_paise=4500000,
        storage="128GB",
        condition="Like New",
        description="Boosted priority listing",
        approval_status="approved",
        inventory_status="available",
        is_featured=True
    )
    db.session.add(boosted_listing)
    db.session.commit()

    # Query catalog
    catalog = query_catalog(brand="Apple", sort_by="latest")
    items = catalog.items

    # Boosted phone must appear first in catalog
    assert len(items) >= 2
    assert items[0].id == boosted_listing.id
    assert items[0].is_priority is True


def test_admin_dashboard_metrics_for_boosts(client, admin_user, seller_user, approved_listing):
    # Create subscription
    sub = ListingSubscription(
        id=str(uuid.uuid4()),
        listing_id=approved_listing.id,
        seller_id=seller_user.id,
        plan_name="Priority Placement (Sell First)",
        amount_paise=4900,
        status="active"
    )
    approved_listing.is_featured = True
    db.session.add(sub)
    db.session.commit()

    # Admin session
    with client.session_transaction() as sess:
        sess["user_id"] = admin_user.id
        sess["user_name"] = admin_user.name
        sess["user_email"] = admin_user.email
        sess["admin_authenticated"] = True

    response = client.get("/admin")
    assert response.status_code == 200
    assert b"Priority Boosts (&#8377;49)" in response.data or b"Priority Boosts" in response.data
    assert b"Boost Revenue" in response.data
    assert b"49" in response.data
