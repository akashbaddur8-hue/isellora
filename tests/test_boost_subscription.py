"""
Tests for Priority Placement (₹49) Subscription Feature:
- Access control and ownership verification for boost subscription
- ₹49 (4900 paise) subscription record creation and status updates
- Listing ranking priority (boosted listing ranks #1 at top of catalog)
- Admin dashboard subscription metrics
"""
import io
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


def test_boost_activation_rejected_without_payment_gateway(client, seller_user, approved_listing):
    with client.session_transaction() as sess:
        sess["user_id"] = seller_user.id
        sess["user_name"] = seller_user.name
        sess["user_email"] = seller_user.email

    assert not approved_listing.is_featured
    assert not approved_listing.is_priority

    # Attempt to activate Priority Placement subscription via POST
    response = client.post(f"/seller/boost/{approved_listing.id}", follow_redirects=True)
    assert response.status_code == 200
    assert (b"Unable to initialize priority placement payment" in response.data or 
            b"temporarily unavailable pending payment gateway integration" in response.data)

    # Verify listing was NOT boosted
    updated_listing = db.session.get(Listing, approved_listing.id)
    assert updated_listing.is_featured is False
    assert updated_listing.is_priority is False

    # Verify no unverified ListingSubscription record created
    subs = ListingSubscription.query.filter_by(listing_id=approved_listing.id).all()
    assert len(subs) == 0


def test_boost_page_shows_active_when_already_boosted(client, seller_user, approved_listing):
    approved_listing.is_featured = True
    db.session.commit()

    with client.session_transaction() as sess:
        sess["user_id"] = seller_user.id
        sess["user_name"] = seller_user.name
        sess["user_email"] = seller_user.email

    response = client.get(f"/seller/boost/{approved_listing.id}")
    assert response.status_code == 200
    assert b"Priority Placement is Active" in response.data


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
    # Create verified paid subscription
    sub = ListingSubscription(
        id=str(uuid.uuid4()),
        listing_id=approved_listing.id,
        seller_id=seller_user.id,
        plan_name="Priority Placement (Sell First)",
        amount_paise=4900,
        status="active",
        payment_status="paid"
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


def _create_test_image():
    return (io.BytesIO(b"\xff\xd8\xff\xe0" + b"\x00" * 30), "phone.jpg")


def test_sell_page_renders_listing_type_cards(client, seller_user):
    with client.session_transaction() as sess:
        sess["user_id"] = seller_user.id
        sess["user_name"] = seller_user.name
        sess["user_email"] = seller_user.email

    response = client.get("/sell")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Choose Listing Type" in html
    assert "Free Listing" in html
    assert "Priority Listing" in html
    assert "Post your phone on iSellora without any listing fee." in html
    assert "Give your phone greater visibility with priority placement after approval." in html
    assert "₹0" in html
    assert "₹49" in html


def test_submit_free_listing_workflow(client, seller_user):
    with client.session_transaction() as sess:
        sess["user_id"] = seller_user.id
        sess["user_name"] = seller_user.name
        sess["user_email"] = seller_user.email

    img_stream, img_name = _create_test_image()
    response = client.post("/sell", data={
        "brand": "OnePlus",
        "model": "11R 5G",
        "price": "28000",
        "storage": "128GB",
        "description": "Clean OnePlus 11R.",
        "listing_type": "free",
        "photos": [(img_stream, img_name)]
    }, content_type="multipart/form-data")

    # Should redirect to seller dashboard
    assert response.status_code == 302
    assert "/seller/listings" in response.headers["Location"] or "/seller-dashboard" in response.headers["Location"]

    listing = Listing.query.filter_by(seller_id=seller_user.id, model="11R 5G").first()
    assert listing is not None
    assert listing.approval_status == "pending"
    assert listing.is_featured is False
    assert listing.is_priority is False


def test_submit_priority_listing_redirects_to_boost_checkout(client, seller_user):
    with client.session_transaction() as sess:
        sess["user_id"] = seller_user.id
        sess["user_name"] = seller_user.name
        sess["user_email"] = seller_user.email

    img_stream, img_name = _create_test_image()
    response = client.post("/sell", data={
        "brand": "Google",
        "model": "Pixel 8 Pro",
        "price": "62000",
        "storage": "256GB",
        "description": "Mint condition Pixel 8 Pro.",
        "listing_type": "priority",
        "photos": [(img_stream, img_name)]
    }, content_type="multipart/form-data")

    # Should redirect to boost page because Razorpay is configured
    assert response.status_code == 302
    assert "/seller/boost/" in response.headers["Location"]

    listing = Listing.query.filter_by(seller_id=seller_user.id, model="Pixel 8 Pro").first()
    assert listing is not None
    assert listing.approval_status == "pending"
    # Even though priority was selected, priority boost is NOT active until payment is completed!
    assert listing.is_featured is False
    assert listing.is_priority is False


def test_homepage_hero_renders_clean_theme_and_actions(client):
    response = client.get("/")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Smartphones You Can" in html
    assert "Trust" in html
    assert "Deserve" in html
    assert "Explore Phones" in html
    assert "Sell Your Phone" in html
    assert "Find your upgrade" in html
    assert "Your next phone" in html

