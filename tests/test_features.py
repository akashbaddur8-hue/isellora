"""
Tests for new marketplace features:
- Dedicated error handling
- Printable invoice & certificate
- Buyer reviews & ratings
- Wishlist / saved listings
"""
from backend.models import Order, Listing, Review, Wishlist
from backend.database import db
from backend.services.order_service import create_order, transition_order_status


def test_custom_404_error_page(client):
    response = client.get("/non-existent-page-route-12345")
    assert response.status_code == 404
    assert b"Page or Phone Not Found" in response.data or b"HTTP 404" in response.data


def test_invoice_access_control(client, buyer_user, other_seller_user, approved_listing):
    order, _ = create_order(
        buyer_id=buyer_user.id,
        listing_id=approved_listing.id,
        buyer_name=buyer_user.name,
        buyer_phone=buyer_user.phone,
        delivery_method="pickup",
        shipping_address="Local Handover"
    )

    # Buyer can view invoice
    with client.session_transaction() as sess:
        sess["user_id"] = buyer_user.id
        sess["user_name"] = buyer_user.name
        sess["user_email"] = buyer_user.email

    response = client.get(f"/orders/{order.id}/invoice")
    assert response.status_code == 200
    assert b"CERTIFICATE & INVOICE" in response.data
    assert approved_listing.brand.encode() in response.data

    # Other non-involved user cannot view invoice
    with client.session_transaction() as sess:
        sess["user_id"] = other_seller_user.id
        sess["user_name"] = other_seller_user.name
        sess["user_email"] = other_seller_user.email

    response = client.get(f"/orders/{order.id}/invoice")
    assert response.status_code == 403


def test_review_submission_workflow(client, buyer_user, seller_user, approved_listing):
    order, _ = create_order(
        buyer_id=buyer_user.id,
        listing_id=approved_listing.id,
        buyer_name=buyer_user.name,
        buyer_phone=buyer_user.phone,
        delivery_method="pickup",
        shipping_address="Local Handover"
    )

    with client.session_transaction() as sess:
        sess["user_id"] = buyer_user.id
        sess["user_name"] = buyer_user.name
        sess["user_email"] = buyer_user.email

    # Cannot review an order that is not delivered yet
    response = client.post(f"/orders/{order.id}/review", data={
        "rating": "5",
        "comment": "Too early review"
    }, follow_redirects=True)
    assert Review.query.filter_by(order_id=order.id).first() is None

    # Transition order from Pending -> Confirmed -> Delivered
    transition_order_status(order, "Confirmed", actor_id=seller_user.id, actor_role="seller")
    transition_order_status(order, "Delivered", actor_id=seller_user.id, actor_role="seller")

    # Now review succeeds
    response = client.post(f"/orders/{order.id}/review", data={
        "rating": "5",
        "comment": "Excellent phone, exactly as described!"
    }, follow_redirects=True)
    assert response.status_code == 200

    review = Review.query.filter_by(order_id=order.id).first()
    assert review is not None
    assert review.rating == 5
    assert review.comment == "Excellent phone, exactly as described!"
    assert review.seller_id == seller_user.id


def test_wishlist_toggle_and_display(client, buyer_user, approved_listing):
    with client.session_transaction() as sess:
        sess["user_id"] = buyer_user.id
        sess["user_name"] = buyer_user.name
        sess["user_email"] = buyer_user.email

    # Toggle to add
    response = client.post(f"/wishlist/toggle/{approved_listing.id}", follow_redirects=True)
    assert response.status_code == 200
    assert Wishlist.query.filter_by(user_id=buyer_user.id, listing_id=approved_listing.id).first() is not None

    # View wishlist page
    response = client.get("/wishlist")
    assert response.status_code == 200
    assert approved_listing.brand.encode() in response.data

    # Toggle to remove
    response = client.post(f"/wishlist/toggle/{approved_listing.id}", follow_redirects=True)
    assert response.status_code == 200
    assert Wishlist.query.filter_by(user_id=buyer_user.id, listing_id=approved_listing.id).first() is None
