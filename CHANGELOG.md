# iSellora Marketplace Changelog

All notable changes made to the iSellora marketplace during this engineering transformation are documented below.

---

## [2.1.0] - October 2026: Complete Razorpay Integration & Payment Hardening

### Razorpay Online Payments & Checkout
- **Integrated Official Razorpay Python SDK (`razorpay>=2.0.1`)**:
  - Configured `RAZORPAY_KEY_ID`, `RAZORPAY_KEY_SECRET`, and `RAZORPAY_WEBHOOK_SECRET` securely in configuration classes (`Config`, `TestingConfig`, `ProductionConfig`).
  - Strict server-side storage of API credentials; updated `.env.example` with safe test placeholders.
- **Authoritative Server-Side Amount Calculation**:
  - All monetary values calculated strictly in integer paise (`total_paise` in INR) from immutable database pricing and delivery rules.
  - Zero trust in client-submitted prices, fees, currencies, or payment IDs.
- **Interactive Razorpay Checkout Flow**:
  - Implemented responsive checkout modal launcher via `checkout.js` in `backend/templates/checkout.html` and standalone fallback launcher `backend/templates/checkout_razorpay.html`.
  - Concurrency-protected single-unit inventory locking upon checkout reservation.
  - Verification endpoint `POST /payment/verify` validating HMAC SHA-256 signatures (`verify_payment_signature`), updating `Payment` and `Order` states atomically, and logging audit history.
  - Payment failure tracking `POST /payment/failed` recording declined transactions and modal dismissals while preserving the reservation for customer retry.

### Asynchronous Webhook Reconciliation Engine
- **Inbound Server-to-Server Webhook Handler (`POST /payment/webhook`)**:
  - Exempt from CSRF (inbound server webhook) but strictly authenticated via HMAC SHA-256 in `X-Razorpay-Signature` against `RAZORPAY_WEBHOOK_SECRET`.
  - Reconciles events asynchronously: `order.paid`, `payment.captured`, `payment.failed`, `refund.processed`.
  - Deduplication & idempotency: records each `WebhookEvent` by unique `event_id`, returning HTTP 200 without duplicate execution on replay.

### Priority Placement (₹49 Boost) Verified Payment
- **Genuine Razorpay Activation**:
  - Replaced temporary deferral notice with active "Pay ₹49 with Razorpay" checkout workflow in `backend/templates/boost_subscription.html`.
  - Endpoints `POST /seller/boost/<id>/create-order` and `POST /seller/boost/<id>/verify` enforce server-side 4900 paise pricing and genuine signature verification.
  - Boost features listing (`is_featured=True`) and ranks device #1 in catalog search only upon verified payment confirmation.

### Inventory Expiry & Administrative Refunds
- **Abandoned Reservation Expiry Job**:
  - `expire_abandoned_reservations(timeout_minutes=30)` safely cancels uncompleted pending checkout orders and releases listing inventory back to `available`.
- **Administrative Refund Management**:
  - Route `POST /admin/payment/<payment_id>/refund` restricted strictly to verified administrators.
  - Executes refund via Razorpay client API, updates `Payment.status="refunded"`, `Order.payment_status="refunded"`, and logs an immutable `AuditEvent`.

---

## [2.0.0] - October 2026

### Architectural & Database Transformation
- **Migrated from Fragile JSON Files to Relational SQLAlchemy**:
  - Implemented 9 relational database models: `User`, `Listing`, `ListingImage`, `Order`, `OrderStatusHistory`, `Notification`, `SupportTicket`, `ReportDispute`, `AuditEvent`.
  - Added primary key UUIDs, foreign keys, cascade deletes, composite indexes, and timestamp columns.
  - Prices normalized from loose text strings into integer paise (`price_paise`) to prevent floating-point rounding inaccuracies.
  - Built an idempotent, robust legacy migration script (`backend/migrate_legacy_data.py`) preserving all users, listings from both backend and root directories, orders, and notifications without silent loss.
  - Backed up all legacy JSON datasets in `data_backup/` and tracked in Git.

### Concurrency Protection & Inventory Security
- **Single-Unit Smartphone Reservation Locking**:
  - Implemented atomic conditional update mechanism (`Listing.query.filter(id==id, approval_status=='approved', inventory_status=='available').update({'inventory_status': 'reserved'})`).
  - Guaranteed database-level concurrency protection so that two concurrent buyers cannot purchase the same single-unit phone listing.
  - Built automatic reservation release on order cancellation.
  - Automated transition to `sold` status upon delivery/handover.

### Application Security Hardening
- **Global CSRF Protection**:
  - Activated Flask-WTF `CSRFProtect(app)`.
  - Added hidden CSRF tokens across all HTML forms and `<meta name="csrf-token">` header support.
  - Added custom `CSRFError` handler returning friendly 400 Bad Request error pages.
- **Authentication & Password Hardening**:
  - Enforced minimum 8-character password policy with letter and number complexity requirements.
  - Password hashing utilizing Werkzeug's secure `scrypt` hashing implementation.
  - Implemented sliding-window brute-force rate-limiting on login endpoints (locks out after 5 failed attempts for 15 minutes).
  - Normalization of all email inputs (lowercase and trimmed).
  - Logout converted to `POST` with CSRF protection.
- **Server-Side Authorization**:
  - Private order access restricted: buyers cannot access or view other buyers' orders.
  - Listing modifications restricted: sellers cannot edit or delete listings belonging to other users.
  - Administrative endpoints restricted: only users with `role="admin"` and active session flags can access `/admin`.
  - Sellers blocked from purchasing their own listings.
  - Added HTTP security headers: `X-Content-Type-Options: nosniff`, `X-Frame-Options: SAMEORIGIN`, `X-XSS-Protection: 1; mode=block`, and strict Referrer-Policy.

