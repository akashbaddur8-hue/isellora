"""
Comprehensive security and integration tests for iSellora Password Reset feature:
1. Forgot Password link appears on the login page.
2. Unknown and registered emails receive the same public response (enumeration prevention).
3. A valid request generates a reset email (verified via test outbox).
4. Invalid tokens are rejected.
5. Expired tokens are rejected.
6. Used tokens cannot be reused.
7. A valid token permits a password change.
8. The new password is stored as a secure hash.
9. The old password no longer works and the new password works.
10. Repeated requests are rate-limited.
11. Email delivery failures are handled safely.
12. Other users' accounts cannot be modified using a reset token.
13. Existing registration, login, and application features remain unaffected.
"""
from datetime import timedelta
import re
import uuid
import pytest
from werkzeug.security import check_password_hash

from backend.database import db
from backend.models import User, PasswordResetToken, utc_now
from backend.services.auth_service import (
    clear_reset_rate_limits, create_password_reset_token,
    verify_password_reset_token, hash_reset_token
)
from backend.services.email_service import TEST_OUTBOX, clear_test_outbox


@pytest.fixture(autouse=True)
def clean_state():
    """Ensure rate limits and test outbox are reset before and after each test."""
    clear_test_outbox()
    clear_reset_rate_limits()
    yield
    clear_test_outbox()
    clear_reset_rate_limits()


# =============================================================================
# 1. Forgot Password Link on Login Page
# =============================================================================
def test_forgot_password_link_appears_on_login_page(client):
    """Verifies clearly visible 'Forgot Password?' link on the login page."""
    response = client.get("/login")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Forgot Password?" in html
    assert 'href="/forgot-password"' in html or 'href="http://localhost/forgot-password"' in html


# =============================================================================
# 2. Account Enumeration Prevention: Identical Responses
# =============================================================================
def test_unknown_and_registered_emails_receive_same_public_response(client, buyer_user):
    """
    Both unknown and registered emails must receive the exact same public response
    to prevent email harvesting/enumeration attacks.
    """
    # 1. Registered email
    resp_reg = client.post("/forgot-password", data={
        "email": buyer_user.email
    }, follow_redirects=True)
    assert resp_reg.status_code == 200
    html_reg = resp_reg.get_data(as_text=True)
    expected_msg = "If an account exists with this email, you will receive password reset instructions shortly."
    assert expected_msg in html_reg

    clear_reset_rate_limits()

    # 2. Unregistered / unknown email
    resp_unreg = client.post("/forgot-password", data={
        "email": "completely_nonexistent_user_98765@example.com"
    }, follow_redirects=True)
    assert resp_unreg.status_code == 200
    html_unreg = resp_unreg.get_data(as_text=True)
    assert expected_msg in html_unreg

    # Verify only 1 email was actually dispatched (to the registered user)
    assert len(TEST_OUTBOX) == 1
    assert TEST_OUTBOX[0]["to"] == buyer_user.email


# =============================================================================
# 3. Valid Request Generates Reset Email & Hashes Token at Rest
# =============================================================================
def test_valid_request_generates_reset_email(client, buyer_user):
    """
    A valid reset request dispatches an email with a secure link,
    and stores ONLY the SHA-256 hash in the database, never the plaintext token.
    """
    resp = client.post("/forgot-password", data={
        "email": buyer_user.email
    }, follow_redirects=True)
    assert resp.status_code == 200

    assert len(TEST_OUTBOX) == 1
    email_data = TEST_OUTBOX[0]
    assert email_data["to"] == buyer_user.email
    reset_url = email_data["reset_url"]
    assert "/reset-password/" in reset_url

    # Extract plain token from URL
    match = re.search(r"/reset-password/([A-Za-z0-9_-]+)", reset_url)
    assert match is not None
    plain_token = match.group(1)

    # Database check: The plaintext token must NOT exist anywhere in database
    token_in_db = PasswordResetToken.query.filter_by(token_hash=plain_token).first()
    assert token_in_db is None

    # The SHA-256 hash of the token MUST exist in the database
    expected_hash = hash_reset_token(plain_token)
    stored_token = PasswordResetToken.query.filter_by(token_hash=expected_hash).first()
    assert stored_token is not None
    assert stored_token.user_id == buyer_user.id
    assert stored_token.is_valid is True
    assert stored_token.used_at is None


