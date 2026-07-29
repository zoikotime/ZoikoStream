# check_channels.py

from sqlalchemy import select
from app.db import SessionLocal
from app.models import Channel

db = SessionLocal()

channels = db.scalars(select(Channel)).all()

if not channels:
    print("No channels found.")
else:
    for ch in channels:
        print("=" * 60)
        print("ID        :", ch.id)
        print("Owner ID  :", ch.owner_id)
        print("Name      :", ch.name)
        print("Slug      :", ch.slug)
        print("Category  :", ch.category)