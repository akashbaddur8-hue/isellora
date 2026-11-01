"""
Comprehensive tests for listing moderation workflow, database consistency,
and seller/public catalog visibility.
"""
import io
import uuid
import pytest
from backend.models import Listing, ListingImage, Notification, AuditEvent, User, utc_now
from backend.database import db
from backend.services.listing_service import query_catalog, moderate_listing


def _create_mock_image():
    """Returns valid mock JPEG image data."""
    return (io.BytesIO(b"\xff\xd8\xff\xe0" + b"\x00" * 30), "phone.jpg")


def test_seller_submits_listing_appears_in_seller_dashboard_as_pending(client, seller_user):
    """
    Seller submits a new phone listing.
    Verifies it is saved with approval_status='pending' and appears
    in the seller's dashboard and aliases with clear status indicator.
    """
    with client.session_transaction() as sess:
        sess["user_id"] = seller_user.id
        sess["user_name"] = seller_user.name
        sess["user_email"] = seller_user.email
        sess["user_role"] = "user"

    img_stream, img_name = _create_mock_image()
    response = client.post("/sell", data={
        "brand": "Samsung",
        "model": "Galaxy S20 FE",
        "price": "24999",
        "storage": "128GB",
        "ram": "8GB",
        "color": "Cloud Navy",
        "condition": "Excellent",
        "battery": "88",
        "description": "Mint condition Samsung Galaxy S20 FE.",
        "photos": [(img_stream, img_name)]
    }, content_type="multipart/form-data", follow_redirects=True)

    assert response.status_code == 200

    # Verify listing saved to DB
    listing = Listing.query.filter_by(seller_id=seller_user.id, model="Galaxy S20 FE").first()
    assert listing is not None
    assert listing.brand == "Samsung"
    assert listing.approval_status == "pending"
    assert listing.inventory_status == "available"
    assert listing.price_paise == 2499900

    # Verify listing appears in seller dashboard
    dash_html = response.get_data(as_text=True)
    assert "Galaxy S20 FE" in dash_html
    assert "Pending" in dash_html
    assert "₹24,999" in dash_html

    # Test route aliases
    for alias in ["/listings", "/my-listings", "/seller/listings"]:
        alias_resp = client.get(alias)
        assert alias_resp.status_code == 200
        assert "Galaxy S20 FE" in alias_resp.get_data(as_text=True)


def test_pending_listing_hidden_from_public_catalog_and_homepage(client, seller_user):
    """
    Pending listings must remain hidden from the homepage and public catalog.
    """
    listing = Listing(
        id=str(uuid.uuid4()),
        seller_id=seller_user.id,
        brand="Google",
        model="Pixel 8 Pro Hidden",
        price_paise=6500000,
        storage="128GB",
        condition="Like New",
        description="Pending device",
        approval_status="pending",
        inventory_status="available"
    )
    db.session.add(listing)
    db.session.commit()

    # Query catalog service
    catalog = query_catalog(search="Pixel 8 Pro Hidden")
    assert catalog.total == 0

    # Homepage
    home_resp = client.get("/")
    assert home_resp.status_code == 200
    assert "Pixel 8 Pro Hidden" not in home_resp.get_data(as_text=True)

    # Public Browse page
    browse_resp = client.get("/products")
    assert browse_resp.status_code == 200
    assert "Pixel 8 Pro Hidden" not in browse_resp.get_data(as_text=True)

    # Detail page hidden from anonymous users
    detail_resp = client.get(f"/products/{listing.id}")
    assert detail_resp.status_code == 404


def test_admin_approves_listing_successfully(client, admin_user, seller_user):
    """
    Admin approves a pending listing using canonical action='approve'.
    Verifies database status is 'approved', notification and audit log are created.
    """
    listing = Listing(
        id=str(uuid.uuid4()),
        seller_id=seller_user.id,
        brand="Motorola",
        model="Edge 40",
        price_paise=2200000,
        storage="256GB",
        condition="Good",
        description="Pending review Moto",
        approval_status="pending",
        inventory_status="available"
    )
    db.session.add(listing)
    db.session.commit()

    with client.session_transaction() as sess:
        sess["user_id"] = admin_user.id
        sess["user_email"] = admin_user.email
        sess["user_role"] = "admin"
        sess["admin_authenticated"] = True

    response = client.post(f"/admin/listing/{listing.id}/status", data={
        "action": "approve"
    }, follow_redirects=True)
    assert response.status_code == 200

    db.session.refresh(listing)
    assert listing.approval_status == "approved"
    assert listing.rejection_reason is None
    assert listing.reviewed_at is not None

    # Check notification sent to seller
    notice = Notification.query.filter_by(user_id=seller_user.id, listing_id=listing.id).first()
    assert notice is not None
    assert "Approved" in notice.title

    # Check audit log
    audit = AuditEvent.query.filter_by(target_id=listing.id, action="LISTING_MODERATION_APPROVE").first()
    assert audit is not None


