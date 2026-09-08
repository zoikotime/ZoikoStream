#!/usr/bin/env python
"""
Seed script to initialize the database with the super admin user and test database connection.
Run this after setting up the database and before starting the server.

Usage:
  python seed.py
"""

import os
import secrets
import sys
from datetime import datetime, timezone

from sqlalchemy import func, select
from app.models import Organization, User, Plan, PlatformSetting
from app.db import Base, engine, get_db
from app.config import settings
from app.security import hash_password
from create_tables import ensure_schema

# Default plan IDENTITY and technical entitlement limits. Limits: None = unlimited.
# Seeded idempotently by slug.
#
# Deliberately NO price_monthly (ZST-LE-COM-001 Section 26: "No hard-coded fallback price,
# tax, discount, deposit percentage or service credit exists"). Seeding a price here would
# make this file the source of commercial truth, which the standard forbids — the chain must
# be: plan identity -> approved pricing registry -> versioned commercial price -> quote/order.
# Plan.price_monthly is nullable; NULL means "no approved price published yet" and every
# reader renders it as unset rather than as free. Populating it is a Finance/Commercial
# action against an approved price book, not a code default.
#
# max_users/max_storage_gb/max_streaming_hours are technical entitlement ceilings the
# platform actually enforces (services/org.py, services/broadcast.py) — operational
# constants, not commercial economics, so they stay.
# Tier NAMES are ZST-COM-PLAN-001 Section 03's: Developer, Business, Enterprise. The
# entitlement ceilings below are unchanged from the earlier Starter/Pro seeding — renaming the
# tiers was a naming correction, not a re-quota'ing. An existing database is brought to these
# names by migrate_plan_names.py; this list only governs a FRESH deployment.
#
# `price_monthly` now carries the APPROVED numbers. ZST-COM-PLAN-001 Section 24 withheld them
# ("intentionally not supplied") and this file therefore held NULL; the Approved Price Book &
# Stripe Billing Wireframe v1.0 supplies them, so the display price is no longer unpublished:
# Developer $49/month, Business $249/month. Enterprise stays NULL with custom_pricing — it is
# contract-priced and must never render a number.
#
# This is the DISPLAY price only. What a customer is actually charged comes from the Stripe
# Price named in STRIPE_SUBSCRIPTION_PRICES, so the two must be kept in step by whoever changes
# either — this column is not the charging authority and never feeds a Checkout Session.
DEFAULT_PLANS = [
    {"name": "Developer", "slug": "developer", "max_users": 5,
     "price_monthly": 49, "currency": "USD",
     "max_storage_gb": 50, "max_streaming_hours": 20,
     "features": ["1 concurrent stream", "720p", "Community support"]},
    {"name": "Business", "slug": "business", "max_users": 30,
     "price_monthly": 249, "currency": "USD",
     "max_storage_gb": 500, "max_streaming_hours": 200,
     "features": ["5 concurrent streams", "1080p", "Recordings", "Email support"]},
    {"name": "Enterprise", "slug": "enterprise", "max_users": None,
     "custom_pricing": True,
     "max_storage_gb": None, "max_streaming_hours": None,
     "features": ["Unlimited streams", "4K", "SSO", "Dedicated support"]},
]

# Default platform settings (key -> {value, category}). Seeded only if the key is missing.
DEFAULT_SETTINGS = {
    "brand": {"category": "brand", "value": {"name": "ZoikoStream", "primary_color": "#8b5cf6",
                                             "support_email": "support@zoikostream.com"}},
    "storage_limits": {"category": "limits", "value": {"default_gb": 50, "max_gb": 5000}},
    "streaming_limits": {"category": "limits", "value": {"default_hours": 20, "max_bitrate_kbps": 8000}},
    "global": {"category": "config", "value": {"signups_enabled": True, "maintenance_mode": False}},
}


def seed_plans(db) -> None:
    for p in DEFAULT_PLANS:
        if not db.scalar(select(Plan).where(Plan.slug == p["slug"])):
            db.add(Plan(**p))
    db.commit()
    print(f"✓ Plans ensured ({len(DEFAULT_PLANS)})")


