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
        query = query.filter(Listing.brand.ilike(f"%{b_clean}%"))

    if condition:
        c_clean = condition.strip().lower()
        query = query.filter(Listing.condition.ilike(f"%{c_clean}%"))

    if storage:
        s_clean = storage.strip().lower()
        query = query.filter(Listing.storage.ilike(f"%{s_clean}%"))

    if ram:
        r_clean = ram.strip().lower()
        query = query.filter(Listing.ram.ilike(f"%{r_clean}%"))

    if min_price is not None and min_price > 0:
        query = query.filter(Listing.price_paise >= int(round(min_price * 100)))

    if max_price is not None and max_price > 0:
        query = query.filter(Listing.price_paise <= int(round(max_price * 100)))

    # Sorting: Priority boosted listings appear first
    if sort_by == "price_asc":
        query = query.order_by(Listing.is_featured.desc(), Listing.price_paise.asc())
    elif sort_by == "price_desc":
        query = query.order_by(Listing.is_featured.desc(), Listing.price_paise.desc())
    else:  # default 'latest'
        query = query.order_by(Listing.is_featured.desc(), Listing.created_at.desc())

    # Pagination
    return query.paginate(page=page, per_page=per_page, error_out=False)


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
    Admin moderation for listings: 'approve', 'reject', or 'hide'.
    Logs audit event and sends notification to seller.
    """
    listing = db.session.get(Listing, listing_id)
    if not listing:
        return False, "Listing not found."

    old_status = listing.approval_status
    if action == "approve":
        listing.approval_status = "approved"
        listing.rejection_reason = None
        listing.reviewed_at = utc_now()
        title = "Your phone is Approved!"
        msg = f"{listing.brand} {listing.model} is now live on iSellora. Buyers can view and purchase it."
    elif action == "reject":
        listing.approval_status = "rejected"
        listing.rejection_reason = reason or "Listing did not meet marketplace quality guidelines."
        listing.reviewed_at = utc_now()
        title = "Listing Needs Changes"
        msg = f"{listing.brand} {listing.model} was rejected: {listing.rejection_reason}"
    elif action in ("hide", "pending"):
        listing.approval_status = "pending"
        listing.reviewed_at = utc_now()
        title = "Listing Under Review"
        msg = f"{listing.brand} {listing.model} is pending administrative review."
    else:
        return False, f"Invalid moderation action: {action}"

    # Log audit event
    audit = AuditEvent(
        user_id=admin_id,
        action=f"LISTING_MODERATION_{action.upper()}",
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

    db.session.commit()
    return True, ""