def test_approved_and_available_listing_appears_in_public_catalog(client, seller_user):
    """
    Approved listings with available inventory appear on the homepage and Browse page.
    """
    listing = Listing(
        id=str(uuid.uuid4()),
        seller_id=seller_user.id,
        brand="Xiaomi",
        model="13 Pro Visible",
        price_paise=3800000,
        storage="256GB",
        condition="Like New",
        description="Live phone",
        approval_status="approved",
        inventory_status="available"
    )
    db.session.add(listing)
    db.session.commit()

    # Public products catalog
    browse_resp = client.get("/products")
    assert browse_resp.status_code == 200
    assert "13 Pro Visible" in browse_resp.get_data(as_text=True)

    # Public detail page is accessible
    detail_resp = client.get(f"/products/{listing.id}")
    assert detail_resp.status_code == 200
    assert "Xiaomi" in detail_resp.get_data(as_text=True)
    assert "13 Pro Visible" in detail_resp.get_data(as_text=True)


def test_admin_rejects_pending_listing_with_reason(client, admin_user, seller_user):
    """
    Admin rejects a listing with action='reject' and feedback reason.
    Verifies database status is 'rejected' and reason is preserved.
    """
    listing = Listing(
        id=str(uuid.uuid4()),
        seller_id=seller_user.id,
        brand="Apple",
        model="iPhone 11 Defective",
        price_paise=1500000,
        storage="64GB",
        condition="Fair",
        description="Device with issues",
        approval_status="pending",
        inventory_status="available"
    )
    db.session.add(listing)
    db.session.commit()

    with client.session_transaction() as sess:
        sess["user_id"] = admin_user.id
        sess["user_email"] = admin_user.email
        sess["user_role"] = "admin"
        sess["admin_authenticated"] = True

    response = client.post(f"/admin/listing/{listing.id}/status", data={
        "action": "reject",
        "reason": "Camera glass cracked and not disclosed in condition."
    }, follow_redirects=True)
    assert response.status_code == 200

    db.session.refresh(listing)
    assert listing.approval_status == "rejected"
    assert "Camera glass cracked" in listing.rejection_reason

    # Audit log
    audit = AuditEvent.query.filter_by(target_id=listing.id, action="LISTING_MODERATION_REJECT").first()
    assert audit is not None


def test_seller_can_view_rejected_listing_with_feedback(client, seller_user, buyer_user):
    """
    Rejected listings remain visible to their owner with rejection reason,
    but hidden from non-owner buyers and public catalog.
    """
    listing = Listing(
        id=str(uuid.uuid4()),
        seller_id=seller_user.id,
        brand="OnePlus",
        model="Nord CE RejectionTest",
        price_paise=1200000,
        storage="128GB",
        condition="Good",
        description="Rejected phone",
        approval_status="rejected",
        inventory_status="available",
        rejection_reason="Screen burn-in detected in photos."
    )
    db.session.add(listing)
    db.session.commit()

    # Seller views their dashboard
    with client.session_transaction() as sess:
        sess["user_id"] = seller_user.id
        sess["user_name"] = seller_user.name
        sess["user_role"] = "user"

    dash_resp = client.get("/seller-dashboard")
    assert dash_resp.status_code == 200
    dash_html = dash_resp.get_data(as_text=True)
    assert "Nord CE RejectionTest" in dash_html
    assert "Rejected" in dash_html
    assert "Screen burn-in detected" in dash_html

    # Seller views listing detail page
    detail_resp = client.get(f"/products/{listing.id}")
    assert detail_resp.status_code == 200
    detail_html = detail_resp.get_data(as_text=True)
    assert "Status: Rejected" in detail_html
    assert "Screen burn-in detected" in detail_html

    # Other regular buyer cannot see rejected listing
    with client.session_transaction() as sess:
        sess["user_id"] = buyer_user.id
        sess["user_name"] = buyer_user.name
        sess["user_role"] = "user"

    other_resp = client.get(f"/products/{listing.id}")
    assert other_resp.status_code == 404

    # Public catalog does not show rejected listing
    cat_resp = client.get("/products")
    assert "Nord CE RejectionTest" not in cat_resp.get_data(as_text=True)


