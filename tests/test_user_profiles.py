"""
Comprehensive automated tests for upgraded user profiles:
- Profile viewing, editing, and database persistence
- Profile avatar upload and validation (format, size limits)
- Public seller profile layout, stats, and privacy
- Strict exclusion of pending/rejected listings on public profiles
- Username format, handle vanity routing, and uniqueness enforcement
- Phone number validation and sensitive credentials security
"""
import io
import uuid
import pytest
from backend.models import User, Listing, Review, db, utc_now
from backend.services.auth_service import validate_username


def _create_mock_image(format="jpg"):
    """Returns valid mock image binary data."""
    if format == "jpg":
        return io.BytesIO(b"\xff\xd8\xff\xe0" + b"\x00" * 30), "avatar.jpg"
    elif format == "png":
        return io.BytesIO(b"\x89PNG\r\n\x1a\n" + b"\x00" * 30), "avatar.png"
    elif format == "webp":
        return io.BytesIO(b"RIFF" + b"\x00" * 4 + b"WEBP" + b"\x00" * 20), "avatar.webp"
    return io.BytesIO(b"plain text fake"), "avatar.txt"


def test_unauthenticated_cannot_access_account_settings(client):
    """Anonymous visitors attempting to access /account are redirected to login."""
    response = client.get("/account")
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


def test_authenticated_user_can_view_account_page(client, seller_user):
    """Authenticated user can view their account dashboard with all tabs."""
    with client.session_transaction() as sess:
        sess["user_id"] = seller_user.id
        sess["user_name"] = seller_user.name
        sess["user_email"] = seller_user.email
        sess["user_role"] = seller_user.role

    response = client.get("/account")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert seller_user.name in html
    assert seller_user.email in html
    assert "Profile Information" in html
    assert "Password &amp; Security" in html or "Password & Security" in html
    assert "Marketplace Activity" in html


def test_user_can_edit_profile_and_persist(client, seller_user, app):
    """User can update name, username, city, state, bio, and phone number."""
    with client.session_transaction() as sess:
        sess["user_id"] = seller_user.id
        sess["user_name"] = seller_user.name
        sess["user_email"] = seller_user.email
        sess["user_role"] = seller_user.role

    response = client.post("/account", data={
        "action": "update_profile",
        "name": "Chetan Kumar",
        "username": "chetankumar_99",
        "phone": "9876543210",
        "city": "Bengaluru",
        "state": "Karnataka",
        "bio": "Certified electronics technician with verified refurbished phones."
    }, follow_redirects=True)

    assert response.status_code == 200
    assert "Profile updated successfully." in response.get_data(as_text=True)

    with app.app_context():
        refreshed = db.session.get(User, seller_user.id)
        assert refreshed.name == "Chetan Kumar"
        assert refreshed.username == "chetankumar_99"
        assert refreshed.phone == "9876543210"
        assert refreshed.city == "Bengaluru"
        assert refreshed.state == "Karnataka"
        assert refreshed.bio == "Certified electronics technician with verified refurbished phones."
        assert refreshed.display_location == "Bengaluru, Karnataka"


def test_profile_changes_persist_across_logout_and_login(client, seller_user, app):
    """Verifies that profile edits remain in the database after logging out and back in."""
    with client.session_transaction() as sess:
        sess["user_id"] = seller_user.id
        sess["user_name"] = seller_user.name
        sess["user_email"] = seller_user.email
        sess["user_role"] = seller_user.role

    client.post("/account", data={
        "action": "update_profile",
        "name": "Persistent Seller",
        "username": "persistseller",
        "phone": "9876500000",
        "city": "Mumbai",
        "state": "Maharashtra",
        "bio": "Specializing in OnePlus and Pixel devices."
    }, follow_redirects=True)

    # Logout
    client.post("/logout", follow_redirects=True)

    # Login again
    login_resp = client.post("/login", data={
        "email": seller_user.email,
        "password": "SecurePass123"
    }, follow_redirects=True)
    assert login_resp.status_code == 200

    # View account
    acc_resp = client.get("/account")
    assert acc_resp.status_code == 200
    html = acc_resp.get_data(as_text=True)
    assert "Persistent Seller" in html
    assert "@persistseller" in html
    assert "Mumbai, Maharashtra" in html


