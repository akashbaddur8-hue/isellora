# iSellora Marketplace Engineering Audit (Phase Zero)

**Date**: October 2026  
**Auditor**: Senior Full-Stack & Application Security Engineering Team  
**Scope**: Entire repository inspection (`backend/`, `static/`, templates, data stores, configuration, environments, and dependencies).

---

## 1. Executive Summary

iSellora is designed as a peer-to-peer and marketplace platform for used and refurbished smartphones. An initial prototype was built using Flask with JSON file persistence, client-side sorting/filtering, and ad-hoc script utilities. While foundational UI flows (home, product browsing, basic listing creation, checkout skeleton, seller/admin views) exist, the application suffers from critical architectural, security, data integrity, and operational liabilities that prevent reliable production operation.

This audit documents findings across nine critical evaluation areas and defines the engineering roadmap to transform iSellora into a secure, robust, and maintainable platform.

---

## 2. Existing Features

| Feature Area | Current Implementation Status | Evaluation |
|---|---|---|
| **Public Landing Page** | `GET /` serves `index.html` displaying 8 approved listings, brand buttons, hero section, and static promotional copy. | Functional visually, but `index.html` is disconnected from `base.html` (duplicates CSS/nav). |
| **Catalog & Products** | `GET /products` loads all approved listings into the DOM; client-side JavaScript handles brand/price filtering. | Lacks server-side filtering, specs/condition filter, RAM filter, pagination, and persistent search. Navbar search passes `?search=` which `GET /products` ignores. |
| **Listing Detail** | `GET /products/<listing_id>` renders `product_detail.html` with image carousel, specifications, seller contact, and a "Buy Now" CTA. | Mostly functional, but lacks structured damage/condition disclosure, dispute/reporting flows, and reviews. Competing template `listing_detail.html` exists. |
| **User Authentication** | `GET/POST /signup`, `GET/POST /login`, `GET /logout`. Password hashed with Werkzeug `scrypt`. | Logout uses `GET` without CSRF; session cookies lack strict security flags; no rate limiting or lockout protection; duplicate email check is in-memory over JSON. |
| **Seller Listing Flow** | `GET/POST /sell` accepts phone specs, condition dropdowns, description, and up to 10 image uploads. | Enforces basic file extension & header magic bytes check. Saves to `backend/static/uploads/`. Listings default to `pending` status. |
| **Listing Modification** | `GET/POST /edit/<listing_id>` and `POST /delete/<listing_id>`. | Only seller owner can edit/delete; resets approval to `pending` on edit. However, deletes can cause orphaned orders if not carefully handled. |
| **Checkout & Order Creation** | `GET/POST /buy/<listing_id>` generates `checkout_token`, accepts buyer delivery details, creates an order in `orders.json`. | **Critical Flaw**: Item is NOT marked reserved or sold upon checkout; concurrent buyers can buy the exact same single-unit phone multiple times. |
| **Order Confirmation** | `GET /order-success/<order_id>` displays placed order summary. | **Broken**: Template attempts to render `<form action="{{ url_for('submit_payment_reference', order_id=order_id) }}">` which fails if executed because `submit_payment_reference` does not exist in `app.py`. Corrupted text encoding on UPI instructions. |
| **Buyer Orders & Tracking** | `GET /orders` displays buyer's past orders and generates third-party courier tracking URLs. | Orders page is completely disconnected from `base.html` (lacks standard navigation and notification menu). |
| **Seller Sales & Tracking** | `GET /sales` and `GET /seller-dashboard` allow seller to view incoming orders, enter tracking details, and update status. | `update_order_status` and `update_delivery` exist with basic validation. |
| **Admin Moderation** | `GET/POST /admin/login`, `GET /admin`, `POST /admin/listing/<listing_id>/status`. | Admin can approve/hide listings. Requires `ADMIN_EMAIL` match. Hardcoded name in template title. Lacks audit logging. |
| **Notifications** | `inject_notifications` context processor and `POST /notifications/<id>/read`. | Basic notification array stored in JSON, loaded on every single page request. |

---

## 3. Broken or Missing Features

1. **Broken Endpoint Reference**: `backend/templates/order_success.html` references `submit_payment_reference`, causing Jinja `BuildError` when evaluated. The route was completely missing in `app.py`.
2. **Ignored Search Query**: The global navbar search form submits `GET /products?search=<query>`, but the `/products` route handler completely ignored the query parameter, displaying all public listings without filtering.
3. **Missing Server-Side Filtering & Pagination**: `GET /products` relied on client-side JS filtering. Filters for condition grade, RAM, storage, price ranges, and sorting were not processed server-side.
4. **Broken / Disconnected Logout**: `GET /logout` is vulnerable to CSRF and prefetching; standard security requires `POST /logout` with valid CSRF tokens.
5. **No Single-Unit Inventory Reservation**: Single-unit phones remain available for purchase after an order is placed, allowing overselling and race conditions.
6. **No Support Ticket or Dispute Workflow**: Users have no mechanism to report listings, file disputes, or contact support through the platform.
7. **No Password Reset or Account Settings**: Missing secure password update flow, account deactivation, and address management.
8. **Missing Automated Test Suite**: Zero automated tests existed in the repository (`test/` folder was empty).

