from flask import (
    Flask, render_template, request, redirect,
    url_for, session, abort
)

from flask_wtf.csrf import CSRFProtect

from werkzeug.security import (
    generate_password_hash, check_password_hash
)

from werkzeug.utils import secure_filename

from datetime import datetime, timezone
from threading import RLock
from urllib.parse import quote_plus

import os
import uuid
import json
import re
import math


# =====================================
# APP CONFIGURATION
# =====================================

app = Flask(
    __name__,
    template_folder="templates"
)


from dotenv import load_dotenv

load_dotenv(
    os.path.join(os.path.dirname(__file__), ".env")
)

app.secret_key = os.environ.get("SECRET_KEY")

if not app.secret_key or len(app.secret_key) < 64:
    raise RuntimeError(
        "SECRET_KEY missing or too short. Check backend/.env"
    )


app.config.update(
    MAX_CONTENT_LENGTH=50 * 1024 * 1024,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax"
)


UPLOAD_FOLDER = os.path.join(
    app.root_path,
    "static",
    "uploads"
)

LISTINGS_FILE = os.path.join(
    app.root_path,
    "listings.json"
)

USERS_FILE = os.path.join(
    app.root_path,
    "users.json"
)

ORDERS_FILE = os.path.join(
    app.root_path,
    "orders.json"
)

NOTIFICATIONS_FILE = os.path.join(
    app.root_path,
    "notifications.json"
)


app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER

os.makedirs(
    UPLOAD_FOLDER,
    exist_ok=True
)


ALLOWED_EXTENSIONS = {
    "png",
    "jpg",
    "jpeg",
    "webp"
}


CONDITION_FIELDS = (
    "screen_damage",
    "body_damage",
    "repair_history",
    "original_display",
    "accessories"
)


data_lock = RLock()


ADMIN_EMAIL = os.environ.get(
    "ADMIN_EMAIL",
    ""
).strip().lower()


# =====================================
# JSON HELPERS
# =====================================

def load_json(filename):

    if not os.path.exists(filename):
        return []

    try:

        with open(
            filename,
            "r",
            encoding="utf-8"
        ) as f:

            data = json.load(f)

        return (
            data
            if isinstance(data, list)
            else []
        )

    except (
        OSError,
        json.JSONDecodeError
    ):

        return []


def save_json(filename, data):

    temp_file = (
        filename
        + "."
        + str(uuid.uuid4())
        + ".tmp"
    )

    try:

        with open(
            temp_file,
            "w",
            encoding="utf-8"
        ) as f:

            json.dump(
                data,
                f,
                indent=4,
                ensure_ascii=False
            )

        os.replace(
            temp_file,
            filename
        )

    finally:

        if os.path.exists(
            temp_file
        ):

            os.remove(
                temp_file
            )


def load_listings():

    return load_json(
        LISTINGS_FILE
    )


def save_listings(data):

    save_json(
        LISTINGS_FILE,
        data
    )


def load_users():

    return load_json(
        USERS_FILE
    )


def save_users(data):

    save_json(
        USERS_FILE,
        data
    )


def load_orders():

    return load_json(
        ORDERS_FILE
    )


def save_orders(data):

    save_json(
        ORDERS_FILE,
        data
    )


# =====================================
# NOTIFICATION HELPERS
# =====================================

def load_notifications():

    return load_json(
        NOTIFICATIONS_FILE
    )


def save_notifications(data):

    save_json(
        NOTIFICATIONS_FILE,
        data
    )


def create_notification(
    user_id,
    title,
    message,
    listing_id=None
):

    if not user_id:
        return

    with data_lock:

        notifications = (
            load_notifications()
        )

        notifications.insert(
            0,
            {
                "id":
                    str(uuid.uuid4()),

                "user_id":
                    user_id,

                "title":
                    title,

                "message":
                    message,

                "listing_id":
                    listing_id,

                "read":
                    False,

                "created_at":
                    current_time()
            }
        )

        save_notifications(
            notifications
        )


@app.context_processor
def inject_notifications():

    user_id = session.get(
        "user_id"
    )

    if not user_id:

        return {
            "notifications": [],
            "unread_notification_count": 0
        }

    my_notifications = [
        notice
        for notice in load_notifications()
        if notice.get(
            "user_id"
        ) == user_id
    ]

    my_notifications.sort(
        key=lambda notice:
        notice.get(
            "created_at",
            ""
        ),
        reverse=True
    )

    unread_count = sum(
        1
        for notice in my_notifications
        if not notice.get(
            "read",
            False
        )
    )

    return {
        "notifications":
            my_notifications[:10],

        "unread_notification_count":
            unread_count
    }


# =====================================
# GENERAL HELPERS
# =====================================

def current_time():

    return datetime.now(
        timezone.utc
    ).isoformat()


def valid_phone(value):

    return bool(
        re.fullmatch(
            r"[0-9]{10}",
            str(value)
        )
    )


def valid_price(value):

    try:

        number = float(
            str(value)
            .replace(",", "")
        )

        return (
            math.isfinite(number)
            and 0 < number <= 100000000
        )

    except (
        TypeError,
        ValueError
    ):

        return False


def valid_battery(value):

    if not value:
        return True

    return (
        isinstance(value, str)
        and value.isdigit()
        and 1 <= int(value) <= 100
    )


def find_listing(listing_id):

    return next(
        (
            phone
            for phone in load_listings()
            if str(
                phone.get("id")
            ) == str(listing_id)
        ),
        None
    )


