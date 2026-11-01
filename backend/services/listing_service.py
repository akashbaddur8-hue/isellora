"""
Listing discovery, multi-criteria filtering, pagination, and moderation service.
"""
import os
import uuid
from werkzeug.utils import secure_filename
from backend.models import Listing, ListingImage, Notification, AuditEvent, utc_now
from backend.database import db

ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "webp"}


def query_catalog(
    search: str = None,
    brand: str = None,
    condition: str = None,
    storage: str = None,
    ram: str = None,
    min_price: float = None,
    max_price: float = None,
    sort_by: str = "latest",
    page: int = 1,
    per_page: int = 12
):
    """
    Executes server-side search, filtering, sorting, and pagination over active approved listings.
    """
    query = Listing.query.filter(
        Listing.approval_status == "approved",
        Listing.inventory_status == "available"
    )

    if search:
        term = f"%{search.strip().lower()}%"
        query = query.filter(
            (Listing.brand.ilike(term)) | (Listing.model.ilike(term)) | (Listing.description.ilike(term))
        )

    if brand:
        b_clean = brand.strip().lower()
        alt_terms = []
        if "pixel" in b_clean:
            alt_terms.append("google")
        if "galaxy" in b_clean:
            alt_terms.append("samsung")
        if "iphone" in b_clean:
            alt_terms.append("apple")
        if b_clean == "google":
            alt_terms.append("pixel")
        if b_clean == "samsung":
            alt_terms.append("galaxy")
        if b_clean == "apple":
            alt_terms.append("iphone")

        from sqlalchemy import or_
        brand_filters = [
            Listing.brand.ilike(f"%{b_clean}%"),
            Listing.model.ilike(f"%{b_clean}%")
        ]
        for alt in alt_terms:
            brand_filters.append(Listing.brand.ilike(f"%{alt}%"))
            brand_filters.append(Listing.model.ilike(f"%{alt}%"))

        query = query.filter(or_(*brand_filters))

    if condition:
        c_clean = condition.strip().lower()
        query = query.filter(Listing.condition.ilike(f"%{c_clean}%"))

    if storage:
        s_clean = storage.strip().lower()
        query = query.filter(Listing.storage.ilike(f"%{s_clean}%"))

    if ram:
        r_clean = ram.strip().lower()
        query = query.filter(Listing.ram.ilike(f"%{r_clean}%"))

    import math

    if min_price is not None and isinstance(min_price, (int, float)) and math.isfinite(min_price) and min_price > 0:
        try:
            min_paise = int(round(min_price * 100))
            query = query.filter(Listing.price_paise >= min_paise)
        except (ValueError, OverflowError):
            pass

    if max_price is not None and isinstance(max_price, (int, float)) and math.isfinite(max_price) and max_price > 0:
        try:
            max_paise = int(round(max_price * 100))
            query = query.filter(Listing.price_paise <= max_paise)
        except (ValueError, OverflowError):
            pass

    # Sorting: Priority boosted listings appear first
    if sort_by == "price_asc":
        query = query.order_by(Listing.is_featured.desc(), Listing.price_paise.asc())
    elif sort_by == "price_desc":
        query = query.order_by(Listing.is_featured.desc(), Listing.price_paise.desc())
    else:  # default 'latest'
        query = query.order_by(Listing.is_featured.desc(), Listing.created_at.desc())

    # Pagination bounds clamping
    try:
        safe_page = max(1, min(int(page), 10000))
    except (ValueError, TypeError, OverflowError):
        safe_page = 1

    try:
        safe_per_page = max(1, min(int(per_page), 100))
    except (ValueError, TypeError, OverflowError):
        safe_per_page = 12

    return query.paginate(page=safe_page, per_page=safe_per_page, error_out=False)


def validate_image_file(file_storage) -> bool:
    """Validates filename extension and checks file magic bytes."""
    if not file_storage or not file_storage.filename:
        return False

    name = secure_filename(file_storage.filename)
    if "." not in name:
        return False

    ext = name.rsplit(".", 1)[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        return False

    pos = file_storage.stream.tell()
    header = file_storage.stream.read(16)
    file_storage.stream.seek(pos)

    if ext in ("jpg", "jpeg"):
        return header.startswith(b"\xff\xd8\xff")
    if ext == "png":
        return header.startswith(b"\x89PNG\r\n\x1a\n")
    if ext == "webp":
        return header.startswith(b"RIFF") and header[8:12] == b"WEBP"

    return False


def moderate_listing(
    listing_id: str,
    action: str,
    admin_id: str,
    reason: str = None
) -> tuple[bool, str]:
    """
    Admin moderation for listings.
    Canonical actions: 'approve', 'reject', 'pending'
    Database statuses: 'approved', 'rejected', 'pending'
    Logs audit event and sends notification to seller.
    """
    if not action or not isinstance(action, str):
        return False, "Missing moderation action."

    action_norm = action.strip().lower()

    listing = db.session.get(Listing, listing_id)
    if not listing:
        return False, "Listing not found."

    # Prevent sellers from moderating their own listings
    if admin_id and listing.seller_id and str(listing.seller_id) == str(admin_id):
        return False, "Sellers cannot approve or moderate their own listings."

    old_status = listing.approval_status

    if action_norm in ("approve", "approved"):
        canonical_action = "approve"
        listing.approval_status = "approved"
        listing.rejection_reason = None
        listing.reviewed_at = utc_now()
        title = "Your phone is Approved!"
        msg = f"{listing.brand} {listing.model} is now live on iSellora. Buyers can view and purchase it."

    elif action_norm in ("reject", "rejected"):
        canonical_action = "reject"
        listing.approval_status = "rejected"
        listing.rejection_reason = reason or "Listing did not meet marketplace quality guidelines."
        listing.reviewed_at = utc_now()
        title = "Listing Needs Changes"
        msg = f"{listing.brand} {listing.model} was rejected: {listing.rejection_reason}"

    elif action_norm in ("pending", "hide", "review"):
        canonical_action = "pending"
        listing.approval_status = "pending"
        listing.reviewed_at = utc_now()
        title = "Listing Under Review"
        msg = f"{listing.brand} {listing.model} is pending administrative review."

    else:
        return False, f"Invalid moderation action: {action}"

    # Log audit event
    audit = AuditEvent(
        user_id=admin_id,
        action=f"LISTING_MODERATION_{canonical_action.upper()}",
        target_type="listing",
        target_id=listing_id,
        details=f"Status changed from {old_status} to {listing.approval_status}. Reason: {reason or 'N/A'}",
        created_at=utc_now()
    )
    db.session.add(audit)

    # Notify seller
    if listing.seller_id:
        notice = Notification(
            user_id=listing.seller_id,
            title=title,
            message=msg,
            listing_id=listing_id,
            created_at=utc_now()
        )
        db.session.add(notice)

    try:
        db.session.commit()
        return True, ""
    except Exception as e:
        db.session.rollback()
        return False, f"Database transaction failed: {str(e)}"
