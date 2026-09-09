"""Marketing and product education (ZST-EC-001 MKT-001 -> MKT-004).

── THE POINT OF THIS FILE ────────────────────────────────────────────────────────────────
`MarketingSubscription` is the ONLY thing in this codebase that constitutes marketing
consent. Not organization membership. Not a verified security contact. Not a status
subscriber. Not a contributor grant, an event registration, a support ticket, a privacy
request, a vulnerability report, or an API key. Every one of those is an operational
relationship, and the regression suite asserts that none of them creates a row here.

Consent is keyed on the EMAIL ADDRESS, not on a user or an organization, because that is the
unit a person can actually withdraw. `user_id`/`organization_id` are correlation only.

── WHY MARKETING PREFERENCES ARE NOT IN Organization.notifications ───────────────────────
`services/notifications.py` owns operational preferences and keeps MARKETING_PREFERENCE_KEYS
deliberately empty with the comment that a future marketing subsystem should have "an obvious
separate home rather than borrowing an operational switch". This is that home. Marketing
topics live on `MarketingSubscription.topics` and nowhere else, so:

    a marketing unsubscribe cannot reach a mandatory IDN/SEC/PRV/COM notice, and
    an operational preference cannot silence or enable marketing.

── TOKENS ────────────────────────────────────────────────────────────────────────────────
Three purposes, three separate tokens, none interchangeable and none of them a credential:

    PURPOSE_MARKETING_VERIFY       double opt-in confirmation, single-use, expiring
    PURPOSE_MARKETING_MANAGE       preference centre handle
    PURPOSE_MARKETING_UNSUBSCRIBE one-click suppression, and suppression only

The unsubscribe token grants no account access by construction: the only thing the endpoint
that accepts it can do is set `status = "unsubscribed"`.
"""

import uuid
from datetime import datetime

from sqlalchemy import (Boolean, DateTime, ForeignKey, Integer, JSON, String, Text,
                        UniqueConstraint, func)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

# ── consent vocabulary ────────────────────────────────────────────────────────────────────

# The four topics, each opted into INDEPENDENTLY. Requesting one never enrols the other
# three: `subscribe()` stores exactly the topics asked for.
TOPIC_RELEASE_NOTES = "release_notes"
TOPIC_FEATURE_ANNOUNCEMENTS = "feature_announcements"
TOPIC_DEVELOPER_EDUCATION = "developer_education"
TOPIC_LIVE_EVENT_EDUCATION = "live_event_education"

MARKETING_TOPICS = (TOPIC_RELEASE_NOTES, TOPIC_FEATURE_ANNOUNCEMENTS,
                    TOPIC_DEVELOPER_EDUCATION, TOPIC_LIVE_EVENT_EDUCATION)

TOPIC_LABELS = {
    TOPIC_RELEASE_NOTES: "Release notes",
    TOPIC_FEATURE_ANNOUNCEMENTS: "Feature announcements",
    TOPIC_DEVELOPER_EDUCATION: "Developer education",
    TOPIC_LIVE_EVENT_EDUCATION: "Live Events education",
}

MARKETING_STATES = ("pending", "active", "unsubscribed")

# Where a subscription came from. Every value is a place a person deliberately asked for
# marketing email. There is intentionally no "event_registration", "support_ticket",
# "contributor_grant", "security_contact" or "status_subscription" value: those are
# operational relationships and cannot be a marketing source.
MARKETING_SOURCES = ("preference_center", "website_form", "guide_request",
                     "webinar_registration", "developer_signup_optin")

# The lawful basis recorded for the consent. "consent" is the only basis this platform
# collects; the column exists so a different basis can be recorded truthfully rather than
# having "consent" asserted for something that was not consent.
CONSENT_BASES = ("consent",)

PURPOSE_MARKETING_VERIFY = "marketing_subscription_verify"
PURPOSE_MARKETING_MANAGE = "marketing_preference_center"
PURPOSE_MARKETING_UNSUBSCRIBE = "marketing_unsubscribe"

VERIFY_TTL_HOURS = 168          # a week: this is not a security flow, and links go stale

# ── MKT-001 ───────────────────────────────────────────────────────────────────────────────

DIGEST_STATES = ("draft", "approved", "published")

# ── MKT-002 ───────────────────────────────────────────────────────────────────────────────

# Authoritative availability lifecycle. `ga` is the ONLY value that may be described with
# generally-available wording; everything else keeps its own wording, permanently.
LIFECYCLE_PREVIEW = "preview"
LIFECYCLE_PILOT = "pilot"
LIFECYCLE_BETA = "beta"
LIFECYCLE_REGIONAL = "regional"
LIFECYCLE_RESTRICTED = "restricted"
LIFECYCLE_INVITE_ONLY = "invite_only"
LIFECYCLE_GA = "ga"