---

## 4. Security Vulnerabilities

1. **CSRF Protection Inactive**: `from flask_wtf.csrf import CSRFProtect` was imported at line 7 of `backend/app.py`, but `CSRFProtect(app)` was never initialized. None of the HTML forms contained CSRF tokens. All `POST` endpoints were completely vulnerable to Cross-Site Request Forgery.
2. **Missing Rate Limiting**: Authentication endpoints (`/login`, `/signup`, `/admin/login`) have no rate limiting or failure throttling, leaving the system exposed to brute-force credential stuffing.
3. **Hardcoded and Committed Secrets**:
   - `backend/.env` contained plain-text email SMTP credentials and a hardcoded `SECRET_KEY`.
   - No `.env.example` existed to guide safe deployment.
4. **Information Disclosure in Tracking**: For courier names that do not match the known provider map, `get_tracking_url` redirects to Google search with customer tracking details in query strings.
5. **Unvalidated Admin Access Configuration**: `is_admin()` relied solely on an environment variable `ADMIN_EMAIL` compared to the logged-in user email, with no role-based database column or authorization policy.
6. **Missing Security Headers**: No `Content-Security-Policy`, `X-Content-Type-Options`, `X-Frame-Options`, `Strict-Transport-Security`, or secure session cookie flags (`SESSION_COOKIE_SECURE`).

---

## 5. Data Integrity Risks

1. **Production JSON Storage**:
   - `users.json`, `listings.json`, `orders.json`, `notifications.json` are flat files.
   - Any concurrent write or server crash during file replacement risks data corruption or data loss despite `RLock` (which only works within a single OS process).
   - In unbounded usage, loading all JSON files on every HTTP request creates massive I/O and memory overhead.
2. **Schema Inconsistency Across Legacy Data**:
   - Some listing records use `images` (lists of paths) while others use `photos` (lists of filenames without directory prefixes).
   - Some listings have `seller_id`, others only have `seller_name` and `phone`/`seller_phone`.
   - Some orders have `null` or empty `buyer_id` and `seller_id`.
   - Prices are stored inconsistently as strings (e.g. `"59999"` vs `"77778"`).
   - Disconnected duplicate data: root directory contained a separate `listings.json` with 2 listings whose images were stored in the root `static/uploads/`, distinct from `backend/listings.json` and `backend/static/uploads/`.
3. **Lack of Relational Constraints & Transactions**:
   - No foreign key enforcement between users, listings, and orders.
   - No atomic transactions protecting order placement and listing state transition.

---

## 6. Duplicate or Obsolete Files

The repository contained numerous conflicting versions, manual backup artifacts, and temporary runner files:
- `backend/admin_backup.html` (competing with `backend/templates/admin.html`)
- `backend/base.html1` (malformed backup file)
- `backend/tempCodeRunnerFile.py` (temporary IDE artifact)
- `backend/test_email.py` (untracked local SMTP test script)
- `backend/templates/app_backup.py` (full copy of `app.py` placed inside templates directory)
- `backend/templates/base_backup.html` & `backend/templates/base_backup_before_premium.html`
- `backend/templates/index_backup.html`
- `backend/templates/login_backup.html`
- `backend/templates/signup_backup.html`
- `backend/templates/listing_detail.html` (standalone duplicate of `product_detail.html`)
- `backend/templates/product.html` (hardcoded mock phone detail page)
- Root `listings.json` (conflicting with `backend/listings.json`)
- Root `venu/` and `venv/` (bundled virtual environments pointing to an external user machine: `C:\Users\Rajeshwari A Yargal\AppData\Local\Programs\Python\Python313`)

---

## 7. Route and Template Mismatches

1. **`index.html` does not extend `base.html`**: Completely isolated markup and CSS; breaks global navigation and profile session states.
2. **`orders.html` and `sales.html` do not extend `base.html`**: Users navigating to orders or sales lose the navbar, notifications badge, and global search.
3. **`checkout.html` does not extend `base.html`**: Isolated layout with unstyled mobile views and hardcoded placeholder text.
4. **`edit_listing.html` does not extend `base.html`**: Outdated form controls not matching the modern design tokens.
5. **`login.html` and `signup.html` do not extend `base.html`**: Lacks standard branding, consistent footer, and alert flash messages.
6. **Form Action Mismatches**: Order success references non-existent `submit_payment_reference`.

---

