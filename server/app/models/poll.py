import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class Poll(Base):
    __tablename__ = "polls"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    stream_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("streams.id"), nullable=False, index=True)
    question: Mapped[str] = mapped_column(String(300), nullable=False)
    # Closed polls stop accepting votes but stay visible with their final results.
    is_closed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    options: Mapped[list["PollOption"]] = relationship(
        back_populates="poll", order_by="PollOption.position", cascade="all, delete-orphan"
    )


class PollOption(Base):
    __tablename__ = "poll_options"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    poll_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("polls.id"), nullable=False, index=True)
    label: Mapped[str] = mapped_column(String(160), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    # Denormalized for fast reads; kept in sync by services/poll.py's vote handler.
    votes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    poll: Mapped["Poll"] = relationship(back_populates="options")


class PollVote(Base):
    """One row per (poll, voter) -- one vote per viewer per poll, same voter_key
    identity as QaVote (see models/qa.py)."""

    __tablename__ = "poll_votes"
    __table_args__ = (UniqueConstraint("poll_id", "voter_key", name="uq_poll_vote_voter"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    poll_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("polls.id"), nullable=False, index=True)
    option_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("poll_options.id"), nullable=False)
    voter_key: Mapped[str] = mapped_column(String(160), nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
