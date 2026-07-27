import secrets
import sys
from sqlalchemy import func, select
from app.models import Organization, User
from app.db import Base, engine, get_db
from app.config import settings
from app.security import hash_password

def seed_database():
    """Initialize database tables and create super admin user."""
    # Create tables
    print("Creating database tables...")
    Base.metadata.create_all(bind=engine)
    print("✓ Tables created successfully")
    
    # Get a database session
    from sqlalchemy.orm import Session
    db = Session(engine)
    
    try:
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
        
        # Random each run -- never hardcode a real password into source control.
        # Rotate it immediately after seeding (see rotate_password.py) if this is more
        # than a throwaway local database.
        temp_password = secrets.token_urlsafe(12)

        admin_user = User(
            org_id=platform_org.id,
            full_name="ZoikoStream Admin",
            email=super_admin_email,
            username=username,
            password_hash=hash_password(temp_password),
            role="super_admin",
            is_active=True,
        )

        db.add(admin_user)
        db.commit()

        print(f"✓ Super admin created successfully!")
        print(f"  Email: {super_admin_email}")
        print(f"  Username: {username}")
        print(f"  Password: {temp_password}")
        print(f"  (save this now -- it is not stored anywhere else in plaintext)")
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
