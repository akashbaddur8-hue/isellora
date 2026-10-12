"""
Authentication and session security tests.
"""
from backend.models import User
from backend.database import db


def test_user_registration_success(client):
    response = client.post("/signup", data={
        "name": "New Tester",
        "email": "newtester@example.com",
        "phone": "9876543299",
        "password": "StrongPassword123"
    }, follow_redirects=True)
    assert response.status_code == 200

    user = User.query.filter_by(email="newtester@example.com").first()
    assert user is not None
    assert user.name == "New Tester"
    assert user.phone == "9876543299"
    assert user.check_password("StrongPassword123") is True
    assert user.check_password("WrongPassword") is False


def test_user_registration_duplicate_email(client, buyer_user):
    response = client.post("/signup", data={
        "name": "Duplicate User",
        "email": buyer_user.email,
        "phone": "9876543299",
        "password": "StrongPassword123"
    })
    assert response.status_code == 400


def test_password_strength_enforcement(client):
    # Weak: under 8 characters
    response = client.post("/signup", data={
        "name": "Short Pass",
        "email": "short@example.com",
        "phone": "9876543299",
        "password": "abc"
    })
    assert response.status_code == 400

    # Weak: no digits
    response = client.post("/signup", data={
        "name": "No Digits",
        "email": "nodigits@example.com",
        "phone": "9876543299",
        "password": "PasswordOnly"
    })
    assert response.status_code == 400


def test_login_success_and_session(client, buyer_user):
    response = client.post("/login", data={
        "email": buyer_user.email,
        "password": "SecurePass123"
    }, follow_redirects=True)
    assert response.status_code == 200

    with client.session_transaction() as sess:
        assert sess.get("user_id") == buyer_user.id
        assert sess.get("user_email") == buyer_user.email


def test_login_invalid_credentials(client, buyer_user):
    response = client.post("/login", data={
        "email": buyer_user.email,
        "password": "WrongPassword123"
    })
    assert response.status_code == 401

    with client.session_transaction() as sess:
        assert sess.get("user_id") is None


def test_logout_session_invalidation(client, buyer_user):
    # Login
    client.post("/login", data={
        "email": buyer_user.email,
        "password": "SecurePass123"
    })

    # Logout
    response = client.post("/logout", follow_redirects=True)
    assert response.status_code == 200

    with client.session_transaction() as sess:
        assert sess.get("user_id") is None
