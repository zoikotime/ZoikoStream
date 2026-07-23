import sys

from sqlalchemy import select

from app.db import SessionLocal
from app.models import User
from app.security import hash_password


def rotate(email: str, new_password: str) -> None:
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == email.lower()))
        if not user:
            print(f"No user found with email {email}")
            return
        user.password_hash = hash_password(new_password)
        db.commit()
        print(f"Password updated for {email} ({user.role})")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print('Usage: python rotate_password.py "<email>" "<new_password>"')
        sys.exit(1)
    rotate(sys.argv[1], sys.argv[2])
