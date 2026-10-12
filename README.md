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

## 2. Payment Arrangement Notice (Phase 1)

> **Important**: Online payment gateway integration (Razorpay, Stripe, cards, automated UPI capture, and webhooks) is **strictly deferred** to a future phase.
>
> In this phase:
> - All orders operate under transparent **Direct Settlement / Cash on Delivery (COD)**.
> - Orders are honestly marked as `Not collected` upon checkout.
> - No fake payment success screens or unverified UPI transfer instructions exist.
> - Buyers verify device hardware condition in person upon pickup or delivery, then settle payment directly.

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

### Test Coverage Highlights:
- **Authentication**: Registration, password complexity validation, duplicate rejection, login, rate limiting, and logout session invalidation.
- **Authorization**: Private order shielding, cross-seller listing edit/delete rejection, non-admin admin route prevention, self-purchase blocking.
- **CSRF Protection**: Verification that POST requests without valid CSRF tokens are rejected with HTTP 400.
- **Listings**: Input boundary validation (price, battery), image magic-byte verification, re-moderation on edit, server-side search and filtering.
- **Orders & Inventory Concurrency**: Atomic claim testing confirming concurrent attempts to buy the same single-unit phone are blocked, duplicate submission idempotency, status transitions, and cancellation inventory release.
- **Data Migration**: Verification that legacy JSON records import without loss.

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