def seed_settings(db) -> None:
    for key, cfg in DEFAULT_SETTINGS.items():
        if not db.get(PlatformSetting, key):
            db.add(PlatformSetting(key=key, value=cfg["value"], category=cfg["category"]))
    db.commit()
    print(f"✓ Platform settings ensured ({len(DEFAULT_SETTINGS)})")

def seed_database():
    """Initialize database schema, default plans/settings, and the super admin user."""
    # Create/upgrade schema (tables + org columns)
    ensure_schema()
    print("✓ Schema ready")

    # Get a database session
    from sqlalchemy.orm import Session
    db = Session(engine)

    try:
        # Reference data — always ensured, independent of the super-admin check below.
        seed_plans(db)
        seed_settings(db)

        # Check if super admin already exists
        super_admin_email = settings.SUPER_ADMIN_EMAIL.lower()
        existing_admin = db.scalar(
            select(User).where(User.email == super_admin_email)
        )

        if existing_admin:
            print(f"✓ Super admin user already exists: {super_admin_email}")
            return True
        
        # Create the ZoikoStream platform organization
        print("Creating platform organization...")
        platform_org = db.scalar(
            select(Organization).where(Organization.name == "ZoikoStream Platform")
        )
        
        if not platform_org:
            platform_org = Organization(name="ZoikoStream Platform")
            db.add(platform_org)
            db.flush()
            print(f"✓ Created platform organization: {platform_org.id}")
        else:
            print(f"✓ Platform organization exists: {platform_org.id}")
        
        # Create super admin user
        print(f"Creating super admin user: {super_admin_email}")
        username = super_admin_email.split("@")[0]
        
        # Check if username exists
        counter = 1
        original_username = username
        while db.scalar(select(User).where(func.lower(User.username) == username)):
            username = f"{original_username}{counter}"
            counter += 1
        
        # SUPER_ADMIN_PASSWORD lets ops set a known password (e.g. from a secret manager);
        # otherwise generate one so nothing predictable ever lands in source control.
        password = os.environ.get("SUPER_ADMIN_PASSWORD") or secrets.token_urlsafe(18)
        generated = "SUPER_ADMIN_PASSWORD" not in os.environ

        admin_user = User(
            org_id=platform_org.id,
            full_name="ZoikoStream Admin",
            email=super_admin_email,
            username=username,
            password_hash=hash_password(password),
            role="super_admin",
            is_active=True,
            # Operator-provisioned, not self-registered: the address comes from
            # SUPER_ADMIN_EMAIL in the deployment environment, not from an untrusted form,
            # so there is no self-asserted address for IDN-001 to verify. Without this the
            # seeded account could never sign in (login gates on email_verified).
            email_verified=True,
            email_verified_at=datetime.now(timezone.utc),
        )

        db.add(admin_user)
        db.commit()

        print(f"✓ Super admin created successfully!")
        print(f"  Email: {super_admin_email}")
        print(f"  Username: {username}")
        if generated:
            print(f"  Password (save this now, shown once): {password}")
        else:
            print("  Password: set from SUPER_ADMIN_PASSWORD env var")
        print(f"\n✓ Database seeding completed successfully!")
        return True
        
    except Exception as e:
        db.rollback()
        print(f"✗ Error during seeding: {e}", file=sys.stderr)
        return False
    finally:
        db.close()

def test_database_connection():
    """Test if the database is accessible."""
    try:
        print("Testing database connection...")
        with engine.connect() as connection:
            result = connection.execute(select(1))
            if result.fetchone():
                print("✓ Database connection successful!")
                return True
    except Exception as e:
        print(f"✗ Database connection failed: {e}", file=sys.stderr)
        print(f"\nTroubleshooting steps:")
        print(f"1. Check if DATABASE_URL in .env is correct")
        print(f"2. Check if Supabase database is running and accessible")
        print(f"3. Verify your network connection")
        print(f"4. Check firewall settings")
        return False

if __name__ == "__main__":
    print("=" * 50)
    print("ZoikoStream Database Seeding")
    print("=" * 50)
    print()
    
    # Test connection first
    if not test_database_connection():
        sys.exit(1)
    
    print()
    
    # Seed the database
    if seed_database():
        sys.exit(0)
    else:
        sys.exit(1)
