"""Anonymous public session lifecycle (API §3, §7; Security/Privacy §7).

A session is a CAPABILITY, not an identity: an opaque random value in an HttpOnly cookie,
stored server-side only as a keyed hash. Every mutating call must present all of:
  path session_id == cookie's session == CSRF header's session, from an approved Origin,
on a session that is neither ENDED nor past its idle/absolute expiry (API §3.1).

Failures are uniform: a wrong cookie, an unknown session id and an expired session all answer
401 GUIDE_SESSION_INVALID, so the endpoint cannot be used to probe which session ids exist
(Security/Privacy §23).
"""
from __future__ import annotations

import secrets
import uuid
from datetime import timedelta

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import crypto, event_context, ratelimit, retention
from .errors import GuideProblem
from .models import (
    GuideAuditEvent, GuidePublicEventContext, GuideSession, GuideSessionContextVersion,
)
from .schemas import PageContext, SessionCreate
from .settings import get_settings

COOKIE_NAME = "zs_guide_sid"
COOKIE_PATH = "/api/v1/public-guide"
CSRF_HEADER = "x-zs-csrf"


def audit(db: Session, request: Request | None, event_type: str, *,
          session_id: uuid.UUID | None = None, **metadata) -> None:
    """Non-content facts only. Callers pass ids/codes/classes — never text or field values."""
    db.add(GuideAuditEvent(
        session_id=session_id, event_type=event_type,
        request_id=getattr(request.state, "guide_request_id", None) if request else None,
        correlation_id=getattr(request.state, "guide_correlation_id", None) if request else None,
        metadata_={**metadata, "retention_policy": retention.POLICY_VERSION},
        purge_after=retention.purge_after("audit"),
    ))


# ── Origin / CSRF ──────────────────────────────────────────────────────────────────────────

def check_origin(request: Request) -> None:
    s = get_settings()
    origin = request.headers.get("origin")
    if origin is None:
        if s.allow_missing_origin():
            return
        raise GuideProblem("GUIDE_PUBLIC_BOUNDARY_DENIED", "Request origin is not permitted.")
    if not s.origin_allowed(origin):
        raise GuideProblem("GUIDE_PUBLIC_BOUNDARY_DENIED", "Request origin is not permitted.")


# ── Creation ───────────────────────────────────────────────────────────────────────────────

def _attach_event_context(db: Session, session: GuideSession,
                          ctx: PageContext) -> uuid.UUID | None:
    verified = event_context.verify(ctx.public_event_context_token)
    if verified is None:
        return None
    row = GuidePublicEventContext(
        session_id=session.session_id, safe_claims=verified.safe_claims,
        token_fingerprint=verified.fingerprint, token_expires_at=verified.expires_at,
        purge_after=retention.purge_after("event_context", at=verified.expires_at),
    )
    db.add(row)
    db.flush()
    return row.event_context_id


def _add_context_version(db: Session, session: GuideSession, ctx: PageContext,
                         version: int) -> GuideSessionContextVersion:
    event_context_id = _attach_event_context(db, session, ctx)
    row = GuideSessionContextVersion(
        session_id=session.session_id, context_version=version, page_id=ctx.page_id,
        page_type=ctx.page_type, path=ctx.path, locale=ctx.locale, timezone=ctx.timezone,
        device_class=ctx.device_class, entry_point=ctx.entry_point,
        event_context_id=event_context_id,
        purge_after=retention.purge_after("session"),
    )
    db.add(row)
    return row


def create_session(db: Session, request: Request, body: SessionCreate) -> tuple[
        GuideSession, str, str]:
    """Returns (session, raw_capability, raw_csrf). The raw values exist only in this
    response; the database holds their keyed hashes."""
    s = get_settings()
    nonce_hash = crypto.keyed_hash(str(body.client_session_nonce), purpose="session-nonce")
    if db.scalar(select(GuideSession.session_id).where(
            GuideSession.client_nonce_hash == nonce_hash)) is not None:
        # A replayed create. Re-issuing the capability would let anyone holding the nonce
        # take over the session, so the client starts over with a fresh nonce instead.
        raise GuideProblem("GUIDE_STATE_CONFLICT",
                           "This session request was already processed. Start a new session.")
    capability = secrets.token_urlsafe(32)
    csrf = secrets.token_urlsafe(32)
    now = retention.now()
    session = GuideSession(
        session_id=uuid.uuid4(),
        capability_hash=crypto.keyed_hash(capability, purpose="session-capability"),
        csrf_hash=crypto.keyed_hash(csrf, purpose="session-csrf"),
        client_nonce_hash=nonce_hash,
        state="ACTIVE",
        locale=body.page_context.locale,
        device_class=body.page_context.device_class,
        analytics_consent=body.consent_snapshot.analytics,
        current_context_version=1,
        created_at=now, last_activity_at=now,
        expires_at=now + timedelta(hours=s.GUIDE_SESSION_ABSOLUTE_HOURS),
        purge_after=retention.purge_after("session", at=now),
    )
    db.add(session)
    db.flush()
    ctx_row = _add_context_version(db, session, body.page_context, 1)
    audit(db, request, "session_created", session_id=session.session_id,
          page_type=body.page_context.page_type, entry_point=body.page_context.entry_point,
          event_context=ctx_row.event_context_id is not None)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise GuideProblem("GUIDE_STATE_CONFLICT",
                           "This session request was already processed. Start a new session.")
    return session, capability, csrf