def is_seller_owner(item):

    return bool(
        session.get("user_id")
        and item.get("seller_id")
        == session["user_id"]
    )


def is_order_seller(order):

    return bool(
        session.get("user_id")
        and order.get("seller_id")
        == session["user_id"]
    )


def login_required():

    return bool(
        session.get("user_id")
    )


# =====================================
# COURIER TRACKING URL
# =====================================

def get_tracking_url(
    courier_name,
    tracking_number
):

    if (
        not courier_name
        or not tracking_number
    ):

        return None

    courier = re.sub(
        r"\s+",
        " ",
        str(courier_name).strip().lower()
    )

    tracking = str(
        tracking_number
    ).strip()

    clean_tracking = re.sub(
        r"[\s-]+",
        "",
        tracking
    )

    tracking_urls = {

        # DTDC
        "dtdc":
            (
                "https://www.dtdc.in/"
                "tracking/tracking_results.asp"
                "?Ttype=awb_no"
                f"&strCnno={clean_tracking}"
            ),

        # Delhivery
        "delhivery":
            (
                "https://www.delhivery.com/"
                f"track/package/{clean_tracking}"
            ),

        # Blue Dart
        "blue dart":
            "https://www.bluedart.com/tracking",

        "bluedart":
            "https://www.bluedart.com/tracking",

        "blue dart express":
            "https://www.bluedart.com/tracking",

        # India Post
        "india post":
            (
                "https://www.indiapost.gov.in/"
                "_layouts/15/"
                "dop.portal.tracking/"
                "trackconsignment.aspx"
            ),

        "indiapost":
            (
                "https://www.indiapost.gov.in/"
                "_layouts/15/"
                "dop.portal.tracking/"
                "trackconsignment.aspx"
            ),

        # Ekart
        "ekart":
            (
                "https://ekartlogistics.com/"
                f"track/{clean_tracking}"
            ),

        "ekart logistics":
            (
                "https://ekartlogistics.com/"
                f"track/{clean_tracking}"
            ),

        # XpressBees
        "xpressbees":
            (
                "https://www.xpressbees.com/"
                "shipment/tracking"
                f"?awb={clean_tracking}"
            ),

        "xpress bees":
            (
                "https://www.xpressbees.com/"
                "shipment/tracking"
                f"?awb={clean_tracking}"
            ),

        # Ecom Express
        "ecom express":
            (
                "https://www.ecomexpress.in/"
                "tracking/"
                f"?awb_field={clean_tracking}"
            ),

        "ecomexpress":
            (
                "https://www.ecomexpress.in/"
                "tracking/"
                f"?awb_field={clean_tracking}"
            ),

        # Professional Couriers
        "professional couriers":
            "https://www.tpcindia.com/track.aspx",

        "professional courier":
            "https://www.tpcindia.com/track.aspx"
    }

    if courier in tracking_urls:

        return tracking_urls[courier]

    search_text = quote_plus(
        f"{courier_name} tracking {tracking_number}"
    )

    return (
        "https://www.google.com/search?q="
        + search_text
    )


# =====================================
# ADMIN
# =====================================

def is_admin():

    user_id = session.get(
        "user_id"
    )

    if not user_id:
        return False

    if not session.get(
        "admin_authenticated",
        False
    ):
        return False

    if not ADMIN_EMAIL:
        return False

    user = next(
        (
            item
            for item in load_users()
            if item.get("id")
            == user_id
        ),
        None
    )

    if user is None:
        return False

    return (
        str(
            user.get(
                "email",
                ""
            )
        )
        .strip()
        .lower()
        == ADMIN_EMAIL
    )


# =====================================
# LISTING APPROVAL
# =====================================

def listing_status(phone):

    return str(
        phone.get(
            "approval_status",
            "approved"
        )
    ).strip().lower()


def is_public_listing(phone):

    return (
        listing_status(phone)
        == "approved"
    )


def public_listings():

    return [
        phone
        for phone in load_listings()
        if is_public_listing(phone)
    ]


def can_view_listing(phone):

    return (
        is_public_listing(phone)
        or is_seller_owner(phone)
        or is_admin()
    )


# =====================================
# IMAGE HELPERS
# =====================================

def image_matches_extension(photo):

    name = secure_filename(
        photo.filename or ""
    )

    if "." not in name:
        return False

    extension = name.rsplit(
        ".",
        1
    )[1].lower()

    if extension not in ALLOWED_EXTENSIONS:
        return False

    position = photo.stream.tell()

    header = photo.stream.read(16)

    photo.stream.seek(position)

    if extension in (
        "jpg",
        "jpeg"
    ):

        return header.startswith(
            b"\xff\xd8\xff"
        )

    if extension == "png":

        return header.startswith(
            b"\x89PNG\r\n\x1a\n"
        )

    if extension == "webp":

        return (
            header.startswith(
                b"RIFF"
            )
            and header[8:12]
            == b"WEBP"
        )

    return False


def delete_uploaded_images(images):

    for image in images:

        if not isinstance(
            image,
            str
        ):
            continue

        if not image.startswith(
            "/static/uploads/"
        ):
            continue

        filename = os.path.basename(
            image
        )

        path = os.path.join(
            UPLOAD_FOLDER,
            filename
        )

        try:

            if os.path.isfile(path):
                os.remove(path)

        except OSError:

            pass


# =====================================
# HOME
# =====================================