def test_profile_username_validation_and_uniqueness(client, seller_user, buyer_user, app):
    """Username format and conflict validations."""
    with client.session_transaction() as sess:
        sess["user_id"] = seller_user.id
        sess["user_name"] = seller_user.name
        sess["user_email"] = seller_user.email
        sess["user_role"] = seller_user.role

    # 1. Invalid username (has spaces or symbols)
    bad_resp = client.post("/account", data={
        "action": "update_profile",
        "name": seller_user.name,
        "username": "bad user!name",
        "phone": seller_user.phone
    }, follow_redirects=True)
    assert "Username can only contain letters, numbers, and underscores." in bad_resp.get_data(as_text=True)

    # 2. Reserved keyword
    reserved_resp = client.post("/account", data={
        "action": "update_profile",
        "name": seller_user.name,
        "username": "admin",
        "phone": seller_user.phone
    }, follow_redirects=True)
    assert "reserved by the system" in reserved_resp.get_data(as_text=True)

    # 3. Duplicate username conflict with another user
    with app.app_context():
        b = db.session.get(User, buyer_user.id)
        b.username = "uniquebuyer"
        db.session.commit()

    conflict_resp = client.post("/account", data={
        "action": "update_profile",
        "name": seller_user.name,
        "username": "uniquebuyer",
        "phone": seller_user.phone
    }, follow_redirects=True)
    assert "already in use by another member" in conflict_resp.get_data(as_text=True)


def test_profile_phone_validation(client, seller_user):
    """Invalid phone numbers are caught and rejected with friendly guidance."""
    with client.session_transaction() as sess:
        sess["user_id"] = seller_user.id
        sess["user_name"] = seller_user.name
        sess["user_email"] = seller_user.email
        sess["user_role"] = seller_user.role

    resp = client.post("/account", data={
        "action": "update_profile",
        "name": seller_user.name,
        "phone": "12345"  # Too short, doesn't match Indian mobile
    }, follow_redirects=True)
    assert "Please provide a valid 10-digit Indian phone number" in resp.get_data(as_text=True)


def test_profile_avatar_upload_valid(client, seller_user, app):
    """User can upload a valid JPEG/PNG profile avatar."""
    with client.session_transaction() as sess:
        sess["user_id"] = seller_user.id
        sess["user_name"] = seller_user.name
        sess["user_email"] = seller_user.email
        sess["user_role"] = seller_user.role

    img_stream, img_name = _create_mock_image(format="jpg")
    response = client.post("/account", data={
        "action": "update_profile",
        "name": seller_user.name,
        "phone": seller_user.phone,
        "avatar": (img_stream, img_name)
    }, content_type="multipart/form-data", follow_redirects=True)

    assert response.status_code == 200
    assert "Profile updated successfully." in response.get_data(as_text=True)

    with app.app_context():
        refreshed = db.session.get(User, seller_user.id)
        assert refreshed.avatar_path is not None
        assert refreshed.avatar_path.startswith("/static/uploads/avatars/avatar_")
        assert refreshed.avatar_url == refreshed.avatar_path


def test_profile_avatar_upload_invalid_type_rejected(client, seller_user):
    """Uploading a non-image text/exe file as avatar is rejected."""
    with client.session_transaction() as sess:
        sess["user_id"] = seller_user.id
        sess["user_name"] = seller_user.name
        sess["user_email"] = seller_user.email
        sess["user_role"] = seller_user.role

    fake_stream, fake_name = _create_mock_image(format="fake")
    response = client.post("/account", data={
        "action": "update_profile",
        "name": seller_user.name,
        "phone": seller_user.phone,
        "avatar": (fake_stream, fake_name)
    }, content_type="multipart/form-data", follow_redirects=True)

    assert response.status_code == 200
    assert "Invalid profile image" in response.get_data(as_text=True)


def test_profile_avatar_upload_oversized_rejected(client, seller_user):
    """Uploading an avatar exceeding the 3MB threshold is rejected."""
    with client.session_transaction() as sess:
        sess["user_id"] = seller_user.id
        sess["user_name"] = seller_user.name
        sess["user_email"] = seller_user.email
        sess["user_role"] = seller_user.role

    # Create oversized 3.5MB stream
    oversized = io.BytesIO(b"\xff\xd8\xff\xe0" + b"\x00" * (3500 * 1024))
    response = client.post("/account", data={
        "action": "update_profile",
        "name": seller_user.name,
        "phone": seller_user.phone,
        "avatar": (oversized, "big.jpg")
    }, content_type="multipart/form-data", follow_redirects=True)

    assert response.status_code == 200
    assert "Profile image must be smaller than 3MB." in response.get_data(as_text=True)


