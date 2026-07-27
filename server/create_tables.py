from app.db import engine, Base

# Import all models so SQLAlchemy knows them
from app.models.organization import Organization
from app.models.user import User
from app.models.membership import Membership
from app.models.channel import Channel
from app.models.stream import Stream
from app.models.chat import ChatMessage
from app.models.registration import Registration
from app.models.recording import Recording
from app.models.view import StreamView


print("Creating tables...")

Base.metadata.create_all(bind=engine)

print("Tables created successfully!")