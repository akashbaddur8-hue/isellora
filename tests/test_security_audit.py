"""
Security and Regression Test Suite for Technical Audit Findings:
- SEC-01: Admin privilege escalation prevention on registration and login
- SEC-02: Production configuration fail-safe and secret validation
- SEC-03: Paid boost activation rejection and 0 unverified revenue
- SEC-04: Order status transitions (shipping requires Shipped, payment decoupled)
- SEC-05: Open redirect prevention in authentication flow
- SEC-06: Support ticket order ownership validation server-side
- SEC-07: Numeric query parameter validation (inf, nan, negative, pagination)
"""
import uuid
import pytest
from backend.models import User, Listing, Order, SupportTicket, ListingSubscription, AuditEvent
from backend.database import db
from backend.config import ProductionConfig
from backend.services.auth_service import is_safe_redirect_url
from backend.services.order_service import transition_order_status, record_cod_payment_collection


# =============================================================================
# SEC-01: Admin Privilege Escalation Prevention
# =============================================================================
def test_signup_with_admin_email_remains_regular_user(client, app):
    """Registering using the ADMIN_EMAIL must never grant admin privileges."""
    admin_email = app.config.get("ADMIN_EMAIL", "akashbaddur2007@gmail.com")
    
    # Ensure user does not already exist
    existing = User.query.filter_by(email=admin_email).first()
    if existing:
        db.session.delete(existing)
        db.session.commit()

    response = client.post("/signup", data={
        "name": "Audit Test User",
        "email": admin_email,
        "phone": "9876543210",
        "password": "SecurePassword123!",
        "confirm_password": "SecurePassword123!"
    }, follow_redirects=True)
    assert response.status_code == 200

    created_user = User.query.filter_by(email=admin_email).first()
    assert created_user is not None
    assert created_user.role == "user"
    assert created_user.is_admin is False


def test_signup_form_role_tampering_ignored(client):
    """Submitting role=admin or is_admin=true in signup form must be ignored."""
    test_email = "tamper_test@example.com"
    response = client.post("/signup", data={
        "name": "Tamper Test",
        "email": test_email,
        "phone": "9876543211",
        "password": "SecurePassword123!",
        "confirm_password": "SecurePassword123!",
        "role": "admin",
        "is_admin": "true",
        "admin": "1"
    }, follow_redirects=True)
    assert response.status_code == 200

    user = User.query.filter_by(email=test_email).first()
    assert user is not None
    assert user.role == "user"
    assert user.is_admin is False


def test_admin_dashboard_requires_verified_admin(client, buyer_user):
    """Anonymous visitors and regular users must receive 403 or redirect away from /admin."""
    # 1. Anonymous visitor
    resp_anon = client.get("/admin", follow_redirects=False)
    assert resp_anon.status_code == 302
    assert "/admin/login" in resp_anon.headers["Location"]

    # 2. Regular logged-in user without admin session
    with client.session_transaction() as sess:
        sess["user_id"] = buyer_user.id
        sess["user_name"] = buyer_user.name
        sess["user_email"] = buyer_user.email
        sess["admin_authenticated"] = False

    resp_user = client.get("/admin", follow_redirects=False)
    assert resp_user.status_code in (302, 403)


# =============================================================================
# SEC-02: Secrets & Production Config Fail-Safe
# =============================================================================
def test_production_config_rejects_missing_or_insecure_secrets(monkeypatch):
    """ProductionConfig.validate_production must fail safe if SECRET_KEY is weak/placeholder."""
    # Missing SECRET_KEY
    monkeypatch.setenv("SECRET_KEY", "")
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost:5432/isellora")
    with pytest.raises(RuntimeError, match="CRITICAL SECURITY CONFIGURATION ERROR"):
        ProductionConfig.validate_production()

    # Known insecure placeholder SECRET_KEY
    monkeypatch.setenv("SECRET_KEY", "isellora-development-fallback-secret-key-32-chars-min!!")
    with pytest.raises(RuntimeError, match="CRITICAL SECURITY CONFIGURATION ERROR"):
        ProductionConfig.validate_production()

    # Too short SECRET_KEY
    monkeypatch.setenv("SECRET_KEY", "short-secret")
    with pytest.raises(RuntimeError, match="CRITICAL SECURITY CONFIGURATION ERROR"):
        ProductionConfig.validate_production()


def test_production_config_rejects_missing_database_url(monkeypatch):
    """ProductionConfig.validate_production must fail safe if DATABASE_URL is missing."""
    monkeypatch.setenv("SECRET_KEY", "a-very-long-random-production-secret-key-64-characters-long-and-secure")
    monkeypatch.setenv("DATABASE_URL", "")
    with pytest.raises(RuntimeError, match="CRITICAL CONFIGURATION ERROR"):
        ProductionConfig.validate_production()