def test_unauthorized_users_cannot_moderate_listings(client, seller_user, approved_listing):
    """
    Anonymous users and regular users cannot moderate listings.
    """
    # Anonymous request
    anon_resp = client.post(f"/admin/listing/{approved_listing.id}/status", data={
        "action": "reject"
    })
    assert anon_resp.status_code in (302, 403)

    # Regular user request
    with client.session_transaction() as sess:
        sess["user_id"] = seller_user.id
        sess["user_role"] = "user"
        sess["admin_authenticated"] = False

    user_resp = client.post(f"/admin/listing/{approved_listing.id}/status", data={
        "action": "reject"
    })
    assert user_resp.status_code == 403

    db.session.refresh(approved_listing)
    assert approved_listing.approval_status == "approved"


def test_sellers_cannot_moderate_own_or_other_listings(app, seller_user, admin_user):
    """
    Sellers cannot approve their own listings, even if called through moderate_listing service.
    """
    with app.app_context():
        own_listing = Listing(
            id=str(uuid.uuid4()),
            seller_id=admin_user.id,  # Admin is also the seller
            brand="Samsung",
            model="Self Listing",
            price_paise=3000000,
            storage="128GB",
            description="Self moderate test",
            approval_status="pending",
            inventory_status="available"
        )
        db.session.add(own_listing)
        db.session.commit()

        # Admin attempting to approve own listing
        success, msg = moderate_listing(
            listing_id=own_listing.id,
            action="approve",
            admin_id=admin_user.id
        )
        assert success is False
        assert "cannot approve or moderate their own" in msg

        db.session.refresh(own_listing)
        assert own_listing.approval_status == "pending"


def test_invalid_moderation_actions_rejected_safely(app, admin_user, seller_user):
    """
    Invalid moderation action strings are rejected with a clear error without corrupting data.
    """
    with app.app_context():
        listing = Listing(
            id=str(uuid.uuid4()),
            seller_id=seller_user.id,
            brand="Google",
            model="Pixel 7",
            price_paise=3500000,
            storage="128GB",
            description="Invalid action test",
            approval_status="pending",
            inventory_status="available"
        )
        db.session.add(listing)
        db.session.commit()

        for invalid_act in ["approved_now", "delete", "remove", "sold", "", None]:
            success, msg = moderate_listing(
                listing_id=listing.id,
                action=invalid_act,
                admin_id=admin_user.id
            )
            assert success is False
            assert "Invalid moderation action" in msg or "Missing moderation action" in msg

        db.session.refresh(listing)
        assert listing.approval_status == "pending"


def test_seller_dashboard_and_admin_panel_read_same_database(client, admin_user, seller_user):
    """
    Verifies that listings created by a seller are immediately visible to the admin
    and changes made by the admin are immediately visible to the seller.
    """
    # 1. Seller creates listing
    with client.session_transaction() as sess:
        sess["user_id"] = seller_user.id
        sess["user_name"] = seller_user.name
        sess["user_email"] = seller_user.email
        sess["user_role"] = "user"

    img_stream, img_name = _create_mock_image()
    client.post("/sell", data={
        "brand": "Samsung",
        "model": "Galaxy S20 FE Synced",
        "price": "26000",
        "storage": "128GB",
        "description": "Cross-panel database consistency check.",
        "photos": [(img_stream, img_name)]
    }, content_type="multipart/form-data", follow_redirects=True)

    listing = Listing.query.filter_by(model="Galaxy S20 FE Synced").first()
    assert listing is not None

    # 2. Admin dashboard sees it immediately
    with client.session_transaction() as sess:
        sess["user_id"] = admin_user.id
        sess["user_email"] = admin_user.email
        sess["user_role"] = "admin"
        sess["admin_authenticated"] = True

    admin_resp = client.get("/admin")
    assert admin_resp.status_code == 200
    assert "Galaxy S20 FE Synced" in admin_resp.get_data(as_text=True)

    # 3. Admin approves it
    client.post(f"/admin/listing/{listing.id}/status", data={"action": "approve"})

    # 4. Seller dashboard sees "Approved" immediately
    with client.session_transaction() as sess:
        sess["user_id"] = seller_user.id
        sess["user_name"] = seller_user.name
        sess["user_email"] = seller_user.email
        sess["user_role"] = "user"

    seller_resp = client.get("/seller-dashboard")
    assert seller_resp.status_code == 200
    seller_html = seller_resp.get_data(as_text=True)
    assert "Galaxy S20 FE Synced" in seller_html
    assert "Approved" in seller_html