@app.route("/")
def home():

    return render_template(
        "index.html",
        phones=
            public_listings()[:8]
    )


@app.route("/product")
def product():

    return render_template(
        "product.html"
    )


@app.route("/products")
def products():

    return render_template(
        "products.html",
        phones=
            public_listings()
    )


@app.route(
    "/products/<listing_id>"
)
def listing_detail(
    listing_id
):

    phone = find_listing(
        listing_id
    )

    if phone is None:
        abort(404)

    if not can_view_listing(
        phone
    ):
        abort(404)

    return render_template(
        "product_detail.html",
        phone=phone
    )


# =====================================
# BUY / CHECKOUT
# =====================================

@app.route(
    "/buy/<listing_id>",
    methods=["GET", "POST"]
)
def buy_now(listing_id):

    if not login_required():

        return redirect(
            url_for("login")
        )

    phone = find_listing(
        listing_id
    )

    if (
        phone is None
        or not is_public_listing(phone)
    ):

        return (
            "This phone is not available.",
            404
        )

    if is_seller_owner(phone):

        return (
            "You cannot buy your own listing.",
            403
        )

    if request.method == "GET":

        token = str(
            uuid.uuid4()
        )

        session[
            "checkout_token"
        ] = token

        session[
            "checkout_listing_id"
        ] = str(listing_id)

        return render_template(
            "checkout.html",
            phone=phone
        )

    buyer_name = request.form.get(
        "buyer_name",
        ""
    ).strip()

    buyer_phone = request.form.get(
        "buyer_phone",
        ""
    ).strip()

    if not 2 <= len(
        buyer_name
    ) <= 100:

        return (
            "Enter a valid full name.",
            400
        )

    if not valid_phone(
        buyer_phone
    ):

        return (
            "Enter a valid 10-digit phone number.",
            400
        )

    delivery_method = request.form.get(
        "delivery_method",
        ""
    ).strip().lower()

    if delivery_method not in (
        "pickup",
        "shipping"
    ):

        return (
            "Invalid delivery method.",
            400
        )

    if delivery_method == "shipping":

        address = request.form.get(
            "address",
            ""
        ).strip()

        pincode = request.form.get(
            "pincode",
            ""
        ).strip()

        if not 10 <= len(
            address
        ) <= 500:

            return (
                "Enter a complete delivery address.",
                400
            )

        if not re.fullmatch(
            r"[0-9]{6}",
            pincode
        ):

            return (
                "Enter a valid 6-digit PIN code.",
                400
            )

        delivery_charge = None

    else:

        address = "Local Pickup"

        pincode = ""

        delivery_charge = 0

    checkout_token = session.get(
        "checkout_token"
    )

    if (
        not checkout_token
        or session.get(
            "checkout_listing_id"
        ) != str(listing_id)
    ):

        return (
            "Checkout expired or already submitted. "
            "Open Buy Now again.",
            409
        )

    with data_lock:

        all_orders = load_orders()

        existing_order = next(
            (
                item
                for item in all_orders
                if item.get(
                    "checkout_token"
                ) == checkout_token
                and item.get(
                    "buyer_id"
                ) == session["user_id"]
            ),
            None
        )

        if existing_order:

            return redirect(
                url_for(
                    "order_confirmation",
                    order_id=
                        existing_order[
                            "order_id"
                        ]
                )
            )

        phone = find_listing(
            listing_id
        )

        if (
            phone is None
            or not is_public_listing(phone)
        ):

            return (
                "This listing is no longer available.",
                404
            )

        if is_seller_owner(phone):

            return (
                "You cannot buy your own listing.",
                403
            )

        order = {

            "order_id":
                str(uuid.uuid4()),

            "checkout_token":
                checkout_token,

            "product_id":
                phone.get("id", ""),

            "brand":
                phone.get("brand", ""),

            "model":
                phone.get("model", ""),

            "price":
                phone.get("price", "0"),

            "storage":
                phone.get("storage", ""),

            "buyer_id":
                session["user_id"],

            "buyer_name":
                buyer_name,

            "buyer_phone":
                buyer_phone,

            "seller_id":
                phone.get(
                    "seller_id",
                    ""
                ),

            "seller_name":
                phone.get(
                    "seller_name",
                    ""
                ),

            "seller_phone":
                phone.get(
                    "seller_phone"
                )
                or phone.get(
                    "phone",
                    ""
                ),

            "delivery_method":
                delivery_method,

            "address":
                address,

            "pincode":
                pincode,

            "delivery_charge":
                delivery_charge,

            "courier_name":
                "",

            "tracking_number":
                "",

            "tracking_url":
                None,

            "status":
                "Pending",

            "payment_status":
                "Not collected",

            "created_at":
                current_time()
        }

        all_orders.insert(
            0,
            order
        )

        try:

            save_orders(
                all_orders
            )

        except OSError:

            return (
                "Could not save order. Please try again.",
                500
            )

    session.pop(
        "checkout_token",
        None
    )

    session.pop(
        "checkout_listing_id",
        None
    )

    try:

        create_notification(
            order.get("seller_id"),
            "New Order Received",
            (
                order.get(
                    "buyer_name",
                    "A buyer"
                )
                + " placed an order for "
                + order.get(
                    "brand",
                    ""
                )
                + " "
                + order.get(
                    "model",
                    ""
                )
                + "."
            ),
            order.get(
                "product_id"
            )
        )

    except OSError:

        app.logger.exception(
            "Could not create new order notification"
        )

    return redirect(
        url_for(
            "order_confirmation",
            order_id=
                order["order_id"]
        )
    )