# =============================================================================
# SEC-03: Paid Priority Boost Deferral & Revenue Integrity
# =============================================================================
def test_sell_form_priority_boost_checkbox_ignored(client, seller_user):
    """Submitting priority_boost in sell form does NOT activate boost or mark is_featured."""
    with client.session_transaction() as sess:
        sess["user_id"] = seller_user.id
        sess["user_name"] = seller_user.name
        sess["user_email"] = seller_user.email
        sess["user_phone"] = seller_user.phone

    import io
    fake_img = (io.BytesIO(b"\xff\xd8\xff\xe0" + b"\x00" * 50), "phone.jpg")
    response = client.post("/sell", data={
        "brand": "OnePlus",
        "model": "11 5G",
        "storage": "256GB",
        "price": "38000",
        "condition": "Excellent",
        "screen_damage": "No Damage",
        "body_damage": "No Damage",
        "repair_history": "Never Repaired",
        "original_display": "Yes",
        "accessories": "Original Box & Charger",
        "description": "Clean phone with box and charger included.",
        "priority_boost": "1",  # Malicious / submitted boost checkbox
        "photos": [fake_img]
    }, content_type="multipart/form-data", follow_redirects=True)

    assert response.status_code == 200

    listing = Listing.query.filter_by(seller_id=seller_user.id, model="11 5G").first()
    assert listing is not None
    assert listing.is_featured is False
    assert listing.is_priority is False

    # No unverified subscription created
    sub = ListingSubscription.query.filter_by(listing_id=listing.id).first()
    assert sub is None


# =============================================================================
# SEC-04: Order Status Transitions & Payment Decoupling
# =============================================================================
def test_shipping_order_cannot_skip_shipped_status(app, seller_user, buyer_user, approved_listing):
    """Shipping order cannot jump directly from Confirmed to Delivered."""
    order = Order(
        id=str(uuid.uuid4()),
        checkout_token=str(uuid.uuid4()),
        buyer_id=buyer_user.id,
        seller_id=seller_user.id,
        listing_id=approved_listing.id,
        brand="Apple",
        model="iPhone 14",
        storage="128GB",
        price_paise=4500000,
        delivery_charge_paise=15000,
        total_paise=4515000,
        delivery_method="shipping",
        buyer_name="Test Buyer",
        buyer_phone="9876543210",
        shipping_address="123 Test Street, Bangalore",
        pincode="560001",
        status="Confirmed",
        payment_method="cod",
        payment_status="Not collected"
    )
    db.session.add(order)
    db.session.commit()

    # Attempt Confirmed -> Delivered on shipping order
    success, msg = transition_order_status(
        order=order,
        new_status="Delivered",
        actor_id=seller_user.id,
        actor_role="seller"
    )
    assert success is False
    assert "must be marked as Shipped" in msg

    # Order status must remain Confirmed
    assert order.status == "Confirmed"


def test_order_delivery_does_not_auto_collect_payment(app, seller_user, buyer_user, approved_listing):
    """Order delivery marks listing sold but decouples payment status."""
    order = Order(
        id=str(uuid.uuid4()),
        checkout_token=str(uuid.uuid4()),
        buyer_id=buyer_user.id,
        seller_id=seller_user.id,
        listing_id=approved_listing.id,
        brand="Apple",
        model="iPhone 14",
        storage="128GB",
        price_paise=4500000,
        delivery_charge_paise=15000,
        total_paise=4515000,
        delivery_method="shipping",
        buyer_name="Test Buyer",
        buyer_phone="9876543210",
        shipping_address="123 Test Street, Bangalore",
        pincode="560001",
        courier_name="BlueDart",
        tracking_number="BD12345678",
        status="Shipped",
        payment_method="cod",
        payment_status="Not collected"
    )
    db.session.add(order)
    db.session.commit()

    # Transition Shipped -> Delivered
    success, msg = transition_order_status(
        order=order,
        new_status="Delivered",
        actor_id=seller_user.id,
        actor_role="seller"
    )
    assert success is True
    assert order.status == "Delivered"
    # Payment status remains decoupled
    assert order.payment_status == "Not collected"

    # Authorized explicit COD collection workflow
    paid_ok, paid_msg = record_cod_payment_collection(
        order=order,
        actor_id=seller_user.id,
        actor_role="seller",
        notes="Cash received at doorstep"
    )
    assert paid_ok is True
    assert order.payment_status == "Collected upon delivery"


