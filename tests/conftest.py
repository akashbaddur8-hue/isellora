"""
Pytest configuration and disposable fixtures for iSellora.
Uses in-memory SQLite and isolated test contexts.
"""
import pytest
import uuid
from backend.app import create_app
from backend.database import db
from backend.models import User, Listing, ListingImage, Order, OrderStatusHistory, Notification, utc_now


@pytest.fixture
def app():
    app = create_app("testing")
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def buyer_user(app):
    user = User(
        id=str(uuid.uuid4()),
        name="Test Buyer",
        email="buyer@test.local",
        phone="9876543210",
        role="user",
        is_active=True
    )
    user.set_password("SecurePass123")
    db.session.add(user)
    db.session.commit()
    return user


@pytest.fixture
def seller_user(app):
    user = User(
        id=str(uuid.uuid4()),
        name="Test Seller",
        email="seller@test.local",
        phone="9876543211",
        role="user",
        is_active=True
    )
    user.set_password("SecurePass123")
    db.session.add(user)
    db.session.commit()
    return user


@pytest.fixture
def other_seller_user(app):
    user = User(
        id=str(uuid.uuid4()),
        name="Other Seller",
        email="other@test.local",
        phone="9876543212",
        role="user",
        is_active=True
    )
    user.set_password("SecurePass123")
    db.session.add(user)
    db.session.commit()
    return user


@pytest.fixture
def admin_user(app):
    user = User(
        id=str(uuid.uuid4()),
        name="Test Administrator",
        email=app.config.get("ADMIN_EMAIL", "akashbaddur2007@gmail.com"),
        phone="9876543213",
        role="admin",
        is_active=True
    )
    user.set_password("AdminPass123")
    db.session.add(user)
    db.session.commit()
    return user


@pytest.fixture
def approved_listing(app, seller_user):
    listing = Listing(
        id=str(uuid.uuid4()),
        seller_id=seller_user.id,
        brand="Apple",
        model="iPhone 15",
        price_paise=5000000,  # Rs 50,000
        storage="128GB",
        battery_health=92,
        condition="Like New",
        description="Pristine condition iPhone 15.",
        approval_status="approved",
        inventory_status="available",
        seller_name=seller_user.name,
        seller_phone=seller_user.phone
    )
    db.session.add(listing)
    db.session.commit()
    return listing