## 8. Missing & Malformed Dependencies

1. **Encoding Issues**: Root `requirements.txt` and `backend/requirements.txt` were both encoded in **UTF-16LE**, causing standard tools (`pip`, automated parsers) to fail or misread them.
2. **Divergent Requirements**:
   - Root listed `Flask-Mail==0.10.0`, `Flask-WTF==1.3.0`, `WTForms==3.2.2`.
   - Backend omitted those packages entirely.
   - Both listed `razorpay==2.0.1` even though online payment gateways are explicitly prohibited in this phase.
3. **Clean Standard Requirements Needed**: A single, clean, UTF-8 `requirements.txt` pinning compatible, modern dependencies:
   - `Flask>=3.0`
   - `Flask-SQLAlchemy>=3.1`
   - `Flask-WTF>=1.2`
   - `WTForms>=3.1`
   - `Werkzeug>=3.0`
   - `python-dotenv>=1.0`
   - `pytest>=8.0`

---

## 9. Architectural Weaknesses

1. **Monolithic Procedural Script**: `backend/app.py` contained over 3,200 lines mixing routing, JSON I/O, HTML rendering, validation, and session management in one file.
2. **Missing Service / Model Layer**: Business rules (e.g. phone condition validation, order state transitions, inventory claims) were scattered in route functions.
3. **No Database Migration System**: No schema definitions, Alembic migrations, or relational integrity checks.
4. **Inconsistent Error Handling**: Raw string tuples `("Error message", 400)` returned to users instead of polished, accessible error states or flash messages.

---

## 10. Recommended Implementation Order

1. **Phase 1: Stabilization & Configuration**
   - Create clean UTF-8 `requirements.txt`, `.env.example`, and consolidated `.gitignore`.
   - Clean up obsolete temporary files while preserving legacy data and media in safe backups.
   - Establish clean package structure (`isellora/` with `models`, `routes`, `services`, `templates`, `static`).

2. **Phase 2: Relational Database Architecture & Legacy Data Migration**
   - Implement SQLAlchemy models: `User`, `Listing`, `ListingImage`, `Order`, `OrderStatusHistory`, `Notification`, `SupportTicket`, `ReportDispute`, `AuditEvent`.
   - Write robust data migration script reading all legacy JSON files (`backend/users.json`, `backend/listings.json`, root `listings.json`, `backend/orders.json`, `backend/notifications.json`).
   - Validate and normalize IDs, passwords, prices (convert to paise integer), phone numbers, and timestamps without losing a single record.
   - Verify backup and restore procedures.

3. **Phase 3: Authentication, Security & CSRF**
   - Activate Flask-WTF `CSRFProtect` globally.
   - Implement CSRF hidden inputs in all HTML forms and AJAX headers.
   - Secure login/signup with email normalization, password complexity validation, and brute-force rate-limiting.
   - Implement secure `POST /logout` with CSRF protection.
   - Enforce server-side authorization: buyer order privacy, seller listing ownership, admin role checks.

4. **Phase 4: Marketplace Business Logic & Concurrency Protection**
   - Atomic checkout workflow: database transaction with single-unit inventory locking/claim, ensuring an item cannot be sold twice concurrently.
   - Server-side price calculation and verification (never trust client amounts).
   - Strict order state machine: `Pending` -> `Confirmed` -> `Shipped` -> `Delivered` / `Cancelled` with status history audit logs.
   - Transparent payment model: "Pay on Delivery / Direct Settlement" with explicit `Not collected` payment status; eliminate fake UPI references and fake payment screens.
   - Admin moderation workflow: `Pending`, `Approved`, `Rejected` with reason logging.

5. **Phase 5: Search, Discovery & Catalog**
   - Server-side search across brand and model.
   - Multi-criteria filtering: Brand, Price Range, Condition Grade, Storage, RAM.
   - Server-side sorting (price asc/desc, newest first) and pagination.

6. **Phase 6: Professional UI/UX & Design System**
   - Standardize all templates onto `base.html`.
   - Cohesive design system with semantic CSS variables, accessible forms, responsive mobile navigation, cards, badges, and empty states.
   - Fix all broken links, endpoints, and encoding glitches.

7. **Phase 7: Trust, Tracking, Dispute & Support Workflows**
   - Support ticket creation and management.
   - Listing reporting system for buyers.
   - Legitimate shipment tracking records with validated carrier references.

8. **Phase 8: Automated QA & Test Suite**
   - Pytest suite covering Auth, Authz, CSRF, Listings, Inventory Concurrency, Order State Transitions, Data Migration, and Error Handling.
   - Execute test suite and verify 100% pass rate.

9. **Phase 9: Deployment & Operational Readiness**
   - Provide WSGI entry point, health-check endpoint (`GET /health`), and detailed setup documentation in `README.md` and `CHANGELOG.md`.
