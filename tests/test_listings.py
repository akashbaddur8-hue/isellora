"""
Listing creation, validation, search, and moderation tests.
"""
import io
from backend.models import Listing
from backend.database import db
from backend.services.listing_service import query_catalog, validate_image_file


def test_listing_creation_validation(client, seller_user):
    with client.session_transaction() as sess:
        sess["user_id"] = seller_user.id
        sess["user_name"] = seller_user.name
        sess["user_phone"] = seller_user.phone

    # Invalid price (< 500)
    response = client.post("/sell", data={
        "brand": "Apple",
        "model": "iPhone 13",
        "price": "100",  # too low
        "storage": "128GB",
        "description": "Good phone",
        "condition": "Good"
    })
    assert response.status_code == 400

    # Invalid battery (> 100)
    response = client.post("/sell", data={
        "brand": "Apple",
        "model": "iPhone 13",
        "price": "25000",
        "storage": "128GB",
        "battery": "150",  # invalid
        "description": "Good phone",
        "condition": "Good"
    })
    assert response.status_code == 400


def test_image_file_type_validation(app):
    with app.app_context():
        # Valid JPG header
        valid_stream = io.BytesIO(b"\xff\xd8\xff\xe0" + b"\x00" * 20)
        from werkzeug.datastructures import FileStorage
        valid_file = FileStorage(stream=valid_stream, filename="test.jpg")
        assert validate_image_file(valid_file) is True

        # Invalid extension .exe
        invalid_stream = io.BytesIO(b"MZ\x90\x00" + b"\x00" * 20)
        invalid_file = FileStorage(stream=invalid_stream, filename="malicious.exe")
        assert validate_image_file(invalid_file) is False


def test_unapproved_listing_not_in_public_catalog(client, seller_user):
    unapproved = Listing(
        seller_id=seller_user.id,
        brand="Xiaomi",
        model="Mi 11",
        price_paise=1500000,
        storage="128GB",
        description="Unapproved device",
        approval_status="pending",
        inventory_status="available"
    )
    db.session.add(unapproved)
    db.session.commit()

    # Public products catalog
    response = client.get("/products")
    assert response.status_code == 200
    assert "Mi 11" not in response.get_data(as_text=True)


def test_editing_listing_resets_to_pending(client, seller_user, approved_listing):
    with client.session_transaction() as sess:
        sess["user_id"] = seller_user.id

    assert approved_listing.approval_status == "approved"

    response = client.post(f"/edit/{approved_listing.id}", data={
        "price": "48000",
        "description": "Updated price description"
    }, follow_redirects=True)
    assert response.status_code == 200

    db.session.refresh(approved_listing)
    assert approved_listing.price_rupees == 48000
    assert approved_listing.approval_status == "pending"


def test_server_side_catalog_filtering(app, seller_user):
    with app.app_context():
        # Add 3 listings
        l1 = Listing(brand="Apple", model="iPhone 15 Pro", price_paise=8000000, storage="128GB", condition="Like New", description="desc", approval_status="approved", inventory_status="available")
        l2 = Listing(brand="Samsung", model="Galaxy S23", price_paise=4500000, storage="256GB", condition="Good", description="desc", approval_status="approved", inventory_status="available")
        l3 = Listing(brand="OnePlus", model="OnePlus 12", price_paise=3500000, storage="256GB", condition="Fair", description="desc", approval_status="approved", inventory_status="available")
        db.session.add_all([l1, l2, l3])
        db.session.commit()

        # Filter by brand
        res_apple = query_catalog(brand="Apple")
        assert res_apple.total == 1
        assert res_apple.items[0].brand == "Apple"

        # Filter by price range (min 40,000, max 60,000)
        res_price = query_catalog(min_price=40000, max_price=60000)
        assert res_price.total == 1
        assert res_price.items[0].model == "Galaxy S23"

        # Filter by condition
        res_cond = query_catalog(condition="Like New")
        assert res_cond.total == 1
        assert res_cond.items[0].model == "iPhone 15 Pro"
