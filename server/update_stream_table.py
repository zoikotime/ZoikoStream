from sqlalchemy import text
from app.db import engine


def add_column():

    with engine.connect() as conn:

        conn.execute(
            text(
                """
                ALTER TABLE streams
                ADD COLUMN IF NOT EXISTS livekit_room VARCHAR(255);
                """
            )
        )

        conn.execute(
            text(
                """
                ALTER TABLE streams
                ADD COLUMN IF NOT EXISTS ended_at TIMESTAMP WITH TIME ZONE;
                """
            )
        )

        conn.commit()

    print("Columns added successfully")


if __name__ == "__main__":
    add_column()