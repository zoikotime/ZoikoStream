from sqlalchemy import text
from app.db import engine


def test_database():
    try:
        with engine.connect() as conn:

            # Check PostgreSQL version
            result = conn.execute(text("SELECT version();"))
            version = result.fetchone()

            print("✅ Database Connected")
            print(version[0])

            # Check tables
            tables = conn.execute(
                text("""
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema='public'
                ORDER BY table_name;
                """)
            )

            print("\n📌 Tables in database:")

            for table in tables:
                print("-", table[0])

    except Exception as e:
        print("❌ Database Connection Failed")
        print(e)


if __name__ == "__main__":
    test_database()