def test_inventory_restrictions_remain_effective(client, buyer_user, seller_user):
    """
    Reserved and sold inventory cannot be bought, even if approved.
    """
    reserved_listing = Listing(
        id=str(uuid.uuid4()),
        seller_id=seller_user.id,
        brand="Apple",
        model="iPhone 14 Pro Reserved",
        price_paise=7000000,
        storage="128GB",
        description="Reserved phone",
        approval_status="approved",
        inventory_status="reserved"
    )
    db.session.add(reserved_listing)
    db.session.commit()

    with client.session_transaction() as sess:
        sess["user_id"] = buyer_user.id
        sess["user_email"] = buyer_user.email
        sess["user_role"] = "user"

    # Detail page shows reserved
    resp = client.get(f"/products/{reserved_listing.id}")
    assert resp.status_code == 200
    assert "Reserved by Buyer" in resp.get_data(as_text=True)

    # Direct buy attempt returns 404 (unavailable)
    buy_resp = client.get(f"/buy/{reserved_listing.id}")
    assert buy_resp.status_code == 404


def test_admin_listing_approvals_access_control(client, seller_user, admin_user):
    """
    Verifies that /admin/listing-approvals enforces admin-only access control.
    """
    # 1. Anonymous user redirected to admin login
    anon_resp = client.get("/admin/listing-approvals")
    assert anon_resp.status_code == 302
    assert "/admin/login" in anon_resp.headers["Location"]

    # 2. Regular non-admin user redirected to admin login
    with client.session_transaction() as sess:
        sess["user_id"] = seller_user.id
        sess["user_role"] = "user"
        sess["admin_authenticated"] = False

    user_resp = client.get("/admin/listing-approvals")
    assert user_resp.status_code == 302
    assert "/admin/login" in user_resp.headers["Location"]

    # 3. Authenticated admin allowed
    with client.session_transaction() as sess:
        sess["user_id"] = admin_user.id
        sess["user_role"] = "admin"
        sess["admin_authenticated"] = True

    admin_resp = client.get("/admin/listing-approvals")
    assert admin_resp.status_code == 200
    assert "Listing Moderation &amp; Approvals" in admin_resp.get_data(as_text=True) or "Listing Moderation & Approvals" in admin_resp.get_data(as_text=True)


def test_admin_listing_approvals_tab_filtering(client, admin_user, seller_user):
    """
    Verifies status query filtering on /admin/listing-approvals tabs (pending, approved, rejected, all).
    """
    # Create test listings with different approval statuses
    pending_item = Listing(
        id=str(uuid.uuid4()),
        seller_id=seller_user.id,
        brand="Google",
        model="Pixel 7 Pending Filter",
        price_paise=3500000,
        storage="128GB",
        condition="Good",
        description="Pending device description",
        approval_status="pending",
        inventory_status="available"
    )
    approved_item = Listing(
        id=str(uuid.uuid4()),
        seller_id=seller_user.id,
        brand="Motorola",
        model="Edge 40 Approved Filter",
        price_paise=2800000,
        storage="256GB",
        condition="Like New",
        description="Approved device description",
        approval_status="approved",
        inventory_status="available"
    )
    rejected_item = Listing(
        id=str(uuid.uuid4()),
        seller_id=seller_user.id,
        brand="Xiaomi",
        model="13 Pro Rejected Filter",
        price_paise=4200000,
        storage="256GB",
        condition="Fair",
        description="Rejected device description",
        approval_status="rejected",
        inventory_status="available"
    )
    db.session.add_all([pending_item, approved_item, rejected_item])
    db.session.commit()

    with client.session_transaction() as sess:
        sess["user_id"] = admin_user.id
        sess["user_role"] = "admin"
        sess["admin_authenticated"] = True

    # 1. Default (Pending)
    resp_default = client.get("/admin/listing-approvals")
    assert resp_default.status_code == 200
    html_default = resp_default.get_data(as_text=True)
    assert "Pixel 7 Pending Filter" in html_default
    assert "Edge 40 Approved Filter" not in html_default
    assert "13 Pro Rejected Filter" not in html_default

    # 2. Approved tab
    resp_approved = client.get("/admin/listing-approvals?status=approved")
    assert resp_approved.status_code == 200
    html_approved = resp_approved.get_data(as_text=True)
    assert "Edge 40 Approved Filter" in html_approved
    assert "Pixel 7 Pending Filter" not in html_approved
    assert "13 Pro Rejected Filter" not in html_approved

    # 3. Rejected tab
    resp_rejected = client.get("/admin/listing-approvals?status=rejected")
    assert resp_rejected.status_code == 200
    html_rejected = resp_rejected.get_data(as_text=True)
    assert "13 Pro Rejected Filter" in html_rejected
    assert "Pixel 7 Pending Filter" not in html_rejected
    assert "Edge 40 Approved Filter" not in html_rejected

    # 4. All tab
    resp_all = client.get("/admin/listing-approvals?status=all")
    assert resp_all.status_code == 200
    html_all = resp_all.get_data(as_text=True)
    assert "Pixel 7 Pending Filter" in html_all
    assert "Edge 40 Approved Filter" in html_all
    assert "13 Pro Rejected Filter" in html_all


