"""
CSRF protection tests.
Verifies that POST requests lacking valid CSRF tokens are rejected when CSRF protection is active.
"""
from backend.app import create_app
from backend.database import db
import pytest


def test_csrf_token_required_on_post():
    app = create_app("testing")
    app.config["WTF_CSRF_ENABLED"] = True

    with app.app_context():
        db.create_all()
        client = app.test_client()

        # POST without CSRF token
        response = client.post("/signup", data={
            "name": "CSRF Attack",
            "email": "attack@example.com",
            "phone": "9876543210",
            "password": "Password123"
        })
        # Flask-WTF rejects with 400 Bad Request
        assert response.status_code == 400
        assert "Security token expired or invalid" in response.get_data(as_text=True)

        db.session.remove()
        db.drop_all()