FEATURE_LIFECYCLES = (LIFECYCLE_PREVIEW, LIFECYCLE_PILOT, LIFECYCLE_BETA,
                      LIFECYCLE_REGIONAL, LIFECYCLE_RESTRICTED, LIFECYCLE_INVITE_ONLY,
                      LIFECYCLE_GA)

# How each lifecycle is described to a customer. These strings are the guard rail: there is
# no code path that pairs a non-GA lifecycle with GA wording, because the wording is looked
# up from the lifecycle rather than composed.
LIFECYCLE_SUBJECT = {
    LIFECYCLE_PREVIEW: "Preview: {feature}",
    LIFECYCLE_PILOT: "Pilot access for {feature}",
    LIFECYCLE_BETA: "Beta: {feature}",
    LIFECYCLE_REGIONAL: "{feature} is rolling out in your region",
    LIFECYCLE_RESTRICTED: "Limited availability: {feature}",
    LIFECYCLE_INVITE_ONLY: "Invitation only: {feature}",
    LIFECYCLE_GA: "{feature} is now available",
}

LIFECYCLE_LABELS = {
    LIFECYCLE_PREVIEW: "Preview",
    LIFECYCLE_PILOT: "Pilot",
    LIFECYCLE_BETA: "Beta",
    LIFECYCLE_REGIONAL: "Regional rollout",
    LIFECYCLE_RESTRICTED: "Limited availability",
    LIFECYCLE_INVITE_ONLY: "Invitation only",
    LIFECYCLE_GA: "Generally available",
}

# Lifecycles whose audience must be an explicitly recorded grant, never a broadcast. A
# feature nobody can turn on must not be announced to everybody.
TARGETED_LIFECYCLES = (LIFECYCLE_PREVIEW, LIFECYCLE_PILOT, LIFECYCLE_BETA,
                       LIFECYCLE_REGIONAL, LIFECYCLE_RESTRICTED, LIFECYCLE_INVITE_ONLY)

ANNOUNCEMENT_STATES = ("draft", "approved", "sent")

# ── MKT-003 ───────────────────────────────────────────────────────────────────────────────

# Sequence steps. Each is sent at most once per enrolment.
ONBOARDING_WELCOME = "welcome"
ONBOARDING_BUILD = "build"
ONBOARDING_PRODUCTION = "production_readiness"

ONBOARDING_STEPS = (ONBOARDING_WELCOME, ONBOARDING_BUILD, ONBOARDING_PRODUCTION)

# Which milestone unlocks which step, and CRUCIALLY what counts as observing it:
#
#   welcome               the opt-in itself. Observable by definition.
#   build                 a credential exists on the organization (Organization.api_keys).
#   production_readiness  a webhook endpoint is VERIFIED (WebhookEndpoint.verified_at).
#
# There is deliberately NO step gated on API traffic. API-key request authentication is not
# wired into the request path and per-key telemetry does not exist (services/api_usage.py
# counts rate-limit refusals by principal, which is not the same fact), so "first successful
# API call" cannot be observed - and an unobservable milestone is not used.
ONBOARDING_MILESTONES = {
    ONBOARDING_WELCOME: "opt_in",
    ONBOARDING_BUILD: "credential_created",
    ONBOARDING_PRODUCTION: "webhook_verified",
}

ONBOARDING_STATES = ("active", "completed", "cancelled")

# ── MKT-004 ───────────────────────────────────────────────────────────────────────────────

GUIDE_KEYS = ("live_event_planning", "broadcast_quality", "hybrid_events",
              "event_security_and_privacy")

GUIDE_LABELS = {
    "live_event_planning": "Planning your first live event",
    "broadcast_quality": "Getting broadcast quality right",
    "hybrid_events": "Running hybrid events",
    "event_security_and_privacy": "Security and privacy for live events",
}

GUIDE_STATES = ("requested", "fulfilled", "failed")

WEBINAR_STATES = ("scheduled", "rescheduled", "cancelled", "completed")
REGISTRATION_STATES = ("registered", "cancelled", "attended")


