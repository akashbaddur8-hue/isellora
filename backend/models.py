"""
SQLAlchemy models for the iSellora Marketplace.
Defines relational schemas for Users, Listings, Images, Orders, History, Notifications, Support, Disputes, and Audits.
"""
from datetime import datetime, timezone
import uuid
import re
from urllib.parse import quote_plus
from werkzeug.security import generate_password_hash, check_password_hash
from backend.database import db

def utc_now():
    return datetime.now(timezone.utc)

def generate_uuid():
    return str(uuid.uuid4())


class User(db.Model):
    __tablename__ = "users"

    id = db.Column(db.String(36), primary_key=True, default=generate_uuid)
    name = db.Column(db.String(100), nullable=False)
    username = db.Column(db.String(50), unique=True, nullable=True, index=True)
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    phone = db.Column(db.String(20), nullable=False)
    avatar_path = db.Column(db.String(255), nullable=True)
    city = db.Column(db.String(100), nullable=True)
    state = db.Column(db.String(100), nullable=True)
    bio = db.Column(db.Text, nullable=True)
    is_verified = db.Column(db.Boolean, default=False, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), default="user", nullable=False)  # 'user', 'admin'
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at = db.Column(db.DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)

    # Relationships
    listings = db.relationship("Listing", backref="seller", lazy="dynamic", foreign_keys="Listing.seller_id")
    buyer_orders = db.relationship("Order", backref="buyer", lazy="dynamic", foreign_keys="Order.buyer_id")
    seller_orders = db.relationship("Order", backref="seller_user", lazy="dynamic", foreign_keys="Order.seller_id")
    notifications = db.relationship("Notification", backref="user", lazy="dynamic", cascade="all, delete-orphan")

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        if not self.password_hash:
            return False
        return check_password_hash(self.password_hash, password)

    @property
    def is_admin(self):
        return self.role == "admin"

    @property
    def avatar_url(self):
        return self.avatar_path if self.avatar_path else None

    @property
    def display_location(self):
        parts = [p.strip() for p in (self.city, self.state) if p and p.strip()]
        return ", ".join(parts) if parts else None

    @property
    def initial(self):
        return (self.name or "U")[0].upper()

    @property
    def member_since(self):
        return self.created_at.strftime("%B %Y") if self.created_at else "Recently"

    @property
    def average_rating(self):
        reviews = Review.query.filter_by(seller_id=self.id).all()
        if not reviews:
            return None
        return round(sum(r.rating for r in reviews) / len(reviews), 1)

    @property
    def review_count(self):
        return Review.query.filter_by(seller_id=self.id).count()

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "username": self.username or "",
            "email": self.email,
            "phone": self.phone,
            "city": self.city or "",
            "state": self.state or "",
            "bio": self.bio or "",
            "avatar_url": self.avatar_url or "",
            "is_verified": self.is_verified,
            "role": self.role,
            "is_active": self.is_active,
            "created_at": self.created_at.isoformat() if self.created_at else ""
        }


