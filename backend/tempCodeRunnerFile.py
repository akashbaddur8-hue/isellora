from flask import Flask, render_template, request, redirect, url_for, session
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
import os
import uuid
import json


# =========================
# FLASK APP
# =========================

app = Flask(__name__, template_folder="templates")

# Session security key
app.secret_key = "isellora-development-secret-key"


# =========================
# SETTINGS
# =========================

UPLOAD_FOLDER = os.path.join("static", "uploads")

LISTINGS_FILE = "listings.json"
USERS_FILE = "users.json"

ALLOWED_EXTENSIONS = {
    "png",
    "jpg",
    "jpeg",
    "webp"
}

app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER

# Maximum request size: 50 MB
app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024

os.makedirs(
    UPLOAD_FOLDER,
    exist_ok=True
)


# =========================
# FILE HELPERS
# =========================

def allowed_file(filename):

    return (
        "." in filename
        and filename.rsplit(
            ".",
            1
        )[1].lower() in ALLOWED_EXTENSIONS
    )


def load_listings():

    if not os.path.exists(LISTINGS_FILE):
        return []

    try:

        with open(
            LISTINGS_FILE,
            "r",
            encoding="utf-8"
        ) as file:

            return json.load(file)

    except (
        json.JSONDecodeError,
        OSError
    ):

        return []


def save_listings(listings):

    with open(
        LISTINGS_FILE,
        "w",
        encoding="utf-8"
    ) as file:

        json.dump(
            listings,
            file,
            indent=4,
            ensure_ascii=False
        )


def load_users():

    if not os.path.exists(USERS_FILE):
        return []

    try:

        with open(
            USERS_FILE,
            "r",
            encoding="utf-8"
        ) as file:

            return json.load(file)

    except (
        json.JSONDecodeError,
        OSError
    ):

        return []


def save_users(users):

    with open(
        USERS_FILE,
        "w",
        encoding="utf-8"
    ) as file:

        json.dump(
            users,
            file,
            indent=4,
            ensure_ascii=False
        )


# =========================
# HOME
# =========================

@app.route("/")
def home():

    return render_template(
        "index.html"
    )


# =========================
# STATIC PRODUCT
# =========================

@app.route("/product")
def product():

    return render_template(
        "product.html"
    )


# =========================
# PRODUCTS
# =========================

@app.route("/products")
def products():

    listings = load_listings()

    return render_template(
        "products.html",
        phones=listings
    )


# =========================
# LISTING DETAILS
# =========================

@app.route("/products/<listing_id>")
def listing_detail(listing_id):

    listings = load_listings()

    phone = next(
        (
            item
            for item in listings
            if item.get("id") == listing_id
        ),
        None
    )

    if phone is None:

        return (
            "Listing not found",
            404
        )

    return render_template(
        "listing_detail.html",
        phone=phone
    )


# =========================
# SELL PHONE
# =========================