# =====================================
# ORDER SUCCESS
# =====================================

@app.route(
    "/order-success/<order_id>"
)
def order_confirmation(
    order_id
):

    if not login_required():

        return redirect(
            url_for("login")
        )

    order = next(
        (
            item
            for item in load_orders()
            if item.get(
                "order_id"
            ) == order_id
            and item.get(
                "buyer_id"
            ) == session["user_id"]
        ),
        None
    )

    if order is None:

        return (
            "Order not found",
            404
        )

    phone = find_listing(
        order.get(
            "product_id"
        )
    )

    if phone is None:

        phone = {

            "brand":
                order.get(
                    "brand",
                    ""
                ),

            "model":
                order.get(
                    "model",
                    ""
                ),

            "price":
                order.get(
                    "price",
                    "0"
                ),

            "storage":
                order.get(
                    "storage",
                    ""
                ),

            "seller_name":
                order.get(
                    "seller_name",
                    ""
                )
        }

    return render_template(
        "order_success.html",
        phone=phone,
        buyer_name=
            order.get(
                "buyer_name",
                "Customer"
            ),
        order=order,
        order_id=
            order["order_id"],
        delivery_method=
            order.get(
                "delivery_method"
            )
    )


# =====================================
# BUYER ORDERS
# =====================================

@app.route("/orders")
def orders():

    if not login_required():

        return redirect(
            url_for("login")
        )

    my_orders = [
        order
        for order in load_orders()
        if order.get(
            "buyer_id"
        ) == session["user_id"]
    ]

    for order in my_orders:

        order["tracking_url"] = (
            get_tracking_url(
                order.get(
                    "courier_name"
                ),
                order.get(
                    "tracking_number"
                )
            )
        )

    return render_template(
        "orders.html",
        orders=my_orders
    )


# =====================================
# SELLER SALES
# =====================================

@app.route("/sales")
def sales():

    if not login_required():

        return redirect(
            url_for("login")
        )

    my_sales = [
        order
        for order in load_orders()
        if order.get(
            "seller_id"
        ) == session["user_id"]
    ]

    for order in my_sales:

        order["tracking_url"] = (
            get_tracking_url(
                order.get(
                    "courier_name"
                ),
                order.get(
                    "tracking_number"
                )
            )
        )

    return render_template(
        "sales.html",
        sales=my_sales
    )


# =====================================
# SELLER DASHBOARD
# =====================================

@app.route(
    "/seller-dashboard"
)
def seller_dashboard():

    if not login_required():

        return redirect(
            url_for("login")
        )

    user_id = session[
        "user_id"
    ]

    my_listings = [
        phone
        for phone in load_listings()
        if phone.get(
            "seller_id"
        ) == user_id
    ]

    my_sales = [
        order
        for order in load_orders()
        if order.get(
            "seller_id"
        ) == user_id
    ]

    total_sales = 0

    for order in my_sales:

        try:

            price = float(
                str(
                    order.get(
                        "price",
                        "0"
                    )
                ).replace(
                    ",",
                    ""
                )
            )

            if (
                math.isfinite(price)
                and price > 0
            ):

                total_sales += price

        except (
            ValueError,
            TypeError
        ):

            pass

    pending_orders = sum(
        1
        for order in my_sales
        if str(
            order.get(
                "status",
                ""
            )
        ).lower()
        == "pending"
    )

    return render_template(
        "seller_dashboard.html",

        total_sales=int(
            total_sales
        ),

        total_listings=len(
            my_listings
        ),

        total_orders=len(
            my_sales
        ),

        pending_orders=
            pending_orders,

        my_listings=
            my_listings,

        my_sales=
            my_sales
    )


# =====================================
# UPDATE ORDER STATUS
# =====================================