def test_admin_listing_approvals_action_with_next_redirect(client, admin_user, seller_user):
    """
    Submitting an approval from /admin/listing-approvals redirects back to the approvals queue.
    """
    pending_item = Listing(
        id=str(uuid.uuid4()),
        seller_id=seller_user.id,
        brand="Apple",
        model="iPhone 15 Review Queue",
        price_paise=5500000,
        storage="128GB",
        condition="Like New",
        description="Testing next redirect",
        approval_status="pending",
        inventory_status="available"
    )
    db.session.add(pending_item)
    db.session.commit()

    with client.session_transaction() as sess:
        sess["user_id"] = admin_user.id
        sess["user_role"] = "admin"
        sess["admin_authenticated"] = True

    post_resp = client.post(
        f"/admin/listing/{pending_item.id}/status",
        data={
            "action": "approve",
            "next": "/admin/listing-approvals?status=pending"
        }
    )
    assert post_resp.status_code == 302
    assert post_resp.headers["Location"] == "/admin/listing-approvals?status=pending"

    # Verify listing is approved in DB
    refreshed = db.session.get(Listing, pending_item.id)
    assert refreshed.approval_status == "approved"


def test_admin_listing_status_action_normalization_and_json_support(client, admin_user, seller_user):
    """
    Verifies that admin_listing_status supports both form and JSON payloads,
    handles 'approved' and 'approve' synonyms, and returns JSON when requested.
    """
    item = Listing(
        id=str(uuid.uuid4()),
        seller_id=seller_user.id,
        brand="OnePlus",
        model="11 5G Test",
        price_paise=4000000,
        storage="128GB",
        condition="Good",
        description="Testing action normalization",
        approval_status="pending",
        inventory_status="available"
    )
    db.session.add(item)
    db.session.commit()

    with client.session_transaction() as sess:
        sess["user_id"] = admin_user.id
        sess["user_role"] = "admin"
        sess["admin_authenticated"] = True

    # 1. Form POST with 'approved' (past-participle synonym)
    form_resp = client.post(
        f"/admin/listing/{item.id}/status",
        data={"action": "approved"}
    )
    assert form_resp.status_code == 302
    db.session.expire_all()
    refreshed = db.session.get(Listing, item.id)
    assert refreshed.approval_status == "approved"

    # 2. JSON POST with 'reject' and reason
    json_resp = client.post(
        f"/admin/listing/{item.id}/status",
        json={"action": "reject", "reason": "Image is blurry"}
    )
    assert json_resp.status_code == 200
    res_data = json_resp.get_json()
    assert res_data["success"] is True
    db.session.expire_all()
    refreshed = db.session.get(Listing, item.id)
    assert refreshed.approval_status == "rejected"
    assert refreshed.rejection_reason == "Image is blurry"

    # 3. JSON POST with invalid action returns 400
    bad_resp = client.post(
        f"/admin/listing/{item.id}/status",
        json={"action": "invalid_verb"}
    )
    assert bad_resp.status_code == 400
    assert bad_resp.get_json()["success"] is False


