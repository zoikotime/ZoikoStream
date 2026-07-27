import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class QaQuestion(Base):
    __tablename__ = "qa_questions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    stream_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("streams.id"), nullable=False, index=True)

    # Null for anonymous/guest viewers, same as ChatMessage -- display_name is a
    # snapshot at ask time so a question still shows a name later.
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    text: Mapped[str] = mapped_column(String(500), nullable=False)

    # Denormalized for fast list ordering; kept in sync with QaVote rows by the
    # vote/unvote handlers (services/qa.py).
    votes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    answered: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class QaVote(Base):
    """One row per (question, voter) so a viewer can't stack upvotes. voter_key is the
    same identity used for the stage raised-hand queue (see services/stage.resolve_identity)
    -- a logged-in user's id, or a guest's registered email; unregistered guests dedupe
    only within a single browser session (see sockets.py), same approximation as
    services/views.record_view."""

    __tablename__ = "qa_votes"
    __table_args__ = (UniqueConstraint("question_id", "voter_key", name="uq_qa_vote_voter"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    question_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("qa_questions.id"), nullable=False, index=True)
    voter_key: Mapped[str] = mapped_column(String(160), nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