class MarketingSubscription(Base):
    """The one and only record of marketing consent.

    Double opt-in: a row starts `pending` and receives nothing but the confirmation request
    until the address proves control of the inbox. `topics` holds exactly what was asked for.
    """

    __tablename__ = "marketing_subscriptions"
    __table_args__ = (
        UniqueConstraint("email", name="uq_marketing_subscription_email"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True,
                                          default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    # Correlation only. A subscription is never resolved FROM a user or an organization -
    # only the address is consent - and deleting neither implies withdrawal.
    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    organization_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)

    status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False,
                                        index=True)
    topics: Mapped[list | None] = mapped_column(JSON, default=list)
    consent_basis: Mapped[str] = mapped_column(String(30), default="consent", nullable=False)
    source: Mapped[str] = mapped_column(String(40), nullable=False)
    # The evidence of consent: when, from where, and what the person was shown. Kept because
    # "we have consent" is a claim that has to be demonstrable.
    consent_ip: Mapped[str | None] = mapped_column(String(64))
    consent_user_agent: Mapped[str | None] = mapped_column(String(300))

    subscribed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                    server_default=func.now())
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    unsubscribed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    verification_purpose: Mapped[str | None] = mapped_column(String(60))
    verification_token_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    verification_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    verification_consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Preference-centre handle and the one-click suppression handle. Separate values with
    # separate purposes: the unsubscribe token must not double as a way to read or change
    # anything else.
    manage_token_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    unsubscribe_token_hash: Mapped[str | None] = mapped_column(String(64), index=True)

    version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                 server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                 server_default=func.now(),
                                                 onupdate=func.now())


class ReleaseDigest(Base):
    """An approved, publishable summary of releases. MKT-001's authoritative gate.

    A `Release` row existing is NOT permission to email anybody: releases/ is an internal
    changelog super admins write, and `Release.notes` is internal prose. A digest has to be
    assembled, given a customer-facing summary, approved by a named person and published
    before one message goes out.
    """

    __tablename__ = "release_digests"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True,
                                          default=uuid.uuid4)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="draft", nullable=False,
                                        index=True)
    # Customer-facing intro. Distinct from any release's internal notes.
    summary: Mapped[str | None] = mapped_column(Text)
    release_ids: Mapped[list | None] = mapped_column(JSON, default=list)

    approved_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    published_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))

    version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                 server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                 server_default=func.now(),
                                                 onupdate=func.now())


class FeatureAvailability(Base):
    """Authoritative rollout state for one feature. MKT-002's source of truth.

    `feature_flags` is a bare boolean toggle with no lifecycle, no plan/region scoping and no
    approval - not enough to make an availability claim from. This row carries the facts an
    announcement needs, and the announcement copy is derived from `lifecycle` rather than
    written per message, so a preview cannot be described as generally available.
    """

    __tablename__ = "feature_availability"
    __table_args__ = (
        UniqueConstraint("feature_key", name="uq_feature_availability_key"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True,
                                          default=uuid.uuid4)
    # Matches feature_flags.key when a flag exists, so the two stay correlated without this
    # table depending on the flag's boolean.
    feature_key: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    feature_name: Mapped[str] = mapped_column(String(160), nullable=False)
    lifecycle: Mapped[str] = mapped_column(String(30), nullable=False)
    customer_summary: Mapped[str | None] = mapped_column(Text)
    documentation_path: Mapped[str | None] = mapped_column(String(300))

    # Plan slugs that may use it. Empty = not plan-restricted.
    eligible_plans: Mapped[list | None] = mapped_column(JSON, default=list)
    # Platform region keys. Empty = not region-restricted. Matching an organization to a
    # region is best-effort (Organization.region is free text), so a region-restricted
    # feature FAILS CLOSED for an organization whose region cannot be resolved.
    eligible_regions: Mapped[list | None] = mapped_column(JSON, default=list)
    rollout_percentage: Mapped[int | None] = mapped_column(Integer)
    effective_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                 server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                 server_default=func.now(),
                                                 onupdate=func.now())


class FeatureAvailabilityGrant(Base):
    """"This organization can actually use this feature." Recorded, never inferred.

    The audience for a preview, pilot, beta, regional, restricted or invite-only feature. An
    organization without a row here is not told the feature is available to it, whatever its
    plan or region says.
    """

    __tablename__ = "feature_availability_grants"
    __table_args__ = (
        UniqueConstraint("availability_id", "org_id", name="uq_feature_grant_org"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True,
                                          default=uuid.uuid4)
    availability_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("feature_availability.id"), nullable=False, index=True)
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id"),
                                              nullable=False, index=True)
    granted_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    granted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                 server_default=func.now())
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class FeatureAnnouncement(Base):
    """One approved announcement of one availability state.

    Versioned against the lifecycle it was approved for: if the feature later moves from
    preview to GA, that is a NEW announcement with its own approval, not a resend of this one
    with different wording.
    """

    __tablename__ = "feature_announcements"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True,
                                          default=uuid.uuid4)
    availability_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("feature_availability.id"), nullable=False, index=True)
    # Frozen at approval time. The message is rendered from THIS, so a lifecycle change after
    # approval cannot silently re-word an approved announcement.
    lifecycle: Mapped[str] = mapped_column(String(30), nullable=False)
    headline: Mapped[str | None] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="draft", nullable=False)
    approved_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                 server_default=func.now())