# =============================================================================
# 4. Invalid Tokens are Rejected
# =============================================================================
def test_invalid_tokens_are_rejected(client):
    """Tampered or non-existent tokens are rejected with a 400 Bad Request."""
    bogus_token = "totally-bogus-token-string-that-does-not-exist"

    # GET request
    get_resp = client.get(f"/reset-password/{bogus_token}")
    assert get_resp.status_code == 400
    assert "Reset Link Unavailable" in get_resp.get_data(as_text=True)

    # POST request
    post_resp = client.post(f"/reset-password/{bogus_token}", data={
        "new_password": "NewValidPass123",
        "confirm_password": "NewValidPass123"
    })
    assert post_resp.status_code == 400
    assert "Reset Link Unavailable" in post_resp.get_data(as_text=True)


# =============================================================================
# 5. Expired Tokens are Rejected
# =============================================================================
def test_expired_tokens_are_rejected(client, buyer_user):
    """Tokens that have exceeded their validity duration are rejected."""
    token_record, plain_token = create_password_reset_token(buyer_user, expires_in_minutes=15)

    # Manually expire the token in the database
    token_record.expires_at = utc_now() - timedelta(minutes=5)
    db.session.commit()

    # GET
    get_resp = client.get(f"/reset-password/{plain_token}")
    assert get_resp.status_code == 400
    assert "expired" in get_resp.get_data(as_text=True).lower()

    # POST
    post_resp = client.post(f"/reset-password/{plain_token}", data={
        "new_password": "NewValidPass123",
        "confirm_password": "NewValidPass123"
    })
    assert post_resp.status_code == 400


# =============================================================================
# 6. Used Tokens Cannot be Reused
# =============================================================================
def test_used_tokens_cannot_be_reused(client, buyer_user):
    """A reset token is single-use and cannot be used a second time."""
    _, plain_token = create_password_reset_token(buyer_user)

    # First use: Successful
    first_resp = client.post(f"/reset-password/{plain_token}", data={
        "new_password": "FirstNewPassword123",
        "confirm_password": "FirstNewPassword123"
    }, follow_redirects=True)
    assert first_resp.status_code == 200
    assert "Your password has been reset successfully" in first_resp.get_data(as_text=True)

    # Second use: Must be rejected
    second_resp = client.post(f"/reset-password/{plain_token}", data={
        "new_password": "SecondNewPassword123",
        "confirm_password": "SecondNewPassword123"
    })
    assert second_resp.status_code == 400
    assert "already been used" in second_resp.get_data(as_text=True).lower() or "unavailable" in second_resp.get_data(as_text=True).lower()


# =============================================================================
# 7. Valid Token Permits Password Change
# =============================================================================
def test_valid_token_permits_password_change(client, buyer_user):
    """User can view the reset form on GET and change password on POST."""
    _, plain_token = create_password_reset_token(buyer_user)

    # GET displays the reset password form with password requirements
    get_resp = client.get(f"/reset-password/{plain_token}")
    assert get_resp.status_code == 200
    html = get_resp.get_data(as_text=True)
    assert "Create New Password" in html
    assert "Save New Password" in html

    # POST updates password
    post_resp = client.post(f"/reset-password/{plain_token}", data={
        "new_password": "BrandNewSecret999",
        "confirm_password": "BrandNewSecret999"
    }, follow_redirects=True)
    assert post_resp.status_code == 200
    assert "Your password has been reset successfully" in post_resp.get_data(as_text=True)


# =============================================================================
# 8. New Password Stored as Secure Hash
# =============================================================================
def test_new_password_stored_as_secure_hash(client, buyer_user):
    """New password must be securely hashed and never saved in plaintext."""
    _, plain_token = create_password_reset_token(buyer_user)

    client.post(f"/reset-password/{plain_token}", data={
        "new_password": "UltraSecretPassword789",
        "confirm_password": "UltraSecretPassword789"
    })

    db.session.refresh(buyer_user)
    assert "UltraSecretPassword789" not in buyer_user.password_hash
    assert check_password_hash(buyer_user.password_hash, "UltraSecretPassword789") is True