@app.route(
    "/update-order-status/<order_id>",
    methods=["POST"]
)
def update_order_status(
    order_id
):

    if not login_required():

        return redirect(
            url_for("login")
        )

    new_status = request.form.get(
        "status",
        ""
    ).strip()

    allowed_statuses = {
        "Pending",
        "Confirmed",
        "Shipped",
        "Delivered",
        "Cancelled"
    }

    if new_status not in allowed_statuses:

        return (
            "Invalid order status.",
            400
        )

    with data_lock:

        all_orders = load_orders()

        order = next(
            (
                item
                for item in all_orders
                if item.get(
                    "order_id"
                ) == order_id
            ),
            None
        )

        if order is None:

            return (
                "Order not found",
                404
            )

        if not is_order_seller(
            order
        ):

            return (
                "You cannot update this order.",
                403
            )

        old_status = order.get(
            "status",
            "Pending"
        )

        if (
            new_status == "Shipped"
            and order.get(
                "delivery_method"
            ) == "pickup"
        ):

            return (
                "Local pickup orders cannot be marked Shipped.",
                400
            )

        if (
            old_status
            in (
                "Delivered",
                "Cancelled"
            )
            and new_status
            != old_status
        ):

            return (
                "Completed or cancelled orders cannot be changed.",
                400
            )

        if (
            new_status == "Shipped"
            and order.get(
                "delivery_method"
            ) == "shipping"
        ):

            if (
                not order.get(
                    "courier_name"
                )
                or not order.get(
                    "tracking_number"
                )
            ):

                return (
                    "Add courier name and tracking number before marking the order as Shipped.",
                    400
                )

        order["status"] = new_status

        order["updated_at"] = (
            current_time()
        )

        save_orders(
            all_orders
        )

        buyer_id = order.get(
            "buyer_id"
        )

        product_name = (
            str(
                order.get(
                    "brand",
                    ""
                )
            )
            + " "
            + str(
                order.get(
                    "model",
                    ""
                )
            )
        ).strip()

        notification_data = {

            "Pending": (
                "📦 Order Pending",
                (
                    f"Your order for "
                    f"{product_name} is waiting "
                    f"for seller confirmation."
                )
            ),

            "Confirmed": (
                "✅ Order Confirmed",
                (
                    f"Your order for "
                    f"{product_name} has been "
                    f"confirmed by the seller."
                )
            ),

            "Shipped": (
                "🚚 Order Shipped",
                (
                    f"Your {product_name} "
                    f"order has been shipped."
                )
            ),

            "Delivered": (
                "🎉 Order Delivered",
                (
                    f"Your {product_name} "
                    f"order has been marked "
                    f"as delivered."
                )
            ),

            "Cancelled": (
                "❌ Order Cancelled",
                (
                    f"Your order for "
                    f"{product_name} "
                    f"has been cancelled."
                )
            )
        }

        title, message = (
            notification_data.get(
                new_status,
                (
                    "Order Updated",
                    (
                        f"Your order status "
                        f"is now {new_status}."
                    )
                )
            )
        )

        if (
            buyer_id
            and new_status != old_status
        ):

            try:

                create_notification(
                    buyer_id,
                    title,
                    message,
                    order.get(
                        "product_id"
                    )
                )

            except OSError:

                app.logger.exception(
                    "Could not create order status notification"
                )

    return redirect(
        url_for("sales")
    )


# =====================================
# UPDATE DELIVERY
# =====================================

@app.route(
    "/update-delivery/<order_id>",
    methods=["POST"]
)
def update_delivery(
    order_id
):

    if not login_required():

        return redirect(
            url_for("login")
        )

    courier_name = request.form.get(
        "courier_name",
        ""
    ).strip()

    tracking_number = request.form.get(
        "tracking_number",
        ""
    ).strip()

    charge_text = request.form.get(
        "delivery_charge",
        ""
    ).strip()

    if (
        len(courier_name) > 100
        or len(tracking_number) > 100
    ):

        return (
            "Courier details are too long.",
            400
        )

    if not courier_name:

        return (
            "Enter courier name.",
            400
        )

    if not tracking_number:

        return (
            "Enter tracking number.",
            400
        )

    charge = None

    if charge_text:

        try:

            charge = float(
                charge_text
            )

            if not (
                math.isfinite(
                    charge
                )
                and 0 <= charge <= 100000
            ):

                raise ValueError

        except ValueError:

            return (
                "Invalid delivery charge.",
                400
            )

    with data_lock:

        all_orders = load_orders()

        order = next(
            (
                item
                for item in all_orders
                if item.get(
                    "order_id"
                ) == order_id
            ),
            None
        )

        if order is None:

            return (
                "Order not found",
                404
            )

        if not is_order_seller(
            order
        ):

            return (
                "Access denied.",
                403
            )

        if order.get(
            "delivery_method"
        ) != "shipping":

            return (
                "Tracking is only available for home delivery.",
                400
            )

        if order.get(
            "status"
        ) in (
            "Delivered",
            "Cancelled"
        ):

            return (
                "Delivery details cannot be changed after completion or cancellation.",
                400
            )

        old_courier = order.get(
            "courier_name",
            ""
        )

        old_tracking = order.get(
            "tracking_number",
            ""
        )

        old_charge = order.get(
            "delivery_charge"
        )

        if charge is not None:

            order[
                "delivery_charge"
            ] = round(
                charge,
                2
            )

        order[
            "courier_name"
        ] = courier_name

        order[
            "tracking_number"
        ] = tracking_number

        order[
            "tracking_url"
        ] = get_tracking_url(
            courier_name,
            tracking_number
        )

        order[
            "updated_at"
        ] = current_time()

        save_orders(
            all_orders
        )

        buyer_id = order.get(
            "buyer_id"
        )

        product_name = (
            str(
                order.get(
                    "brand",
                    ""
                )
            )
            + " "
            + str(
                order.get(
                    "model",
                    ""
                )
            )
        ).strip()

        tracking_changed = (
            old_courier != courier_name
            or old_tracking != tracking_number
            or old_charge != order.get(
                "delivery_charge"
            )
        )

        if (
            buyer_id
            and tracking_changed
        ):

            message_parts = [
                (
                    "Delivery details updated "
                    f"for your {product_name} order."
                )
            ]

            if courier_name:

                message_parts.append(
                    f"Courier: {courier_name}."
                )

            if tracking_number:

                message_parts.append(
                    f"Tracking: {tracking_number}."
                )

            if order.get(
                "delivery_charge"
            ) is not None:

                message_parts.append(
                    "Delivery charge: ₹"
                    + str(
                        order.get(
                            "delivery_charge"
                        )
                    )
                    + "."
                )

            try:

                create_notification(
                    buyer_id,
                    "🚚 Delivery Details Updated",
                    " ".join(
                        message_parts
                    ),
                    order.get(
                        "product_id"
                    )
                )

            except OSError:

                app.logger.exception(
                    "Could not create delivery notification"
                )

    return redirect(
        url_for("sales")
    )