class DeveloperOnboardingEnrolment(Base):
    """An explicit developer-education enrolment and its sequence position.

    Created only from a DEVELOPER_EDUCATION opt-in. Creating an API key, registering a
    webhook, holding an org developer role or calling the API creates nothing here.
    """

    __tablename__ = "developer_onboarding_enrolments"
    __table_args__ = (
        UniqueConstraint("email", name="uq_developer_onboarding_email"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True,
                                          default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    subscription_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("marketing_subscriptions.id"), nullable=False, index=True)
    organization_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    status: Mapped[str] = mapped_column(String(20), default="active", nullable=False,
                                        index=True)
    # Steps already sent, so a step is never repeated and the sequence survives a restart.
    steps_sent: Mapped[list | None] = mapped_column(JSON, default=list)
    opt_in_source: Mapped[str] = mapped_column(String(40), nullable=False)
    opt_in_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                server_default=func.now())
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                 server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                 server_default=func.now(),
                                                 onupdate=func.now())


class GuideRequest(Base):
    """Somebody explicitly asked for one guide.

    Fulfilling it is transactional: they asked, they get the thing they asked for. It does
    NOT create marketing consent - `MarketingSubscription` is separate, and opting in is a
    separate decision recorded separately even when both happen on one form.
    """

    __tablename__ = "guide_requests"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True,
                                          default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    name: Mapped[str | None] = mapped_column(String(200))
    guide: Mapped[str] = mapped_column(String(60), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="requested", nullable=False)
    # Whether they ALSO opted in, and to what. Null means they did not.
    subscription_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    marketing_opt_in: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                   server_default=func.now())
    fulfilled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                 server_default=func.now())


class MarketingWebinar(Base):
    """An education webinar. A marketing object, entirely separate from `events`.

    Nothing here touches the live-event domain: a webinar registrant is not an event
    attendee, and an event attendee is not a webinar registrant.
    """

    __tablename__ = "marketing_webinars"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True,
                                          default=uuid.uuid4)
    reference: Mapped[str] = mapped_column(String(40), unique=True, index=True,
                                           nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    # UTC is the canonical record, spelled in the column name for the same reason
    # ScheduledMaintenance does it.
    starts_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    duration_minutes: Mapped[int] = mapped_column(Integer, default=60, nullable=False)
    previous_starts_at_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20), default="scheduled", nullable=False)
    join_path: Mapped[str | None] = mapped_column(String(300))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Follow-up needs its own approval AND the recipient's LIVE_EVENT_EDUCATION consent.
    followup_approved_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    followup_body: Mapped[str | None] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                 server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                 server_default=func.now(),
                                                 onupdate=func.now())


class MarketingWebinarRegistration(Base):
    """One registration. Registering is consent to be emailed ABOUT THIS WEBINAR.

    It is not consent to a campaign, and `attended_at` is not consent either: attendance is
    an observation, and MKT-004 follow-up checks the LIVE_EVENT_EDUCATION topic instead.
    """

    __tablename__ = "marketing_webinar_registrations"
    __table_args__ = (
        UniqueConstraint("webinar_id", "email", name="uq_webinar_registration_email"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True,
                                          default=uuid.uuid4)
    webinar_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("marketing_webinars.id"),
                                                  nullable=False, index=True)
    email: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    name: Mapped[str | None] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(20), default="registered", nullable=False)
    consent_basis: Mapped[str] = mapped_column(String(40), default="webinar_registration",
                                               nullable=False)
    subscription_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    registered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                    server_default=func.now())
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Only ever set from a real observation. There is no attendance integration, so this
    # stays null unless an operator records it.
    attended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reminders_sent: Mapped[list | None] = mapped_column(JSON, default=list)
    version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                 server_default=func.now())


class MarketingNotice(Base):
    """Durable idempotency ledger for MKT-001 -> MKT-004.

    Keyed per recipient as well as per version, because a digest or announcement is one
    obligation PER SUBSCRIBER: `UNIQUE(kind, subject_id)` would deliver the first person's
    copy and silently drop everyone else's.
    """

    __tablename__ = "marketing_notices"
    __table_args__ = (
        UniqueConstraint("kind", "subject_type", "subject_id", "version", "recipient",
                         name="uq_marketing_notice"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True,
                                          default=uuid.uuid4)
    kind: Mapped[str] = mapped_column(String(60), nullable=False)
    subject_type: Mapped[str] = mapped_column(String(40), nullable=False)
    subject_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False,
                                                  index=True)
    version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    recipient: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                              server_default=func.now())
