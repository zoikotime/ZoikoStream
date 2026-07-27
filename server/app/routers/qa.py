from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import User
from app.models.qa import QaQuestion, QaVote
from app.models.stream import Stream
from app.schemas.qa import QaQuestionOut
from app.security import get_current_user, get_optional_user
from app.services.stage import MANAGER_ROLES, resolve_identity
from app.sockets import room_for, serialize_question, sio

router = APIRouter(prefix="/streams/{stream_id}/qa", tags=["Q&A"])


def _require_moderator(user: User) -> None:
    if user.role not in MANAGER_ROLES:
        raise HTTPException(403, "Only org admins, hosts, and moderators can moderate Q&A")


def _get_stream_in_org(db: Session, user: User, stream_id: str) -> Stream:
    stream = db.scalar(select(Stream).where(Stream.id == stream_id, Stream.org_id == user.org_id))
    if not stream:
        raise HTTPException(404, "Event not found")
    return stream


def _get_question(db: Session, stream_id: str, question_id: str) -> QaQuestion:
    question = db.scalar(
        select(QaQuestion).where(QaQuestion.id == question_id, QaQuestion.stream_id == stream_id)
    )
    if not question:
        raise HTTPException(404, "Question not found")
    return question


# LIST QUESTIONS
# Public, gated the same way as GET /streams/{id} and the chat history endpoint. `email`
# lets a registered guest see their own upvotes reflected, same as get_viewer_token --
# an anonymous caller has no stable identity via REST so voted_by_me stays False for them.
@router.get("", response_model=list[QaQuestionOut])
def list_questions(
    stream_id: str,
    email: str | None = None,
    db: Session = Depends(get_db),
    user: User | None = Depends(get_optional_user),
):
    stream = db.get(Stream, stream_id)
    if not stream or not stream.visible_to(user):
        raise HTTPException(404, "Event not found")

    questions = db.scalars(
        select(QaQuestion)
        .where(QaQuestion.stream_id == stream_id, QaQuestion.is_deleted.is_(False))
        .order_by(QaQuestion.votes.desc(), QaQuestion.created_at.asc())
    ).all()

    voter_key = resolve_identity(user, email) if (user or email) else None
    voted_ids = set()
    if voter_key and questions:
        voted_ids = set(
            db.scalars(
                select(QaVote.question_id).where(
                    QaVote.voter_key == voter_key,
                    QaVote.question_id.in_([q.id for q in questions]),
                )
            ).all()
        )

    out = []
    for q in questions:
        item = QaQuestionOut.model_validate(q)
        item.voted_by_me = q.id in voted_ids
        out.append(item)
    return out


# MARK ANSWERED
@router.post("/{question_id}/answer", response_model=QaQuestionOut)
async def mark_answered(
    stream_id: str,
    question_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_moderator(user)
    _get_stream_in_org(db, user, stream_id)
    question = _get_question(db, stream_id, question_id)

    question.answered = True
    db.commit()
    db.refresh(question)

    await sio.emit("qa:updated", serialize_question(question), room=room_for(stream_id))
    return question


# UNMARK ANSWERED
@router.delete("/{question_id}/answer", response_model=QaQuestionOut)
async def unmark_answered(
    stream_id: str,
    question_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_moderator(user)
    _get_stream_in_org(db, user, stream_id)
    question = _get_question(db, stream_id, question_id)

    question.answered = False
    db.commit()
    db.refresh(question)

    await sio.emit("qa:updated", serialize_question(question), room=room_for(stream_id))
    return question


# DELETE QUESTION (soft delete)
@router.delete("/{question_id}")
async def delete_question(
    stream_id: str,
    question_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_moderator(user)
    _get_stream_in_org(db, user, stream_id)
    question = _get_question(db, stream_id, question_id)

    question.is_deleted = True
    db.commit()

    await sio.emit("qa:deleted", {"id": str(question.id)}, room=room_for(stream_id))
    return {"message": "Question deleted"}