# =============================================================================
# 9. Old Password No Longer Works and New Password Works
# =============================================================================
def test_old_password_no_longer_works_and_new_password_works(client, buyer_user):
    """After reset, the user cannot authenticate with the old password, only the new one."""
    _, plain_token = create_password_reset_token(buyer_user)

    client.post(f"/reset-password/{plain_token}", data={
        "new_password": "ChangedPassword321",
        "confirm_password": "ChangedPassword321"
    })

    # 1. Login with old password ("SecurePass123" from fixture) fails
    old_login = client.post("/login", data={
        "email": buyer_user.email,
        "password": "SecurePass123"
    })
    assert old_login.status_code == 401
    assert "Invalid email or password" in old_login.get_data(as_text=True)

    # 2. Login with new password succeeds
    new_login = client.post("/login", data={
        "email": buyer_user.email,
        "password": "ChangedPassword321"
    }, follow_redirects=True)
    assert new_login.status_code == 200
    assert f"Welcome back, {buyer_user.name}!" in new_login.get_data(as_text=True)


# =============================================================================
# 10. Repeated Requests are Rate-Limited
# =============================================================================
def test_repeated_requests_are_rate_limited(client, buyer_user):
    """Excessive password reset requests trigger HTTP 429 Too Many Requests."""
    # First 3 requests succeed
    for _ in range(3):
        resp = client.post("/forgot-password", data={"email": buyer_user.email})
        assert resp.status_code == 302

    # 4th request must be rate-limited
    rate_limited_resp = client.post("/forgot-password", data={"email": buyer_user.email})
    assert rate_limited_resp.status_code == 429
    assert "Too many password reset requests" in rate_limited_resp.get_data(as_text=True)


# =============================================================================
# 11. Email Delivery Failures Handled Safely
# =============================================================================
def test_email_delivery_failures_handled_safely(client, buyer_user, monkeypatch):
    """If SMTP or delivery service fails, app handles it gracefully without crashing."""
    import backend.app

    def mock_failing_email(email, name, url):
        return False, "Failed to connect to SMTP server."

    monkeypatch.setattr(backend.app, "send_password_reset_email", mock_failing_email)

    resp = client.post("/forgot-password", data={"email": buyer_user.email})
    assert resp.status_code == 500
    assert "technical issue delivering the reset instructions" in resp.get_data(as_text=True)


# =============================================================================
# 12. Other Users' Accounts Cannot be Modified
# =============================================================================
def test_other_users_accounts_cannot_be_modified(client, buyer_user, seller_user):
    """
    Using User A's token only alters User A's password;
    User B's credentials remain untouched.
    """
    seller_original_hash = seller_user.password_hash

    # Generate token for buyer_user
    _, buyer_token = create_password_reset_token(buyer_user)

    # Reset buyer_user's password
    client.post(f"/reset-password/{buyer_token}", data={
        "new_password": "NewBuyerPassword777",
        "confirm_password": "NewBuyerPassword777"
    })

    db.session.refresh(buyer_user)
    db.session.refresh(seller_user)

    # Buyer password was updated
    assert check_password_hash(buyer_user.password_hash, "NewBuyerPassword777") is True

    # Seller password hash is 100% unchanged
    assert seller_user.password_hash == seller_original_hash
    assert check_password_hash(seller_user.password_hash, "SecurePass123") is True


# =============================================================================
# 13. Existing Authentication & Core Features Unaffected
# =============================================================================
def test_existing_authentication_and_features_unaffected(client, seller_user, admin_user):
    """Verifies existing login, dashboard, and catalog routes remain unaffected."""
    # 1. Normal login works
    login_resp = client.post("/login", data={
        "email": seller_user.email,
        "password": "SecurePass123"
    }, follow_redirects=True)
    assert login_resp.status_code == 200
    assert f"Welcome back, {seller_user.name}!" in login_resp.get_data(as_text=True)

    # 2. Public catalog accessible
    catalog_resp = client.get("/products")
    assert catalog_resp.status_code == 200

    # 3. Log out and verify forgot password page loads cleanly
    client.get("/logout", follow_redirects=True)
    forgot_resp = client.get("/forgot-password")
    assert forgot_resp.status_code == 200
    assert "Forgot Password?" in forgot_resp.get_data(as_text=True)