@app.route(
    "/sell",
    methods=["GET", "POST"]
)
def sell():

    if request.method == "POST":

        brand = request.form.get(
            "brand",
            ""
        ).strip()

        model = request.form.get(
            "model",
            ""
        ).strip()

        price = request.form.get(
            "price",
            ""
        ).strip()

        storage = request.form.get(
            "storage",
            ""
        ).strip()

        battery = request.form.get(
            "battery",
            ""
        ).strip()

        condition = request.form.get(
            "condition",
            ""
        ).strip()

        description = request.form.get(
            "description",
            ""
        ).strip()

        listing_type = request.form.get(
            "listing_type",
            "free"
        ).strip()

        seller_name = request.form.get(
            "seller_name",
            ""
        ).strip()

        seller_phone = request.form.get(
            "seller_phone",
            ""
        ).strip()

        photos = request.files.getlist(
            "photos"
        )

        # -------------------------
        # VALIDATION
        # -------------------------

        if not brand:

            return (
                "Please select phone brand.",
                400
            )

        if not model:

            return (
                "Please enter phone model.",
                400
            )

        if not price:

            return (
                "Please enter price.",
                400
            )

        if not storage:

            return (
                "Please select storage.",
                400
            )

        if not battery:

            return (
                "Please enter battery health.",
                400
            )

        if not condition:

            return (
                "Please select condition.",
                400
            )

        if not seller_name:

            return (
                "Please enter seller name.",
                400
            )

        if (
            not seller_phone.isdigit()
            or len(seller_phone) != 10
        ):

            return (
                "Please enter a valid 10-digit phone number.",
                400
            )

        # -------------------------
        # PHOTO VALIDATION
        # -------------------------

        valid_photos = [
            photo
            for photo in photos
            if photo and photo.filename
        ]

        if not valid_photos:

            return (
                "Please upload at least one photo.",
                400
            )

        if len(valid_photos) > 10:

            return (
                "Maximum 10 photos allowed.",
                400
            )

        saved_images = []

        # -------------------------
        # SAVE PHOTOS
        # -------------------------

        for photo in valid_photos:

            if not allowed_file(
                photo.filename
            ):

                return (
                    "Only JPG, JPEG, PNG and WEBP images are allowed.",
                    400
                )

            extension = (
                photo.filename
                .rsplit(
                    ".",
                    1
                )[1]
                .lower()
            )

            unique_name = (
                str(uuid.uuid4())
                + "."
                + extension
            )

            filename = secure_filename(
                unique_name
            )

            file_path = os.path.join(
                app.config["UPLOAD_FOLDER"],
                filename
            )

            photo.save(
                file_path
            )

            saved_images.append(
                "/static/uploads/"
                + filename
            )

        # -------------------------
        # CREATE LISTING
        # -------------------------

        listing = {

            "id": str(
                uuid.uuid4()
            ),

            "brand": brand,

            "model": model,

            "price": price,

            "storage": storage,

            "battery": battery,

            "condition": condition,

            "description": description,

            "listing_type": listing_type,

            "seller_name": seller_name,

            "seller_phone": seller_phone,

            "images": saved_images,

            "created_at": ""
        }

        # Connect listing with logged-in user
        if session.get("user_id"):

            listing["seller_id"] = session.get(
                "user_id"
            )

        listings = load_listings()

        listings.insert(
            0,
            listing
        )

        save_listings(
            listings
        )

        return redirect(
            url_for("products")
        )

    return render_template(
        "sell.html"
    )


# =========================
# EDIT LISTING
# =========================

@app.route(
    "/edit/<listing_id>",
    methods=["GET", "POST"]
)
def edit_listing(listing_id):

    listings = load_listings()

    phone = next(
        (
            item
            for item in listings
            if item.get("id") == listing_id
        ),
        None
    )

    if phone is None:

        return (
            "Listing not found",
            404
        )

    # -------------------------
    # UPDATE
    # -------------------------

    if request.method == "POST":

        phone["brand"] = request.form.get(
            "brand",
            phone.get(
                "brand",
                ""
            )
        ).strip()

        phone["model"] = request.form.get(
            "model",
            phone.get(
                "model",
                ""
            )
        ).strip()

        phone["price"] = request.form.get(
            "price",
            phone.get(
                "price",
                ""
            )
        ).strip()

        phone["storage"] = request.form.get(
            "storage",
            phone.get(
                "storage",
                ""
            )
        ).strip()

        phone["battery"] = request.form.get(
            "battery",
            phone.get(
                "battery",
                ""
            )
        ).strip()

        phone["condition"] = request.form.get(
            "condition",
            phone.get(
                "condition",
                ""
            )
        ).strip()

        phone["description"] = request.form.get(
            "description",
            phone.get(
                "description",
                ""
            )
        ).strip()

        save_listings(
            listings
        )

        return redirect(
            url_for(
                "listing_detail",
                listing_id=listing_id
            )
        )

    return render_template(
        "edit_listing.html",
        phone=phone
    )


# =========================
# DELETE LISTING
# =========================

@app.route(
    "/delete/<listing_id>",
    methods=["POST"]
)
def delete_listing(listing_id):

    listings = load_listings()

    listing = next(
        (
            item
            for item in listings
            if item.get("id") == listing_id
        ),
        None
    )

    if listing is None:

        return (
            "Listing not found",
            404
        )

    # -------------------------
    # DELETE IMAGES
    # -------------------------

    for image in listing.get(
        "images",
        []
    ):

        if image.startswith(
            "/static/uploads/"
        ):

            filename = image.split(
                "/static/uploads/",
                1
            )[1]

            file_path = os.path.join(
                app.config["UPLOAD_FOLDER"],
                filename
            )

            if os.path.exists(
                file_path
            ):

                try:

                    os.remove(
                        file_path
                    )

                except OSError:

                    pass

    # -------------------------
    # REMOVE LISTING
    # -------------------------

    listings = [
        item
        for item in listings
        if item.get("id")
        != listing_id
    ]

    save_listings(
        listings
    )

    return redirect(
        url_for("products")
    )