### Payment Strategy (Deferred Integration)
- **Eliminated Fake Payment Gateways & Broken Placeholders**:
  - Removed missing `submit_payment_reference` action and corrupted UPI text encoding from templates.
  - Replaced ambiguous payment flows with clear, honest **Direct Settlement / Cash on Delivery (COD)**.
  - Orders explicitly marked as `Not collected` upon checkout.
  - Added transparent buyer and seller notices stating payment is settled directly upon physical device inspection.

### Marketplace Logic & Catalog Discovery
- **Server-Side Search & Multi-Criteria Filtering**:
  - Server-side substring search across brand, model, and description.
  - Multi-select filters for Brand, Overall Condition Grade, Storage, RAM, and Min/Max Price.
  - Server-side sorting (`latest`, `price_asc`, `price_desc`) and pagination controls (12 items per page).
- **Mandatory Condition Disclosures**:
  - Structured seller disclosures for Screen Damage, Body Damage, Repair History, Original Display authenticity, Accessories, and Account Lock status.
- **Order State Machine**:
  - Enforced valid status transitions (`Pending` → `Confirmed` → `Shipped` → `Delivered` / `Cancelled`).
  - Delivery courier tracking validation: orders cannot be marked as `Shipped` without valid carrier name and tracking number.
  - Append-only `OrderStatusHistory` logging all transitions with timestamp, actor, role, and notes.

### Frontend UI/UX Redesign
- **Cohesive Design System**:
  - Standardized all 17 templates to inherit from `base.html`.
  - Developed a modern design system using CSS custom properties (Plus Jakarta Sans typography, clean neutral surfaces, indigo accent tokens).
  - Replaced isolated, unstyled pages (`index.html`, `checkout.html`, `orders.html`, `sales.html`, `login.html`, `signup.html`, `edit_listing.html`) with fully responsive layouts.
  - Implemented accessible flash alert components with dismiss controls.
  - Responsive navigation with mobile drawer toggle and user profile dropdown.

### Trust, Support & Operational Tools
- **Customer Support Tickets**: Built support ticket creation and user tracking workflow.
- **Listing Report / Dispute System**: Buyers can flag suspicious or inaccurate listings directly to the Trust & Safety team.
- **Health Check Endpoint**: Built `GET /health` reporting database connectivity status without exposing internal secrets.

### Dependency & Environment Hygiene
- **Consolidated Requirements**: Replaced divergent UTF-16LE files with a single, authoritative UTF-8 `requirements.txt`.
- **Created `.env.example`**: Clean template with safe placeholders only.
- **Created `.gitignore`**: Excludes virtual environments (`venv/`, `venu/`), local databases, `.env` secrets, and cache folders.
- **Removed Obsolete Backups**: Safely archived and deleted 12 redundant and competing backup files while keeping original git history.

### Automated Testing Suite
- **Comprehensive Pytest Suite (`tests/`)**:
  - 24 automated tests covering Authentication, Authorization, CSRF rejection, Listing validations, Image magic bytes, Concurrency race conditions, Order state transitions, and Idempotent JSON migrations.
  - 100% test pass rate.

---

## [2.1.0] - October 2026

### Critical Security Hardening & Audit Remediation
- **Admin Privilege Escalation Prevention (SEC-01)**:
  - Eliminated automatic admin promotion in `signup()` and `admin_login()`. Public signups are unconditionally assigned `role="user"`.
  - Removed client role tampering opportunities. Added audit logging for administrator provisioning.
  - Provided dedicated CLI command `flask create-admin` for authorized administrator creation.
- **Repository Hygiene & Production Configuration Fail-Safe (SEC-02)**:
  - Updated `.gitignore` and `.dockerignore` to exclude `data_backup/`, local databases, uploads, logs, and sensitive files.
  - Implemented `ProductionConfig.validate_production()` ensuring the app will not start in production with missing or insecure placeholder secrets or missing `DATABASE_URL`.
- **Payment Strategy & Revenue Integrity (SEC-03)**:
  - Disabled unpaid priority placement boost activations. Display professional notices indicating online payments are deferred pending verified payment gateway integration.
  - Admin dashboard revenue and count metrics updated to only report verified subscriptions (`payment_status == 'paid'`).
- **Order Lifecycle & Payment Decoupling (SEC-04)**:
  - Enforced strict order status state transitions: shipping orders cannot jump from `Confirmed` to `Delivered` without `Shipped`. Courier tracking details are required prior to shipment.
  - Decoupled physical delivery from payment collection; introduced explicit authorized `record_cod_payment_collection` workflow.
- **Open Redirect Protection (SEC-05)**:
  - Implemented `is_safe_redirect_url` validation rejecting protocol-relative (`//evil.example`), backslash (`/\`), and cross-domain redirects in post-login flow.
- **Support Ticket Authorization (SEC-06)**:
  - Validated order references in support tickets server-side to guarantee users can only link tickets to their own orders.
- **Input Fuzzing & Numeric Parameter Hardening (SEC-07)**:
  - Protected catalog endpoints from `inf`, `-inf`, `nan`, out-of-range pagination, and malformed numeric parameters with finite-float bounds checking.
- **Database Reconciliation**:
  - Reconciled dual SQLite databases (`instance/isellora.db` and `backend/isellora.db`). Standardized on `instance/isellora.db` as canonical, synchronized tables, and backed up pre-audit state.
- **Expanded Test Coverage**:
  - Added 26 dedicated security and regression tests in `tests/test_security_audit.py`. Full test suite contains 61 passing tests (100% pass rate).