# =====================================
# SELL PHONE
# =====================================

@app.route(
    "/sell",
    methods=["GET", "POST"]
)
def sell():

    if request.method == "GET":

        return render_template(
            "sell.html"
        )

    if not login_required():

        return redirect(
            url_for("login")
        )

    fields = (
        "brand",
        "model",
        "price",
        "storage",
        "battery",
        "condition",
        "description",
        "seller_name",
        "seller_phone"
    ) + CONDITION_FIELDS

    values = {
        field:
            request.form.get(
                field,
                ""
            ).strip()
        for field in fields
    }

    required = (
        "brand",
        "model",
        "price",
        "storage",
        "condition",
        "description",
        "seller_name",
        "seller_phone"
    ) + CONDITION_FIELDS

    if any(
        not values[field]
        for field in required
    ):

        return (
            "Please fill all required fields.",
            400
        )

    if (
        len(values["model"]) > 80
        or len(values["seller_name"]) > 80
    ):

        return (
            "Model or seller name is too long.",
            400
        )

    if len(
        values["description"]
    ) > 1000:

        return (
            "Description is too long.",
            400
        )

    if not valid_price(
        values["price"]
    ):

        return (
            "Please enter a valid price.",
            400
        )

    if not valid_battery(
        values["battery"]
    ):

        return (
            "Battery health must be between 1 and 100.",
            400
        )

    if not valid_phone(
        values["seller_phone"]
    ):

        return (
            "Enter a valid 10-digit seller phone number.",
            400
        )

    listing_type = request.form.get(
        "listing_type",
        "free"
    ).strip()

    if listing_type != "free":

        return (
            "Paid listings are not available yet. Choose Free Listing.",
            400
        )

    photos = [
        photo
        for photo in request.files.getlist(
            "photos"
        )
        if photo
        and photo.filename
    ]

    if not 1 <= len(
        photos
    ) <= 10:

        return (
            "Upload between 1 and 10 photos.",
            400
        )

    for photo in photos:

        if not image_matches_extension(
            photo
        ):

            return (
                "Only valid JPG, JPEG, PNG and WEBP images are allowed.",
                400
            )

    saved_images = []

    try:

        for photo in photos:

            extension = secure_filename(
                photo.filename
            ).rsplit(
                ".",
                1
            )[1].lower()

            filename = (
                str(uuid.uuid4())
                + "."
                + extension
            )

            photo.save(
                os.path.join(
                    UPLOAD_FOLDER,
                    filename
                )
            )

            saved_images.append(
                "/static/uploads/"
                + filename
            )

        listing = {

            "id":
                str(uuid.uuid4()),

            **values,

            "listing_type":
                "free",

            "seller_id":
                session["user_id"],

            "images":
                saved_images,

            "approval_status":
                "pending",

            "created_at":
                current_time()
        }

        with data_lock:

            listings = load_listings()

            listings.insert(
                0,
                listing
            )

            save_listings(
                listings
            )

    except OSError:

        delete_uploaded_images(
            saved_images
        )

        return (
            "Could not save listing. Please try again.",
            500
        )

    return redirect(
        url_for("account")
    )


# =====================================
# EDIT LISTING
# =====================================

@app.route(
    "/edit/<listing_id>",
    methods=["GET", "POST"]
)
def edit_listing(
    listing_id
):

    if not login_required():

        return redirect(
            url_for("login")
        )

    if request.method == "GET":

        phone = find_listing(
            listing_id
        )

        if phone is None:

            return (
                "Listing not found",
                404
            )

        if not is_seller_owner(
            phone
        ):

            return (
                "You cannot edit this listing.",
                403
            )

        return render_template(
            "edit_listing.html",
            phone=phone
        )

    editable_fields = (
        "brand",
        "model",
        "price",
        "storage",
        "battery",
        "condition",
        "description"
    ) + CONDITION_FIELDS

    updates = {
        field:
            request.form[field].strip()
        for field in editable_fields
        if field in request.form
    }

    with data_lock:

        listings = load_listings()

        phone = next(
            (
                item
                for item in listings
                if str(
                    item.get("id")
                ) == str(listing_id)
            ),
            None
        )

        if phone is None:

            return (
                "Listing not found",
                404
            )

        if not is_seller_owner(
            phone
        ):

            return (
                "You cannot edit this listing.",
                403
            )

        new_price = updates.get(
            "price",
            phone.get(
                "price",
                "0"
            )
        )

        if not valid_price(
            new_price
        ):

            return (
                "Invalid price.",
                400
            )

        new_battery = updates.get(
            "battery",
            str(
                phone.get(
                    "battery",
                    ""
                )
            )
        )

        if not valid_battery(
            new_battery
        ):

            return (
                "Invalid battery health.",
                400
            )

        if any(
            len(value) > 1000
            for value in updates.values()
        ):

            return (
                "Field is too long.",
                400
            )

        if updates:

            phone.update(
                updates
            )

            phone[
                "updated_at"
            ] = current_time()

            phone[
                "approval_status"
            ] = "pending"

        save_listings(
            listings
        )

    return redirect(
        url_for(
            "listing_detail",
            listing_id=
                listing_id
        )
    )


# =====================================
# DELETE LISTING
# =====================================

