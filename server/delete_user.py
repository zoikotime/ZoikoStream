from sqlalchemy import select
from app.db import SessionLocal
from app.models import User

EMAIL = "gundakrishna2004@gmail.com"

db = SessionLocal()

try:
    user = db.scalar(select(User).where(User.email == EMAIL))

    if not user:
        print(f"No user found with email: {EMAIL}")
    else:
        db.delete(user)
        db.commit()
        print(f"Deleted user: {EMAIL}")

finally:
    db.close()