def set_session_cookie(response, capability: str) -> None:
    s = get_settings()
    response.set_cookie(
        COOKIE_NAME, capability, httponly=True, samesite="lax", path=COOKIE_PATH,
        # Secure everywhere except plain-http local development, where browsers would drop
        # the cookie. localhost is a secure context in modern browsers, but the API may be
        # reached as 127.0.0.1 too, so the dev exemption is explicit.
        secure=s.is_production() or s.ENVIRONMENT.strip().lower() not in ("development",
                                                                           "dev", "test"),
        max_age=s.GUIDE_SESSION_ABSOLUTE_HOURS * 3600,
    )


def clear_session_cookie(response) -> None:
    response.delete_cookie(COOKIE_NAME, path=COOKIE_PATH)


# ── Authentication of an existing session ──────────────────────────────────────────────────

def _expired(session: GuideSession) -> str | None:
    s = get_settings()
    now = retention.now()
    if now >= session.expires_at:
        return "absolute_expiry"
    if now - session.last_activity_at >= timedelta(minutes=s.GUIDE_SESSION_IDLE_MINUTES):
        return "idle_expiry"
    return None


def end_session(db: Session, request: Request | None, session: GuideSession,
                reason: str) -> None:
    now = retention.now()
    session.state = "ENDED"
    session.ended_at = now
    session.end_reason = reason
    session.active_flow_kind = None
    session.active_flow_id = None
    # Accelerated purge after an explicit fresh start (Security/Privacy §16.1). Expiry keeps
    # the normal schedule so abuse correlation still works.
    if reason == "user_ended":
        session.purge_after = min(session.purge_after, now + timedelta(hours=24))
    audit(db, request, "session_ended", session_id=session.session_id, reason=reason)


def load_session(db: Session, request: Request, session_id: uuid.UUID, *,
                 mutating: bool, lock: bool = False) -> GuideSession:
    if mutating:
        check_origin(request)
    capability = request.cookies.get(COOKIE_NAME)
    if not capability:
        raise GuideProblem("GUIDE_SESSION_INVALID")
    stmt = select(GuideSession).where(GuideSession.session_id == session_id)
    if lock:
        stmt = stmt.with_for_update()
    session = db.scalar(stmt)
    if session is None or not crypto.constant_time_equal(
            session.capability_hash,
            crypto.keyed_hash(capability, purpose="session-capability")):
        raise GuideProblem("GUIDE_SESSION_INVALID")
    if session.state == "ENDED":
        raise GuideProblem("GUIDE_SESSION_INVALID")
    reason = _expired(session)
    if reason:
        end_session(db, request, session, reason)
        db.commit()
        raise GuideProblem("GUIDE_SESSION_INVALID", "This conversation has expired.")
    if mutating:
        csrf = request.headers.get(CSRF_HEADER)
        if not csrf or not crypto.constant_time_equal(
                session.csrf_hash, crypto.keyed_hash(csrf, purpose="session-csrf")):
            raise GuideProblem("GUIDE_SESSION_INVALID", "The request could not be verified.")
    s = get_settings()
    ratelimit.enforce(request, "session", str(session.session_id),
                      s.GUIDE_RL_SESSION_REQUESTS_PER_MIN)
    session.last_activity_at = retention.now()
    return session


def update_context(db: Session, request: Request, session: GuideSession,
                   ctx: PageContext) -> GuideSessionContextVersion:
    version = session.current_context_version + 1
    row = _add_context_version(db, session, ctx, version)
    session.current_context_version = version
    session.locale = ctx.locale
    session.device_class = ctx.device_class
    audit(db, request, "context_updated", session_id=session.session_id,
          context_version=version, page_type=ctx.page_type,
          event_context=row.event_context_id is not None)
    return row


def current_event_claims(db: Session, session: GuideSession) -> dict | None:
    """Safe D2 claims from the latest context version, if still within token lifetime."""
    row = db.scalar(
        select(GuidePublicEventContext)
        .join(GuideSessionContextVersion,
              GuideSessionContextVersion.event_context_id
              == GuidePublicEventContext.event_context_id)
        .where(GuideSessionContextVersion.session_id == session.session_id,
               GuideSessionContextVersion.context_version == session.current_context_version)
    )
    if row is None or row.token_expires_at <= retention.now():
        return None
    return dict(row.safe_claims)