@app.route(
    "/delete/<listing_id>",
    methods=["POST"]
)
def delete_listing(
    listing_id
):

    if not login_required():

        return redirect(
            url_for("login")
        )

    with data_lock:

        listings = load_listings()

        listing = next(
            (
                item
                for item in listings
                if str(
                    item.get("id")
                ) == str(listing_id)
            ),
            None
        )

        if listing is None:

            return (
                "Listing not found",
                404
            )

        if not is_seller_owner(
            listing
        ):

            return (
                "You cannot delete this listing.",
                403
            )

        has_orders = any(
            str(
                order.get(
                    "product_id"
                )
            ) == str(listing_id)
            for order in load_orders()
        )

        listings = [
            item
            for item in listings
            if str(
                item.get("id")
            ) != str(listing_id)
        ]

        save_listings(
            listings
        )

    if not has_orders:

        images = (
            listing.get("images")
            or listing.get("photos")
            or []
        )

        delete_uploaded_images(
            images
        )

    return redirect(
        url_for("products")
    )


# =====================================
# SIGN UP
# =====================================

@app.route(
    "/signup",
    methods=["GET", "POST"]
)
def signup():

    if request.method == "GET":

        return render_template(
            "signup.html"
        )

    name = request.form.get(
        "name",
        ""
    ).strip()

    email = request.form.get(
        "email",
        ""
    ).strip().lower()

    phone = request.form.get(
        "phone",
        ""
    ).strip()

    password = request.form.get(
        "password",
        ""
    )

    if not name:

        return (
            "Enter your name.",
            400
        )

    if not email or "@" not in email:

        return (
            "Enter a valid email.",
            400
        )

    if not valid_phone(
        phone
    ):

        return (
            "Enter a valid phone number.",
            400
        )

    if len(password) < 6:

        return (
            "Password must have at least 6 characters.",
            400
        )

    with data_lock:

        users = load_users()

        if any(
            user.get("email")
            == email
            for user in users
        ):

            return (
                "Email already registered.",
                400
            )

        user = {

            "id":
                str(uuid.uuid4()),

            "name":
                name,

            "email":
                email,

            "phone":
                phone,

            "password":
                generate_password_hash(
                    password
                )
        }

        users.append(
            user
        )

        save_users(
            users
        )

    return redirect(
        url_for("login")
    )


# =====================================
# LOGIN
# =====================================

@app.route(
    "/login",
    methods=["GET", "POST"]
)
def login():

    if request.method == "GET":

        return render_template(
            "login.html"
        )

    email = request.form.get(
        "email",
        ""
    ).strip().lower()

    password = request.form.get(
        "password",
        ""
    )

    if (
        ADMIN_EMAIL
        and email == ADMIN_EMAIL
    ):

        return redirect(
            url_for("admin_login")
        )

    user = next(
        (
            item
            for item in load_users()
            if item.get("email")
            == email
        ),
        None
    )

    if (
        user is None
        or not check_password_hash(
            user.get(
                "password",
                ""
            ),
            password
        )
    ):

        return (
            "Incorrect email or password.",
            401
        )

    session.clear()

    session[
        "user_id"
    ] = user["id"]

    session[
        "user_name"
    ] = user["name"]

    session[
        "user_email"
    ] = user["email"]

    session[
        "admin_authenticated"
    ] = False

    return redirect(
        url_for("home")
    )


# =====================================
# LOGOUT
# =====================================

@app.route("/logout")
def logout():

    session.clear()

    return redirect(
        url_for("home")
    )


# =====================================
# ACCOUNT
# =====================================

@app.route("/account")
def account():

    if not login_required():

        return redirect(
            url_for("login")
        )

    user_id = session[
        "user_id"
    ]

    user = next(
        (
            item
            for item in load_users()
            if item.get("id")
            == user_id
        ),
        None
    )

    if user is None:

        session.clear()

        return redirect(
            url_for("login")
        )

    my_listings = [
        phone
        for phone in load_listings()
        if phone.get(
            "seller_id"
        ) == user_id
    ]

    return render_template(
        "account.html",
        user=user,
        my_listings=my_listings
    )


# =====================================
# READ NOTIFICATION
# =====================================

@app.route(
    "/notifications/<notification_id>/read",
    methods=["POST"]
)
def read_notification(
    notification_id
):

    if not login_required():

        return redirect(
            url_for("login")
        )

    with data_lock:

        all_notifications = (
            load_notifications()
        )

        notice = next(
            (
                item
                for item in all_notifications
                if item.get("id")
                == notification_id
                and item.get("user_id")
                == session["user_id"]
            ),
            None
        )

        if notice is None:
            abort(404)

        if not notice.get(
            "read",
            False
        ):

            notice["read"] = True

            notice[
                "read_at"
            ] = current_time()

            save_notifications(
                all_notifications
            )

        listing_id = notice.get(
            "listing_id"
        )

    if listing_id:

        phone = find_listing(
            listing_id
        )

        if phone and can_view_listing(
            phone
        ):

            return redirect(
                url_for(
                    "listing_detail",
                    listing_id=
                        listing_id
                )
            )

    return redirect(
        url_for("account")
    )


# =====================================
# ADMIN LOGIN
# =====================================

