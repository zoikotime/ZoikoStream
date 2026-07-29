import urllib.parse
from sqlalchemy import create_engine, text

# 1. Start with the raw password containing the special characters
raw_password = "Zoiko@123@"  # If this password throws an auth error later, swap it with: "Zoikostream1100"

# 2. Encode the password safely so the '@' doesn't confuse the URL parser
safe_password = urllib.parse.quote_plus(raw_password)

# 3. Insert the safe password into your team's exact connection URL string
DATABASE_URL="postgresql://postgres.xrjswzcrcgxwpixdalkt:Zoikostream1100@aws-1-eu-west-2.pooler.supabase.com:6543/postgres"

# 4. Initialize engine and run script
engine = create_engine(DATABASE_URL)

try:
    with engine.connect() as conn:
        print("Dropping stale tables...")
        conn.execute(text("DROP TABLE IF EXISTS event_assignments CASCADE;"))
        conn.execute(text("DROP TABLE IF EXISTS events CASCADE;"))
        conn.commit()
        print("Tables dropped successfully!")
except Exception as e:
    print(f"An error occurred: {e}")
