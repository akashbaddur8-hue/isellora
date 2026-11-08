"""
iSellora Marketplace Application
Full-featured, secure, and relational-backed marketplace for second-hand smartphones.
"""
import os
import sys
import uuid
import cloudinary
import cloudinary.uploader
from cloudinary.utils import cloudinary_url

# Ensure project root is in sys.path so 'backend' is recognized regardless of invocation method
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
INSTANCE_DIR = os.path.join(PROJECT_ROOT, "instance")
os.makedirs(INSTANCE_DIR, exist_ok=True)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from datetime import datetime, timezone
from urllib.parse import quote_plus
from flask import (
    Flask, render_template, request, redirect, Response,
    url_for, session, abort, flash, jsonify, current_app
)
from flask_wtf.csrf import CSRFProtect, CSRFError
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
import click

from backend.database import db
from backend.models import (
    User, Listing, ListingImage, Order, OrderStatusHistory,
    Notification, SupportTicket, ReportDispute, AuditEvent,
    Review, Wishlist, ListingSubscription, Payment, WebhookEvent,
    PasswordResetToken, utc_now, get_legitimate_tracking_url
)
from backend.config import config_by_name, ProductionConfig
from backend.services.auth_service import (
    is_rate_limited, record_failed_attempt, clear_failed_attempts,
    validate_password_strength, normalize_email, validate_phone,
    validate_username, is_safe_redirect_url,
    validate_email_format, is_reset_rate_limited, record_reset_request,
    is_submission_rate_limited, record_submission_attempt,
    create_password_reset_token, verify_password_reset_token,
    consume_password_reset_token
)
from backend.services.email_service import send_password_reset_email
from backend.services.listing_service import (
    query_catalog, validate_image_file, moderate_listing
)
from backend.services.order_service import (
    create_order, transition_order_status
)
from backend.services.inventory_service import (
    release_listing_reservation
)
from backend.services.payment_service import (
    create_order_razorpay_payment, create_boost_razorpay_payment,
    verify_checkout_signature, verify_webhook_signature,
    process_order_checkout_callback, process_boost_checkout_callback,
    record_payment_failure, process_webhook, initiate_payment_refund
)

csrf = CSRFProtect()


def upload_to_cloudinary(file_storage, folder):
    """Upload an image to persistent Cloudinary storage."""
    if not current_app.config.get("CLOUDINARY_CONFIGURED"):
        raise RuntimeError("Cloudinary is not configured. Image upload is unavailable.")

    result = cloudinary.uploader.upload(
        file_storage,
        folder=folder,
        resource_type="image"
    )

    public_id = result.get("public_id")
    if not public_id:
        raise RuntimeError("Cloudinary did not return an image ID.")

    image_url, _ = cloudinary_url(
        public_id,
        secure=True,
        quality="auto",
        fetch_format="auto"
    )

    if not image_url:
        raise RuntimeError("Cloudinary did not generate an image URL.")

    return image_url


