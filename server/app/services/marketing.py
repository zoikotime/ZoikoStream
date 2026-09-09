"""Marketing and product education service (ZST-EC-001 MKT-001 -> MKT-004).

── THE ONE RULE ──────────────────────────────────────────────────────────────────────────
`eligible()` is the only gate to every marketing send in this module, and it consults
`MarketingSubscription` and nothing else. There is no code path from an operational
relationship to a marketing message: this module never reads `StatusSubscriber`,
`OrganizationSecurityContact`, `EventContributorGrant`, `EventRegistration`,
`SupportTicket`, `PrivacyRequest`, `VulnerabilityReport`, `Organization.api_keys` or
organization membership to BUILD an audience. It reads two of them - api_keys and
WebhookEndpoint - only to observe an already-enrolled developer's milestone, never to enrol
anybody.

── AND THE COROLLARY ─────────────────────────────────────────────────────────────────────
`unsubscribe()` writes exactly one row: this subscription. It cannot reach
`Organization.notifications`, a security contact, a status subscription or any mandatory
family, so a marketing opt-out can never silence an IDN, SEC, PRV, COM or SUP notice.
"""

import hashlib
import hmac
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import (
    GUIDE_KEYS,
    GUIDE_LABELS,
    LIFECYCLE_GA,
    LIFECYCLE_LABELS,
    LIFECYCLE_SUBJECT,
    MARKETING_SOURCES,
    MARKETING_TOPICS,
    ONBOARDING_MILESTONES,
    ONBOARDING_STEPS,
    PURPOSE_MARKETING_MANAGE,
    PURPOSE_MARKETING_UNSUBSCRIBE,
    PURPOSE_MARKETING_VERIFY,
    TARGETED_LIFECYCLES,
    TOPIC_DEVELOPER_EDUCATION,
    TOPIC_FEATURE_ANNOUNCEMENTS,
    TOPIC_LABELS,
    TOPIC_LIVE_EVENT_EDUCATION,
    TOPIC_RELEASE_NOTES,
    MARKETING_VERIFY_TTL_HOURS,
    DeveloperOnboardingEnrolment,
    FeatureAnnouncement,
    FeatureAvailability,
    FeatureAvailabilityGrant,
    GuideRequest,
    MarketingNotice,
    MarketingSubscription,
    MarketingWebinar,
    MarketingWebinarRegistration,
    Organization,
    Release,
    ReleaseDigest,
    Subscription,
    WebhookEndpoint,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(moment: datetime | None) -> datetime | None:
    if moment is None:
        return None
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def _hash(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def _claim(db: Session, kind: str, subject_type: str, subject_id, version: int,
           recipient: str = "") -> bool:
    db.add(MarketingNotice(kind=kind, subject_type=subject_type, subject_id=subject_id,
                           version=version, recipient=(recipient or "").lower()))
    try:
        db.commit()
        return True
    except IntegrityError:
        db.rollback()
        return False


def _release(db: Session, kind: str, subject_type: str, subject_id, version: int,
             recipient: str = "") -> None:
    row = db.scalar(select(MarketingNotice).where(
        MarketingNotice.kind == kind, MarketingNotice.subject_type == subject_type,
        MarketingNotice.subject_id == subject_id, MarketingNotice.version == version,
        MarketingNotice.recipient == (recipient or "").lower()))
    if row is not None:
        db.delete(row)
        db.commit()


# ══════════════════════════════════════════════════════════════════════════════════════════
# consent foundation
# ══════════════════════════════════════════════════════════════════════════════════════════

def clean_topics(topics) -> list[str]:
    """Exactly the requested topics, deduped and ordered. Never widened.

    Asking for release notes does not enrol anyone in the other three: this returns what was
    asked for, and an empty request stays empty rather than defaulting to everything.
    """
    seen = []
    for topic in topics or []:
        if topic in MARKETING_TOPICS and topic not in seen:
            seen.append(topic)
    return seen


def find(db: Session, email: str) -> MarketingSubscription | None:
    return db.scalar(select(MarketingSubscription).where(
        MarketingSubscription.email == (email or "").strip().lower()))


def subscribe(db: Session, *, email: str, topics: list[str], source: str,
              user_id=None, organization_id=None, consent_ip: str | None = None,
              consent_user_agent: str | None = None
              ) -> tuple[MarketingSubscription | None, str | None]:
    """Start a double opt-in marketing subscription.

    Returns (subscription, raw_verification_token). The row starts `pending` and receives
    nothing but the confirmation request until the address proves control of the inbox.

    `source` must be one of MARKETING_SOURCES - all five are places a person deliberately
    asked. There is intentionally no source value for an event registration, a support
    ticket, a contributor grant, a security contact or a status subscription, so this
    function cannot be called on behalf of one.
    """
    address = (email or "").strip().lower()
    if "@" not in address or len(address) > 255:
        return None, None
    if source not in MARKETING_SOURCES:
        return None, None
    wanted = clean_topics(topics)
    if not wanted:
        # A subscription to nothing is not consent to anything.
        return None, None

    raw = secrets.token_urlsafe(32)
    existing = find(db, address)
    if existing is not None:
        if existing.status == "active":
            # Already consented: ADD the newly requested topics rather than replacing, and
            # do not restart verification. Nothing is silently removed either.
            merged = list(existing.topics or [])
            for topic in wanted:
                if topic not in merged:
                    merged.append(topic)
            existing.topics = merged
            existing.version += 1
            db.commit()
            db.refresh(existing)
            return existing, None
        existing.topics = wanted
        existing.status = "pending"
        existing.source = source
        existing.unsubscribed_at = None
        existing.verification_purpose = PURPOSE_MARKETING_VERIFY
        existing.verification_token_hash = _hash(raw)
        existing.verification_expires_at = _now() + timedelta(hours=MARKETING_VERIFY_TTL_HOURS)
        existing.verification_consumed_at = None
        existing.consent_ip = consent_ip
        existing.consent_user_agent = (consent_user_agent or "")[:300] or None
        existing.version += 1
        db.commit()
        db.refresh(existing)
        return existing, raw

    row = MarketingSubscription(
        email=address, user_id=user_id, organization_id=organization_id,
        status="pending", topics=wanted, consent_basis="consent", source=source,
        consent_ip=consent_ip,
        consent_user_agent=(consent_user_agent or "")[:300] or None,
        verification_purpose=PURPOSE_MARKETING_VERIFY,
        verification_token_hash=_hash(raw),
        verification_expires_at=_now() + timedelta(hours=MARKETING_VERIFY_TTL_HOURS),
        version=0)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row, raw


def confirm(db: Session, *, token: str) -> tuple[MarketingSubscription | None, str,
                                                 str | None, str | None]:
    """Redeem a confirmation token.

    Returns (subscription, outcome, manage_token, unsubscribe_token). The two handles are
    minted here and are SEPARATE values with separate purposes - the unsubscribe handle is
    not a way to read or change preferences, and neither is a credential.
    """
    if not token:
        return None, "invalid", None, None
    row = db.scalar(select(MarketingSubscription).where(
        MarketingSubscription.verification_token_hash == _hash(token)))
    if row is None:
        return None, "invalid", None, None
    if not hmac.compare_digest(_hash(token), row.verification_token_hash or ""):
        return None, "invalid", None, None
    if row.verification_consumed_at is not None:
        return None, "already_used", None, None
    if row.verification_expires_at is None or row.verification_expires_at <= _now():
        return None, "expired", None, None

    manage = secrets.token_urlsafe(32)
    unsub = secrets.token_urlsafe(32)
    row.status = "active"
    row.verified_at = _now()
    row.verification_consumed_at = _now()
    row.manage_token_hash = _hash(manage)
    row.unsubscribe_token_hash = _hash(unsub)
    row.version += 1
    db.commit()
    db.refresh(row)
    return row, "active", manage, unsub


def by_manage_token(db: Session, token: str) -> MarketingSubscription | None:
    if not token:
        return None
    row = db.scalar(select(MarketingSubscription).where(
        MarketingSubscription.manage_token_hash == _hash(token)))
    if row is None or not hmac.compare_digest(_hash(token), row.manage_token_hash or ""):
        return None
    return row


def by_unsubscribe_token(db: Session, token: str) -> MarketingSubscription | None:
    """Resolve the one-click suppression handle.

    Purpose-bound: the only thing the caller of this function can do is unsubscribe. It is
    deliberately not interchangeable with the manage handle, so a forwarded unsubscribe link
    cannot be used to read somebody's preferences.
    """
    if not token:
        return None
    row = db.scalar(select(MarketingSubscription).where(
        MarketingSubscription.unsubscribe_token_hash == _hash(token)))
    if row is None or not hmac.compare_digest(_hash(token),
                                              row.unsubscribe_token_hash or ""):
        return None
    return row


def update_topics(db: Session, subscription: MarketingSubscription,
                  topics: list[str]) -> tuple[bool, list[str]]:
    """Replace the topic set. Returns (changed, previous)."""
    wanted = clean_topics(topics)
    previous = list(subscription.topics or [])
    if sorted(wanted) == sorted(previous):
        return False, previous
    subscription.topics = wanted
    # Choosing zero topics is a valid way to stop everything, and is recorded as such.
    if not wanted and subscription.status == "active":
        subscription.status = "unsubscribed"
        subscription.unsubscribed_at = _now()
    subscription.version += 1
    db.commit()
    db.refresh(subscription)
    return True, previous


def unsubscribe(db: Session, subscription: MarketingSubscription) -> bool:
    """Suppress ALL marketing for this address, immediately and durably.

    Writes this row and nothing else. There is no reference here to
    `Organization.notifications`, to a security contact, to a status subscription or to any
    mandatory family - a marketing opt-out is incapable of suppressing a security, identity,
    privacy, billing or support notice.
    """
    if subscription.status == "unsubscribed":
        return False
    subscription.status = "unsubscribed"
    subscription.unsubscribed_at = _now()
    subscription.version += 1
    db.commit()
    db.refresh(subscription)
    # Marketing consent is withdrawn, so an in-flight education sequence stops too.
    cancel_onboarding(db, subscription.email)
    return True


def eligible(db: Session, topic: str, email: str) -> bool:
    """Is this address consented to receive this topic RIGHT NOW?

    The single gate every marketing send in this module passes through.
    """
    if topic not in MARKETING_TOPICS:
        return False
    row = find(db, email)
    return bool(row and row.status == "active" and topic in (row.topics or []))


def audience(db: Session, topic: str) -> list[MarketingSubscription]:
    """Every actively-consented subscriber for one topic. The only audience builder."""
    if topic not in MARKETING_TOPICS:
        return []
    rows = db.scalars(select(MarketingSubscription).where(
        MarketingSubscription.status == "active")).all()
    return [r for r in rows if topic in (r.topics or [])]


def manage_url(token: str) -> str:
    from urllib.parse import quote

    from ..email import public_base_url

    return f"{public_base_url()}/preferences?t={quote(token, safe='')}"


def unsubscribe_url(token: str) -> str:
    from urllib.parse import quote

    from ..email import public_base_url

    return f"{public_base_url()}/unsubscribe?t={quote(token, safe='')}"


def _links(db: Session, subscription: MarketingSubscription) -> tuple[str | None, str | None]:
    """Preference-centre and one-click unsubscribe links for a subscriber.

    Returns (None, None) when the handles are missing, and the caller then does not send:
    a marketing message with no working unsubscribe is not a message we are willing to send.
    """
    # The raw handles exist only at confirmation time, so a stored subscriber's links are
    # re-minted here and the hashes rotated. That keeps the invariant that only a hash is at
    # rest while still guaranteeing every message carries a live unsubscribe.
    if subscription.status != "active":
        return None, None
    manage = secrets.token_urlsafe(32)
    unsub = secrets.token_urlsafe(32)
    subscription.manage_token_hash = _hash(manage)
    subscription.unsubscribe_token_hash = _hash(unsub)
    db.commit()
    return manage_url(manage), unsubscribe_url(unsub)


# ══════════════════════════════════════════════════════════════════════════════════════════
# MKT-001 — release notes digest
# ══════════════════════════════════════════════════════════════════════════════════════════

def approve_release(db: Session, release: Release, *, customer_summary: str,
                    approved_by, documentation_path: str | None = None,
                    rollout_status: str | None = None) -> bool:
    """Make one release distributable.

    Requires a customer-facing summary written for the purpose. `Release.notes` is internal
    admin prose and is never what goes out - nothing in this module reads it.
    """
    if not (customer_summary or "").strip():
        return False
    if approved_by is None:
        return False
    release.customer_summary = customer_summary
    release.customer_visible = True
    release.documentation_path = documentation_path
    release.rollout_status = rollout_status
    release.approved_by = approved_by
    release.approved_at = _now()
    db.commit()
    db.refresh(release)
    return True


def create_digest(db: Session, *, title: str, period_start: datetime,
                  period_end: datetime, release_ids: list,
                  summary: str | None = None) -> ReleaseDigest | None:
    """Draft a digest from APPROVED releases only.

    A release that nobody approved is silently excluded, and a digest with nothing approved
    in it is refused rather than sent empty.
    """
    if period_end <= period_start:
        return None
    approved = []
    for rid in release_ids or []:
        release = db.get(Release, rid if isinstance(rid, uuid.UUID) else uuid.UUID(str(rid)))
        if release is None:
            continue
        if not release.customer_visible or release.approved_by is None:
            continue
        if not (release.customer_summary or "").strip():
            continue
        approved.append(str(release.id))
    if not approved:
        return None

    digest = ReleaseDigest(
        title=title, period_start=period_start, period_end=period_end,
        status="draft", summary=summary, release_ids=approved, version=0)
    db.add(digest)
    db.commit()
    db.refresh(digest)
    return digest


def approve_digest(db: Session, digest: ReleaseDigest, *, approved_by) -> bool:
    if digest.status != "draft" or approved_by is None:
        return False
    digest.status = "approved"
    digest.approved_by = approved_by
    digest.approved_at = _now()
    digest.version += 1
    db.commit()
    db.refresh(digest)
    return True


def publish_digest(db: Session, digest: ReleaseDigest, *, published_by=None) -> bool:
    """Publish an APPROVED digest. Commits before any fan-out."""
    if digest.status != "approved":
        return False
    digest.status = "published"
    digest.published_at = _now()
    digest.published_by = published_by
    digest.version += 1
    db.commit()
    db.refresh(digest)
    return True


def digest_entries(db: Session, digest: ReleaseDigest) -> list[dict]:
    """The customer-facing content. `Release.notes` is deliberately not read."""
    out = []
    for rid in digest.release_ids or []:
        release = db.get(Release, uuid.UUID(str(rid)))
        if release is None or not release.customer_visible:
            continue
        out.append({
            "version": release.version,
            "title": release.title,
            # The approved customer summary, never the internal notes.
            "summary": release.customer_summary,
            "released_at": as_utc(release.released_at),
            "documentation_path": release.documentation_path,
            "rollout_status": release.rollout_status,
        })
    out.sort(key=lambda e: e["released_at"] or _now())
    return out


def notify_digest(db: Session, background, digest: ReleaseDigest) -> int:
    """Fan out a published digest to RELEASE_NOTES subscribers. Returns messages queued."""
    from .. import email as email_mod

    if digest.status != "published" or digest.published_at is None:
        return 0
    entries = digest_entries(db, digest)
    if not entries:
        return 0

    sent = 0
    for subscriber in audience(db, TOPIC_RELEASE_NOTES):
        if not _claim(db, "release_digest", "release_digest", digest.id, digest.version,
                      subscriber.email):
            continue
        manage, unsub = _links(db, subscriber)
        if not unsub:
            _release(db, "release_digest", "release_digest", digest.id, digest.version,
                     subscriber.email)
            continue
        sent += 1

        def send(addr=subscriber.email, m=manage, u=unsub, ver=digest.version):
            ok = email_mod.send_release_digest_email(
                to=addr, title=digest.title, summary=digest.summary, entries=entries,
                period_start=as_utc(digest.period_start),
                period_end=as_utc(digest.period_end),
                manage_url=m, unsubscribe_url=u)
            if not ok:
                _release(db, "release_digest", "release_digest", digest.id, ver, addr)

        background.add_task(send)
    return sent


# ══════════════════════════════════════════════════════════════════════════════════════════
# MKT-002 — feature availability announcement
# ══════════════════════════════════════════════════════════════════════════════════════════

def set_availability(db: Session, *, feature_key: str, feature_name: str, lifecycle: str,
                     customer_summary: str | None = None,
                     eligible_plans: list[str] | None = None,
                     eligible_regions: list[str] | None = None,
                     rollout_percentage: int | None = None,
                     documentation_path: str | None = None,
                     effective_at: datetime | None = None) -> FeatureAvailability | None:
    """Record authoritative rollout state for a feature."""
    from ..models import FEATURE_LIFECYCLES, REGION_KEYS

    if lifecycle not in FEATURE_LIFECYCLES:
        return None
    regions = [r for r in (eligible_regions or []) if r in REGION_KEYS]

    row = db.scalar(select(FeatureAvailability).where(
        FeatureAvailability.feature_key == feature_key))
    if row is None:
        row = FeatureAvailability(feature_key=feature_key, feature_name=feature_name)
        db.add(row)
    row.feature_name = feature_name
    row.lifecycle = lifecycle
    row.customer_summary = customer_summary
    row.eligible_plans = list(eligible_plans or [])
    row.eligible_regions = regions
    row.rollout_percentage = rollout_percentage
    row.documentation_path = documentation_path
    row.effective_at = effective_at
    db.commit()
    db.refresh(row)
    return row


def grant_availability(db: Session, availability: FeatureAvailability, *, org_id,
                       granted_by=None) -> FeatureAvailabilityGrant | None:
    """Record that one organization can actually use the feature."""
    if db.get(Organization, org_id) is None:
        return None
    existing = db.scalar(select(FeatureAvailabilityGrant).where(
        FeatureAvailabilityGrant.availability_id == availability.id,
        FeatureAvailabilityGrant.org_id == org_id))
    if existing is not None:
        existing.revoked_at = None
        db.commit()
        return existing
    row = FeatureAvailabilityGrant(availability_id=availability.id, org_id=org_id,
                                   granted_by=granted_by)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def region_key(org: Organization) -> str | None:
    """Best-effort platform region for an organization, or None.

    `Organization.region` is free text (real data contains values like "US East") and
    `country` is empty, so this maps only what it can name confidently and returns None
    otherwise. Callers treat None as NOT eligible for a region-restricted feature - failing
    closed, because telling a customer a feature is available in their region when we cannot
    establish their region is exactly the claim MKT-002 forbids.
    """
    raw = (getattr(org, "region", None) or "").strip().lower()
    if not raw:
        return None
    table = {
        "na": ("us", "usa", "united states", "canada", "north america", "us east",
               "us west", "us-east", "us-west"),
        "eu": ("eu", "europe", "united kingdom", "uk", "germany", "france", "ireland",
               "eu west", "eu-west"),
        "apac": ("apac", "asia", "asia pacific", "india", "singapore", "australia",
                 "japan", "ap south", "ap-south"),
        "sa": ("sa", "south america", "brazil", "latam"),
    }
    for key, needles in table.items():
        if any(raw == n or raw.startswith(f"{n} ") or n in raw for n in needles):
            return key
    return None


def org_eligible(db: Session, availability: FeatureAvailability,
                 org: Organization) -> bool:
    """Can THIS organization actually use the feature? Every check fails closed.

    plan     the organization's active subscription plan must be in `eligible_plans`.
    region   its resolved region must be in `eligible_regions`; unresolvable means no.
    grant    for a preview/pilot/beta/regional/restricted/invite-only feature, an explicit
             FeatureAvailabilityGrant must exist. A targeted feature is never broadcast.
    """
    if org is None:
        return False

    plans = availability.eligible_plans or []
    if plans:
        subscription = db.scalar(select(Subscription).where(
            Subscription.org_id == org.id,
            Subscription.status.in_(("active", "trialing"))))
        if subscription is None:
            return False
        from ..models import Plan

        plan = db.get(Plan, subscription.plan_id) if subscription.plan_id else None
        if plan is None or plan.slug not in plans:
            return False

    regions = availability.eligible_regions or []
    if regions:
        resolved = region_key(org)
        if resolved is None or resolved not in regions:
            return False

    if availability.lifecycle in TARGETED_LIFECYCLES:
        grant = db.scalar(select(FeatureAvailabilityGrant).where(
            FeatureAvailabilityGrant.availability_id == availability.id,
            FeatureAvailabilityGrant.org_id == org.id,
            FeatureAvailabilityGrant.revoked_at.is_(None)))
        if grant is None:
            return False
    return True


def create_announcement(db: Session, availability: FeatureAvailability, *, body: str,
                        headline: str | None = None) -> FeatureAnnouncement | None:
    """Draft an announcement, FREEZING the lifecycle it describes.

    Freezing matters: if the feature later moves to GA, this approved announcement keeps
    saying "preview" and a GA announcement needs its own approval.
    """
    if not (body or "").strip():
        return None
    existing = db.scalars(select(FeatureAnnouncement).where(
        FeatureAnnouncement.availability_id == availability.id)).all()
    row = FeatureAnnouncement(
        availability_id=availability.id, lifecycle=availability.lifecycle,
        headline=headline, body=body, status="draft", version=len(existing) + 1)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def approve_announcement(db: Session, announcement: FeatureAnnouncement, *,
                         approved_by) -> bool:
    if announcement.status != "draft" or approved_by is None:
        return False
    announcement.status = "approved"
    announcement.approved_by = approved_by
    announcement.approved_at = _now()
    db.commit()
    db.refresh(announcement)
    return True


def announcement_subject(announcement: FeatureAnnouncement,
                         availability: FeatureAvailability) -> str:
    """Subject derived from the FROZEN lifecycle, never composed per message.

    This is the structural reason a preview cannot be announced as generally available:
    the wording is looked up, and only LIFECYCLE_GA maps to available-now phrasing.
    """
    template = LIFECYCLE_SUBJECT.get(announcement.lifecycle,
                                     LIFECYCLE_SUBJECT[LIFECYCLE_GA])
    return template.format(feature=availability.feature_name)


def announcement_recipients(db: Session, availability: FeatureAvailability
                            ) -> list[MarketingSubscription]:
    """Subscribed to feature announcements AND actually eligible.

    Both conditions, always. A subscriber whose organization cannot use the feature is
    excluded, and a subscriber with no organization is excluded from a TARGETED feature
    (there is nothing to check eligibility against, so it fails closed).
    """
    out = []
    for subscriber in audience(db, TOPIC_FEATURE_ANNOUNCEMENTS):
        org = (db.get(Organization, subscriber.organization_id)
               if subscriber.organization_id else None)
        if org is None:
            # No organization to evaluate. A GA feature with no plan/region restriction is
            # genuinely available to everyone; anything targeted is not.
            if availability.lifecycle == LIFECYCLE_GA and not (
                    availability.eligible_plans or availability.eligible_regions):
                out.append(subscriber)
            continue
        if org_eligible(db, availability, org):
            out.append(subscriber)
    return out


def notify_announcement(db: Session, background, announcement: FeatureAnnouncement) -> int:
    """Send an APPROVED announcement to eligible, consented recipients."""
    from .. import email as email_mod

    if announcement.status not in ("approved", "sent"):
        return 0
    availability = db.get(FeatureAvailability, announcement.availability_id)
    if availability is None:
        return 0

    subject = announcement_subject(announcement, availability)
    sent = 0
    for subscriber in announcement_recipients(db, availability):
        if not _claim(db, "feature_announcement", "feature_announcement", announcement.id,
                      announcement.version, subscriber.email):
            continue
        manage, unsub = _links(db, subscriber)
        if not unsub:
            _release(db, "feature_announcement", "feature_announcement", announcement.id,
                     announcement.version, subscriber.email)
            continue
        sent += 1

        def send(addr=subscriber.email, m=manage, u=unsub, ver=announcement.version):
            ok = email_mod.send_feature_announcement_email(
                to=addr, subject=subject, feature_name=availability.feature_name,
                lifecycle=announcement.lifecycle,
                lifecycle_label=LIFECYCLE_LABELS.get(announcement.lifecycle, "Available"),
                headline=announcement.headline, body=announcement.body,
                documentation_path=availability.documentation_path,
                effective_at=as_utc(availability.effective_at),
                manage_url=m, unsubscribe_url=u)
            if not ok:
                _release(db, "feature_announcement", "feature_announcement",
                         announcement.id, ver, addr)

        background.add_task(send)
    if sent and announcement.status == "approved":
        announcement.status = "sent"
        announcement.sent_at = _now()
        db.commit()
    return sent


# ══════════════════════════════════════════════════════════════════════════════════════════
# MKT-003 — developer onboarding series
# ══════════════════════════════════════════════════════════════════════════════════════════

def enrol_developer(db: Session, *, email: str, source: str,
                    organization_id=None) -> DeveloperOnboardingEnrolment | None:
    """Enrol an address in the developer education series.

    Requires an ACTIVE MarketingSubscription carrying DEVELOPER_EDUCATION. Creating an API
    key, registering a webhook, holding a developer role or calling the API reaches this
    function from nowhere - the only caller is the opt-in path.
    """
    subscription = find(db, email)
    if subscription is None or subscription.status != "active":
        return None
    if TOPIC_DEVELOPER_EDUCATION not in (subscription.topics or []):
        return None

    existing = db.scalar(select(DeveloperOnboardingEnrolment).where(
        DeveloperOnboardingEnrolment.email == subscription.email))
    if existing is not None:
        if existing.status == "cancelled":
            existing.status = "active"
            existing.cancelled_at = None
            db.commit()
            db.refresh(existing)
        return existing

    row = DeveloperOnboardingEnrolment(
        email=subscription.email, subscription_id=subscription.id,
        organization_id=organization_id or subscription.organization_id,
        status="active", steps_sent=[], opt_in_source=source, opt_in_at=_now())
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def cancel_onboarding(db: Session, email: str) -> bool:
    """Stop future sequence messages. Called by unsubscribe(), and idempotent."""
    row = db.scalar(select(DeveloperOnboardingEnrolment).where(
        DeveloperOnboardingEnrolment.email == (email or "").strip().lower()))
    if row is None or row.status == "cancelled":
        return False
    row.status = "cancelled"
    row.cancelled_at = _now()
    db.commit()
    return True


def milestone_reached(db: Session, enrolment: DeveloperOnboardingEnrolment,
                      milestone: str) -> bool:
    """Has this OBSERVABLE milestone actually happened?

    Three milestones, and each is read from a real record:

        opt_in              the enrolment exists.
        credential_created  Organization.api_keys is non-empty.
        webhook_verified    a WebhookEndpoint for the org has verified_at set.

    Anything else returns False. In particular there is no "first successful API call":
    API-key request authentication is not wired into the request path and per-key telemetry
    does not exist, so that milestone cannot be observed - and an unobservable milestone is
    not used rather than being faked from something adjacent.
    """
    if milestone == "opt_in":
        return True
    org = (db.get(Organization, enrolment.organization_id)
           if enrolment.organization_id else None)
    if org is None:
        return False
    if milestone == "credential_created":
        return bool(org.api_keys)
    if milestone == "webhook_verified":
        return db.scalar(select(WebhookEndpoint).where(
            WebhookEndpoint.org_id == org.id,
            WebhookEndpoint.verified_at.isnot(None))) is not None
    return False


def next_step(db: Session, enrolment: DeveloperOnboardingEnrolment) -> str | None:
    """The next sequence step whose milestone is observed and which was not already sent."""
    if enrolment.status != "active":
        return None
    sent = set(enrolment.steps_sent or [])
    for step in ONBOARDING_STEPS:
        if step in sent:
            continue
        if milestone_reached(db, enrolment, ONBOARDING_MILESTONES[step]):
            return step
        # Steps are ordered: an unmet milestone stops the sequence rather than skipping ahead.
        return None
    return None


def notify_onboarding_step(db: Session, background,
                           enrolment: DeveloperOnboardingEnrolment, step: str) -> bool:
    """Send one sequence step, exactly once, and only while consent stands."""
    from .. import email as email_mod

    if step not in ONBOARDING_STEPS:
        return False
    if enrolment.status != "active":
        return False
    if step in (enrolment.steps_sent or []):
        return False
    # Re-check consent at send time: an unsubscribe between enrolment and delivery wins.
    if not eligible(db, TOPIC_DEVELOPER_EDUCATION, enrolment.email):
        return False

    subscription = find(db, enrolment.email)
    if not _claim(db, f"onboarding_{step}", "developer_onboarding", enrolment.id, 0,
                  enrolment.email):
        return False
    manage, unsub = _links(db, subscription)
    if not unsub:
        _release(db, f"onboarding_{step}", "developer_onboarding", enrolment.id, 0,
                 enrolment.email)
        return False

    enrolment.steps_sent = list(enrolment.steps_sent or []) + [step]
    if len(enrolment.steps_sent) >= len(ONBOARDING_STEPS):
        enrolment.status = "completed"
        enrolment.completed_at = _now()
    db.commit()

    def send():
        ok = email_mod.send_developer_onboarding_email(
            to=enrolment.email, step=step,
            milestone=ONBOARDING_MILESTONES[step],
            manage_url=manage, unsubscribe_url=unsub)
        if not ok:
            _release(db, f"onboarding_{step}", "developer_onboarding", enrolment.id, 0,
                     enrolment.email)

    background.add_task(send)
    return True


# ══════════════════════════════════════════════════════════════════════════════════════════
# MKT-004 — live events education, guides and webinars
# ══════════════════════════════════════════════════════════════════════════════════════════

def request_guide(db: Session, *, email: str, guide: str, name: str | None = None,
                  marketing_opt_in: bool = False, consent_ip: str | None = None,
                  consent_user_agent: str | None = None
                  ) -> tuple[GuideRequest | None, str | None]:
    """Somebody asked for one guide.

    Returns (request, raw_verification_token). The token is non-None ONLY when they ALSO
    ticked the marketing opt-in - fulfilling the thing they asked for is transactional, and
    is deliberately not conflated with consent to future campaigns.
    """
    address = (email or "").strip().lower()
    if "@" not in address or guide not in GUIDE_KEYS:
        return None, None

    row = GuideRequest(email=address, name=name, guide=guide, status="requested",
                       marketing_opt_in=bool(marketing_opt_in), version=0)
    db.add(row)

    raw = None
    if marketing_opt_in:
        # A SEPARATE consent record, with its own double opt-in. The guide is sent either
        # way; the campaign subscription only starts if they confirm the address.
        subscription, raw = subscribe(
            db, email=address, topics=[TOPIC_LIVE_EVENT_EDUCATION],
            source="guide_request", consent_ip=consent_ip,
            consent_user_agent=consent_user_agent)
        if subscription is not None:
            row.subscription_id = subscription.id
    db.commit()
    db.refresh(row)
    return row, raw


def notify_guide(db: Session, background, request: GuideRequest) -> bool:
    """Fulfil a guide request. Transactional: they asked for this specific thing.

    Carries no campaign content and creates no consent. It links the preference centre so
    somebody who did not opt in can still see they are not subscribed to anything.
    """
    from .. import email as email_mod

    if request.status != "requested":
        return False
    if not _claim(db, "guide_fulfilled", "guide_request", request.id, request.version,
                  request.email):
        return False
    request.status = "fulfilled"
    request.fulfilled_at = _now()
    db.commit()

    def send():
        ok = email_mod.send_guide_email(
            to=request.email, name=request.name, guide=request.guide,
            guide_label=GUIDE_LABELS.get(request.guide, "Guide"),
            subscribed=bool(request.subscription_id and request.marketing_opt_in))
        if not ok:
            request.status = "requested"
            request.fulfilled_at = None
            db.commit()
            _release(db, "guide_fulfilled", "guide_request", request.id, request.version,
                     request.email)

    background.add_task(send)
    return True


def schedule_webinar(db: Session, *, title: str, starts_at_utc: datetime,
                     description: str | None = None, duration_minutes: int = 60,
                     join_path: str | None = None) -> MarketingWebinar | None:
    if starts_at_utc is None:
        return None
    row = MarketingWebinar(
        title=title, description=description,
        starts_at_utc=starts_at_utc.astimezone(timezone.utc),
        duration_minutes=max(5, duration_minutes), status="scheduled",
        join_path=join_path, version=0)
    row.reference = f"WBN-{_now().year}-{secrets.token_hex(3).upper()}"
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def register_for_webinar(db: Session, webinar: MarketingWebinar, *, email: str,
                         name: str | None = None
                         ) -> MarketingWebinarRegistration | None:
    """Register for one webinar.

    Registering is consent to be emailed ABOUT THIS WEBINAR and nothing else: no
    MarketingSubscription is created here, and `subscription_id` is only ever populated by
    correlating an existing one.
    """
    address = (email or "").strip().lower()
    if "@" not in address:
        return None
    if webinar.status in ("cancelled", "completed"):
        return None

    existing = db.scalar(select(MarketingWebinarRegistration).where(
        MarketingWebinarRegistration.webinar_id == webinar.id,
        MarketingWebinarRegistration.email == address))
    if existing is not None:
        if existing.status == "cancelled":
            existing.status = "registered"
            existing.cancelled_at = None
            existing.version += 1
            db.commit()
            db.refresh(existing)
        return existing

    subscription = find(db, address)
    row = MarketingWebinarRegistration(
        webinar_id=webinar.id, email=address, name=name, status="registered",
        consent_basis="webinar_registration",
        subscription_id=subscription.id if subscription else None,
        reminders_sent=[], version=0)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def cancel_registration(db: Session, registration: MarketingWebinarRegistration) -> bool:
    if registration.status != "registered":
        return False
    registration.status = "cancelled"
    registration.cancelled_at = _now()
    registration.version += 1
    db.commit()
    return True


def reschedule_webinar(db: Session, webinar: MarketingWebinar,
                       *, starts_at_utc: datetime) -> tuple[bool, datetime | None]:
    """Move a webinar, keeping what it WAS so the message can show the change."""
    if webinar.status in ("cancelled", "completed"):
        return False, None
    new = starts_at_utc.astimezone(timezone.utc)
    if new == as_utc(webinar.starts_at_utc):
        return False, None
    previous = webinar.starts_at_utc
    webinar.previous_starts_at_utc = previous
    webinar.starts_at_utc = new
    webinar.status = "rescheduled"
    webinar.version += 1
    db.commit()
    db.refresh(webinar)
    return True, as_utc(previous)


def cancel_webinar(db: Session, webinar: MarketingWebinar) -> bool:
    if webinar.status in ("cancelled", "completed"):
        return False
    webinar.status = "cancelled"
    webinar.cancelled_at = _now()
    webinar.version += 1
    db.commit()
    db.refresh(webinar)
    return True


def complete_webinar(db: Session, webinar: MarketingWebinar, *,
                     followup_body: str | None = None, followup_approved_by=None) -> bool:
    """Mark a webinar delivered, optionally recording an APPROVED follow-up."""
    if webinar.status not in ("scheduled", "rescheduled"):
        return False
    webinar.status = "completed"
    webinar.completed_at = _now()
    if followup_body and followup_approved_by is not None:
        webinar.followup_body = followup_body
        webinar.followup_approved_by = followup_approved_by
    webinar.version += 1
    db.commit()
    db.refresh(webinar)
    return True


def record_attendance(db: Session, registration: MarketingWebinarRegistration) -> bool:
    """Record that somebody actually attended. An observation, never consent.

    There is no attendance integration, so this is only ever called by an operator with real
    knowledge - and MKT-004 follow-up checks the LIVE_EVENT_EDUCATION topic regardless.
    """
    if registration.status == "cancelled":
        return False
    if registration.attended_at is not None:
        return False
    registration.attended_at = _now()
    registration.status = "attended"
    registration.version += 1
    db.commit()
    return True


def active_registrations(db: Session, webinar: MarketingWebinar) -> list:
    return list(db.scalars(select(MarketingWebinarRegistration).where(
        MarketingWebinarRegistration.webinar_id == webinar.id,
        MarketingWebinarRegistration.status.in_(("registered", "attended")))).all())


def notify_webinar(db: Session, background, webinar: MarketingWebinar, *, variant: str,
                   previous_start: datetime | None = None) -> int:
    """Send one webinar lifecycle message to its registrants.

    `confirmation`, `reminder`, `rescheduled` and `cancelled` are TRANSACTIONAL to somebody
    who registered - they are about the thing that person signed up for. `followup` is
    promotional and is gated separately below.
    """
    from .. import email as email_mod

    if variant not in ("confirmation", "reminder", "rescheduled", "cancelled"):
        return 0
    sent = 0
    for registration in active_registrations(db, webinar):
        if not _claim(db, f"webinar_{variant}", "webinar", webinar.id, webinar.version,
                      registration.email):
            continue
        sent += 1

        def send(reg=registration, ver=webinar.version):
            ok = email_mod.send_webinar_email(
                to=reg.email, name=reg.name, variant=variant,
                reference=webinar.reference, title=webinar.title,
                description=webinar.description,
                starts_at_utc=as_utc(webinar.starts_at_utc),
                previous_starts_at_utc=as_utc(previous_start),
                duration_minutes=webinar.duration_minutes,
                join_path=webinar.join_path)
            if not ok:
                _release(db, f"webinar_{variant}", "webinar", webinar.id, ver, reg.email)

        background.add_task(send)
    return sent


def notify_webinar_followup(db: Session, background, webinar: MarketingWebinar) -> int:
    """Send the promotional follow-up. Two independent gates.

    1. The follow-up must be APPROVED and have a body.
    2. Each recipient must hold LIVE_EVENT_EDUCATION consent.

    Attendance is explicitly not one of them: attending a webinar is not consent to a
    campaign, so a registrant who never opted in gets nothing here.
    """
    from .. import email as email_mod

    if webinar.status != "completed":
        return 0
    if webinar.followup_approved_by is None or not (webinar.followup_body or "").strip():
        return 0

    sent = 0
    for registration in active_registrations(db, webinar):
        if not eligible(db, TOPIC_LIVE_EVENT_EDUCATION, registration.email):
            continue
        subscription = find(db, registration.email)
        if not _claim(db, "webinar_followup", "webinar", webinar.id, webinar.version,
                      registration.email):
            continue
        manage, unsub = _links(db, subscription)
        if not unsub:
            _release(db, "webinar_followup", "webinar", webinar.id, webinar.version,
                     registration.email)
            continue
        sent += 1

        def send(reg=registration, m=manage, u=unsub, ver=webinar.version):
            ok = email_mod.send_webinar_followup_email(
                to=reg.email, name=reg.name, title=webinar.title,
                body=webinar.followup_body, manage_url=m, unsubscribe_url=u)
            if not ok:
                _release(db, "webinar_followup", "webinar", webinar.id, ver, reg.email)

        background.add_task(send)
    return sent


def sweep(db: Session, background) -> dict:
    """Advance developer onboarding sequences whose next milestone is now observed.

    Rides the shared leader-elected ticker. Webinar reminders are NOT swept here: no reminder
    threshold policy is configured (the same honest gap MAINTENANCE_REMINDER_HOURS has), so
    a reminder is sent only when an operator asks for one.
    """
    counts = {"onboarding_steps": 0}
    enrolments = db.scalars(select(DeveloperOnboardingEnrolment).where(
        DeveloperOnboardingEnrolment.status == "active")).all()
    for enrolment in enrolments:
        step = next_step(db, enrolment)
        if step and notify_onboarding_step(db, background, enrolment, step):
            counts["onboarding_steps"] += 1
    return counts