class Listing(db.Model):
    __tablename__ = "listings"

    id = db.Column(db.String(36), primary_key=True, default=generate_uuid)
    seller_id = db.Column(db.String(36), db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    
    brand = db.Column(db.String(100), nullable=False, index=True)
    model = db.Column(db.String(150), nullable=False, index=True)
    price_paise = db.Column(db.BigInteger, nullable=False)  # Price in paise (INR * 100)
    
    storage = db.Column(db.String(50), nullable=False, index=True)
    ram = db.Column(db.String(50), nullable=True)
    color = db.Column(db.String(50), nullable=True)
    battery_health = db.Column(db.Integer, nullable=True)  # Percentage 1-100
    condition = db.Column(db.String(50), nullable=False, default="Good", index=True)
    description = db.Column(db.Text, nullable=False)

    # Trust & Disclosure
    screen_damage = db.Column(db.String(100), default="No Damage")
    body_damage = db.Column(db.String(100), default="No Damage")
    repair_history = db.Column(db.String(100), default="Never Repaired")
    original_display = db.Column(db.String(50), default="Yes")
    accessories = db.Column(db.String(100), default="Phone Only")
    known_defects = db.Column(db.Text, nullable=True)
    account_lock_status = db.Column(db.String(100), default="Clean / Unlocked")
    purchase_age = db.Column(db.String(100), nullable=True)

    # Administrative and Status
    listing_type = db.Column(db.String(20), default="free")  # 'free', 'featured'
    is_featured = db.Column(db.Boolean, default=False, nullable=False, index=True)
    featured_at = db.Column(db.DateTime(timezone=True), nullable=True)
    approval_status = db.Column(db.String(20), default="pending", nullable=False, index=True)  # 'pending', 'approved', 'rejected', 'archived'
    inventory_status = db.Column(db.String(20), default="available", nullable=False, index=True)  # 'available', 'reserved', 'sold'
    rejection_reason = db.Column(db.Text, nullable=True)
    reviewed_at = db.Column(db.DateTime(timezone=True), nullable=True)

    # Preserved seller snapshot (in case user record changes or is anonymous in legacy data)
    seller_name = db.Column(db.String(100), nullable=True)
    seller_phone = db.Column(db.String(20), nullable=True)

    created_at = db.Column(db.DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at = db.Column(db.DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)

    # Relationships
    images = db.relationship("ListingImage", backref="listing", cascade="all, delete-orphan", order_by="ListingImage.display_order")

    @property
    def price_rupees(self):
        return self.price_paise // 100

    @property
    def formatted_price(self):
        return f"₹{self.price_rupees:,.0f}"

    @property
    def primary_image(self):
        if self.images:
            return self.images[0].image_path
        return "/static/images/phone-placeholder.svg"

    @property
    def all_image_paths(self):
        return [img.image_path for img in self.images]

    @property
    def is_purchasable(self):
        return self.approval_status == "approved" and self.inventory_status == "available"

    @property
    def is_priority(self):
        return bool(self.is_featured)

    def to_dict(self):
        return {
            "id": self.id,
            "seller_id": self.seller_id,
            "brand": self.brand,
            "model": self.model,
            "price": str(self.price_rupees),
            "price_paise": self.price_paise,
            "formatted_price": self.formatted_price,
            "storage": self.storage,
            "ram": self.ram or "",
            "color": self.color or "",
            "battery": str(self.battery_health) if self.battery_health is not None else "",
            "battery_health": self.battery_health,
            "condition": self.condition,
            "description": self.description,
            "screen_damage": self.screen_damage,
            "body_damage": self.body_damage,
            "repair_history": self.repair_history,
            "original_display": self.original_display,
            "accessories": self.accessories,
            "listing_type": self.listing_type,
            "is_featured": self.is_priority,
            "featured_at": self.featured_at.isoformat() if self.featured_at else "",
            "approval_status": self.approval_status,
            "inventory_status": self.inventory_status,
            "seller_name": self.seller_name or (self.seller.name if self.seller else ""),
            "seller_phone": self.seller_phone or (self.seller.phone if self.seller else ""),
            "images": self.all_image_paths,
            "created_at": self.created_at.isoformat() if self.created_at else "",
            "reviewed_at": self.reviewed_at.isoformat() if self.reviewed_at else ""
        }


class ListingImage(db.Model):
    __tablename__ = "listing_images"

    id = db.Column(db.String(36), primary_key=True, default=generate_uuid)
    listing_id = db.Column(db.String(36), db.ForeignKey("listings.id", ondelete="CASCADE"), nullable=False, index=True)
    image_path = db.Column(db.String(255), nullable=False)
    display_order = db.Column(db.Integer, default=0, nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), default=utc_now, nullable=False)


class Order(db.Model):
    __tablename__ = "orders"

    id = db.Column(db.String(36), primary_key=True, default=generate_uuid)
    checkout_token = db.Column(db.String(64), unique=True, nullable=False, index=True)
    
    buyer_id = db.Column(db.String(36), db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    seller_id = db.Column(db.String(36), db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    listing_id = db.Column(db.String(36), db.ForeignKey("listings.id", ondelete="SET NULL"), nullable=True, index=True)

    # Immutable item details snapshot
    brand = db.Column(db.String(100), nullable=False)
    model = db.Column(db.String(150), nullable=False)
    storage = db.Column(db.String(50), nullable=True)
    
    # Financial snapshots stored as paise
    price_paise = db.Column(db.BigInteger, nullable=False)
    delivery_charge_paise = db.Column(db.BigInteger, default=0, nullable=False)
    total_paise = db.Column(db.BigInteger, nullable=False)

    # Delivery & Contact snapshot
    delivery_method = db.Column(db.String(20), default="pickup", nullable=False)  # 'pickup', 'shipping'
    buyer_name = db.Column(db.String(100), nullable=False)
    buyer_phone = db.Column(db.String(20), nullable=False)
    shipping_address = db.Column(db.Text, nullable=False)
    pincode = db.Column(db.String(10), nullable=True)
    courier_name = db.Column(db.String(100), nullable=True)
    tracking_number = db.Column(db.String(100), nullable=True)
    
    seller_name = db.Column(db.String(100), nullable=True)
    seller_phone = db.Column(db.String(20), nullable=True)

    # Status machine
    status = db.Column(db.String(20), default="Pending", nullable=False, index=True)  # 'Pending', 'Confirmed', 'Shipped', 'Delivered', 'Cancelled'
    
    # Payment architecture (Razorpay online checkout & Cash on Delivery)
    payment_method = db.Column(db.String(50), default="cod", nullable=False)  # 'razorpay', 'cod'
    payment_status = db.Column(db.String(50), default="Not collected", nullable=False, index=True)  # 'created', 'pending', 'authorized', 'paid', 'failed', 'refunded', 'Not collected', 'Collected upon delivery'
    razorpay_order_id = db.Column(db.String(100), nullable=True, index=True)
    razorpay_payment_id = db.Column(db.String(100), nullable=True, index=True)
    
    # Cancellation audit
    cancellation_reason = db.Column(db.Text, nullable=True)
    cancelled_by = db.Column(db.String(50), nullable=True)  # 'buyer', 'seller', 'admin', 'system'

    created_at = db.Column(db.DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at = db.Column(db.DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)

    # Relationships
    listing = db.relationship("Listing", foreign_keys=[listing_id])
    status_history = db.relationship("OrderStatusHistory", backref="order", cascade="all, delete-orphan", order_by="OrderStatusHistory.created_at")

    @property
    def price_rupees(self):
        return self.price_paise // 100

    @property
    def delivery_charge_rupees(self):
        return self.delivery_charge_paise // 100

    @property
    def total_rupees(self):
        return self.total_paise // 100

    @property
    def formatted_total(self):
        return f"₹{self.total_rupees:,.0f}"

    @property
    def tracking_url(self):
        if not self.courier_name or not self.tracking_number:
            return None
        return get_legitimate_tracking_url(self.courier_name, self.tracking_number)

    def to_dict(self):
        return {
            "order_id": self.id,
            "checkout_token": self.checkout_token,
            "product_id": self.listing_id or "",
            "brand": self.brand,
            "model": self.model,
            "price": str(self.price_rupees),
            "storage": self.storage or "",
            "buyer_id": self.buyer_id,
            "buyer_name": self.buyer_name,
            "buyer_phone": self.buyer_phone,
            "seller_id": self.seller_id,
            "seller_name": self.seller_name or "",
            "seller_phone": self.seller_phone or "",
            "delivery_method": self.delivery_method,
            "address": self.shipping_address,
            "pincode": self.pincode or "",
            "delivery_charge": self.delivery_charge_rupees,
            "courier_name": self.courier_name or "",
            "tracking_number": self.tracking_number or "",
            "tracking_url": self.tracking_url,
            "status": self.status,
            "payment_status": self.payment_status,
            "created_at": self.created_at.isoformat() if self.created_at else "",
            "updated_at": self.updated_at.isoformat() if self.updated_at else ""
        }


class OrderStatusHistory(db.Model):
    __tablename__ = "order_status_history"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    order_id = db.Column(db.String(36), db.ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, index=True)
    from_status = db.Column(db.String(30), nullable=True)
    to_status = db.Column(db.String(30), nullable=False)
    actor_id = db.Column(db.String(36), nullable=True)
    actor_role = db.Column(db.String(30), nullable=False)  # 'buyer', 'seller', 'admin', 'system'
    notes = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), default=utc_now, nullable=False)


class Notification(db.Model):
    __tablename__ = "notifications"

    id = db.Column(db.String(36), primary_key=True, default=generate_uuid)
    user_id = db.Column(db.String(36), db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    title = db.Column(db.String(150), nullable=False)
    message = db.Column(db.Text, nullable=False)
    listing_id = db.Column(db.String(36), nullable=True)
    order_id = db.Column(db.String(36), nullable=True)
    is_read = db.Column(db.Boolean, default=False, nullable=False, index=True)
    created_at = db.Column(db.DateTime(timezone=True), default=utc_now, nullable=False)

    def to_dict(self):
        return {
            "id": self.id,
            "user_id": self.user_id,
            "title": self.title,
            "message": self.message,
            "listing_id": self.listing_id,
            "order_id": self.order_id,
            "read": self.is_read,
            "created_at": self.created_at.isoformat() if self.created_at else ""
        }


class SupportTicket(db.Model):
    __tablename__ = "support_tickets"

    id = db.Column(db.String(36), primary_key=True, default=generate_uuid)
    ticket_number = db.Column(db.String(20), unique=True, nullable=False, index=True)
    user_id = db.Column(db.String(36), db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    order_id = db.Column(db.String(36), nullable=True)
    
    subject = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, nullable=False)
    status = db.Column(db.String(20), default="Open", nullable=False, index=True)  # 'Open', 'In Progress', 'Resolved', 'Closed'
    priority = db.Column(db.String(20), default="Medium", nullable=False)  # 'Low', 'Medium', 'High'
    resolution_notes = db.Column(db.Text, nullable=True)
    
    created_at = db.Column(db.DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at = db.Column(db.DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)

    user = db.relationship("User", foreign_keys=[user_id])


class ReportDispute(db.Model):
    __tablename__ = "reports_disputes"

    id = db.Column(db.String(36), primary_key=True, default=generate_uuid)
    reporter_id = db.Column(db.String(36), db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    listing_id = db.Column(db.String(36), db.ForeignKey("listings.id", ondelete="SET NULL"), nullable=True, index=True)
    order_id = db.Column(db.String(36), db.ForeignKey("orders.id", ondelete="SET NULL"), nullable=True, index=True)
    
    report_type = db.Column(db.String(100), nullable=False)
    reason = db.Column(db.Text, nullable=False)
    status = db.Column(db.String(20), default="Pending", nullable=False, index=True)  # 'Pending', 'Reviewed', 'Dismissed', 'Action Taken'
    admin_notes = db.Column(db.Text, nullable=True)
    
    created_at = db.Column(db.DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at = db.Column(db.DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)

    reporter = db.relationship("User", foreign_keys=[reporter_id])
    listing = db.relationship("Listing", foreign_keys=[listing_id])
    order = db.relationship("Order", foreign_keys=[order_id])


class Review(db.Model):
    __tablename__ = "reviews"

    id = db.Column(db.String(36), primary_key=True, default=generate_uuid)
    order_id = db.Column(db.String(36), db.ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, unique=True, index=True)
    buyer_id = db.Column(db.String(36), db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    seller_id = db.Column(db.String(36), db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    listing_id = db.Column(db.String(36), db.ForeignKey("listings.id", ondelete="SET NULL"), nullable=True, index=True)
    rating = db.Column(db.Integer, nullable=False)  # 1 to 5
    comment = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), default=utc_now, nullable=False)

    order = db.relationship("Order", backref=db.backref("review", uselist=False))
    buyer = db.relationship("User", foreign_keys=[buyer_id])
    seller = db.relationship("User", foreign_keys=[seller_id])
    listing = db.relationship("Listing", foreign_keys=[listing_id])


class Wishlist(db.Model):
    __tablename__ = "wishlists"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id = db.Column(db.String(36), db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    listing_id = db.Column(db.String(36), db.ForeignKey("listings.id", ondelete="CASCADE"), nullable=False, index=True)
    created_at = db.Column(db.DateTime(timezone=True), default=utc_now, nullable=False)

    __table_args__ = (
        db.UniqueConstraint("user_id", "listing_id", name="uq_user_listing_wishlist"),
    )

    listing = db.relationship("Listing")


class ListingSubscription(db.Model):
    __tablename__ = "listing_subscriptions"

    id = db.Column(db.String(36), primary_key=True, default=generate_uuid)
    listing_id = db.Column(db.String(36), db.ForeignKey("listings.id", ondelete="CASCADE"), nullable=False, index=True)
    seller_id = db.Column(db.String(36), db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    plan_name = db.Column(db.String(100), default="Priority Placement (Sell First)", nullable=False)
    amount_paise = db.Column(db.Integer, default=4900, nullable=False)  # ₹49 = 4900 paise
    status = db.Column(db.String(30), default="active", nullable=False)  # 'active', 'expired', 'cancelled'
    payment_method = db.Column(db.String(50), default="razorpay", nullable=False)
    payment_status = db.Column(db.String(30), default="pending", nullable=False)  # 'pending', 'paid', 'failed'
    razorpay_order_id = db.Column(db.String(100), nullable=True, index=True)
    razorpay_payment_id = db.Column(db.String(100), nullable=True, index=True)
    created_at = db.Column(db.DateTime(timezone=True), default=utc_now, nullable=False)
    expires_at = db.Column(db.DateTime(timezone=True), nullable=True)

    listing = db.relationship("Listing", backref=db.backref("subscriptions", lazy="dynamic", cascade="all, delete-orphan"))
    seller = db.relationship("User", foreign_keys=[seller_id])

    @property
    def amount_rupees(self):
        return self.amount_paise // 100

    def to_dict(self):
        return {
            "id": self.id,
            "listing_id": self.listing_id,
            "seller_id": self.seller_id,
            "plan_name": self.plan_name,
            "amount_rupees": self.amount_rupees,
            "status": self.status,
            "payment_status": self.payment_status,
            "razorpay_order_id": self.razorpay_order_id or "",
            "razorpay_payment_id": self.razorpay_payment_id or "",
            "created_at": self.created_at.isoformat() if self.created_at else "",
            "expires_at": self.expires_at.isoformat() if self.expires_at else ""
        }


class Payment(db.Model):
    __tablename__ = "payments"

    id = db.Column(db.String(36), primary_key=True, default=generate_uuid)
    order_id = db.Column(db.String(36), db.ForeignKey("orders.id", ondelete="CASCADE"), nullable=True, index=True)
    subscription_id = db.Column(db.String(36), db.ForeignKey("listing_subscriptions.id", ondelete="CASCADE"), nullable=True, index=True)
    user_id = db.Column(db.String(36), db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)

    purpose = db.Column(db.String(50), default="order", nullable=False, index=True)  # 'order', 'priority_boost'
    provider = db.Column(db.String(50), default="razorpay", nullable=False)  # 'razorpay', 'cod'

    razorpay_order_id = db.Column(db.String(100), unique=True, nullable=True, index=True)
    razorpay_payment_id = db.Column(db.String(100), unique=True, nullable=True, index=True)
    razorpay_signature = db.Column(db.String(255), nullable=True)

    # Manual UPI payment reference and admin review
    upi_reference = db.Column(db.String(100), nullable=True, unique=True, index=True)
    upi_submitted_at = db.Column(db.DateTime(timezone=True), nullable=True)
    reviewed_by = db.Column(db.String(36), db.ForeignKey("users.id"), nullable=True)
    reviewed_at = db.Column(db.DateTime(timezone=True), nullable=True)
    review_note = db.Column(db.String(500), nullable=True)

    amount_paise = db.Column(db.BigInteger, nullable=False)
    currency = db.Column(db.String(10), default="INR", nullable=False)
    status = db.Column(db.String(30), default="created", nullable=False, index=True)  # 'created', 'pending', 'authorized', 'paid', 'failed', 'refunded', 'partially_refunded'

    error_code = db.Column(db.String(100), nullable=True)
    error_description = db.Column(db.Text, nullable=True)

    refund_id = db.Column(db.String(100), nullable=True, index=True)
    refund_status = db.Column(db.String(50), nullable=True)
    refund_amount_paise = db.Column(db.BigInteger, default=0, nullable=False)

    created_at = db.Column(db.DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at = db.Column(db.DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)
    paid_at = db.Column(db.DateTime(timezone=True), nullable=True)

    order = db.relationship("Order", backref=db.backref("payments", lazy="dynamic", cascade="all, delete-orphan"))
    subscription = db.relationship("ListingSubscription", backref=db.backref("payments", lazy="dynamic", cascade="all, delete-orphan"))
    user = db.relationship("User", foreign_keys=[user_id])

    @property
    def amount_rupees(self):
        return self.amount_paise // 100

    @property
    def formatted_amount(self):
        return f"₹{self.amount_rupees:,.0f}"

    def to_dict(self):
        return {
            "id": self.id,
            "order_id": self.order_id,
            "subscription_id": self.subscription_id,
            "user_id": self.user_id,
            "purpose": self.purpose,
            "provider": self.provider,
            "razorpay_order_id": self.razorpay_order_id,
            "razorpay_payment_id": self.razorpay_payment_id,
            "amount_rupees": self.amount_rupees,
            "currency": self.currency,
            "status": self.status,
            "refund_id": self.refund_id,
            "refund_amount_paise": self.refund_amount_paise,
            "created_at": self.created_at.isoformat() if self.created_at else "",
            "paid_at": self.paid_at.isoformat() if self.paid_at else ""
        }


class WebhookEvent(db.Model):
    __tablename__ = "webhook_events"

    id = db.Column(db.String(36), primary_key=True, default=generate_uuid)
    event_id = db.Column(db.String(100), unique=True, nullable=False, index=True)
    event_type = db.Column(db.String(100), nullable=False, index=True)
    payload = db.Column(db.Text, nullable=True)
    status = db.Column(db.String(30), default="processed", nullable=False)  # 'processed', 'ignored', 'failed'
    created_at = db.Column(db.DateTime(timezone=True), default=utc_now, nullable=False)


class AuditEvent(db.Model):
    __tablename__ = "audit_events"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id = db.Column(db.String(36), nullable=True, index=True)
    action = db.Column(db.String(100), nullable=False, index=True)
    target_type = db.Column(db.String(50), nullable=True)
    target_id = db.Column(db.String(100), nullable=True)
    details = db.Column(db.Text, nullable=True)
    ip_address = db.Column(db.String(50), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), default=utc_now, nullable=False)


class PasswordResetToken(db.Model):
    __tablename__ = "password_reset_tokens"

    id = db.Column(db.String(36), primary_key=True, default=generate_uuid)
    user_id = db.Column(db.String(36), db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    token_hash = db.Column(db.String(64), unique=True, nullable=False, index=True)
    created_at = db.Column(db.DateTime(timezone=True), default=utc_now, nullable=False)
    expires_at = db.Column(db.DateTime(timezone=True), nullable=False, index=True)
    used_at = db.Column(db.DateTime(timezone=True), nullable=True)
    is_revoked = db.Column(db.Boolean, default=False, nullable=False)
    ip_address = db.Column(db.String(50), nullable=True)
    user_agent = db.Column(db.String(255), nullable=True)

    user = db.relationship("User", backref=db.backref("reset_tokens", lazy="dynamic", cascade="all, delete-orphan"))

    @property
    def is_expired(self):
        if not self.expires_at:
            return True
        now = utc_now()
        if self.expires_at.tzinfo is None:
            now = datetime.now(timezone.utc).replace(tzinfo=None)
        return now >= self.expires_at

    @property
    def is_valid(self):
        return (not self.is_revoked) and (self.used_at is None) and (not self.is_expired)

    def to_dict(self):
        return {
            "id": self.id,
            "user_id": self.user_id,
            "created_at": self.created_at.isoformat() if self.created_at else "",
            "expires_at": self.expires_at.isoformat() if self.expires_at else "",
            "used_at": self.used_at.isoformat() if self.used_at else None,
            "is_revoked": self.is_revoked,
            "is_valid": self.is_valid
        }


def get_legitimate_tracking_url(courier_name, tracking_number):
    """
    Returns an official courier tracking URL when the courier is recognized.
    Never exposes tracking numbers to unverified external search engines.
    """
    if not courier_name or not tracking_number:
        return None

    courier = re.sub(r"\s+", " ", str(courier_name).strip().lower())
    clean_tracking = re.sub(r"[\s-]+", "", str(tracking_number).strip())

    tracking_urls = {
        "dtdc": f"https://www.dtdc.in/tracking/tracking_results.asp?Ttype=awb_no&strCnno={clean_tracking}",
        "delhivery": f"https://www.delhivery.com/track/package/{clean_tracking}",
        "blue dart": "https://www.bluedart.com/tracking",
        "bluedart": "https://www.bluedart.com/tracking",
        "blue dart express": "https://www.bluedart.com/tracking",
        "india post": "https://www.indiapost.gov.in/_layouts/15/dop.portal.tracking/trackconsignment.aspx",
        "indiapost": "https://www.indiapost.gov.in/_layouts/15/dop.portal.tracking/trackconsignment.aspx",
        "ekart": f"https://ekartlogistics.com/track/{clean_tracking}",
        "ekart logistics": f"https://ekartlogistics.com/track/{clean_tracking}",
        "xpressbees": f"https://www.xpressbees.com/shipment/tracking?awb={clean_tracking}",
        "xpress bees": f"https://www.xpressbees.com/shipment/tracking?awb={clean_tracking}",
        "ecom express": f"https://www.ecomexpress.in/tracking/?awb_field={clean_tracking}",
        "ecomexpress": f"https://www.ecomexpress.in/tracking/?awb_field={clean_tracking}",
        "professional couriers": "https://www.tpcindia.com/track.aspx",
        "professional courier": "https://www.tpcindia.com/track.aspx"
    }

    return tracking_urls.get(courier)