# =========================
# SIGN UP
# =========================

@app.route(
    "/signup",
    methods=["GET", "POST"]
)
def signup():

    if request.method == "POST":

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

        # -------------------------
        # VALIDATION
        # -------------------------

        if not name:

            return (
                "Please enter your name.",
                400
            )

        if not email:

            return (
                "Please enter your email.",
                400
            )

        if (
            not phone.isdigit()
            or len(phone) != 10
        ):

            return (
                "Please enter a valid 10-digit phone number.",
                400
            )

        if len(password) < 6:

            return (
                "Password must contain at least 6 characters.",
                400
            )

        users = load_users()

        # -------------------------
        # DUPLICATE EMAIL
        # -------------------------

        existing_user = next(
            (
                user
                for user in users
                if user.get("email")
                == email
            ),
            None
        )

        if existing_user:

            return (
                "An account with this email already exists.",
                400
            )

        # -------------------------
        # CREATE USER
        # -------------------------

        user = {

            "id": str(
                uuid.uuid4()
            ),

            "name": name,

            "email": email,

            "phone": phone,

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

    return render_template(
        "signup.html"
    )


# =========================
# LOGIN
# =========================

@app.route(
    "/login",
    methods=["GET", "POST"]
)
def login():

    if request.method == "POST":

        email = request.form.get(
            "email",
            ""
        ).strip().lower()

        password = request.form.get(
            "password",
            ""
        )

        users = load_users()

        user = next(
            (
                user
                for user in users
                if user.get("email")
                == email
            ),
            None
        )

        # -------------------------
        # USER NOT FOUND
        # -------------------------

        if user is None:

            return """
            <!DOCTYPE html>
            <html>
            <head>
                <title>Login Error</title>
                <meta name="viewport"
                      content="width=device-width,
                      initial-scale=1.0">
            </head>

            <body style="
                font-family: Arial;
                text-align: center;
                padding-top: 80px;
            ">

                <h2>
                    ❌ Email or password is incorrect.
                </h2>

                <p>
                    <a href="/login">
                        Try Again
                    </a>
                </p>

            </body>
            </html>
            """, 401

        # -------------------------
        # PASSWORD CHECK
        # -------------------------

        if not check_password_hash(
            user.get(
                "password",
                ""
            ),
            password
        ):

            return """
            <!DOCTYPE html>
            <html>
            <head>
                <title>Login Error</title>
                <meta name="viewport"
                      content="width=device-width,
                      initial-scale=1.0">
            </head>

            <body style="
                font-family: Arial;
                text-align: center;
                padding-top: 80px;
            ">

                <h2>
                    ❌ Email or password is incorrect.
                </h2>

                <p>
                    <a href="/login">
                        Try Again
                    </a>
                </p>

            </body>
            </html>
            """, 401

        # -------------------------
        # CREATE LOGIN SESSION
        # -------------------------

        session["user_id"] = user.get(
            "id"
        )

        session["user_name"] = user.get(
            "name"
        )

        session["user_email"] = user.get(
            "email"
        )

        return redirect(
            url_for("home")
        )

    return render_template(
        "login.html"
    )


# =========================
# LOGOUT
# =========================

@app.route("/logout")
def logout():

    session.clear()

    return redirect(
        url_for("home")
    )


# =========================
# ACCOUNT
# =========================

@app.route("/account")
def account():

    # Login agilla andre login page ge kalisu
    if not session.get("user_id"):

        return redirect(
            url_for("login")
        )

    # Users load madu
    users = load_users()

    # Current logged-in user find madu
    user = next(
        (
            u
            for u in users
            if u.get("id")
            == session.get("user_id")
        ),
        None
    )

    # User siglilla andre logout madi
    if user is None:

        session.clear()

        return redirect(
            url_for("login")
        )

    # All listings load madu
    listings = load_listings()

    # Current user post madiruva listings matra
    my_listings = [
        phone
        for phone in listings
        if phone.get("seller_id")
        == session.get("user_id")
    ]

    # Account template display madu
    return render_template(
        "account.html",
        user=user,
        my_listings=my_listings
    )


# =========================
# RUN APPLICATION
# =========================

if __name__ == "__main__":

    app.run(
        debug=True,
        host="127.0.0.1",
        port=5000
    )

