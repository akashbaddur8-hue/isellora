# iSellora Smartphone Marketplace

**iSellora** is a peer-to-peer and marketplace platform for certified pre-owned and refurbished smartphones. Built with Python, Flask, SQLAlchemy, and a mobile-responsive Jinja frontend, iSellora combines hardware condition transparency, database-enforced concurrency locks, and an auditable order lifecycle.

---

## 1. Key Architectural Features

- **Relational Data Architecture**: Replaced fragile, unbounded JSON files with SQLAlchemy models (`User`, `Listing`, `ListingImage`, `Order`, `OrderStatusHistory`, `Notification`, `SupportTicket`, `ReportDispute`, `AuditEvent`).
- **Single-Unit Inventory Protection**: Concurrency control prevents two concurrent buyers from purchasing the exact same phone listing. Atomic conditional updates guarantee race condition safety.
- **Server-Side Price Calculation**: Order totals are strictly computed on the server from immutable database price snapshots and verified shipping rules; client-submitted prices are never trusted.
- **Server-Side Catalog Search & Filtering**: Multi-parameter search across brand, model, storage, RAM, condition grade, and price ranges with server-side pagination and sorting.
- **Condition & Damage Disclosure Standard**: Mandatory seller disclosures for screen scratches, body dents, battery health (1-100%), repair history, original display status, and account locks.
- **Order Lifecycle Machine**: Validated transitions (`Pending` → `Confirmed` → `Shipped` → `Delivered` / `Cancelled`) with immutable audit trails.
- **CSRF & Security Hardening**: Global Flask-WTF CSRF protection, password hashing with `scrypt`, credential brute-force rate-limiting, and defensive security headers (`nosniff`, `SAMEORIGIN`, CSP).
- **Legitimate Carrier Tracking**: Dedicated tracking URLs for recognized Indian couriers (DTDC, Delhivery, Blue Dart, India Post, Ekart, XpressBees, Ecom Express, Professional Couriers) without search-engine leakage.
- **Role-Based Admin Console**: Role-protected dashboard with genuine database-calculated metrics, moderation queues (Approve/Reject with feedback), dispute logs, and audit trails.

---

## 2. Payment Gateway Architecture (Phase 2: Razorpay & COD)

iSellora implements a production-grade, secure multi-channel payment architecture supporting both **Razorpay Online Checkout** and **Cash on Delivery (COD)**:

- **Official Razorpay Python SDK & Checkout Integration**:
  - Secure checkout launched via `https://checkout.razorpay.com/v1/checkout.js` with fallback redirection modal.
  - Server-side cryptographic HMAC SHA-256 signature verification (`razorpay.utility.verify_payment_signature`).
  - Authoritative amount calculation strictly performed on the server side in integer paise (`order.total_paise` in INR); client-submitted amounts/currencies are never trusted.
- **Webhook Reconciliation Engine**:
  - Inbound server-to-server webhook endpoint (`POST /payment/webhook`) with HMAC SHA-256 validation via `X-Razorpay-Signature` and `RAZORPAY_WEBHOOK_SECRET`.
  - Durable `WebhookEvent` model providing permanent audit trail and deduplication against repeated gateway retries.
  - Event reconciliation for `order.paid`, `payment.captured`, `payment.failed`, and `refund.processed`.
- **Cash on Delivery (COD) Separation**:
  - COD orders create orders in `Pending` status with `payment_status="Not collected"` without calling Razorpay order creation.
  - Payment status remains strictly decoupled from shipping delivery status; authorized post-delivery collection is recorded via `record_cod_payment_collection`.
- **Verified Priority Placement (₹49 Boost)**:
  - Sellers can boost smartphone listings to the #1 position in catalog search feeds for ₹49 (4900 paise).
  - Direct or unverified activations are blocked; priority placement activates only upon verified Razorpay payment.
- **Inventory Concurrency & Reservation Expiry**:
  - Checkout atomically locks single-unit inventory (`inventory_status="reserved"`).
  - Abandoned reservations automatically expire after 30 minutes via `expire_abandoned_reservations()`, returning unsold devices to `available` status.
- **Authorized Refund Workflows**:
  - Role-protected administrative refund workflow (`POST /admin/payment/<id>/refund`) calling Razorpay refund API with immutable audit tracking.

---

## 3. Technology Stack

- **Backend**: Python 3.11+, Flask 3.1, Flask-SQLAlchemy 3.1, Flask-WTF 1.3, WTForms 3.2, Werkzeug 3.1.
- **Database**: SQLite (local development and automated tests), PostgreSQL (production-ready via `DATABASE_URL`).
- **Frontend**: Responsive Jinja2 templates, Semantic CSS design system with CSS custom properties, vanilla ES6 JavaScript.
- **Testing**: `pytest` with in-memory database fixtures and 100% test pass rate.
- **Production Server**: Gunicorn WSGI.

---

## 4. Local Quickstart Setup

### Step 1: Clone and Set Up Virtual Environment

```bash
git clone <repository_url>
cd iSellora

# Create virtual environment
python -m venv .venv

# Activate virtual environment
# Windows:
.venv\Scripts\activate
# Linux/macOS:
source .venv/bin/activate
```

