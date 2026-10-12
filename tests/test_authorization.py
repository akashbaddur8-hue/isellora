"""
Server-side authorization and access control tests.
"""
from backend.models import Order, Listing
from backend.database import db
from backend.services.order_service import create_order


def test_buyer_cannot_access_other_buyer_order(client, buyer_user, other_seller_user, approved_listing):
    # Buyer places an order
    order, _ = create_order(
        buyer_id=buyer_user.id,
        listing_id=approved_listing.id,
        buyer_name=buyer_user.name,
        buyer_phone=buyer_user.phone,
        delivery_method="pickup",
        shipping_address="Local Handover"
    )

    # Log in as other user
    with client.session_transaction() as sess:
        sess["user_id"] = other_seller_user.id
        sess["user_name"] = other_seller_user.name
        sess["user_email"] = other_seller_user.email

    response = client.get(f"/order-success/{order.id}")
    assert response.status_code == 404  # Private order returns 404 to non-owner


def test_seller_cannot_edit_other_seller_listing(client, approved_listing, other_seller_user):
    # Log in as other seller
    with client.session_transaction() as sess:
        sess["user_id"] = other_seller_user.id
        sess["user_name"] = other_seller_user.name
        sess["user_email"] = other_seller_user.email

    # Try to edit listing
    response = client.post(f"/edit/{approved_listing.id}", data={
        "price": "35000",
        "description": "Hacked description"
    })
    assert response.status_code == 403

    # Verify listing was not changed
    db.session.refresh(approved_listing)
    assert approved_listing.price_rupees == 50000


def test_seller_cannot_delete_other_seller_listing(client, approved_listing, other_seller_user):
    with client.session_transaction() as sess:
        sess["user_id"] = other_seller_user.id

    response = client.post(f"/delete/{approved_listing.id}")
    assert response.status_code == 403


def test_non_admin_cannot_access_admin_dashboard(client, buyer_user):
    with client.session_transaction() as sess:
        sess["user_id"] = buyer_user.id
        sess["user_role"] = "user"
        sess["admin_authenticated"] = False

    response = client.get("/admin", follow_redirects=False)
    # Redirects to admin login
    assert response.status_code == 302
    assert "/admin/login" in response.headers["Location"]


def test_non_admin_cannot_moderate_listing(client, buyer_user, approved_listing):
    with client.session_transaction() as sess:
        sess["user_id"] = buyer_user.id
        sess["user_role"] = "user"
        sess["admin_authenticated"] = False

    response = client.post(f"/admin/listing/{approved_listing.id}/status", data={
        "status": "rejected"
    })
    assert response.status_code in (302, 403)

    db.session.refresh(approved_listing)
    assert approved_listing.approval_status == "approved"


def test_seller_cannot_purchase_own_listing(client, seller_user, approved_listing):
    with client.session_transaction() as sess:
        sess["user_id"] = seller_user.id

    response = client.get(f"/buy/{approved_listing.id}")
    assert response.status_code == 403