def test_public_seller_profile_displays_only_approved_and_available(client, seller_user, app):
    """
    Public seller profile renders active approved devices and does not expose
    internal or unapproved inventory.
    """
    with app.app_context():
        # Approved & available listing
        l1 = Listing(
            id=str(uuid.uuid4()),
            seller_id=seller_user.id,
            brand="Apple",
            model="iPhone 13 Approved",
            price_paise=4999900,
            storage="128GB",
            condition="Like New",
            description="Approved phone description",
            approval_status="approved",
            inventory_status="available"
        )
        db.session.add(l1)
        db.session.commit()

    response = client.get(f"/seller/{seller_user.id}")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert seller_user.name in html
    assert "iPhone 13 Approved" in html
    assert "₹49,999" in html
    assert "Available Smartphones (1)" in html


def test_public_seller_profile_hides_pending_and_rejected_listings(client, seller_user, app):
    """
    Pending and rejected items MUST NEVER appear on public seller profiles.
    """
    with app.app_context():
        # Pending listing
        pending_l = Listing(
            id=str(uuid.uuid4()),
            seller_id=seller_user.id,
            brand="Google",
            model="Pixel 8 Pro Pending Secret",
            price_paise=6000000,
            storage="256GB",
            condition="Like New",
            description="Pending description",
            approval_status="pending",
            inventory_status="available"
        )
        # Rejected listing
        rejected_l = Listing(
            id=str(uuid.uuid4()),
            seller_id=seller_user.id,
            brand="Xiaomi",
            model="13 Ultra Rejected Secret",
            price_paise=4000000,
            storage="256GB",
            condition="Fair",
            description="Rejected description",
            approval_status="rejected",
            inventory_status="available"
        )
        db.session.add_all([pending_l, rejected_l])
        db.session.commit()

    # Public visitor views seller profile
    response = client.get(f"/seller/{seller_user.id}")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Pixel 8 Pro Pending Secret" not in html
    assert "13 Ultra Rejected Secret" not in html


def test_public_seller_profile_empty_state_when_no_listings(client, other_seller_user):
    """When a seller has no active listings, renders a clean, friendly empty state."""
    response = client.get(f"/seller/{other_seller_user.id}")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "No Active Listings Currently Available" in html
    assert "Browse All Smartphones" in html


def test_public_seller_profile_does_not_leak_phone_or_email(client, seller_user, app):
    """
    Ensures public seller profiles protect user privacy by never exposing
    phone numbers or email addresses in public HTML.
    """
    with app.app_context():
        u = db.session.get(User, seller_user.id)
        u.phone = "9876543211"
        u.email = "private_seller_email@test.local"
        db.session.commit()

    response = client.get(f"/seller/{seller_user.id}")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "9876543211" not in html
    assert "private_seller_email@test.local" not in html
    assert "Buyer &amp; Privacy Protection" in html or "Buyer & Privacy Protection" in html


def test_vanity_username_redirect_route(client, seller_user, app):
    """Route /u/<username> redirects to the seller's profile page."""
    with app.app_context():
        u = db.session.get(User, seller_user.id)
        u.username = "vanityseller"
        db.session.commit()

    # Access via vanity URL
    resp = client.get("/u/vanityseller")
    assert resp.status_code == 302
    assert f"/seller/{seller_user.id}" in resp.headers["Location"]


def test_change_password_requires_correct_current_password(client, seller_user):
    """Password change fails with incorrect current password."""
    with client.session_transaction() as sess:
        sess["user_id"] = seller_user.id
        sess["user_name"] = seller_user.name
        sess["user_email"] = seller_user.email
        sess["user_role"] = seller_user.role

    resp = client.post("/account", data={
        "action": "change_password",
        "current_password": "WrongPassword999",
        "new_password": "NewSecurePass456!",
        "confirm_password": "NewSecurePass456!"
    }, follow_redirects=True)

    assert "Current password is incorrect." in resp.get_data(as_text=True)
