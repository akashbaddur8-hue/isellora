"""
Data migration verification tests.
Verifies that legacy JSON datasets can be imported into the relational database without silent loss.
"""
from backend.app import create_app
from backend.database import db
from backend.models import User, Listing, Order, Notification
from backend.migrate_legacy_data import run_migration


def test_legacy_data_migration_and_idempotency():
    app = create_app("testing")
    with app.app_context():
        db.create_all()

        # Run 1: First migration
        report1 = run_migration(app=app, dry_run=False)
        assert report1["users"]["imported"] >= 2
        assert report1["listings"]["imported"] >= 10
        assert report1["orders"]["imported"] >= 3
        assert report1["notifications"]["imported"] >= 3

        # Verify data integrity
        user_akash = User.query.filter_by(email="akashbaddur2007@gmail.com").first()
        assert user_akash is not None
        assert user_akash.role == "admin"
        assert user_akash.phone == "7975551621"

        # Check listing price in paise
        sample_listing = Listing.query.first()
        assert sample_listing.price_paise > 0
        assert sample_listing.price_rupees > 0

        # Check delivered order marked listing sold
        delivered_order = Order.query.filter_by(status="Delivered").first()
        assert delivered_order is not None
        if delivered_order.listing_id:
            sold_listing = db.session.get(Listing, delivered_order.listing_id)
            if sold_listing:
                assert sold_listing.inventory_status == "sold"

        # Run 2: Idempotent rerun
        report2 = run_migration(app=app, dry_run=False)
        assert report2["users"]["imported"] == 0
        assert report2["listings"]["imported"] == 0
        assert report2["orders"]["imported"] == 0

        db.session.remove()
        db.drop_all()