def create_app(config_name=None):
    if config_name is None:
        config_name = os.environ.get("FLASK_ENV", "development")

    app = Flask(
        __name__,
        instance_path=INSTANCE_DIR,
        template_folder=os.path.join(os.path.dirname(__file__), "templates"),
        static_folder=os.path.join(os.path.dirname(__file__), "static")
    )

    if config_name == "production":
        ProductionConfig.validate_production()

    config_class = config_by_name.get(config_name, config_by_name["development"])
    app.config.from_object(config_class)

    # Cloudinary persistent image storage
    cloud_name = os.environ.get("CLOUDINARY_CLOUD_NAME", "").strip()
    cloud_api_key = os.environ.get("CLOUDINARY_API_KEY", "").strip()
    cloud_api_secret = os.environ.get("CLOUDINARY_API_SECRET", "").strip()

    app.config["CLOUDINARY_CONFIGURED"] = bool(
        cloud_name and cloud_api_key and cloud_api_secret
    )

    if app.config["CLOUDINARY_CONFIGURED"]:
        cloudinary.config(
            cloud_name=cloud_name,
            api_key=cloud_api_key,
            api_secret=cloud_api_secret,
            secure=True
        )
        app.logger.info("Cloudinary image storage configured.")
    else:
        app.logger.warning("Cloudinary credentials are missing.")

    # Razorpay Test Mode credential startup validation (never printing secret values)
    if not app.config.get("TESTING"):
        status = config_class.validate_razorpay()
        if not status["configured"]:
            app.logger.warning(
                "Razorpay Test Mode credentials incomplete in backend/.env (Missing/Placeholder: %s). "
                "Online card/UPI checkout will be paused until valid test credentials are provided. "
                "Cash on Delivery (COD) remains active.",
                ", ".join(status["missing"])
            )
        else:
            app.logger.info("Razorpay Test Mode active. Gateway Key ID: %s", status["masked_key_id"])
            if not status["has_webhook_secret"]:
                app.logger.warning(
                    "RAZORPAY_WEBHOOK_SECRET is not configured in backend/.env. "
                    "Inbound webhook signatures will be rejected until a webhook secret is set."
                )

        mail_status = config_class.validate_email_config()
        if not mail_status["configured"]:
            app.logger.info(
                "Mail server is not configured in backend/.env. "
                "Development mode active: Password reset emails will be captured in local outbox (instance/dev_email_outbox.log). "
                "To enable live SMTP delivery, configure MAIL_SERVER, MAIL_PORT, MAIL_USERNAME, MAIL_PASSWORD in backend/.env."
            )
        else:
            app.logger.info("Mail server active: %s:%s (Sender: %s)", mail_status["mail_server"], mail_status["mail_port"], mail_status["default_sender"])

    # Initialize extensions
    db.init_app(app)
    csrf.init_app(app)

    # Ensure uploads folder and avatars subfolder exist
    os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)
    os.makedirs(os.path.join(app.config["UPLOAD_FOLDER"], "avatars"), exist_ok=True)

    # Ensure schema updates and missing tables are present
    with app.app_context():
        try:
            from sqlalchemy import inspect, text
            inspector = inspect(db.engine)
            existing_tables = inspector.get_table_names()
            with db.engine.connect() as conn:
                if "listings" in existing_tables:
                    listing_cols = [c["name"] for c in inspector.get_columns("listings")]
                    if "is_featured" not in listing_cols:
                        conn.execute(text("ALTER TABLE listings ADD COLUMN is_featured BOOLEAN DEFAULT 0"))
                    if "featured_at" not in listing_cols:
                        conn.execute(text("ALTER TABLE listings ADD COLUMN featured_at DATETIME"))
                if "payments" in existing_tables:
                    payment_cols = [c["name"] for c in inspector.get_columns("payments")]
                    if "upi_reference" not in payment_cols:
                        conn.execute(text("ALTER TABLE payments ADD COLUMN upi_reference VARCHAR(100)"))
                    if "upi_submitted_at" not in payment_cols:
                        conn.execute(text("ALTER TABLE payments ADD COLUMN upi_submitted_at TIMESTAMP WITH TIME ZONE"))
                    if "reviewed_by" not in payment_cols:
                        conn.execute(text("ALTER TABLE payments ADD COLUMN reviewed_by VARCHAR(36)"))
                    if "reviewed_at" not in payment_cols:
                        conn.execute(text("ALTER TABLE payments ADD COLUMN reviewed_at TIMESTAMP WITH TIME ZONE"))
                    if "review_note" not in payment_cols:
                        conn.execute(text("ALTER TABLE payments ADD COLUMN review_note VARCHAR(500)"))

                    conn.execute(text(
                        "CREATE UNIQUE INDEX IF NOT EXISTS ix_payments_upi_reference "
                        "ON payments (upi_reference)"
                    ))

                if "users" in existing_tables:
                    user_cols = [c["name"] for c in inspector.get_columns("users")]
                    if "username" not in user_cols:
                        conn.execute(text("ALTER TABLE users ADD COLUMN username VARCHAR(50)"))
                    if "avatar_path" not in user_cols:
                        conn.execute(text("ALTER TABLE users ADD COLUMN avatar_path VARCHAR(255)"))
                    if "city" not in user_cols:
                        conn.execute(text("ALTER TABLE users ADD COLUMN city VARCHAR(100)"))
                    if "state" not in user_cols:
                        conn.execute(text("ALTER TABLE users ADD COLUMN state VARCHAR(100)"))
                    if "bio" not in user_cols:
                        conn.execute(text("ALTER TABLE users ADD COLUMN bio TEXT"))
                    if "is_verified" not in user_cols:
                        conn.execute(text("ALTER TABLE users ADD COLUMN is_verified BOOLEAN DEFAULT 0"))
                conn.commit()
            db.create_all()
        except Exception as e:
            app.logger.warning(f"Database schema auto-check: {e}")

    # =========================================================================
    # CONTEXT PROCESSORS & SECURITY HEADERS
    # =========================================================================
    @app.context_processor
    def inject_globals():
        user_id = session.get("user_id")
        notifications = []
        unread_count = 0
        current_user = None

        if user_id:
            current_user = db.session.get(User, user_id)
            if current_user and current_user.is_active:
                notices_query = Notification.query.filter_by(user_id=user_id).order_by(Notification.created_at.desc())
                notifications = notices_query.limit(10).all()
                unread_count = Notification.query.filter_by(user_id=user_id, is_read=False).count()
            elif current_user and not current_user.is_active:
                session.clear()
                current_user = None

        rzp_key_id = current_app.config.get("RAZORPAY_KEY_ID", "")
        rzp_configured = bool(
            rzp_key_id and
            rzp_key_id != "rzp_test_placeholder_key_id" and
            current_app.config.get("RAZORPAY_KEY_SECRET") and
            current_app.config.get("RAZORPAY_KEY_SECRET") != "placeholder_razorpay_secret_key"
        )

        return {
            "current_user": current_user,
            "notifications": notifications,
            "unread_notification_count": unread_count,
            "now": utc_now(),
            "razorpay_key_id": rzp_key_id,
            "razorpay_configured": rzp_configured
        }

    @app.after_request
    def set_security_headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        if not app.debug:
            response.headers["Content-Security-Policy"] = (
                "default-src 'self' https: data:; "
                "script-src 'self' 'unsafe-inline' https:; "
                "style-src 'self' 'unsafe-inline' https:; "
                "img-src 'self' data: https: /static/;"
            )
        return response

    # =========================================================================
    # ERROR HANDLERS
    # =========================================================================
    @app.errorhandler(CSRFError)
    def handle_csrf_error(e):
        app.logger.warning(f"CSRF failure: {e.description} from {request.remote_addr}")
        return render_template(
            "error.html",
            status_code=400,
            error_title="Security Token Expired",
            error_message="Security token expired or invalid. Please refresh the page and try again."
        ), 400

    @app.errorhandler(400)
    def bad_request(e):
        return render_template(
            "error.html",
            status_code=400,
            error_title="Bad Request",
            error_message=getattr(e, "description", "The request could not be processed due to invalid parameters.")
        ), 400

    @app.errorhandler(403)
    def forbidden(e):
        return render_template(
            "error.html",
            status_code=403,
            error_title="Access Forbidden",
            error_message="You do not have permission to view or perform actions on this resource."
        ), 403

    @app.errorhandler(404)
    def not_found(e):
        return render_template(
            "error.html",
            status_code=404,
            error_title="Page or Phone Not Found",
            error_message="The smartphone listing or page you are looking for has been removed, sold, or does not exist."
        ), 404

    @app.errorhandler(409)
    def conflict(e):
        return render_template(
            "error.html",
            status_code=409,
            error_title="Inventory Conflict",
            error_message=getattr(e, "description", "This item was modified or reserved by another buyer during checkout.")
        ), 409

    @app.errorhandler(500)
    def internal_error(e):
        db.session.rollback()
        app.logger.exception("Internal server error")
        return render_template(
            "error.html",
            status_code=500,
            error_title="Internal System Error",
            error_message="An unexpected system error occurred. Our engineering team has been alerted."
        ), 500

    # =========================================================================
    # AUTHENTICATION HELPERS
    # =========================================================================
    def get_current_user():
        user_id = session.get("user_id")
        if not user_id:
            return None
        user = db.session.get(User, user_id)
        if not user or not user.is_active:
            return None
        return user

    def is_admin():
        user = get_current_user()
        if not user:
            return False
        return user.role == "admin" and session.get("admin_authenticated", False)

    # =========================================================================
    # PUBLIC DISCOVERY ROUTES
    # =========================================================================
    @app.route("/")
    def home():
        # Fetch featured and newest approved listings (priority boosted first)
        featured_listings = Listing.query.filter_by(
            approval_status="approved", inventory_status="available"
        ).order_by(Listing.is_featured.desc(), Listing.created_at.desc()).limit(8).all()
        total_available = Listing.query.filter_by(
            approval_status="approved", inventory_status="available"
        ).count()

        # Count available approved phones per brand
        from sqlalchemy import func
        brand_rows = db.session.query(
            func.lower(Listing.brand), func.count(Listing.id)
        ).filter(
            Listing.approval_status == "approved",
            Listing.inventory_status == "available"
        ).group_by(func.lower(Listing.brand)).all()
        brand_counts = {row[0]: row[1] for row in brand_rows}

        return render_template(
            "index.html",
            phones=featured_listings,
            total_catalog_count=total_available,
            brand_counts=brand_counts
        )

    @app.route("/brands")
    @app.route("/shop-by-brand")
    def shop_by_brand():
        brand = request.args.get("brand", "").strip()
        if brand:
            return redirect(url_for("products", brand=brand))
        return redirect(url_for("products"))

    @app.route("/brand/<brand_name>")
    def brand_catalog(brand_name):
        return redirect(url_for("products", brand=brand_name.strip()))

    @app.route("/product")
    def product():
        # Legacy route: redirect to catalog
        return redirect(url_for("products"))

    @app.route("/products")
    def products():
        search = request.args.get("search", "").strip()
        brand = request.args.get("brand", "").strip()
        condition = request.args.get("condition", "").strip()
        storage = request.args.get("storage", "").strip()
        ram = request.args.get("ram", "").strip()
        sort_by = request.args.get("sort", "latest").strip()
        
        import math

        try:
            page_val = int(request.args.get("page", 1))
            page = max(1, min(page_val, 10000))
        except (ValueError, TypeError, OverflowError):
            page = 1

        def _safe_price(val):
            if not val:
                return None
            try:
                p = float(str(val).replace(",", "").replace("â‚¹", "").strip())
                if not math.isfinite(p) or math.isnan(p) or p < 0 or p > 100000000:
                    return None
                return p
            except (ValueError, TypeError, OverflowError):
                return None

        min_price = _safe_price(request.args.get("min_price"))
        max_price = _safe_price(request.args.get("max_price"))

        # If min_price > max_price, swap them safely so query remains valid
        if min_price is not None and max_price is not None and min_price > max_price:
            min_price, max_price = max_price, min_price

        pagination = query_catalog(
            search=search,
            brand=brand,
            condition=condition,
            storage=storage,
            ram=ram,
            min_price=min_price,
            max_price=max_price,
            sort_by=sort_by,
            page=page,
            per_page=12
        )

        return render_template(
            "products.html",
            pagination=pagination,
            phones=pagination.items,
            search=search,
            brand=brand,
            condition=condition,
            storage=storage,
            ram=ram,
            min_price=min_price,
            max_price=max_price,
            sort=sort_by
        )

    @app.route("/products/<listing_id>")
    def listing_detail(listing_id):
        listing = db.session.get(Listing, listing_id)
        if not listing:
            abort(404)

        user = get_current_user()
        # Allow viewing if approved, or if viewer is the seller owner, or if viewer is admin
        is_owner = bool(user and listing.seller_id == user.id)
        admin_viewer = is_admin()

        if listing.approval_status != "approved" and not (is_owner or admin_viewer):
            abort(404)

        return render_template(
            "product_detail.html",
            phone=listing,
            is_owner=is_owner
        )

    # =========================================================================
    # GOOGLE SEARCH CONSOLE - SITEMAP & ROBOTS
    # =========================================================================

    @app.route("/sitemap.xml")
    def sitemap():
        base_url = "https://isellora.in"

        xml_parts = [
            '<?xml version="1.0" encoding="UTF-8"?>',
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        ]

        for path in ["/", "/products"]:
            xml_parts.append(
                f"<url><loc>{base_url}{path}</loc></url>"
            )

        listings = Listing.query.filter_by(
            approval_status="approved",
            inventory_status="available"
        ).all()

        for listing in listings:
            product_url = base_url + url_for(
                "listing_detail", listing_id=listing.id
            )
            xml_parts.append(
                f"<url><loc>{product_url}</loc></url>"
            )

        xml_parts.append("</urlset>")

        return Response("\n".join(xml_parts), mimetype="application/xml")

    @app.route("/robots.txt")
    def robots_txt():
        content = (
            "User-agent: *\n"
            "Allow: /\n"
            "Disallow: /admin\n"
            "Disallow: /account\n"
            "Disallow: /orders\n"
            "Disallow: /login\n"
            "Disallow: /signup\n"
            "Sitemap: https://isellora.in/sitemap.xml\n"
        )
        return Response(content, mimetype="text/plain")

    # =========================================================================
    # CHECKOUT & PAYMENT ROUTES (Razorpay Online Checkout & COD)
    # =========================================================================
    @app.route("/buy/<listing_id>", methods=["GET", "POST"])
    def buy_now(listing_id):
        user = get_current_user()
        if not user:
            flash("Please log in to purchase this smartphone.", "info")
            return redirect(url_for("login", next=request.path))

        listing = db.session.get(Listing, listing_id)
        if not listing or listing.approval_status != "approved" or listing.inventory_status != "available":
            return render_template("base.html", error_message="This phone is no longer available for purchase."), 404

        if listing.seller_id == user.id:
            return render_template("base.html", error_message="You cannot purchase your own listing."), 403

        razorpay_key_id = current_app.config.get("RAZORPAY_KEY_ID", "")

        if request.method == "GET":
            token = str(uuid.uuid4())
            session["checkout_token"] = token
            session["checkout_listing_id"] = str(listing_id)
            return render_template(
                "checkout.html",
                phone=listing,
                checkout_token=token,
                razorpay_key_id=razorpay_key_id
            )

        # POST submission
        checkout_token = request.form.get("checkout_token") or session.get("checkout_token")
        if not checkout_token or session.get("checkout_listing_id") != str(listing_id):
            return render_template("base.html", error_message="Checkout session expired. Please start checkout again."), 409

        buyer_name = request.form.get("buyer_name", "").strip()
        buyer_phone = request.form.get("buyer_phone", "").strip()
        delivery_method = request.form.get("delivery_method", "pickup").strip().lower()
        shipping_address = request.form.get("address", "").strip()
        pincode = request.form.get("pincode", "").strip()
        payment_method = request.form.get("payment_method", "cod").strip().lower()

        if not (2 <= len(buyer_name) <= 100):
            flash("Please enter a valid full name (2-100 characters).", "error")
            return render_template("checkout.html", phone=listing, checkout_token=checkout_token, razorpay_key_id=razorpay_key_id), 400

        if not validate_phone(buyer_phone):
            flash("Please enter a valid 10-digit phone number.", "error")
            return render_template("checkout.html", phone=listing, checkout_token=checkout_token, razorpay_key_id=razorpay_key_id), 400

        if delivery_method not in ("pickup", "shipping"):
            flash("Invalid delivery method selected.", "error")
            return render_template("checkout.html", phone=listing, checkout_token=checkout_token, razorpay_key_id=razorpay_key_id), 400

        if payment_method not in ("cod", "razorpay", "upi"):
            flash("Invalid payment method selected.", "error")
            return render_template("checkout.html", phone=listing, checkout_token=checkout_token, razorpay_key_id=razorpay_key_id), 400

        if delivery_method == "shipping":
            if len(shipping_address) < 10:
                flash("Please enter a complete delivery address.", "error")
                return render_template("checkout.html", phone=listing, checkout_token=checkout_token, razorpay_key_id=razorpay_key_id), 400
            if not pincode.isdigit() or len(pincode) != 6:
                flash("Please enter a valid 6-digit PIN code.", "error")
                return render_template("checkout.html", phone=listing, checkout_token=checkout_token, razorpay_key_id=razorpay_key_id), 400
        else:
            shipping_address = "Local Pickup arranged directly with seller"
            pincode = None

        # 1. Create internal order and reserve inventory atomically
        order, error = create_order(
            buyer_id=user.id,
            listing_id=listing_id,
            buyer_name=buyer_name,
            buyer_phone=buyer_phone,
            delivery_method=delivery_method,
            shipping_address=shipping_address,
            pincode=pincode,
            checkout_token=checkout_token,
            payment_method=payment_method
        )

        if error:
            if request.is_json:
                return jsonify({"success": False, "error": error}), 409
            flash(error, "error")
            return render_template("checkout.html", phone=listing, checkout_token=checkout_token, razorpay_key_id=razorpay_key_id), 409

        # Handle Cash on Delivery (COD)
        if payment_method == "cod":
            session.pop("checkout_token", None)
            session.pop("checkout_listing_id", None)
            flash("Your smartphone order has been reserved! Payment will be collected upon delivery.", "success")
            if request.is_json:
                return jsonify({
                    "success": True,
                    "payment_method": "cod",
                    "redirect_url": url_for("order_confirmation", order_id=order.id)
                })
            return redirect(url_for("order_confirmation", order_id=order.id))

        # Handle Manual UPI (payment remains pending until admin verifies UTR)
        if payment_method == "upi":
            existing_upi_payment = Payment.query.filter_by(
                order_id=order.id,
                provider="upi_manual",
                purpose="order"
            ).first()

            if not existing_upi_payment:
                upi_payment = Payment(
                    order_id=order.id,
                    user_id=user.id,
                    purpose="order",
                    provider="upi_manual",
                    amount_paise=order.total_paise,
                    currency="INR",
                    status="pending"
                )
                db.session.add(upi_payment)
                db.session.commit()

            session.pop("checkout_token", None)
            session.pop("checkout_listing_id", None)
            flash("Your order is reserved. Complete the UPI transfer and submit the UTR for admin verification.", "success")
            if request.is_json or request.headers.get("X-Requested-With") == "XMLHttpRequest":
                return jsonify({
                    "success": True,
                    "payment_method": "upi",
                    "order_id": order.id,
                    "redirect_url": url_for("order_confirmation", order_id=order.id)
                })
            return redirect(url_for("order_confirmation", order_id=order.id))

        # Handle Online Payment (Razorpay Checkout)
        rzp_data, rzp_err = create_order_razorpay_payment(order, user)
        if rzp_err:
            if request.is_json:
                return jsonify({"success": False, "error": rzp_err}), 400
            flash(rzp_err, "error")
            return render_template("checkout.html", phone=listing, checkout_token=checkout_token, razorpay_key_id=razorpay_key_id), 400

        session.pop("checkout_token", None)
        session.pop("checkout_listing_id", None)

        if request.is_json or request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify({
                "success": True,
                "payment_method": "razorpay",
                "order_id": order.id,
                "razorpay": rzp_data
            })

        return render_template(
            "checkout_razorpay.html",
            order=order,
            phone=listing,
            razorpay=rzp_data
        )

    @app.route("/payment/verify", methods=["POST"])
    def payment_verify():
        user = get_current_user()
        if not user:
            if request.is_json:
                return jsonify({"success": False, "error": "Authentication required."}), 401
            return redirect(url_for("login"))

        data = request.get_json(silent=True) or request.form
        razorpay_order_id = (data.get("razorpay_order_id") or "").strip()
        razorpay_payment_id = (data.get("razorpay_payment_id") or "").strip()
        razorpay_signature = (data.get("razorpay_signature") or "").strip()

        if not razorpay_order_id or not razorpay_payment_id or not razorpay_signature:
            if request.is_json:
                return jsonify({"success": False, "error": "Missing payment signature verification parameters."}), 400
            flash("Payment verification failed: missing verification tokens.", "error")
            return redirect(url_for("orders"))

        success, msg, order = process_order_checkout_callback(
            razorpay_order_id=razorpay_order_id,
            razorpay_payment_id=razorpay_payment_id,
            razorpay_signature=razorpay_signature,
            current_user_id=user.id
        )

        if not success or not order:
            if request.is_json:
                return jsonify({"success": False, "error": msg}), 400
            flash(f"Payment verification failed: {msg}", "error")
            return redirect(url_for("orders"))

        flash(f"Payment of {order.formatted_total} verified successfully! Your smartphone order is confirmed.", "success")
        if request.is_json:
            return jsonify({
                "success": True,
                "message": msg,
                "redirect_url": url_for("order_confirmation", order_id=order.id)
            })
        return redirect(url_for("order_confirmation", order_id=order.id))

    @app.route("/payment/failed", methods=["POST"])
    def payment_failed():
        user = get_current_user()
        if not user:
            return jsonify({"success": False, "error": "Authentication required."}), 401

        data = request.get_json(silent=True) or request.form
        razorpay_order_id = (data.get("razorpay_order_id") or "").strip()
        error_code = (data.get("error_code") or "PAYMENT_CANCELLED").strip()
        error_description = (data.get("error_description") or "Payment checkout was cancelled or dismissed.").strip()

        record_payment_failure(
            razorpay_order_id=razorpay_order_id,
            error_code=error_code,
            error_description=error_description,
            user_id=user.id
        )

        return jsonify({"success": True, "message": "Failure state recorded."})

    @app.route("/payment/webhook", methods=["POST"])
    @csrf.exempt
    def payment_webhook():
        raw_body = request.get_data()
        signature = request.headers.get("X-Razorpay-Signature", "").strip()

        if not signature:
            return jsonify({"error": "Missing X-Razorpay-Signature header."}), 400

        success, msg, status_code = process_webhook(raw_body, signature)
        return jsonify({
            "success": success,
            "status": "success" if success else "failed",
            "message": msg
        }), status_code

    @app.route("/order-success/<order_id>")
    def order_confirmation(order_id):
        user = get_current_user()
        if not user:
            return redirect(url_for("login"))

        order = db.session.get(Order, order_id)
        if not order or (order.buyer_id != user.id and not is_admin()):
            abort(404)

        return render_template("order_success.html", order=order)

    @app.route("/orders")
    def orders():
        user = get_current_user()
        if not user:
            return redirect(url_for("login", next=request.path))

        my_orders = Order.query.filter_by(buyer_id=user.id).order_by(Order.created_at.desc()).all()

        # Latest manual UPI payment attempt for each buyer order
        upi_records = Payment.query.filter_by(
            user_id=user.id,
            provider="upi_manual",
            purpose="order"
        ).order_by(Payment.created_at.desc()).all()

        upi_payments_by_order = {}
        for payment in upi_records:
            upi_payments_by_order.setdefault(payment.order_id, payment)

        return render_template(
            "orders.html",
            orders=my_orders,
            upi_payments_by_order=upi_payments_by_order
        )

    @app.route("/orders/<order_id>/upi-reference", methods=["POST"])
    def submit_upi_reference(order_id):
        user = get_current_user()
        if not user:
            return redirect(url_for("login", next=request.path))

        order = db.session.get(Order, order_id)
        if not order or order.buyer_id != user.id:
            abort(404)

        if order.payment_method != "upi":
            abort(400)

        if order.payment_status == "paid" or order.status == "Cancelled":
            flash("This order cannot accept another UPI reference.", "error")
            return redirect(url_for("orders"))

        reference = (request.form.get("upi_reference") or "").strip().replace(" ", "")
        if not (8 <= len(reference) <= 100) or not reference.isalnum():
            flash("Enter a valid UPI transaction reference (8-100 letters or digits).", "error")
            return redirect(url_for("orders"))

        duplicate = Payment.query.filter(
            Payment.upi_reference == reference
        ).first()
        if duplicate:
            flash("This UPI reference has already been submitted.", "error")
            return redirect(url_for("orders"))

        payment = Payment.query.filter_by(
            order_id=order.id,
            user_id=user.id,
            provider="upi_manual",
            purpose="order"
        ).order_by(Payment.created_at.desc()).first()

        if not payment:
            flash("UPI payment record was not found. Please contact support.", "error")
            return redirect(url_for("orders"))

        if payment.status == "paid":
            flash("This payment has already been verified.", "info")
            return redirect(url_for("orders"))

        if payment.upi_reference:
            flash("A UPI reference has already been submitted for this payment.", "error")
            return redirect(url_for("orders"))

        payment.upi_reference = reference
        payment.upi_submitted_at = utc_now()
        payment.status = "pending"
        order.payment_status = "pending"

        db.session.add(AuditEvent(
            user_id=user.id,
            action="UPI_REFERENCE_SUBMITTED",
            target_type="Payment",
            target_id=payment.id,
            details=f"UPI reference submitted for order {order.id}",
            ip_address=request.remote_addr
        ))
        db.session.commit()

        flash("UPI reference submitted. Payment remains pending until admin verification.", "success")
        return redirect(url_for("orders"))

    @app.route("/orders/<order_id>/cancel", methods=["POST"])
    def cancel_order(order_id):
        user = get_current_user()
        if not user:
            abort(403)

        order = db.session.get(Order, order_id)
        if not order or (order.buyer_id != user.id and not is_admin()):
            abort(404)

        if order.status != "Pending":
            flash("Orders that have already been confirmed or shipped cannot be cancelled directly. Please contact support.", "error")
            return redirect(url_for("orders"))

        reason = request.form.get("reason", "Cancelled by buyer").strip()
        success, msg = transition_order_status(
            order=order,
            new_status="Cancelled",
            actor_id=user.id,
            actor_role="buyer",
            notes=reason
        )

        if success:
            flash("Your order has been cancelled and the listing was released.", "success")
        else:
            flash(msg, "error")

        return redirect(url_for("orders"))

    @app.route("/orders/<order_id>/invoice")
    def order_invoice(order_id):
        user = get_current_user()
        if not user:
            return redirect(url_for("login", next=request.path))

        order = db.session.get(Order, order_id)
        if not order:
            abort(404)

        is_buyer = order.buyer_id == user.id
        is_seller = order.seller_id == user.id
        admin_auth = is_admin()

        if not (is_buyer or is_seller or admin_auth):
            abort(403)

        phone = db.session.get(Listing, order.listing_id) if order.listing_id else None
        return render_template("invoice.html", order=order, phone=phone)

    @app.route("/orders/<order_id>/review", methods=["POST"])
    def submit_review(order_id):
        user = get_current_user()
        if not user:
            abort(403)

        order = db.session.get(Order, order_id)
        if not order or order.buyer_id != user.id:
            abort(404)

        if order.status != "Delivered":
            flash("Reviews can only be submitted for successfully delivered orders.", "error")
            return redirect(url_for("orders"))

        existing_review = Review.query.filter_by(order_id=order.id).first()
        if existing_review:
            flash("You have already submitted a review for this order.", "info")
            return redirect(url_for("orders"))

        try:
            rating = int(request.form.get("rating", 5))
            if not 1 <= rating <= 5:
                rating = 5
        except (ValueError, TypeError):
            rating = 5

        comment = request.form.get("comment", "").strip()

        review = Review(
            order_id=order.id,
            buyer_id=user.id,
            seller_id=order.seller_id,
            listing_id=order.listing_id,
            rating=rating,
            comment=comment
        )
        db.session.add(review)

        if order.seller_id:
            notice = Notification(
                user_id=order.seller_id,
                title="New Buyer Rating",
                message=f"Buyer {user.name} rated your handover {rating}/5 stars.",
                order_id=order.id
            )
            db.session.add(notice)

        db.session.commit()
        flash("Thank you! Your seller rating and review has been recorded.", "success")
        return redirect(url_for("orders"))

    @app.route("/wishlist/toggle/<listing_id>", methods=["POST"])
    def toggle_wishlist(listing_id):
        user = get_current_user()
        if not user:
            if request.headers.get("X-Requested-With") == "XMLHttpRequest" or request.is_json:
                return jsonify({"error": "Login required"}), 401
            return redirect(url_for("login", next=request.referrer or url_for("products")))

        phone = db.session.get(Listing, listing_id)
        if not phone:
            abort(404)

        item = Wishlist.query.filter_by(user_id=user.id, listing_id=listing_id).first()
        if item:
            db.session.delete(item)
            db.session.commit()
            saved = False
            msg = "Removed from your wishlist."
        else:
            item = Wishlist(user_id=user.id, listing_id=listing_id)
            db.session.add(item)
            db.session.commit()
            saved = True
            msg = "Added to your wishlist!"

        if request.headers.get("X-Requested-With") == "XMLHttpRequest" or request.is_json:
            return jsonify({"saved": saved, "message": msg})

        flash(msg, "success" if saved else "info")
        return redirect(request.referrer or url_for("listing_detail", listing_id=listing_id))

    @app.route("/wishlist")
    def wishlist():
        user = get_current_user()
        if not user:
            return redirect(url_for("login", next=request.path))

        saved_items = Wishlist.query.filter_by(user_id=user.id).order_by(Wishlist.created_at.desc()).all()
        phones = [item.listing for item in saved_items if item.listing and item.listing.approval_status == "approved"]
        return render_template("wishlist.html", phones=phones)

    @app.route("/sales")
    def sales():
        user = get_current_user()
        if not user:
            return redirect(url_for("login", next=request.path))

        my_sales = Order.query.filter_by(seller_id=user.id).order_by(Order.created_at.desc()).all()
        return render_template("sales.html", sales=my_sales)

    @app.route("/seller-dashboard")
    @app.route("/listings")
    @app.route("/my-listings")
    @app.route("/seller/listings")
    def seller_dashboard():
        user = get_current_user()
        if not user:
            return redirect(url_for("login", next=request.path))

        my_listings = Listing.query.filter_by(seller_id=user.id).order_by(Listing.created_at.desc()).all()
        my_sales = Order.query.filter_by(seller_id=user.id).order_by(Order.created_at.desc()).all()

        total_earnings = sum(order.price_rupees for order in my_sales if order.status == "Delivered")
        pending_orders = sum(1 for order in my_sales if order.status == "Pending")
        total_listed_value = sum(
            l.price_rupees for l in my_listings 
            if l.inventory_status == "available"
        )

        return render_template(
            "seller_dashboard.html",
            listings=my_listings,
            sales=my_sales,
            total_earnings=total_earnings,
            pending_orders_count=pending_orders,
            total_listed_value=total_listed_value
        )

    @app.route("/update-order-status/<order_id>", methods=["POST"])
    def update_order_status(order_id):
        user = get_current_user()
        if not user:
            abort(403)

        order = db.session.get(Order, order_id)
        if not order:
            abort(404)

        # Seller ownership or admin check
        if order.seller_id != user.id and not is_admin():
            abort(403)

        new_status = request.form.get("status", "").strip()
        notes = request.form.get("notes", "").strip()

        actor_role = "admin" if is_admin() else "seller"
        success, msg = transition_order_status(
            order=order,
            new_status=new_status,
            actor_id=user.id,
            actor_role=actor_role,
            notes=notes
        )

        if success:
            flash(f"Order status updated to {new_status}.", "success")
        else:
            flash(msg, "error")

        return redirect(url_for("sales"))

    @app.route("/update-delivery/<order_id>", methods=["POST"])
    def update_delivery(order_id):
        user = get_current_user()
        if not user:
            abort(403)

        order = db.session.get(Order, order_id)
        if not order:
            abort(404)

        if order.seller_id != user.id and not is_admin():
            abort(403)

        if order.delivery_method != "shipping":
            flash("Delivery tracking can only be added for home delivery orders.", "error")
            return redirect(url_for("sales"))

        courier_name = request.form.get("courier_name", "").strip()
        tracking_number = request.form.get("tracking_number", "").strip()

        if not courier_name or not tracking_number:
            flash("Please enter both courier name and tracking number.", "error")
            return redirect(url_for("sales"))

        order.courier_name = courier_name
        order.tracking_number = tracking_number
        order.updated_at = utc_now()
        db.session.commit()

        flash("Delivery details updated successfully.", "success")
        return redirect(url_for("sales"))

    # =========================================================================
    # SELLER LISTING MANAGEMENT
    # =========================================================================
    @app.route("/sell", methods=["GET", "POST"])
    def sell():
        user = get_current_user()
        if not user:
            flash("Please log in to list a smartphone for sale.", "info")
            return redirect(url_for("login", next=request.path))

        if request.method == "GET":
            return render_template("sell.html", user=user)

        brand = request.form.get("brand", "").strip()
        model = request.form.get("model", "").strip()
        price_text = request.form.get("price", "").strip()
        storage = request.form.get("storage", "").strip()
        ram = request.form.get("ram", "").strip()
        color = request.form.get("color", "").strip()
        condition = request.form.get("condition", "Good").strip()
        battery_text = request.form.get("battery", "").strip()
        description = request.form.get("description", "").strip()

        # Disclosure fields
        screen_damage = request.form.get("screen_damage", "No Damage").strip()
        body_damage = request.form.get("body_damage", "No Damage").strip()
        repair_history = request.form.get("repair_history", "Never Repaired").strip()
        original_display = request.form.get("original_display", "Yes").strip()
        accessories = request.form.get("accessories", "Phone Only").strip()
        seller_phone = request.form.get("seller_phone", user.phone).strip()

        if not (brand and model and price_text and storage and description):
            flash("Please fill in all required fields (brand, model, storage, price, description).", "error")
            return render_template("sell.html", user=user), 400

        try:
            price_float = float(price_text.replace(",", "").replace("â‚¹", ""))
            if price_float < 500 or price_float > 500000:
                raise ValueError
            price_paise = int(round(price_float * 100))
        except ValueError:
            flash("Please enter a valid price between â‚¹500 and â‚¹5,00,000.", "error")
            return render_template("sell.html", user=user), 400

        battery_val = None
        if battery_text:
            try:
                b_int = int(battery_text)
                if 1 <= b_int <= 100:
                    battery_val = b_int
                else:
                    raise ValueError
            except ValueError:
                flash("Battery health must be an integer percentage between 1 and 100.", "error")
                return render_template("sell.html", user=user), 400

        # Handle file uploads
        uploaded_files = request.files.getlist("photos")
        valid_files = [f for f in uploaded_files if f and f.filename]

        if not (1 <= len(valid_files) <= 10):
            flash("Please upload between 1 and 10 product photos.", "error")
            return render_template("sell.html", user=user), 400

        for f in valid_files:
            if not validate_image_file(f):
                flash("Only JPG, JPEG, PNG, and WEBP image files are supported.", "error")
                return render_template("sell.html", user=user), 400

        # Auto-align brand if user entered a known brand in model name
        model_low = model.lower()
        if "samsung" in model_low or "galaxy" in model_low:
            brand = "Samsung"
        elif "iphone" in model_low or "apple" in model_low:
            brand = "Apple"
        elif "oneplus" in model_low or "nord" in model_low:
            brand = "OnePlus"
        elif "pixel" in model_low:
            brand = "Google"
        elif "xiaomi" in model_low or "redmi" in model_low or "poco" in model_low:
            brand = "Xiaomi"
        elif "motorola" in model_low or "moto" in model_low:
            brand = "Motorola"
        elif "realme" in model_low:
            brand = "Realme"
        elif "vivo" in model_low:
            brand = "Vivo"
        elif "oppo" in model_low:
            brand = "Oppo"

        listing_id = str(uuid.uuid4())
        new_listing = Listing(
            id=listing_id,
            seller_id=user.id,
            brand=brand,
            model=model,
            price_paise=price_paise,
            storage=storage,
            ram=ram or None,
            color=color or None,
            battery_health=battery_val,
            condition=condition,
            description=description,
            screen_damage=screen_damage,
            body_damage=body_damage,
            repair_history=repair_history,
            original_display=original_display,
            accessories=accessories,
            seller_name=user.name,
            seller_phone=seller_phone,
            approval_status="pending",
            inventory_status="available",
            created_at=utc_now(),
            updated_at=utc_now()
        )
        db.session.add(new_listing)

        # Upload photos to persistent Cloudinary storage
        try:
            for idx, f in enumerate(valid_files):
                image_url = upload_to_cloudinary(f, "isellora/products")
                img_rec = ListingImage(
                    listing_id=listing_id,
                    image_path=image_url,
                    display_order=idx,
                    created_at=utc_now()
                )
                db.session.add(img_rec)
        except Exception:
            db.session.rollback()
            app.logger.exception("Cloudinary product image upload failed.")
            flash("Image upload failed. Please try again.", "error")
            return render_template("sell.html", user=user), 503

        # Determine chosen listing type (Free vs Priority Boost)
        listing_type = request.form.get("listing_type", "free").strip().lower()
        if request.form.get("priority_boost"):
            listing_type = "priority"
        is_priority_chosen = (listing_type == "priority")

        try:
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            app.logger.error(f"Failed to save listing: {e}")
            flash("An unexpected error occurred while saving your listing. Please try again.", "error")
            return render_template("sell.html", user=user), 500

        rzp_configured = bool(app.config.get("RAZORPAY_KEY_ID") and app.config.get("RAZORPAY_KEY_SECRET"))
        if is_priority_chosen and rzp_configured:
            flash("Listing submitted for review! Complete your â‚¹49 priority payment now to activate #1 top placement once approved.", "info")
            return redirect(url_for("seller_boost_listing", listing_id=new_listing.id))
        elif is_priority_chosen and not rzp_configured:
            flash("Your listing has been submitted for review! (Note: Paid priority boosts are temporarily unavailable pending gateway setup).", "info")
            return redirect(url_for("seller_dashboard"))
        else:
            flash("Your listing has been submitted for review! It will be visible once approved by our moderation team.", "success")
            return redirect(url_for("seller_dashboard"))

    @app.route("/seller/boost/<listing_id>", methods=["GET", "POST"])
    def seller_boost_listing(listing_id):
        user = get_current_user()
        if not user:
            return redirect(url_for("login", next=request.path))

        listing = db.session.get(Listing, listing_id)
        if not listing:
            abort(404)

        if listing.seller_id != user.id and not is_admin():
            abort(403)

        razorpay_key_id = current_app.config.get("RAZORPAY_KEY_ID", "")

        if request.method == "POST":
            # If standard form POST, initialize Razorpay payment
            rzp_data, rzp_err = create_boost_razorpay_payment(listing, user)
            if rzp_err:
                flash(rzp_err, "error")
                return redirect(url_for("seller_boost_listing", listing_id=listing.id))
            return render_template(
                "boost_subscription.html",
                listing=listing,
                subscription=None,
                paid_boost_enabled=True,
                razorpay=rzp_data,
                razorpay_key_id=razorpay_key_id
            )

        # GET request: render priority boost page with active Razorpay checkout
        subscription = ListingSubscription.query.filter_by(
            listing_id=listing.id, status="active", payment_status="paid"
        ).order_by(ListingSubscription.created_at.desc()).first()
        return render_template(
            "boost_subscription.html",
            listing=listing,
            subscription=subscription,
            paid_boost_enabled=True,
            razorpay_key_id=razorpay_key_id
        )

    @app.route("/seller/boost/<listing_id>/create-order", methods=["POST"])
    def seller_boost_create_order(listing_id):
        user = get_current_user()
        if not user:
            return jsonify({"success": False, "error": "Authentication required."}), 401

        listing = db.session.get(Listing, listing_id)
        if not listing:
            return jsonify({"success": False, "error": "Listing not found."}), 404

        if listing.seller_id != user.id and not is_admin():
            return jsonify({"success": False, "error": "Unauthorized."}), 403

        rzp_data, err = create_boost_razorpay_payment(listing, user)
        if err:
            return jsonify({"success": False, "error": err}), 400

        return jsonify({"success": True, "razorpay": rzp_data})

    @app.route("/seller/boost/<listing_id>/verify", methods=["POST"])
    def seller_boost_verify(listing_id):
        user = get_current_user()
        if not user:
            return jsonify({"success": False, "error": "Authentication required."}), 401

        listing = db.session.get(Listing, listing_id)
        if not listing:
            return jsonify({"success": False, "error": "Listing not found."}), 404

        data = request.get_json(silent=True) or request.form
        razorpay_order_id = (data.get("razorpay_order_id") or "").strip()
        razorpay_payment_id = (data.get("razorpay_payment_id") or "").strip()
        razorpay_signature = (data.get("razorpay_signature") or "").strip()

        if not razorpay_order_id or not razorpay_payment_id or not razorpay_signature:
            return jsonify({"success": False, "error": "Missing signature verification tokens."}), 400

        success, msg, subscription = process_boost_checkout_callback(
            razorpay_order_id=razorpay_order_id,
            razorpay_payment_id=razorpay_payment_id,
            razorpay_signature=razorpay_signature,
            current_user_id=user.id
        )

        if not success:
            return jsonify({"success": False, "error": msg}), 400

        flash("Priority Placement (â‚¹49) activated! Your smartphone is now pinned at #1 in search results.", "success")
        return jsonify({
            "success": True,
            "message": msg,
            "redirect_url": url_for("seller_dashboard")
        })

    @app.route("/edit/<listing_id>", methods=["GET", "POST"])
    def edit_listing(listing_id):
        user = get_current_user()
        if not user:
            return redirect(url_for("login"))

        listing = db.session.get(Listing, listing_id)
        if not listing:
            abort(404)

        if listing.seller_id != user.id and not is_admin():
            abort(403)

        if request.method == "GET":
            return render_template("edit_listing.html", phone=listing)

        # Process edits
        price_text = request.form.get("price", "").strip()
        description = request.form.get("description", "").strip()
        condition = request.form.get("condition", listing.condition).strip()
        battery_text = request.form.get("battery", "").strip()

        if price_text:
            try:
                p_float = float(price_text.replace(",", "").replace("â‚¹", ""))
                if p_float < 500 or p_float > 500000:
                    raise ValueError
                listing.price_paise = int(round(p_float * 100))
            except ValueError:
                flash("Invalid price value.", "error")
                return render_template("edit_listing.html", phone=listing), 400

        if battery_text:
            try:
                b_int = int(battery_text)
                if 1 <= b_int <= 100:
                    listing.battery_health = b_int
            except ValueError:
                pass

        if description:
            listing.description = description

        listing.condition = condition
        listing.screen_damage = request.form.get("screen_damage", listing.screen_damage).strip()
        listing.body_damage = request.form.get("body_damage", listing.body_damage).strip()
        listing.repair_history = request.form.get("repair_history", listing.repair_history).strip()
        listing.accessories = request.form.get("accessories", listing.accessories).strip()

        # Sensitive modifications re-trigger moderation unless edited by admin
        if not is_admin():
            listing.approval_status = "pending"
            listing.rejection_reason = None

        listing.updated_at = utc_now()
        db.session.commit()

        flash("Listing updated. Sensitive edits require administrative re-approval before going live.", "info")
        return redirect(url_for("listing_detail", listing_id=listing.id))

    @app.route("/delete/<listing_id>", methods=["POST"])
    def delete_listing(listing_id):
        user = get_current_user()
        if not user:
            abort(403)

        listing = db.session.get(Listing, listing_id)
        if not listing:
            abort(404)

        if listing.seller_id != user.id and not is_admin():
            abort(403)

        # Check if there are active orders
        has_active_orders = Order.query.filter_by(listing_id=listing_id).filter(Order.status.in_(["Pending", "Confirmed", "Shipped"])).first()
        if has_active_orders:
            flash("Cannot delete a listing that has an active in-progress order.", "error")
            return redirect(url_for("seller_dashboard"))

        # Mark archived rather than hard deleting to preserve historical order integrity
        listing.approval_status = "archived"
        listing.inventory_status = "sold"
        listing.updated_at = utc_now()
        db.session.commit()

        flash("Listing has been archived and removed from the active marketplace.", "success")
        return redirect(url_for("seller_dashboard"))

    # =========================================================================
    # USER AUTHENTICATION & ACCOUNT
    # =========================================================================
    @app.route("/signup", methods=["GET", "POST"])
    def signup():
        if request.method == "GET":
            return render_template("signup.html")

        name = request.form.get("name", "").strip()
        email = normalize_email(request.form.get("email", ""))
        phone = request.form.get("phone", "").strip()
        password = request.form.get("password", "")

        if not name or len(name) < 2:
            flash("Please enter your full name.", "error")
            return render_template("signup.html"), 400

        if not email or "@" not in email:
            flash("Please enter a valid email address.", "error")
            return render_template("signup.html"), 400

        if not validate_phone(phone):
            flash("Please enter a valid 10-digit mobile number.", "error")
            return render_template("signup.html"), 400

        is_strong, pwd_err = validate_password_strength(password)
        if not is_strong:
            flash(pwd_err, "error")
            return render_template("signup.html"), 400

        existing = User.query.filter_by(email=email).first()
        if existing:
            flash("An account with this email already exists. Please log in.", "error")
            return render_template("signup.html"), 400

        # Public registration ALWAYS creates a regular customer account.
        # Administrative accounts can ONLY be provisioned via the secure CLI command.
        new_user = User(
            name=name,
            email=email,
            phone=phone,
            role="user",
            is_active=True
        )
        new_user.set_password(password)

        db.session.add(new_user)
        db.session.commit()

        flash("Account created successfully! Please log in to continue.", "success")
        return redirect(url_for("login"))

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "GET":
            if session.get("user_id"):
                return redirect(url_for("home"))
            return render_template("login.html")

        email = normalize_email(request.form.get("email", ""))
        password = request.form.get("password", "")
        client_ip = request.remote_addr or "unknown"

        # Rate limiting check
        if is_rate_limited(email) or is_rate_limited(client_ip):
            flash("Too many failed login attempts. For security, access is temporarily locked. Please try again in 15 minutes.", "error")
            return render_template("login.html"), 429

        user = User.query.filter_by(email=email).first()

        if not user or not user.is_active or not user.check_password(password):
            record_failed_attempt(email)
            record_failed_attempt(client_ip)
            flash("Invalid email or password.", "error")
            return render_template("login.html"), 401

        # Clear failed attempts
        clear_failed_attempts(email)
        clear_failed_attempts(client_ip)

        session.clear()
        session["user_id"] = user.id
        session["user_name"] = user.name
        session["user_email"] = user.email
        session["user_role"] = user.role
        session["admin_authenticated"] = False

        flash(f"Welcome back, {user.name}!", "success")
        next_url = request.values.get("next")
        if next_url and is_safe_redirect_url(next_url):
            return redirect(next_url)
        return redirect(url_for("home"))

    @app.route("/logout", methods=["GET", "POST"])
    def logout():
        session.clear()
        flash("You have been signed out.", "info")
        return redirect(url_for("home"))

    @app.route("/forgot-password", methods=["GET", "POST"])
    def forgot_password():
        if request.method == "GET":
            if session.get("user_id"):
                return redirect(url_for("home"))
            return render_template("forgot_password.html")

        email_raw = request.form.get("email", "")
        email = normalize_email(email_raw)
        client_ip = request.remote_addr or "unknown"

        # 1. Server-side email format validation
        if not validate_email_format(email):
            flash("Please enter a valid email address.", "error")
            return render_template("forgot_password.html", submitted_email=email_raw), 400

        # 2. Rate limiting check (per-email and per-IP)
        if is_reset_rate_limited(email) or is_reset_rate_limited(client_ip):
            flash("Too many password reset requests. For security, please wait 15 minutes before requesting another link.", "error")
            return render_template("forgot_password.html", submitted_email=email_raw), 429

        # Record attempt for rate limiting
        record_reset_request(email)
        record_reset_request(client_ip)

        # 3. Lookup user
        user = User.query.filter_by(email=email).first()

        # 4. If account exists and is active, create token and send email
        if user and user.is_active:
            user_agent = request.user_agent.string if request.user_agent else None
            token_record, plaintext_token = create_password_reset_token(
                user=user,
                ip_address=client_ip,
                user_agent=user_agent,
                expires_in_minutes=current_app.config.get("PASSWORD_RESET_EXPIRY_MINUTES", 15)
            )

            # Build reset URL using trusted configured base URL
            base_url = (current_app.config.get("APP_BASE_URL") or "").rstrip("/")
            if not base_url:
                base_url = request.host_url.rstrip("/")
            reset_url = f"{base_url}/reset-password/{plaintext_token}"

            # Transmit email
            success, send_msg = send_password_reset_email(user.email, user.name, reset_url)
            if not success:
                flash("We encountered a technical issue delivering the reset instructions. Please try again later.", "error")
                return render_template("forgot_password.html", submitted_email=email_raw), 500

            # Log audit event
            audit = AuditEvent(
                user_id=user.id,
                action="password_reset_requested",
                target_type="user",
                target_id=user.id,
                details="Password reset token issued and email sent",
                ip_address=client_ip
            )
            db.session.add(audit)
            db.session.commit()

        # 5. Generic response to prevent account enumeration
        flash("If an account exists with this email, you will receive password reset instructions shortly.", "success")
        return redirect(url_for("login"))

    @app.route("/reset-password/<token>", methods=["GET", "POST"])
    def reset_password(token):
        client_ip = request.remote_addr or "unknown"

        # Rate limit token validation / submissions
        if is_submission_rate_limited(client_ip):
            flash("Too many attempts. For security, please wait 15 minutes before trying again.", "error")
            return render_template("reset_password.html", valid=False, error_message="Too many attempts. Please try again later."), 429

        # Verify token validity
        is_valid, error_msg, token_record = verify_password_reset_token(token)

        if request.method == "GET":
            if not is_valid:
                record_submission_attempt(client_ip)
                return render_template("reset_password.html", valid=False, error_message=error_msg), 400
            return render_template("reset_password.html", valid=True, token=token)

        # POST: Password update
        record_submission_attempt(client_ip)

        if not is_valid or not token_record:
            return render_template("reset_password.html", valid=False, error_message=error_msg), 400

        new_password = request.form.get("new_password", "")
        confirm_password = request.form.get("confirm_password", "")

        if not new_password or not confirm_password:
            flash("Please provide and confirm your new password.", "error")
            return render_template("reset_password.html", valid=True, token=token), 400

        if new_password != confirm_password:
            flash("The passwords do not match. Please try again.", "error")
            return render_template("reset_password.html", valid=True, token=token), 400

        # Validate password policy
        is_strong, strength_err = validate_password_strength(new_password)
        if not is_strong:
            flash(strength_err, "error")
            return render_template("reset_password.html", valid=True, token=token), 400

        # Atomically consume token and update password
        success, result_msg = consume_password_reset_token(token_record, new_password)
        if not success:
            flash(result_msg, "error")
            return render_template("reset_password.html", valid=False, error_message=result_msg), 400

        # Audit event
        audit = AuditEvent(
            user_id=token_record.user_id,
            action="password_reset_completed",
            target_type="user",
            target_id=token_record.user_id,
            details="Password successfully changed via secure reset token",
            ip_address=client_ip
        )
        db.session.add(audit)
        db.session.commit()

        # Invalidate any active session
        session.clear()

        flash("Your password has been reset successfully! You can now sign in with your new password.", "success")
        return redirect(url_for("login"))

    @app.route("/account", methods=["GET", "POST"])
    def account():
        user = get_current_user()
        if not user:
            return redirect(url_for("login"))

        if request.method == "POST":
            action = request.form.get("action")
            if action == "change_password":
                current_pwd = request.form.get("current_password", "")
                new_pwd = request.form.get("new_password", "")
                confirm_pwd = request.form.get("confirm_password", "")

                if not user.check_password(current_pwd):
                    flash("Current password is incorrect.", "error")
                elif confirm_pwd and new_pwd != confirm_pwd:
                    flash("New password and confirmation do not match.", "error")
                else:
                    is_strong, err_msg = validate_password_strength(new_pwd)
                    if not is_strong:
                        flash(err_msg, "error")
                    else:
                        user.set_password(new_pwd)
                        user.updated_at = utc_now()
                        db.session.commit()
                        flash("Password updated successfully.", "success")
                return redirect(url_for("account", tab="security"))

            elif action == "update_profile":
                new_name = request.form.get("name", "").strip()
                new_username = request.form.get("username", "").strip()
                new_phone = request.form.get("phone", "").strip()
                new_city = request.form.get("city", "").strip()
                new_state = request.form.get("state", "").strip()
                new_bio = request.form.get("bio", "").strip()
                remove_avatar = request.form.get("remove_avatar") == "true"

                errors = []
                if not new_name or len(new_name) < 2:
                    errors.append("Full name must be at least 2 characters long.")
                elif len(new_name) > 100:
                    errors.append("Full name cannot exceed 100 characters.")

                if new_username:
                    valid_u, u_msg = validate_username(new_username)
                    if not valid_u:
                        errors.append(u_msg)
                    else:
                        existing_u = User.query.filter(
                            User.username.ilike(new_username),
                            User.id != user.id
                        ).first()
                        if existing_u:
                            errors.append(f"The username '@{new_username}' is already in use by another member.")

                if not new_phone or not validate_phone(new_phone):
                    errors.append("Please provide a valid 10-digit Indian phone number.")

                if len(new_bio) > 500:
                    errors.append("Bio cannot exceed 500 characters.")

                # Handle avatar upload
                avatar_file = request.files.get("avatar")
                saved_avatar_path = None
                if avatar_file and avatar_file.filename:
                    avatar_file.seek(0, os.SEEK_END)
                    file_size = avatar_file.tell()
                    avatar_file.seek(0)
                    if file_size > 3 * 1024 * 1024:
                        errors.append("Profile image must be smaller than 3MB.")
                    elif not validate_image_file(avatar_file):
                        errors.append("Invalid profile image. Supported formats: JPG, PNG, WEBP.")
                    else:
                        try:
                            saved_avatar_path = upload_to_cloudinary(
                                avatar_file,
                                "isellora/avatars"
                            )
                        except Exception:
                            current_app.logger.exception("Cloudinary avatar upload failed.")
                            errors.append("Profile image upload failed. Please try again.")

                if errors:
                    for err in errors:
                        flash(err, "error")
                    return redirect(url_for("account", tab="profile"))

                # Apply updates
                user.name = new_name
                user.username = new_username if new_username else None
                user.phone = new_phone
                user.city = new_city[:100] if new_city else None
                user.state = new_state[:100] if new_state else None
                user.bio = new_bio[:500] if new_bio else None

                if remove_avatar:
                    user.avatar_path = None
                elif saved_avatar_path:
                    user.avatar_path = saved_avatar_path

                user.updated_at = utc_now()
                db.session.commit()
                session["user_name"] = user.name
                flash("Profile updated successfully.", "success")
                return redirect(url_for("account", tab="profile"))

            return redirect(url_for("account"))

        active_tab = request.args.get("tab", "profile").strip().lower()
        if active_tab not in ("profile", "security", "activity", "danger"):
            active_tab = "profile"

        my_listings = Listing.query.filter_by(seller_id=user.id).order_by(Listing.created_at.desc()).all()
        approved_listings = [l for l in my_listings if l.approval_status == 'approved' and l.inventory_status == 'available']
        pending_listings = [l for l in my_listings if l.approval_status == 'pending']
        sold_listings = [l for l in my_listings if l.inventory_status == 'sold']

        recent_orders = Order.query.filter_by(buyer_id=user.id).order_by(Order.created_at.desc()).limit(5).all()
        recent_sales = Order.query.filter_by(seller_id=user.id).order_by(Order.created_at.desc()).limit(5).all()
        seller_reviews = Review.query.filter_by(seller_id=user.id).order_by(Review.created_at.desc()).all()

        return render_template(
            "account.html",
            user=user,
            active_tab=active_tab,
            my_listings=my_listings,
            approved_listings=approved_listings,
            pending_listings=pending_listings,
            sold_listings=sold_listings,
            recent_orders=recent_orders,
            recent_sales=recent_sales,
            seller_reviews=seller_reviews
        )

    @app.route("/seller/<user_id>")
    @app.route("/user/<user_id>", endpoint="user_profile")
    def seller_profile(user_id):
        seller = db.session.get(User, user_id)
        if not seller or not seller.is_active:
            abort(404)

        current_user = get_current_user()
        is_own_profile = bool(current_user and current_user.id == seller.id)
        is_admin_viewer = is_admin()

        # Public listings strictly limited to approved & available
        active_listings = Listing.query.filter_by(
            seller_id=seller.id,
            approval_status="approved",
            inventory_status="available"
        ).order_by(Listing.is_featured.desc(), Listing.created_at.desc()).all()

        completed_sales_count = Order.query.filter_by(
            seller_id=seller.id,
            status="Delivered"
        ).count()

        reviews = Review.query.filter_by(seller_id=seller.id).order_by(Review.created_at.desc()).all()

        return render_template(
            "seller_profile.html",
            seller=seller,
            listings=active_listings,
            completed_sales_count=completed_sales_count,
            reviews=reviews,
            is_own_profile=is_own_profile,
            is_admin_viewer=is_admin_viewer
        )

    @app.route("/u/<username>")
    def seller_profile_by_username(username):
        clean = (username or "").strip().lstrip("@")
        seller = User.query.filter(User.username.ilike(clean), User.is_active.is_(True)).first()
        if not seller:
            abort(404)
        return redirect(url_for("seller_profile", user_id=seller.id))

    @app.route("/account/deactivate", methods=["POST"])
    def deactivate_account():
        user = get_current_user()
        if not user:
            abort(403)

        user.is_active = False
        user.updated_at = utc_now()
        db.session.commit()
        session.clear()
        flash("Your account has been deactivated.", "info")
        return redirect(url_for("home"))

    # =========================================================================
    # NOTIFICATIONS
    # =========================================================================
    @app.route("/notifications/<notification_id>/read", methods=["POST"])
    def read_notification(notification_id):
        user = get_current_user()
        if not user:
            abort(403)

        notice = db.session.get(Notification, notification_id)
        if not notice or notice.user_id != user.id:
            abort(404)

        notice.is_read = True
        db.session.commit()

        if notice.listing_id:
            return redirect(url_for("listing_detail", listing_id=notice.listing_id))
        elif notice.order_id:
            return redirect(url_for("orders"))
        return redirect(url_for("account"))

    # =========================================================================
    # SUPPORT & DISPUTES
    # =========================================================================
    @app.route("/support", methods=["GET", "POST"])
    def support():
        user = get_current_user()
        user_tickets = []
        if user:
            user_tickets = SupportTicket.query.filter_by(user_id=user.id).order_by(SupportTicket.created_at.desc()).all()

        if request.method == "POST":
            if not user:
                flash("Please log in to submit a support ticket.", "error")
                return redirect(url_for("login"))

            subject = request.form.get("subject", "").strip()
            description = request.form.get("description", "").strip()
            order_id = request.form.get("order_id", "").strip() or None

            if order_id:
                order = db.session.get(Order, order_id)
                is_authorized_order = order and (
                    order.buyer_id == user.id or
                    order.seller_id == user.id or
                    is_admin()
                )
                if not is_authorized_order:
                    flash("The specified order could not be found or is not associated with your account.", "error")
                    return render_template("support.html", tickets=user_tickets), 400

            if not subject or not description:
                flash("Please provide both a subject and details for your ticket.", "error")
                return render_template("support.html", tickets=user_tickets), 400

            ticket_num = f"TKT-{uuid.uuid4().hex[:6].upper()}"
            ticket = SupportTicket(
                ticket_number=ticket_num,
                user_id=user.id,
                order_id=order_id,
                subject=subject,
                description=description,
                status="Open",
                priority="Medium"
            )
            db.session.add(ticket)
            db.session.commit()

            flash(f"Support ticket {ticket_num} submitted. Our team will review it shortly.", "success")
            return redirect(url_for("support"))

        return render_template("support.html", tickets=user_tickets)

    @app.route("/report/<listing_id>", methods=["POST"])
    def report_listing(listing_id):
        user = get_current_user()
        if not user:
            flash("Please log in to report a listing.", "error")
            return redirect(url_for("login"))

        listing = db.session.get(Listing, listing_id)
        if not listing:
            abort(404)

        report_type = request.form.get("report_type", "Misrepresented Condition").strip()
        reason = request.form.get("reason", "").strip()

        if not reason:
            flash("Please specify a reason for reporting this listing.", "error")
            return redirect(url_for("listing_detail", listing_id=listing_id))

        dispute = ReportDispute(
            reporter_id=user.id,
            listing_id=listing_id,
            report_type=report_type,
            reason=reason,
            status="Pending"
        )
        db.session.add(dispute)
        db.session.commit()

        flash("Listing report submitted. Our Trust & Safety team will inspect this device.", "info")
        return redirect(url_for("listing_detail", listing_id=listing_id))

    # =========================================================================
    # ADMIN ROUTES
    # =========================================================================
    @app.route("/admin/login", methods=["GET", "POST"])
    def admin_login():
        if request.method == "GET":
            if is_admin():
                return redirect(url_for("admin_dashboard"))
            return render_template("admin_login.html")

        email = normalize_email(request.form.get("email", ""))
        password = request.form.get("password", "")
        client_ip = request.remote_addr or "unknown"

        if is_rate_limited(email) or is_rate_limited(client_ip):
            flash("Too many failed attempts. Please try again after 15 minutes.", "error")
            return render_template("admin_login.html"), 429

        user = User.query.filter_by(email=email).first()
        if not user or not user.is_active or user.role != "admin" or not user.check_password(password):
            record_failed_attempt(email)
            record_failed_attempt(client_ip)
            flash("Invalid administrator credentials.", "error")
            return render_template("admin_login.html"), 401

        clear_failed_attempts(email)
        clear_failed_attempts(client_ip)

        session.clear()
        session["user_id"] = user.id
        session["user_name"] = user.name
        session["user_email"] = user.email
        session["user_role"] = "admin"
        session["admin_authenticated"] = True

        # Audit event
        audit = AuditEvent(
            user_id=user.id,
            action="ADMIN_LOGIN_SUCCESS",
            ip_address=request.remote_addr,
            created_at=utc_now()
        )
        db.session.add(audit)
        db.session.commit()

        return redirect(url_for("admin_dashboard"))

    @app.route("/admin/logout", methods=["POST"])
    def admin_logout():
        session.clear()
        return redirect(url_for("admin_login"))

    @app.route("/admin")
    def admin_dashboard():
        if not is_admin():
            return redirect(url_for("admin_login"))

        total_users = User.query.count()
        total_listings = Listing.query.count()
        active_listings = Listing.query.filter_by(approval_status="approved", inventory_status="available").count()
        pending_approvals = Listing.query.filter_by(approval_status="pending").count()
        
        total_orders = Order.query.count()
        delivered_orders = Order.query.filter_by(status="Delivered").count()
        cancelled_orders = Order.query.filter_by(status="Cancelled").count()
        
        open_tickets = SupportTicket.query.filter(SupportTicket.status.in_(["Open", "In Progress"])).count()
        pending_reports = ReportDispute.query.filter_by(status="Pending").count()

        users_list = User.query.order_by(User.created_at.desc()).limit(20).all()
        listings_list = Listing.query.order_by(Listing.created_at.desc()).limit(30).all()
        orders_list = Order.query.order_by(Order.created_at.desc()).limit(20).all()
        tickets_list = SupportTicket.query.order_by(SupportTicket.created_at.desc()).limit(10).all()
        reports_list = ReportDispute.query.order_by(ReportDispute.created_at.desc()).limit(10).all()
        audit_logs = AuditEvent.query.order_by(AuditEvent.created_at.desc()).limit(20).all()

        # Manual UPI references awaiting admin review
        upi_payments = Payment.query.filter(
            Payment.provider == "upi_manual",
            Payment.purpose == "order",
            Payment.upi_reference.isnot(None),
            Payment.status == "pending"
        ).order_by(Payment.upi_submitted_at.asc()).limit(50).all()

        # Only genuine, verified paid subscriptions count toward active boosts and revenue
        verified_boosts = ListingSubscription.query.filter_by(status="active", payment_status="paid").all()
        total_boosts = len(verified_boosts)
        boost_revenue = sum(s.amount_rupees for s in verified_boosts)

        return render_template(
            "admin.html",
            total_users=total_users,
            total_listings=total_listings,
            active_listings=active_listings,
            pending_approvals=pending_approvals,
            total_orders=total_orders,
            delivered_orders=delivered_orders,
            cancelled_orders=cancelled_orders,
            open_tickets=open_tickets,
            pending_reports=pending_reports,
            total_boosts=total_boosts,
            boost_revenue=boost_revenue,
            users=users_list,
            listings=listings_list,
            orders=orders_list,
            tickets=tickets_list,
            reports=reports_list,
            audit_logs=audit_logs,
            upi_payments=upi_payments
        )

    @app.route("/admin/support/<ticket_id>/update", methods=["POST"])
    def admin_update_support_ticket(ticket_id):
        if not is_admin():
            abort(403)

        ticket = db.session.get(SupportTicket, ticket_id)
        if ticket is None:
            abort(404)

        new_status = request.form.get("status", "").strip()
        notes = request.form.get("resolution_notes", "").strip()
        allowed_statuses = {"Open", "In Progress", "Resolved", "Closed"}

        if new_status not in allowed_statuses:
            flash("Invalid support ticket status.", "error")
            return redirect(url_for("admin_dashboard"))

        admin_user = get_current_user()
        old_status = ticket.status
        ticket.status = new_status
        ticket.resolution_notes = notes or None

        db.session.add(AuditEvent(
            user_id=admin_user.id,
            action="SUPPORT_TICKET_UPDATED",
            target_type="SupportTicket",
            target_id=ticket.id,
            details=f"Support ticket {ticket.ticket_number} status changed from {old_status} to {new_status}.",
            ip_address=request.remote_addr,
            created_at=utc_now()
        ))

        db.session.commit()
        flash(f"Support ticket {ticket.ticket_number} updated.", "success")
        return redirect(url_for("admin_dashboard"))

    @app.route("/admin/listing-approvals")
    def admin_listing_approvals():
        if not is_admin():
            return redirect(url_for("admin_login"))

        status_filter = request.args.get("status", "pending").strip().lower()
        if status_filter not in ("pending", "approved", "rejected", "all"):
            status_filter = "pending"

        pending_count = Listing.query.filter_by(approval_status="pending").count()
        approved_count = Listing.query.filter_by(approval_status="approved").count()
        rejected_count = Listing.query.filter_by(approval_status="rejected").count()
        all_count = Listing.query.count()

        query = Listing.query
        if status_filter in ("pending", "approved", "rejected"):
            query = query.filter_by(approval_status=status_filter)

        listings = query.order_by(Listing.created_at.desc()).all()

        return render_template(
            "admin_listing_approvals.html",
            listings=listings,
            status_filter=status_filter,
            pending_count=pending_count,
            approved_count=approved_count,
            rejected_count=rejected_count,
            all_count=all_count
        )

    @app.route("/admin/listing/<listing_id>/status", methods=["POST"])
    def admin_listing_status(listing_id):
        if not is_admin():
            abort(403)

        data = request.get_json(silent=True) or request.form
        action = (data.get("action") or data.get("status") or "").strip().lower()
        reason = (data.get("reason") or "").strip()

        if not action:
            if request.is_json or request.headers.get("X-Requested-With") == "XMLHttpRequest":
                return jsonify({"success": False, "error": "Missing moderation action."}), 400
            flash("Missing moderation action.", "error")
            return redirect(url_for("admin_dashboard"))

        admin_user_id = session.get("user_id")
        success, msg = moderate_listing(
            listing_id=listing_id,
            action=action,
            admin_id=admin_user_id,
            reason=reason
        )

        if request.is_json or request.headers.get("X-Requested-With") == "XMLHttpRequest":
            if success:
                return jsonify({"success": True, "message": f"Listing moderation updated: {action}."})
            return jsonify({"success": False, "error": msg}), 400

        if success:
            flash(f"Listing moderation updated: {action}.", "success")
        else:
            flash(msg, "error")

        next_url = data.get("next") or request.referrer
        if next_url and is_safe_redirect_url(next_url):
            return redirect(next_url)
        return redirect(url_for("admin_dashboard"))

    @app.route("/admin/payment/<payment_id>/upi/verify", methods=["POST"])
    def admin_verify_upi_payment(payment_id):
        if not is_admin():
            abort(403)

        payment = db.session.get(Payment, payment_id)
        if (
            not payment
            or payment.provider != "upi_manual"
            or payment.purpose != "order"
            or not payment.upi_reference
            or payment.status != "pending"
        ):
            flash("UPI payment is not available for verification.", "error")
            return redirect(url_for("admin_dashboard"))

        order = db.session.get(Order, payment.order_id)
        if not order or order.payment_method != "upi":
            abort(404)

        if order.payment_status == "paid" or order.status == "Cancelled":
            flash("This order cannot be verified again.", "error")
            return redirect(url_for("admin_dashboard"))

        admin_user = get_current_user()
        now = utc_now()

        # Admin must independently confirm the bank/UPI credit before this action.
        payment.status = "paid"
        payment.paid_at = now
        payment.reviewed_by = admin_user.id
        payment.reviewed_at = now
        payment.review_note = "UPI payment independently verified by admin."
        order.payment_status = "paid"

        db.session.add(AuditEvent(
            user_id=admin_user.id,
            action="UPI_PAYMENT_VERIFIED",
            target_type="Payment",
            target_id=payment.id,
            details=f"UPI payment verified for order {order.id}.",
            ip_address=request.remote_addr
        ))
        if order.buyer_id:
            db.session.add(Notification(
                user_id=order.buyer_id,
                title="UPI Payment Verified",
                message=f"Your UPI payment for order {order.id} has been verified.",
                order_id=order.id
            ))
        if order.seller_id:
            db.session.add(Notification(
                user_id=order.seller_id,
                title="Buyer Payment Verified",
                message=f"UPI payment for order {order.id} has been verified.",
                order_id=order.id
            ))

        db.session.commit()
        flash("UPI payment verified. Order payment status is now paid.", "success")
        return redirect(url_for("admin_dashboard"))

    @app.route("/admin/payment/<payment_id>/upi/reject", methods=["POST"])
    def admin_reject_upi_payment(payment_id):
        if not is_admin():
            abort(403)

        payment = db.session.get(Payment, payment_id)
        if (
            not payment
            or payment.provider != "upi_manual"
            or payment.purpose != "order"
            or not payment.upi_reference
            or payment.status != "pending"
        ):
            flash("UPI payment is not available for review.", "error")
            return redirect(url_for("admin_dashboard"))

        order = db.session.get(Order, payment.order_id)
        if not order or order.payment_method != "upi":
            abort(404)

        if order.payment_status == "paid" or order.status == "Cancelled":
            flash("This order cannot be rejected.", "error")
            return redirect(url_for("admin_dashboard"))

        admin_user = get_current_user()
        reason = (request.form.get("reason") or "").strip()
        if not reason or len(reason) > 500:
            flash("Enter a rejection reason (maximum 500 characters).", "error")
            return redirect(url_for("admin_dashboard"))

        now = utc_now()
        payment.status = "failed"
        payment.error_code = "UPI_REFERENCE_REJECTED"
        payment.error_description = reason
        payment.reviewed_by = admin_user.id
        payment.reviewed_at = now
        payment.review_note = reason
        order.payment_status = "failed"

        db.session.add(AuditEvent(
            user_id=admin_user.id,
            action="UPI_PAYMENT_REJECTED",
            target_type="Payment",
            target_id=payment.id,
            details=f"UPI reference rejected for order {order.id}: {reason}",
            ip_address=request.remote_addr
        ))
        if order.buyer_id:
            db.session.add(Notification(
                user_id=order.buyer_id,
                title="UPI Payment Needs Attention",
                message=f"Your UPI reference for order {order.id} was not verified. Reason: {reason}",
                order_id=order.id
            ))

        db.session.commit()
        flash("UPI reference rejected. The order has not been cancelled.", "warning")
        return redirect(url_for("admin_dashboard"))

    @app.route("/admin/payment/<payment_id>/refund", methods=["POST"])
    def admin_refund_payment(payment_id):
        if not is_admin():
            abort(403)

        data = request.get_json(silent=True) or request.form
        reason = (data.get("reason") or "").strip() or "Customer refund approved by admin."
        payment = db.session.get(Payment, payment_id)
        if not payment:
            if request.is_json:
                return jsonify({"success": False, "error": "Payment record not found."}), 404
            flash("Payment record not found.", "error")
            return redirect(url_for("admin_dashboard"))

        amount_paise = payment.amount_paise
        user = get_current_user()
        success, msg = initiate_payment_refund(
            payment_id=payment.id,
            amount_paise=amount_paise,
            reason=reason,
            actor_id=user.id if user else "admin",
            actor_role="admin"
        )

        if success:
            flash(msg, "success")
        else:
            flash(f"Refund failed: {msg}", "error")

        if request.is_json:
            return jsonify({"success": success, "message": msg}), (200 if success else 400)
        return redirect(url_for("admin_dashboard"))

    # =========================================================================
    # SYSTEM HEALTH CHECK
    # =========================================================================
    @app.route("/health")
    def health():
        try:
            db.session.execute(db.select(1))
            db_status = "healthy"
        except Exception:
            db_status = "degraded"

        return jsonify({
            "status": "healthy" if db_status == "healthy" else "degraded",
            "service": "iSellora Marketplace",
            "database": db_status,
            "timestamp": utc_now().isoformat()
        }), 200 if db_status == "healthy" else 503

    # =========================================================================
    # CLI COMMANDS
    # =========================================================================
    @app.cli.command("create-admin")
    @click.argument("email")
    @click.argument("password")
    @click.option("--name", default="Administrator", help="Admin display name")
    def create_admin_command(email, password, name):
        """Create a new administrator or promote an existing user."""
        email = normalize_email(email)
        user = User.query.filter_by(email=email).first()
        if user:
            user.role = "admin"
            user.set_password(password)
            user.is_active = True
            if name and name != "Administrator":
                user.name = name
            audit = AuditEvent(
                user_id=user.id,
                action="ADMIN_ROLE_PROVISIONED",
                target_type="user",
                target_id=user.id,
                details=f"User role updated to admin via CLI provisioning.",
                created_at=utc_now()
            )
            db.session.add(audit)
            db.session.commit()
            click.echo(f"Successfully promoted existing user '{email}' to administrator.")
        else:
            user = User(
                name=name,
                email=email,
                role="admin",
                is_active=True
            )
            user.set_password(password)
            db.session.add(user)
            db.session.flush()
            audit = AuditEvent(
                user_id=user.id,
                action="ADMIN_ACCOUNT_CREATED",
                target_type="user",
                target_id=user.id,
                details=f"New administrator account created via CLI.",
                created_at=utc_now()
            )
            db.session.add(audit)
            db.session.commit()
            click.echo(f"Successfully created new administrator '{email}'.")

    return app


# Application instance for WSGI servers & runners
app = create_app()

if __name__ == "__main__":
    backend_env = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    extra_files = [backend_env] if os.path.exists(backend_env) else []
    app.run(debug=True, host="127.0.0.1", port=5000, extra_files=extra_files)