# =============================================================================
# SEC-05: Safe Redirect Validation (Open Redirect Prevention)
# =============================================================================
def test_is_safe_redirect_url():
    """Verify is_safe_redirect_url rejects all malicious redirect patterns."""
    # Safe relative paths
    assert is_safe_redirect_url("/") is True
    assert is_safe_redirect_url("/products") is True
    assert is_safe_redirect_url("/account") is True
    assert is_safe_redirect_url("/seller/dashboard") is True

    # Malicious protocol-relative URLs
    assert is_safe_redirect_url("//evil.example.com") is False
    assert is_safe_redirect_url("///evil.example.com") is False

    # Backslash and Windows drive path tricks
    assert is_safe_redirect_url("/\\evil.example.com") is False
    assert is_safe_redirect_url("\\evil.example.com") is False
    assert is_safe_redirect_url("/%5cevil.example.com") is False

    # External schemes
    assert is_safe_redirect_url("https://evil.example.com") is False
    assert is_safe_redirect_url("http://evil.example.com") is False
    assert is_safe_redirect_url("javascript:alert(1)") is False
    assert is_safe_redirect_url("data:text/html,evil") is False

    # Empty or whitespace
    assert is_safe_redirect_url("") is False
    assert is_safe_redirect_url("   ") is False
    assert is_safe_redirect_url(None) is False


def test_login_open_redirect_fallback(client, buyer_user):
    """Submitting next=//evil.example on login falls back to home."""
    response = client.post("/login?next=//evil.example.com", data={
        "email": buyer_user.email,
        "password": "SecurePass123"
    }, follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["Location"] in ("/", "http://localhost/", "http://127.0.0.1/")
    assert "evil.example.com" not in response.headers["Location"]


# =============================================================================
# SEC-06: Support Ticket Order Ownership Validation
# =============================================================================
def test_support_ticket_rejects_unowned_order(client, buyer_user, seller_user, approved_listing):
    """Buyer A cannot create a support ticket referencing Buyer B's order."""
    # Create order belonging to other buyer (seller_user as buyer here)
    other_order = Order(
        id=str(uuid.uuid4()),
        checkout_token=str(uuid.uuid4()),
        buyer_id=seller_user.id,
        seller_id="third-party-seller",
        listing_id=approved_listing.id,
        brand="Apple",
        model="iPhone 14",
        storage="128GB",
        price_paise=4500000,
        delivery_charge_paise=0,
        total_paise=4500000,
        delivery_method="pickup",
        buyer_name="Other Buyer",
        buyer_phone="9876543210",
        shipping_address="Local",
        status="Confirmed",
        payment_method="cod",
        payment_status="Not collected"
    )
    db.session.add(other_order)
    db.session.commit()

    # Log in as buyer_user
    with client.session_transaction() as sess:
        sess["user_id"] = buyer_user.id
        sess["user_name"] = buyer_user.name
        sess["user_email"] = buyer_user.email

    # Submit ticket referencing other buyer's order
    response = client.post("/support", data={
        "subject": "Unauthorized access attempt",
        "category": "Order Issue",
        "order_id": other_order.id,
        "message": "I am trying to claim another user's order."
    }, follow_redirects=True)

    # Must return 400 bad request without linking ticket to that order
    assert response.status_code == 400

    # Ensure no ticket created for other_order.id by buyer_user
    ticket = SupportTicket.query.filter_by(user_id=buyer_user.id, order_id=other_order.id).first()
    assert ticket is None


# =============================================================================
# SEC-07: Safe Handling of Malformed Numeric Inputs
# =============================================================================
@pytest.mark.parametrize("query_param", [
    "min_price=inf",
    "min_price=-inf",
    "min_price=nan",
    "min_price=-5000",
    "max_price=inf",
    "max_price=-inf",
    "max_price=nan",
    "min_price=not_a_number",
    "min_price=99999999999999999999",
    "page=inf",
    "page=-1",
    "page=0",
    "page=999999",
    "page=abc"
])
def test_products_catalog_numeric_input_safety(client, query_param):
    """GET /products with malformed numeric parameters must return 200 without unhandled 500 crashes."""
    response = client.get(f"/products?{query_param}")
    assert response.status_code == 200
    assert b"iSellora" in response.data


def test_products_catalog_inverted_prices_handled(client):
    """GET /products with min_price > max_price must be handled gracefully."""
    response = client.get("/products?min_price=50000&max_price=10000")
    assert response.status_code == 200


def test_create_admin_script_provisions_admin_with_phone(app, monkeypatch):
    """
    create_admin CLI must provision new admin users with a valid phone number,
    satisfying NOT NULL constraint on users.phone.
    """
    import sys
    from create_admin import main as create_admin_main
    admin_email = f"testadmin_{uuid.uuid4().hex[:6]}@isellora.com"
    monkeypatch.setattr(sys, "argv", [
        "create_admin.py",
        "--email", admin_email,
        "--password", "AdminPassword123!",
        "--name", "System Administrator",
        "--phone", "9876543210"
    ])

    create_admin_main(app=app)

    with app.app_context():
        user = User.query.filter_by(email=admin_email).first()
        assert user is not None
        assert user.role == "admin"
        assert user.phone == "9876543210"
        assert user.name == "System Administrator"
        assert user.check_password("AdminPassword123!") is True


