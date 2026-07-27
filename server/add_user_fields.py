from sqlalchemy import text
from app.db import engine


def add_columns():
    with engine.connect() as conn:
        conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS reset_attempts INTEGER NOT NULL DEFAULT 0;"))
        conn.commit()

    print("Columns added successfully")


if __name__ == "__main__":
    add_columns()