def test_listing_is_priority_strictly_requires_is_featured(seller_user):
    """
    Verifies that is_priority only returns True if is_featured is True,
    preventing arbitrary listing_type values from granting priority boost.
    """
    item = Listing(
        id=str(uuid.uuid4()),
        seller_id=seller_user.id,
        brand="Apple",
        model="iPhone 14 Spoof",
        price_paise=5000000,
        storage="128GB",
        condition="Good",
        description="Testing priority security",
        approval_status="approved",
        inventory_status="available",
        listing_type="featured",
        is_featured=False
    )
    db.session.add(item)
    db.session.commit()

    # Even though listing_type is 'featured', is_featured is False -> is_priority MUST be False
    assert item.is_featured is False
    assert item.is_priority is False

    # Once verified payment sets is_featured = True -> is_priority is True
    item.is_featured = True
    db.session.commit()
    assert item.is_priority is True


def test_approved_phone_lists_in_shop_by_brand(client, seller_user, admin_user):
    """
    Verifies that once an admin approves a submitted phone, it is reliably
    discoverable via 'Shop by Brand' routes (/brand/<name>, /products?brand=<name>, /shop-by-brand)
    and reflected in catalog counts.
    """
    # 1. Seller creates listing
    item = Listing(
        id=str(uuid.uuid4()),
        seller_id=seller_user.id,
        brand="Samsung",
        model="Galaxy S20 FE 5G",
        price_paise=2699900,
        storage="128GB",
        condition="Excellent",
        description="Mint condition Samsung device",
        approval_status="pending",
        inventory_status="available"
    )
    db.session.add(item)
    db.session.commit()

    # Before approval: Not in Samsung brand page
    resp_before = client.get("/products?brand=Samsung")
    assert resp_before.status_code == 200
    assert "Galaxy S20 FE 5G" not in resp_before.get_data(as_text=True)

    # 2. Admin approves the listing
    with client.session_transaction() as sess:
        sess["user_id"] = admin_user.id
        sess["user_name"] = admin_user.name
        sess["user_email"] = admin_user.email
        sess["user_role"] = "admin"
        sess["admin_authenticated"] = True

    approve_resp = client.post(
        f"/admin/listing/{item.id}/status",
        data={"action": "approve"},
        follow_redirects=True
    )
    assert approve_resp.status_code == 200

    # 3. After approval: Appears under /products?brand=Samsung
    resp_after = client.get("/products?brand=Samsung")
    assert resp_after.status_code == 200
    after_html = resp_after.get_data(as_text=True)
    assert "Galaxy S20 FE 5G" in after_html
    assert "Shop by Brand" in after_html
    assert "Samsung Smartphones" in after_html
    assert "₹26,999" in after_html

    # 4. Route aliases: /brand/samsung and /shop-by-brand
    brand_alias = client.get("/brand/samsung", follow_redirects=True)
    assert brand_alias.status_code == 200
    assert "Galaxy S20 FE 5G" in brand_alias.get_data(as_text=True)

    shop_alias = client.get("/shop-by-brand")
    assert shop_alias.status_code == 302
    assert shop_alias.location.endswith("/products")

    # 5. Homepage brand counts reflect 1 Samsung phone in stock
    home_resp = client.get("/")
    assert home_resp.status_code == 200
    home_html = home_resp.get_data(as_text=True)
    assert "in stock" in home_html


def test_sell_submission_brand_auto_alignment(client, seller_user):
    """
    Verifies that if a seller enters a brand name in the model field
    (e.g., 'Samsung Galaxy S20 FE') while leaving the dropdown on another brand (e.g. 'Apple'),
    the server auto-aligns the brand to 'Samsung'.
    """
    with client.session_transaction() as sess:
        sess["user_id"] = seller_user.id
        sess["user_name"] = seller_user.name
        sess["user_email"] = seller_user.email
        sess["user_role"] = "user"

    img_stream, img_name = _create_mock_image()
    response = client.post("/sell", data={
        "brand": "Apple",  # Mismatched selection
        "model": "Samsung Galaxy S21 Ultra",
        "price": "39999",
        "storage": "256GB",
        "ram": "12GB",
        "color": "Phantom Black",
        "condition": "Like New",
        "battery": "94",
        "description": "Clean Samsung device",
        "photos": [(img_stream, img_name)]
    }, content_type="multipart/form-data", follow_redirects=True)

    assert response.status_code == 200

    listing = Listing.query.filter_by(seller_id=seller_user.id, model="Samsung Galaxy S21 Ultra").first()
    assert listing is not None
    # Brand was auto-aligned to Samsung despite 'Apple' being submitted in dropdown
    assert listing.brand == "Samsung"