### Step 2: Install Dependencies

```bash
pip install -r requirements.txt
```

### Step 3: Configure Environment Variables

Copy the example environment template:

```bash
cp .env.example .env
```

Ensure `.env` contains your desired settings:
```env
FLASK_APP=backend/app.py
FLASK_ENV=development
SECRET_KEY=replace-with-a-secure-random-64-character-string
DATABASE_URL=sqlite:///backend/isellora.db
ADMIN_EMAIL=akashbaddur2007@gmail.com
```

### Step 4: Run Legacy Data Migration

To import all legacy JSON records (`users.json`, `listings.json`, `orders.json`, `notifications.json`) into the relational database:

```bash
python backend/migrate_legacy_data.py
```

*Note: The migration script is completely idempotent and safe to run multiple times without duplicating records.*

### Step 5: Start the Development Server

```bash
python backend/app.py
```

Open your browser to: **`http://127.0.0.1:5000`**

---

## 5. Running Automated Tests

Run the full automated test suite using `pytest`:

```bash
pytest tests -v
```

### Test Coverage Highlights (81 Automated Tests Passing — 100%):
- **Razorpay Payment Integration (`test_razorpay_integration.py` - 20 tests)**:
  - Authoritative amount calculation in integer paise (INR).
  - Razorpay order creation and single-unit inventory reservation.
  - HMAC SHA-256 checkout signature verification and order confirmation.
  - Rejection of invalid signatures, order ID mismatches, and unauthorized user verification attempts.
  - Idempotent callback handling without duplicate history or state corruptions.
  - Payment failure tracking and modal dismissal logging preserving retry capability.
  - Independent Cash on Delivery (COD) workflow and authorized post-delivery collection.
  - Webhook HMAC signature verification and rejection of forged webhooks.
  - Asynchronous webhook reconciliation for `order.paid`, `payment.captured`, `payment.failed`, and `refund.processed`.
  - Webhook deduplication and replay protection via `WebhookEvent`.
  - Priority Placement (₹49 / 4900 paise) boost order creation and verified activation.
  - Automatic inventory release on abandoned reservation expiry (30-minute timeout).
  - Authorized administrative refund workflows with full audit logging.
- **Security Audit & Hardening (`test_security_audit.py` - 26 tests)**: Privilege escalation prevention, production configuration validation, order transition enforcement, redirect safety, support ticket ownership, price bounds checking.
- **Priority Placement & Subscriptions (`test_boost_subscription.py` - 5 tests)**: Boost page access control, catalog priority sorting, admin subscription revenue metrics.
- **Orders & Inventory Concurrency (`test_orders.py` - 5 tests)**: Atomic claim testing confirming concurrent attempts to buy the same single-unit phone are blocked, duplicate submission idempotency, status transitions, and cancellation inventory release.
- **Authentication & Authorization (`test_auth.py`, `test_authorization.py` - 14 tests)**: Registration, password complexity, brute-force rate-limiting, private order shielding, cross-seller listing protection, and admin isolation.
- **Listings, Reviews, Wishlist & Features (`test_listings.py`, `test_features.py`, `test_csrf.py`, `test_migration.py` - 11 tests)**.

---

## 6. Database Backup & Restoration

### Backup Procedure (SQLite)

```bash
# Automated copy of the database and media assets
mkdir -p backups/$(date +%Y%m%d)
cp backend/isellora.db backups/$(date +%Y%m%d)/
cp -r backend/static/uploads backups/$(date +%Y%m%d)/
```

### Backup Procedure (Production PostgreSQL)

```bash
pg_dump -U username -h localhost -d isellora_db -F c -b -v -f "backups/isellora_$(date +%Y%m%d).dump"
```

### Restoration Procedure (PostgreSQL)

```bash
pg_restore -U username -h localhost -d isellora_db -v "backups/isellora_backup.dump"
```

---

## 7. Production Deployment Guide

1. **WSGI Server**: Run via Gunicorn:
   ```bash
   gunicorn -w 4 -b 0.0.0.0:8000 --access-logfile - --error-logfile - "backend.app:app"
   ```
2. **Reverse Proxy (Nginx)**: Configure Nginx with TLS termination (HTTPS), HTTP/2, client upload body limits (`client_max_body_size 50M`), and static file caching for `/static/`.
3. **Environment Secrets**: Provide a strong, cryptographically generated `SECRET_KEY` (64+ characters) and ensure `SESSION_COOKIE_SECURE=True` is enabled in production.
4. **Health Check Endpoint**:
   - URL: `GET /health`
   - Response:
     ```json
     {
       "database": "healthy",
       "service": "iSellora Marketplace",
       "status": "healthy",
       "timestamp": "2026-10-13T00:23:00.000000+00:00"
     }
     ```
     *Returns HTTP 200 when healthy, or HTTP 503 if database connectivity is degraded. No sensitive secrets or credentials are leaked.*

---

## 8. Default Credentials (From Legacy Migration)

- **Admin Account**: `akashbaddur2007@gmail.com`
- **Buyer Test Account**: `alokbaddur2007@gmail.com`
- **Admin Portal**: Accessible at `GET /admin/login`
