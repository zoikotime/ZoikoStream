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
from app.models.qa import QaQuestion, QaVote
from app.models.poll import Poll, PollOption, PollVote
from app.models.plan import Plan
from app.models.subscription import Subscription
from app.models.api_key import ApiKey
from app.models.feature_flag import FeatureFlag
from app.models.audit_log import AuditLog
from app.models.release import Release
from app.models.platform_setting import PlatformSetting
from app.models.support_ticket import SupportTicket
from app.models.invitation import Invitation


print("Creating tables...")

Base.metadata.create_all(bind=engine)

print("Tables created successfully!")