@app.route(
    "/admin/login",
    methods=["GET", "POST"]
)
def admin_login():

    if request.method == "GET":

        if is_admin():

            return redirect(
                url_for(
                    "admin_dashboard"
                )
            )

        return render_template(
            "admin_login.html"
        )

    if not ADMIN_EMAIL:

        app.logger.error(
            "ADMIN_EMAIL is not configured."
        )

        return render_template(
            "admin_login.html",
            error=
                "Admin login is not configured."
        ), 503

    email = request.form.get(
        "email",
        ""
    ).strip().lower()

    password = request.form.get(
        "password",
        ""
    )

    error = (
        "Invalid admin email or password."
    )

    if email != ADMIN_EMAIL:

        return render_template(
            "admin_login.html",
            error=error
        ), 401

    user = next(
        (
            item
            for item in load_users()
            if str(
                item.get(
                    "email",
                    ""
                )
            ).strip().lower()
            == ADMIN_EMAIL
        ),
        None
    )

    if (
        user is None
        or not check_password_hash(
            user.get(
                "password",
                ""
            ),
            password
        )
    ):

        return render_template(
            "admin_login.html",
            error=error
        ), 401

    session.clear()

    session[
        "user_id"
    ] = user["id"]

    session[
        "user_name"
    ] = user["name"]

    session[
        "user_email"
    ] = user["email"]

    session[
        "admin_authenticated"
    ] = True

    return redirect(
        url_for(
            "admin_dashboard"
        )
    )


# =====================================
# ADMIN LOGOUT
# =====================================

@app.route(
    "/admin/logout",
    methods=["POST"]
)
def admin_logout():

    if not is_admin():

        return redirect(
            url_for("admin_login")
        )

    session.clear()

    return redirect(
        url_for("admin_login")
    )


# =====================================
# ADMIN LISTING STATUS
# =====================================

@app.route(
    "/admin/listing/<listing_id>/status",
    methods=["POST"]
)
def admin_listing_status(
    listing_id
):

    if not is_admin():

        return redirect(
            url_for("admin_login")
        )

    new_status = request.form.get(
        "status",
        ""
    ).strip().lower()

    if new_status not in (
        "approved",
        "pending",
        "hidden"
    ):

        return (
            "Invalid listing status.",
            400
        )

    with data_lock:

        listings = load_listings()

        phone = next(
            (
                item
                for item in listings
                if str(
                    item.get("id")
                ) == str(listing_id)
            ),
            None
        )

        if phone is None:
            abort(404)

        old_status = listing_status(
            phone
        )

        if old_status == new_status:

            return redirect(
                url_for(
                    "admin_dashboard"
                )
            )

        phone[
            "approval_status"
        ] = new_status

        phone[
            "reviewed_at"
        ] = current_time()

        save_listings(
            listings
        )

        seller_id = phone.get(
            "seller_id"
        )

        phone_name = (
            str(
                phone.get(
                    "brand",
                    ""
                )
            )
            + " "
            + str(
                phone.get(
                    "model",
                    ""
                )
            )
        ).strip()

        if new_status == "approved":

            title = (
                "Your phone is Approved!"
            )

            message = (
                phone_name
                + " is now live on iSellora. "
                + "Buyers can view and order it."
            )

        elif new_status == "pending":

            title = (
                "Your phone is Pending"
            )

            message = (
                phone_name
                + " is waiting for admin review. "
                + "It is not publicly visible."
            )

        else:

            title = (
                "Your phone is Hidden"
            )

            message = (
                phone_name
                + " has been hidden by admin. "
                + "Buyers cannot view it."
            )

        if seller_id:

            try:

                create_notification(
                    seller_id,
                    title,
                    message,
                    listing_id
                )

            except OSError:

                app.logger.exception(
                    "Could not save seller notification"
                )

    return redirect(
        url_for(
            "admin_dashboard"
        )
    )


# =====================================
# ADMIN DASHBOARD
# =====================================

@app.route("/admin")
def admin_dashboard():

    if not is_admin():

        return redirect(
            url_for("admin_login")
        )

    all_users = load_users()

    all_listings = load_listings()

    all_orders = load_orders()

    safe_users = [

        {
            "id":
                user.get(
                    "id",
                    ""
                ),

            "name":
                user.get(
                    "name",
                    ""
                ),

            "email":
                user.get(
                    "email",
                    ""
                ),

            "phone":
                user.get(
                    "phone",
                    ""
                )
        }

        for user in all_users
    ]

    safe_orders = [

        {
            "order_id":
                order.get(
                    "order_id",
                    ""
                ),

            "brand":
                order.get(
                    "brand",
                    ""
                ),

            "model":
                order.get(
                    "model",
                    ""
                ),

            "price":
                order.get(
                    "price",
                    "0"
                ),

            "status":
                order.get(
                    "status",
                    "Pending"
                ),

            "payment_status":
                order.get(
                    "payment_status",
                    "Not collected"
                ),

            "created_at":
                order.get(
                    "created_at",
                    ""
                )
        }

        for order in all_orders
    ]

    pending_orders = sum(
        1
        for order in all_orders
        if order.get(
            "status"
        ) == "Pending"
    )

    pending_listings = sum(
        1
        for phone in all_listings
        if listing_status(
            phone
        ) == "pending"
    )

    return render_template(
        "admin.html",

        total_users=
            len(all_users),

        total_listings=
            len(all_listings),

        total_orders=
            len(all_orders),

        pending_orders=
            pending_orders,

        pending_listings=
            pending_listings,

        users=
            safe_users,

        listings=
            all_listings,

        orders=
            safe_orders
    )


# =====================================
# RUN APP
# =====================================


if __name__ == "__main__":
    app.run(
        debug=False,
        host="127.0.0.1",
        port=5000
    )
