from app.db import engine, Base

# Import all models so SQLAlchemy knows them
from app.models.organization import Organization
from app.models.user import User
from app.models.channel import Channel
from app.models.stream import Stream


print("Creating tables...")

Base.metadata.create_all(bind=engine)

print("Tables created successfully!")