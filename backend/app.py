"""
iSellora Marketplace Application
Full-featured, secure, and relational-backed marketplace for second-hand smartphones.
"""
import os
import uuid
from datetime import datetime, timezone
from urllib.parse import quote_plus
from flask import (
    Flask, render_template, request, redirect,
    url_for, session, abort, flash, jsonify
)
from flask_wtf.csrf import CSRFProtect, CSRFError
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash

from backend.database import db
from backend.models import (
    User, Listing, ListingImage, Order, OrderStatusHistory,
    Notification, SupportTicket, ReportDispute, AuditEvent,
    utc_now, get_legitimate_tracking_url
)
from backend.config import config_by_name
from backend.services.auth_service import (
    is_rate_limited, record_failed_attempt, clear_failed_attempts,
    validate_password_strength, normalize_email, validate_phone
)
from backend.services.listing_service import (
    query_catalog, validate_image_file, moderate_listing
)
from backend.services.order_service import (
    create_order, transition_order_status
)
from backend.services.inventory_service import (
    release_listing_reservation
)

csrf = CSRFProtect()


def create_app(config_name=None):
    if config_name is None:
        config_name = os.environ.get("FLASK_ENV", "development")

    app = Flask(
        __name__,
        template_folder=os.path.join(os.path.dirname(__file__), "templates"),
        static_folder=os.path.join(os.path.dirname(__file__), "static")
    )

    config_class = config_by_name.get(config_name, config_by_name["development"])
    app.config.from_object(config_class)

    # Initialize extensions
    db.init_app(app)
    csrf.init_app(app)

    # Ensure uploads folder exists
    os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)

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

        return {
            "current_user": current_user,
            "notifications": notifications,
            "unread_notification_count": unread_count,
            "now": utc_now()
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
        return render_template("base.html", error_message="Security token expired or invalid. Please refresh the page and try again."), 400

    @app.errorhandler(400)
    def bad_request(e):
        return render_template("base.html", error_message=getattr(e, "description", "Bad Request")), 400

    @app.errorhandler(403)
    def forbidden(e):
        return render_template("base.html", error_message="Access Denied: You do not have permission to view or modify this resource."), 403

    @app.errorhandler(404)
    def not_found(e):
        return render_template("base.html", error_message="The requested page or listing could not be found."), 404

    @app.errorhandler(409)
    def conflict(e):
        return render_template("base.html", error_message=getattr(e, "description", "Conflict: Item was modified or claimed by another request.")), 409

    @app.errorhandler(500)
    def internal_error(e):
        db.session.rollback()
        app.logger.exception("Internal server error")
        return render_template("base.html", error_message="An unexpected system error occurred. Our engineering team has been alerted."), 500

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
        # Fetch featured and newest approved listings
        featured_listings = Listing.query.filter_by(
            approval_status="approved", inventory_status="available"
        ).order_by(Listing.created_at.desc()).limit(8).all()
        return render_template("index.html", phones=featured_listings)

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
        
        try:
            page = max(1, int(request.args.get("page", 1)))
        except ValueError:
            page = 1

        try:
            min_price = float(request.args.get("min_price", 0)) if request.args.get("min_price") else None
        except ValueError:
            min_price = None

        try:
            max_price = float(request.args.get("max_price", 0)) if request.args.get("max_price") else None
        except ValueError:
            max_price = None

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
    # CHECKOUT & ORDER ROUTES (Payment Gateway strictly deferred)
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

        if request.method == "GET":
            token = str(uuid.uuid4())
            session["checkout_token"] = token
            session["checkout_listing_id"] = str(listing_id)
            return render_template("checkout.html", phone=listing, checkout_token=token)

        # POST submission
        checkout_token = request.form.get("checkout_token") or session.get("checkout_token")
        if not checkout_token or session.get("checkout_listing_id") != str(listing_id):
            return render_template("base.html", error_message="Checkout session expired. Please start checkout again."), 409

        buyer_name = request.form.get("buyer_name", "").strip()
        buyer_phone = request.form.get("buyer_phone", "").strip()
        delivery_method = request.form.get("delivery_method", "pickup").strip().lower()
        shipping_address = request.form.get("address", "").strip()
        pincode = request.form.get("pincode", "").strip()

        if not (2 <= len(buyer_name) <= 100):
            flash("Please enter a valid full name (2-100 characters).", "error")
            return render_template("checkout.html", phone=listing, checkout_token=checkout_token), 400

        if not validate_phone(buyer_phone):
            flash("Please enter a valid 10-digit phone number.", "error")
            return render_template("checkout.html", phone=listing, checkout_token=checkout_token), 400

        if delivery_method not in ("pickup", "shipping"):
            flash("Invalid delivery method selected.", "error")
            return render_template("checkout.html", phone=listing, checkout_token=checkout_token), 400

        if delivery_method == "shipping":
            if len(shipping_address) < 10:
                flash("Please enter a complete delivery address.", "error")
                return render_template("checkout.html", phone=listing, checkout_token=checkout_token), 400
            if not pincode.isdigit() or len(pincode) != 6:
                flash("Please enter a valid 6-digit PIN code.", "error")
                return render_template("checkout.html", phone=listing, checkout_token=checkout_token), 400
        else:
            shipping_address = "Local Pickup arranged directly with seller"
            pincode = None

        order, error = create_order(
            buyer_id=user.id,
            listing_id=listing_id,
            buyer_name=buyer_name,
            buyer_phone=buyer_phone,
            delivery_method=delivery_method,
            shipping_address=shipping_address,
            pincode=pincode,
            checkout_token=checkout_token
        )

        if error:
            flash(error, "error")
            return render_template("checkout.html", phone=listing, checkout_token=checkout_token), 409

        session.pop("checkout_token", None)
        session.pop("checkout_listing_id", None)

        return redirect(url_for("order_confirmation", order_id=order.id))

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
        return render_template("orders.html", orders=my_orders)

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

    @app.route("/sales")
    def sales():
        user = get_current_user()
        if not user:
            return redirect(url_for("login", next=request.path))

        my_sales = Order.query.filter_by(seller_id=user.id).order_by(Order.created_at.desc()).all()
        return render_template("sales.html", sales=my_sales)

    @app.route("/seller-dashboard")
    def seller_dashboard():
        user = get_current_user()
        if not user:
            return redirect(url_for("login", next=request.path))

        my_listings = Listing.query.filter_by(seller_id=user.id).order_by(Listing.created_at.desc()).all()
        my_sales = Order.query.filter_by(seller_id=user.id).order_by(Order.created_at.desc()).all()

        total_earnings = sum(order.price_rupees for order in my_sales if order.status == "Delivered")
        pending_orders = sum(1 for order in my_sales if order.status == "Pending")

        return render_template(
            "seller_dashboard.html",
            listings=my_listings,
            sales=my_sales,
            total_earnings=total_earnings,
            pending_orders_count=pending_orders
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
            price_float = float(price_text.replace(",", "").replace("₹", ""))
            if price_float < 500 or price_float > 500000:
                raise ValueError
            price_paise = int(round(price_float * 100))
        except ValueError:
            flash("Please enter a valid price between ₹500 and ₹5,00,000.", "error")
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

        # Save photos
        for idx, f in enumerate(valid_files):
            ext = secure_filename(f.filename).rsplit(".", 1)[1].lower()
            fname = f"{uuid.uuid4()}.{ext}"
            f.save(os.path.join(app.config["UPLOAD_FOLDER"], fname))
            img_rec = ListingImage(
                listing_id=listing_id,
                image_path=f"/static/uploads/{fname}",
                display_order=idx,
                created_at=utc_now()
            )
            db.session.add(img_rec)

        db.session.commit()
        flash("Your listing has been submitted for review! It will be visible once approved by our moderation team.", "success")
        return redirect(url_for("seller_dashboard"))

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
                p_float = float(price_text.replace(",", "").replace("₹", ""))
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

        admin_email = app.config.get("ADMIN_EMAIL", "").strip().lower()
        role = "admin" if email == admin_email else "user"

        new_user = User(
            name=name,
            email=email,
            phone=phone,
            role=role,
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
        session["admin_authenticated"] = user.is_admin

        flash(f"Welcome back, {user.name}!", "success")
        next_url = request.args.get("next")
        if next_url and next_url.startswith("/"):
            return redirect(next_url)
        return redirect(url_for("home"))

    @app.route("/logout", methods=["GET", "POST"])
    def logout():
        session.clear()
        flash("You have been signed out.", "info")
        return redirect(url_for("home"))

    @app.route("/account", methods=["GET", "POST"])
    def account():
        user = get_current_user()
        if not user:
            return redirect(url_for("login"))

        if request.method == "POST":
            # Profile update or password change
            action = request.form.get("action")
            if action == "change_password":
                current_pwd = request.form.get("current_password", "")
                new_pwd = request.form.get("new_password", "")

                if not user.check_password(current_pwd):
                    flash("Current password is incorrect.", "error")
                else:
                    is_strong, err_msg = validate_password_strength(new_pwd)
                    if not is_strong:
                        flash(err_msg, "error")
                    else:
                        user.set_password(new_pwd)
                        user.updated_at = utc_now()
                        db.session.commit()
                        flash("Password updated successfully.", "success")
            elif action == "update_profile":
                new_name = request.form.get("name", "").strip()
                new_phone = request.form.get("phone", "").strip()
                if new_name and len(new_name) >= 2:
                    user.name = new_name
                if validate_phone(new_phone):
                    user.phone = new_phone
                user.updated_at = utc_now()
                db.session.commit()
                session["user_name"] = user.name
                flash("Profile updated successfully.", "success")

            return redirect(url_for("account"))

        my_listings = Listing.query.filter_by(seller_id=user.id).order_by(Listing.created_at.desc()).all()
        my_orders = Order.query.filter_by(buyer_id=user.id).order_by(Order.created_at.desc()).limit(5).all()

        return render_template(
            "account.html",
            user=user,
            my_listings=my_listings,
            recent_orders=my_orders
        )

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
        admin_cfg_email = app.config.get("ADMIN_EMAIL", "").strip().lower()

        if email != admin_cfg_email:
            flash("Invalid administrator credentials.", "error")
            return render_template("admin_login.html"), 401

        user = User.query.filter_by(email=email).first()
        if not user or not user.check_password(password):
            flash("Invalid administrator credentials.", "error")
            return render_template("admin_login.html"), 401

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
            users=users_list,
            listings=listings_list,
            orders=orders_list,
            tickets=tickets_list,
            reports=reports_list,
            audit_logs=audit_logs
        )

    @app.route("/admin/listing/<listing_id>/status", methods=["POST"])
    def admin_listing_status(listing_id):
        if not is_admin():
            abort(403)

        action = request.form.get("status", "").strip().lower()
        reason = request.form.get("reason", "").strip()

        admin_user_id = session.get("user_id")
        success, msg = moderate_listing(
            listing_id=listing_id,
            action=action,
            admin_id=admin_user_id,
            reason=reason
        )

        if success:
            flash(f"Listing moderation updated: {action}.", "success")
        else:
            flash(msg, "error")

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

    return app


# Application instance for WSGI servers & runners
app = create_app()

if __name__ == "__main__":
    app.run(debug=True, host="127.0.0.1", port=5000)
