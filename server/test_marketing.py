"""MKT foundation and MKT-001 -> MKT-004 (ZST-EC-001).

The separation the spec calls the critical requirement gets the most tests, and the one that
matters most is `test_no_operational_relationship_creates_consent`: it walks a person through
being a status subscriber, a security contact, an org member, a contributor, an event
registrant, a support requester, a privacy requester, a vulnerability reporter, a memorial
event's grantee and an API-key holder, and asserts that none of it put a row in
`marketing_subscriptions`.

    FOUNDATION  operational != consent      test_no_operational_relationship_creates_consent
    FOUNDATION  topics are not widened      test_one_topic_is_not_four
    FOUNDATION  unsubscribe is immediate    test_unsubscribe_suppresses_immediately
    FOUNDATION  and reaches nothing else    test_unsubscribe_cannot_silence_mandatory_mail
    MKT-001     approval gates distribution test_raw_release_does_not_send
    MKT-001     internal notes never ship   test_internal_release_notes_never_ship
    MKT-002     preview stays preview       test_lifecycle_wording_is_never_upgraded
    MKT-002     eligibility is enforced     test_recipient_must_be_eligible
    MKT-003     activity != enrolment       test_api_key_alone_does_not_enrol
    MKT-003     no fabricated telemetry     test_no_unobservable_milestone
    MKT-004     the guide is not consent    test_guide_fulfilment_creates_no_consent
    MKT-004     attendance is not consent   test_attendance_is_not_consent

Run with `python test_marketing.py` (or pytest).
"""
import hashlib
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from sqlalchemy import select
from starlette.testclient import TestClient

import app.email as email_mod
import app.main as m
from app.db import SessionLocal
from app.models import (
    LIFECYCLE_GA,
    MARKETING_TOPICS,
    ONBOARDING_STEPS,
    TARGETED_LIFECYCLES,
    TOPIC_DEVELOPER_EDUCATION,
    TOPIC_FEATURE_ANNOUNCEMENTS,
    TOPIC_LIVE_EVENT_EDUCATION,
    TOPIC_RELEASE_NOTES,
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
    Plan,
    Release,
    ReleaseDigest,
    Subscription,
    User,
    WebhookEndpoint,
)
from app.security import hash_password
from app.services import marketing as mkt

VERIFY = email_mod.MKT_VERIFY_SUBJECT
CONFIRMED = email_mod.MKT_CONFIRMED_SUBJECT
PREFERENCES = email_mod.MKT_PREFERENCES_SUBJECT
UNSUBSCRIBED = email_mod.MKT_UNSUBSCRIBED_SUBJECT
DIGEST = email_mod.MKT_001_SUBJECT

PASSWORD = "correct-horse-battery"

# Internal release prose. If this ever appears in an outbound message, internal notes shipped.
INTERNAL_NOTES = ("INTERNAL: reverted Dave's migration, hotfixed the leak in worker.py, "
                  "customer ACME-4471 was affected, do not mention externally")


class _Resp:
    status_code = 200
    text = "{}"

    def raise_for_status(self):
        return None


class Captured:
    def __init__(self, fail=False):
        self.calls, self.fail = [], fail

    def __call__(self, url, headers=None, json=None, timeout=None):
        self.calls.append({"url": url, "payload": json or {}})
        if self.fail:
            import httpx
            raise httpx.ConnectError("simulated Resend outage")
        return _Resp()

    @property
    def subjects(self):
        return [c["payload"]["subject"] for c in self.calls]

    def of(self, subject):
        hits = [c["payload"] for c in self.calls if c["payload"]["subject"] == subject]
        assert hits, f"no message with subject {subject!r}; got {self.subjects}"
        return hits[-1]

    def to(self, subject):
        return sorted(c["payload"]["to"][0].lower()
                      for c in self.calls if c["payload"]["subject"] == subject)

    def count(self, subject):
        return sum(1 for s in self.subjects if s == subject)

    def blob(self):
        return "".join(c["payload"].get("text", "") + c["payload"].get("html", "")
                       for c in self.calls)


_LEAKS: list[str] = []


def _deny(url, headers=None, json=None, timeout=None):
    _LEAKS.append((json or {}).get("subject", "?"))
    raise RuntimeError("outbound email attempted outside a capture context")


email_mod.httpx.post = _deny


def _capture(fail=False):
    cap = Captured(fail=fail)
    return cap, patch.object(email_mod.httpx, "post", cap)


class _Bg:
    def add_task(self, fn, *args, **kwargs):
        fn(*args, **kwargs)


def _new_email(tag="mkt"):
    return f"{tag}-{uuid.uuid4().hex[:12]}@example.com"


def _now():
    return datetime.now(timezone.utc)


class World:
    """Two organizations on different plans, so MKT-002 eligibility is really testable."""

    def __init__(self):
        db = SessionLocal()
        try:
            self.plan_enterprise = Plan(
                name=f"Enterprise {uuid.uuid4().hex[:5]}",
                slug=f"enterprise-{uuid.uuid4().hex[:6]}", price_monthly=0,
                currency="USD", is_active=True)
            self.plan_free = Plan(
                name=f"Free {uuid.uuid4().hex[:5]}",
                slug=f"free-{uuid.uuid4().hex[:6]}", price_monthly=0,
                currency="USD", is_active=True)
            db.add_all([self.plan_enterprise, self.plan_free])
            db.flush()
            self.enterprise_slug = self.plan_enterprise.slug
            self.free_slug = self.plan_free.slug

            # Enterprise tenant in Europe.
            org = Organization(name=f"Ent Co {uuid.uuid4().hex[:6]}", status="active",
                               timezone="UTC", region="EU West")
            # Free tenant in Asia Pacific.
            free = Organization(name=f"Free Co {uuid.uuid4().hex[:6]}", status="active",
                                timezone="UTC", region="APAC")
            # A tenant whose region is unresolvable free text: must FAIL CLOSED.
            vague = Organization(name=f"Vague Co {uuid.uuid4().hex[:6]}", status="active",
                                 timezone="UTC", region="Zone 7")
            db.add_all([org, free, vague])
            db.flush()
            self.org_id, self.free_org_id, self.vague_org_id = org.id, free.id, vague.id

            db.add_all([
                Subscription(org_id=org.id, plan_id=self.plan_enterprise.id,
                             status="active", seats=10),
                Subscription(org_id=free.id, plan_id=self.plan_free.id,
                             status="active", seats=1),
                Subscription(org_id=vague.id, plan_id=self.plan_enterprise.id,
                             status="active", seats=5),
            ])
            self.admin_email = _new_email("admin")
            self.admin_id = self._u(db, org.id, "super_admin", self.admin_email)
            self.member_email = _new_email("member")
            self.member_id = self._u(db, org.id, "org_admin", self.member_email)
            org.owner_user_id = self.admin_id
            free.owner_user_id = self._u(db, free.id, "org_admin", _new_email("freeadmin"))
            vague.owner_user_id = self._u(db, vague.id, "org_admin",
                                          _new_email("vagueadmin"))
            db.commit()
        finally:
            db.close()

    def _u(self, db, org_id, role, email):
        user = User(org_id=org_id, full_name="Marketing Person", role=role,
                    is_active=True, email=email.lower(),
                    username=f"u{uuid.uuid4().hex[:10]}",
                    password_hash=hash_password(PASSWORD), email_verified=True,
                    email_verified_at=_now())
        db.add(user)
        db.flush()
        return user.id

    def subscriber(self, topics, *, organization_id=None, source="preference_center"):
        """A CONFIRMED subscriber. Returns (email, manage_token, unsubscribe_token)."""
        db = SessionLocal()
        try:
            address = _new_email("sub")
            subscription, raw = mkt.subscribe(
                db, email=address, topics=topics, source=source,
                organization_id=organization_id)
            assert subscription is not None and raw
            found, outcome, manage, unsub = mkt.confirm(db, token=raw)
            assert outcome == "active", outcome
            return address, manage, unsub
        finally:
            db.close()

    def cleanup(self):
        db = SessionLocal()
        try:
            db.query(MarketingNotice).delete()
            db.query(MarketingWebinarRegistration).delete()
            db.query(MarketingWebinar).delete()
            db.query(GuideRequest).delete()
            db.query(DeveloperOnboardingEnrolment).delete()
            db.query(MarketingSubscription).delete()
            db.query(FeatureAnnouncement).delete()
            db.query(FeatureAvailabilityGrant).delete()
            db.query(FeatureAvailability).delete()
            db.query(ReleaseDigest).delete()
            db.query(Release).filter(Release.title.like("MKT test%")).delete()
            db.commit()
            for org_id in (self.org_id, self.free_org_id, self.vague_org_id):
                db.query(WebhookEndpoint).filter(
                    WebhookEndpoint.org_id == org_id).delete()
                db.query(Subscription).filter(Subscription.org_id == org_id).delete()
                db.commit()
                org = db.get(Organization, org_id)
                if org is not None:
                    org.owner_user_id = None
                    db.commit()
                db.query(User).filter(User.org_id == org_id).delete()
                db.commit()
                if org is not None:
                    db.delete(org)
                db.commit()
            for plan in (self.plan_enterprise, self.plan_free):
                row = db.get(Plan, plan.id)
                if row is not None:
                    db.delete(row)
            db.commit()
        except Exception:
            db.rollback()
        finally:
            db.close()


RESULTS = []


def run(fn):
    world = World()
    try:
        fn(world)
        RESULTS.append((fn.__name__, None))
        print(f"ok  {fn.__name__}")
    except Exception as exc:  # noqa: BLE001
        RESULTS.append((fn.__name__, exc))
        print(f"FAIL {fn.__name__}: {type(exc).__name__}: {exc}")
    finally:
        world.cleanup()


# ══ FOUNDATION — consent ════════════════════════════════════════════════════════════════

def test_double_opt_in_is_required(w):
    """5 - and a pending subscriber receives nothing."""
    db = SessionLocal()
    try:
        address = _new_email("optin")
        subscription, raw = mkt.subscribe(db, email=address,
                                           topics=[TOPIC_RELEASE_NOTES],
                                           source="website_form")
        assert subscription.status == "pending"
        assert subscription.consent_basis == "consent"
        assert len(raw) >= 40
        assert subscription.verification_token_hash == hashlib.sha256(
            raw.encode()).hexdigest()
        assert subscription.verification_expires_at > _now()
        # A pending subscriber is not in any audience.
        assert mkt.eligible(db, TOPIC_RELEASE_NOTES, address) is False
        assert address not in [s.email for s in mkt.audience(db, TOPIC_RELEASE_NOTES)]

        found, outcome, manage, unsub = mkt.confirm(db, token=raw)
        assert outcome == "active" and manage and unsub
        assert manage != unsub, "the manage and unsubscribe handles must be distinct"
        assert found.verified_at is not None
        assert mkt.eligible(db, TOPIC_RELEASE_NOTES, address) is True
        # Single use.
        assert mkt.confirm(db, token=raw)[1] == "already_used"
        # An address with no topic is not consent to anything.
        assert mkt.subscribe(db, email=_new_email(), topics=[],
                             source="website_form")[0] is None
    finally:
        db.close()


def test_one_topic_is_not_four(w):
    """Requesting one topic never enrols the other three."""
    db = SessionLocal()
    try:
        address, manage, _u = w.subscriber([TOPIC_RELEASE_NOTES])
        subscription = mkt.find(db, address)
        assert subscription.topics == [TOPIC_RELEASE_NOTES]
        for other in (TOPIC_FEATURE_ANNOUNCEMENTS, TOPIC_DEVELOPER_EDUCATION,
                      TOPIC_LIVE_EVENT_EDUCATION):
            assert mkt.eligible(db, other, address) is False, other
        # Unknown topics are dropped rather than stored.
        assert mkt.clean_topics(["release_notes", "everything", None]) == \
            [TOPIC_RELEASE_NOTES]
    finally:
        db.close()


def test_no_operational_relationship_creates_consent(w):
    """The critical requirement, walked end to end.

    Every one of these is a real operational relationship created through the real service,
    and NONE of them may put a row in `marketing_subscriptions`.
    """
    from app.models import (
        EventContributorGrant, EventRegistration, OrganizationSecurityContact,
        PrivacyRequest, StatusSubscriber, SupportTicket, VulnerabilityReport,
    )
    from app.services import security_comms as sc
    from app.services import status_publication as sp
    from app.services import vuln_disclosure as vd

    db = SessionLocal()
    try:
        org = db.get(Organization, w.org_id)
        people = {}

        # 1. A status subscriber - confirmed, so genuinely opted IN to status.
        people["status"] = _new_email("statussub")
        sp.seed_components(db)
        sub, raw = sp.subscribe(db, email=people["status"], components=[], regions=[])
        sp.confirm(db, token=raw)

        # 2. A verified security contact.
        people["security"] = _new_email("seccontact")
        contact, craw = sc.nominate_contact(db, org, email=people["security"],
                                             display_name="Sec", created_by=w.admin_id)
        sc.verify_contact(db, token=craw)

        # 3. An organization member (created in the World).
        people["member"] = w.member_email

        # 4. A contributor grant.
        from app.models import Event
        event = Event(org_id=org.id, created_by=w.admin_id, title="MKT test memorial",
                      status="published", visibility="private", category="memorial",
                      start_time=_now() + timedelta(days=3))
        db.add(event)
        db.flush()
        people["contributor"] = _new_email("contributor")
        db.add(EventContributorGrant(event_id=event.id, org_id=org.id,
                                     email=people["contributor"].lower(),
                                     role="speaker", status="invited",
                                     invited_by=w.admin_id))

        # 5. An event registrant / memorial-service participant. `category="memorial"`
        #    above makes this the bereavement case the spec singles out.
        people["memorial_participant"] = _new_email("mourner")
        db.add(EventRegistration(event_id=event.id, name="Attendee",
                                 email=people["memorial_participant"].lower()))

        # 6. A support requester.
        people["support"] = _new_email("supportreq")
        db.add(SupportTicket(org_id=org.id, subject="Help", message="Please help",
                             status="open", priority="normal",
                             requester_email=people["support"].lower()))

        # 7. A privacy requester.
        people["privacy"] = _new_email("privacyreq")
        db.add(PrivacyRequest(org_id=org.id,
                              request_reference=f"PRV-{uuid.uuid4().hex[:6]}",
                              request_type="access", status="received",
                              requester_email=people["privacy"].lower()))

        # 8. A vulnerability reporter.
        people["reporter"] = _new_email("researcher")
        vd.submit(db, reporter_email=people["reporter"], title="An issue",
                  category="configuration",
                  description="A description long enough to be accepted by the service.")

        # 9. An API-key holder / developer.
        org.api_keys = [{"id": str(uuid.uuid4()), "name": "prod",
                         "prefix": "zs_live_abc", "created_at": _now().isoformat()}]
        people["developer"] = w.admin_email
        db.commit()

        # NOT ONE of them is a marketing subscriber.
        for role, address in people.items():
            assert db.query(MarketingSubscription).filter(
                MarketingSubscription.email == address.lower()).count() == 0, (
                f"{role} was enrolled in marketing")
            assert mkt.find(db, address) is None, f"{role} has a marketing subscription"
            for topic in MARKETING_TOPICS:
                assert mkt.eligible(db, topic, address) is False, f"{role} / {topic}"

        # And no audience picks any of them up.
        for topic in MARKETING_TOPICS:
            audience = {s.email for s in mkt.audience(db, topic)}
            for role, address in people.items():
                assert address.lower() not in audience, f"{role} in {topic} audience"

        # Only an explicit subscription enrols anybody - including these same people.
        confirmed, _m, _u = w.subscriber([TOPIC_RELEASE_NOTES])
        assert mkt.eligible(db, TOPIC_RELEASE_NOTES, confirmed) is True

        db.query(EventRegistration).filter(
            EventRegistration.event_id == event.id).delete()
        db.query(EventContributorGrant).filter(
            EventContributorGrant.event_id == event.id).delete()
        db.query(SupportTicket).filter(
            SupportTicket.requester_email == people["support"].lower()).delete()
        db.query(PrivacyRequest).filter(
            PrivacyRequest.requester_email == people["privacy"].lower()).delete()
        db.query(VulnerabilityReport).filter(
            VulnerabilityReport.reporter_email == people["reporter"].lower()).delete()
        db.query(StatusSubscriber).filter(
            StatusSubscriber.email == people["status"].lower()).delete()
        db.query(OrganizationSecurityContact).filter(
            OrganizationSecurityContact.email == people["security"].lower()).delete()
        db.delete(db.get(Event, event.id))
        db.commit()
    finally:
        db.close()


def test_marketing_service_reads_no_operational_table(w):
    """Structural: the audience builder cannot see an operational relationship.

    Stronger than the behavioural test above - it proves there is no code path at all, not
    just that today's paths happen not to fire.
    """
    import ast
    import inspect

    source = inspect.getsource(mkt)
    tree = ast.parse(source)
    # Every name referenced anywhere in the module, docstrings excluded by construction.
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    names |= {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    for operational in ("StatusSubscriber", "OrganizationSecurityContact",
                        "EventContributorGrant", "EventRegistration", "SupportTicket",
                        "PrivacyRequest", "VulnerabilityReport", "Invitation",
                        "ContributorSession", "TrustEvidenceRequest"):
        assert operational not in names, (
            f"services/marketing.py references {operational} - an operational "
            "relationship must not be reachable from a marketing audience")

    # The two it MAY read are read only for milestone observation, never for enrolment.
    enrol = inspect.getsource(mkt.enrol_developer)
    for operational in ("api_keys", "WebhookEndpoint", "User", "Organization"):
        assert operational not in enrol, (
            f"enrol_developer consults {operational} - enrolment must come only from "
            "an explicit opt-in")


def test_marketing_preferences_are_not_operational_preferences(w):
    """Marketing lives in its own store, never in Organization.notifications."""
    from app.services import notifications

    # The operational preference catalog still has NO marketing key.
    assert notifications.MARKETING_PREFERENCE_KEYS == frozenset()
    keys = {entry["key"] for entry in notifications.CATALOG}
    for topic in MARKETING_TOPICS:
        assert topic not in keys, f"{topic} leaked into the operational preference catalog"

    db = SessionLocal()
    try:
        address, manage, _u = w.subscriber([TOPIC_RELEASE_NOTES],
                                           organization_id=w.org_id)
        org = db.get(Organization, w.org_id)
        # Consenting to marketing wrote nothing into the org's operational preferences.
        for topic in MARKETING_TOPICS:
            assert topic not in (org.notifications or {}), topic
    finally:
        db.close()


def test_preference_change_and_unsubscribe_notices(w):
    """6 - and a no-op change sends nothing."""
    db = SessionLocal()
    try:
        address, manage, unsub = w.subscriber([TOPIC_RELEASE_NOTES])
        subscription = mkt.find(db, address)
        changed, previous = mkt.update_topics(db, subscription, [TOPIC_RELEASE_NOTES])
        assert changed is False and previous == [TOPIC_RELEASE_NOTES]

        changed, previous = mkt.update_topics(
            db, subscription, [TOPIC_RELEASE_NOTES, TOPIC_FEATURE_ANNOUNCEMENTS])
        assert changed is True and previous == [TOPIC_RELEASE_NOTES]
        assert mkt.eligible(db, TOPIC_FEATURE_ANNOUNCEMENTS, address) is True

        # Choosing zero topics is a valid way to stop everything.
        changed, previous = mkt.update_topics(db, subscription, [])
        assert changed is True
        assert subscription.status == "unsubscribed"
        for topic in MARKETING_TOPICS:
            assert mkt.eligible(db, topic, address) is False
    finally:
        db.close()


def test_unsubscribe_suppresses_immediately(w):
    """7 - and the suppression is durable, not just in-process."""
    db = SessionLocal()
    try:
        address, manage, unsub = w.subscriber([TOPIC_RELEASE_NOTES,
                                               TOPIC_FEATURE_ANNOUNCEMENTS])
        subscription = mkt.find(db, address)
        assert mkt.unsubscribe(db, subscription) is True
        assert subscription.status == "unsubscribed"
        assert subscription.unsubscribed_at is not None
        # Immediately, in a SEPARATE session: committed, not pending.
        other = SessionLocal()
        try:
            assert mkt.eligible(other, TOPIC_RELEASE_NOTES, address) is False
            assert mkt.eligible(other, TOPIC_FEATURE_ANNOUNCEMENTS, address) is False
            assert address.lower() not in {
                s.email for s in mkt.audience(other, TOPIC_RELEASE_NOTES)}
        finally:
            other.close()
        assert mkt.unsubscribe(db, subscription) is False

        cap, ctx = _capture()
        with ctx:
            email_mod.send_marketing_unsubscribed_email(
                address, resubscribe_url="https://example.test/preferences")
        text = cap.of(UNSUBSCRIBED)["text"]
        assert "Account, security, billing, privacy and support emails are separate" in text
        assert "status page" in text
    finally:
        db.close()


def test_unsubscribe_cannot_silence_mandatory_mail(w):
    """8, 9, 10 - the corollary of the separation."""
    import ast
    import inspect

    from app.services import notifications

    db = SessionLocal()
    try:
        address, manage, unsub = w.subscriber([TOPIC_RELEASE_NOTES],
                                              organization_id=w.org_id)
        subscription = mkt.find(db, address)
        org = db.get(Organization, w.org_id)
        before = dict(org.notifications or {})
        mkt.unsubscribe(db, subscription)
        db.expire_all()
        # Structural: `unsubscribe` writes THIS row and nothing else.
        body = ast.parse(inspect.getsource(mkt.unsubscribe).lstrip()).body[0]
        if ast.get_docstring(body):
            body.body = body.body[1:]
        code = ast.unparse(body)
        for other in ("Organization", "notifications", "StatusSubscriber",
                      "OrganizationSecurityContact", "security_alerts"):
            assert other not in code, f"unsubscribe touches {other}"
        assert dict(db.get(Organization, w.org_id).notifications or {}) == before

        # And no mandatory family is marketing, in either direction.
        for family in ("IDN-001", "IDN-005", "SEC-001", "PRV-001", "COM-001", "COM-006",
                       "SUP-001", "TRU-001", "MED-011"):
            assert notifications.is_marketing(family) is False, family
            assert notifications.is_mandatory(family) is True, family
        for family in notifications.MARKETING_FAMILIES:
            assert notifications.is_mandatory(family) is False, family
    finally:
        db.close()


def test_every_marketing_sender_requires_unsubscribe(w):
    """Structural: promotional mail cannot be constructed without one-click unsubscribe."""
    import inspect

    promotional = ("send_release_digest_email", "send_feature_announcement_email",
                   "send_developer_onboarding_email", "send_webinar_followup_email",
                   "send_marketing_confirmed_email", "send_marketing_preferences_email")
    for name in promotional:
        params = inspect.signature(getattr(email_mod, name)).parameters
        assert "unsubscribe_url" in params, f"{name} has no unsubscribe"
        assert params["unsubscribe_url"].default is inspect.Parameter.empty, (
            f"{name}'s unsubscribe_url is optional - it must be required")

    # And the inverse: no security, identity, privacy, billing or support sender takes one,
    # so unsubscribing cannot reach them even by accident.
    for name in ("send_security_advisory_email", "send_vulnerability_received_email",
                 "send_vulnerability_update_email", "send_trust_access_approved_email"):
        params = inspect.signature(getattr(email_mod, name)).parameters
        assert "unsubscribe_url" not in params, f"{name} accepts an unsubscribe link"


def test_unsubscribe_token_grants_nothing_else(w):
    """The one-click handle is purpose-bound and is not a credential."""
    db = SessionLocal()
    try:
        address, manage, unsub = w.subscriber([TOPIC_RELEASE_NOTES])
        # The handles are not interchangeable.
        assert mkt.by_unsubscribe_token(db, manage) is None
        assert mkt.by_manage_token(db, unsub) is None
        assert mkt.by_unsubscribe_token(db, unsub) is not None
    finally:
        db.close()

    client = TestClient(m.app)
    # Reading preferences with the unsubscribe handle fails.
    assert client.get(f"/api/trust/marketing/preferences?t={unsub}").status_code == 404
    # And it grants no account access anywhere.
    assert client.get(f"/api/organization/overview",
                      headers={"Authorization": f"Bearer {unsub}"}).status_code == 401


def test_public_consent_endpoints(w):
    """The preference centre works without a login, because unsubscribing must."""
    client = TestClient(m.app)
    address = _new_email("api")
    cap, ctx = _capture()
    with ctx:
        r = client.post("/api/trust/marketing/subscribe", json={
            "email": address, "topics": [TOPIC_RELEASE_NOTES], "source": "website_form"})
        assert r.status_code == 202, r.text
        assert r.json()["status"] == "pending_verification"
        assert cap.count(VERIFY) == 1
        token = cap.of(VERIFY)["text"].split("confirm=")[1].split()[0].strip()

        c = client.post("/api/trust/marketing/confirm", json={"token": token})
        assert c.status_code == 200, c.text
        manage = c.json()["manage_token"]
        assert cap.count(CONFIRMED) == 1

        p = client.get(f"/api/trust/marketing/preferences?t={manage}")
        assert p.status_code == 200 and p.json()["topics"] == [TOPIC_RELEASE_NOTES]
        u = client.patch(f"/api/trust/marketing/preferences?t={manage}",
                         json={"topics": [TOPIC_LIVE_EVENT_EDUCATION]})
        assert u.status_code == 200 and u.json()["changed"] is True

    db = SessionLocal()
    try:
        row = mkt.find(db, address)
        assert row.topics == [TOPIC_LIVE_EVENT_EDUCATION]
        assert row.consent_ip is not None, "consent evidence must be recorded"
    finally:
        db.close()


# ══ MKT-001 ═════════════════════════════════════════════════════════════════════════════

def _release(db, w, *, approved=True, version="2026.9.1"):
    release = Release(version=version, title=f"MKT test release {version}",
                      notes=INTERNAL_NOTES, channel="production",
                      released_by="ops", released_at=_now() - timedelta(days=1))
    db.add(release)
    db.commit()
    db.refresh(release)
    if approved:
        assert mkt.approve_release(
            db, release, customer_summary="Faster event start-up and clearer errors.",
            approved_by=w.admin_id, documentation_path="/docs/events",
            rollout_status="All regions") is True
    return release


def test_raw_release_does_not_send(w):
    """1, 2 - a Release row is not permission to email anybody."""
    db = SessionLocal()
    try:
        unapproved = _release(db, w, approved=False)
        assert unapproved.customer_visible is False
        assert unapproved.approved_by is None
        # A digest cannot even be built from it.
        assert mkt.create_digest(db, title="Nope",
                                 period_start=_now() - timedelta(days=30),
                                 period_end=_now(),
                                 release_ids=[unapproved.id]) is None
        # Approval requires a customer-facing summary AND a named approver.
        assert mkt.approve_release(db, unapproved, customer_summary="",
                                   approved_by=w.admin_id) is False
        assert mkt.approve_release(db, unapproved, customer_summary="Real summary.",
                                   approved_by=None) is False
        cap, ctx = _capture()
        with ctx:
            pass
        assert cap.calls == []
    finally:
        db.close()


def test_digest_requires_approval_before_publication(w):
    """2, 3, 4."""
    db = SessionLocal()
    try:
        release = _release(db, w)
        address, manage, unsub = w.subscriber([TOPIC_RELEASE_NOTES])
        digest = mkt.create_digest(db, title="September at Zoiko",
                                    period_start=_now() - timedelta(days=30),
                                    period_end=_now(), release_ids=[release.id],
                                    summary="A quieter month, deliberately.")
        assert digest is not None and digest.status == "draft"
        # A draft cannot be published, and an unapproved digest sends nothing.
        assert mkt.publish_digest(db, digest) is False
        cap0, ctx0 = _capture()
        with ctx0:
            assert mkt.notify_digest(db, _Bg(), digest) == 0
        assert cap0.calls == []

        assert mkt.approve_digest(db, digest, approved_by=None) is False
        assert mkt.approve_digest(db, digest, approved_by=w.admin_id) is True
        assert digest.approved_by == w.admin_id
        # Approved but not yet published: still nothing goes out.
        cap1, ctx1 = _capture()
        with ctx1:
            assert mkt.notify_digest(db, _Bg(), digest) == 0
        assert cap1.calls == []

        assert mkt.publish_digest(db, digest, published_by=w.admin_id) is True
        cap, ctx = _capture()
        with ctx:
            assert mkt.notify_digest(db, _Bg(), digest) == 1
        assert cap.to(DIGEST) == [address.lower()]
    finally:
        db.close()


def test_internal_release_notes_never_ship(w):
    """7 - and the digest carries the approved summary instead."""
    import ast
    import inspect

    db = SessionLocal()
    try:
        release = _release(db, w)
        address, manage, unsub = w.subscriber([TOPIC_RELEASE_NOTES])
        digest = mkt.create_digest(db, title="September",
                                    period_start=_now() - timedelta(days=30),
                                    period_end=_now(), release_ids=[release.id])
        mkt.approve_digest(db, digest, approved_by=w.admin_id)
        mkt.publish_digest(db, digest, published_by=w.admin_id)
        cap, ctx = _capture()
        with ctx:
            mkt.notify_digest(db, _Bg(), digest)
        blob = cap.blob()
        assert INTERNAL_NOTES not in blob, "internal release notes were mailed"
        for leak in ("INTERNAL:", "Dave's migration", "worker.py", "ACME-4471"):
            assert leak not in blob, f"internal note leaked: {leak}"
        assert "Faster event start-up" in blob, "the approved summary should be there"

        # Structural: nothing in the digest path reads Release.notes. Checked against the
        # CODE with docstrings and comments stripped - both functions legitimately explain
        # in prose that they do NOT read it, and a bare substring search would flag the
        # explanation rather than a defect.
        for fn in (mkt.digest_entries, mkt.notify_digest):
            body = ast.parse(inspect.getsource(fn).lstrip()).body[0]
            if ast.get_docstring(body):
                body.body = body.body[1:]
            assert ".notes" not in ast.unparse(body), (
                f"{fn.__name__} reads Release.notes")
    finally:
        db.close()


def test_digest_reaches_only_subscribers_and_carries_links(w):
    """3, 5, 6, 8."""
    db = SessionLocal()
    try:
        release = _release(db, w)
        wanted, _m1, _u1 = w.subscriber([TOPIC_RELEASE_NOTES])
        # Subscribed to a DIFFERENT topic: must not receive the digest.
        other, _m2, _u2 = w.subscriber([TOPIC_FEATURE_ANNOUNCEMENTS])
        # Unsubscribed.
        gone, _m3, _u3 = w.subscriber([TOPIC_RELEASE_NOTES])
        mkt.unsubscribe(db, mkt.find(db, gone))

        digest = mkt.create_digest(db, title="September",
                                    period_start=_now() - timedelta(days=30),
                                    period_end=_now(), release_ids=[release.id])
        mkt.approve_digest(db, digest, approved_by=w.admin_id)
        mkt.publish_digest(db, digest, published_by=w.admin_id)
        cap, ctx = _capture()
        with ctx:
            queued = mkt.notify_digest(db, _Bg(), digest)
            # 8 - a second publication run adds nothing.
            n = len(cap.calls)
            assert mkt.notify_digest(db, _Bg(), digest) == 0
        assert len(cap.calls) == n
        got = cap.to(DIGEST)
        assert got == [wanted.lower()], got
        assert other.lower() not in got and gone.lower() not in got

        # 5, 6 - one-click unsubscribe and a preference centre in the message.
        text = cap.of(DIGEST)["text"]
        assert "/unsubscribe?t=" in text
        assert "/preferences?t=" in text
    finally:
        db.close()


# ══ MKT-002 ═════════════════════════════════════════════════════════════════════════════

def _availability(db, lifecycle, **kw):
    kw.setdefault("feature_key", f"feat_{uuid.uuid4().hex[:8]}")
    kw.setdefault("feature_name", "Multi-region replay")
    kw.setdefault("customer_summary", "Replay served from the nearest region.")
    return mkt.set_availability(db, lifecycle=lifecycle, **kw)


def test_lifecycle_wording_is_never_upgraded(w):
    """2, 3, 4, 5, 6 - the critical MKT-002 requirement."""
    db = SessionLocal()
    try:
        for lifecycle, must_say, must_not_say in (
                ("preview", "Preview:", "is now available"),
                ("pilot", "Pilot access", "is now available"),
                ("beta", "Beta:", "is now available"),
                ("regional", "rolling out in your region", "is now available"),
                ("restricted", "Limited availability", "is now available"),
                ("invite_only", "Invitation only", "is now available"),
                ("ga", "is now available", "Preview:")):
            availability = _availability(db, lifecycle)
            announcement = mkt.create_announcement(
                db, availability, body="Here is what it does.")
            subject = mkt.announcement_subject(announcement, availability)
            assert must_say in subject, f"{lifecycle}: {subject}"
            assert must_not_say not in subject, f"{lifecycle} said {must_not_say!r}"
            # The lifecycle is FROZEN on the announcement.
            assert announcement.lifecycle == lifecycle

        # A lifecycle change AFTER approval cannot re-word an approved announcement.
        availability = _availability(db, "preview")
        announcement = mkt.create_announcement(db, availability, body="Preview copy.")
        mkt.approve_announcement(db, announcement, approved_by=w.admin_id)
        mkt.set_availability(db, feature_key=availability.feature_key,
                             feature_name=availability.feature_name, lifecycle="ga")
        db.refresh(availability)
        assert availability.lifecycle == "ga"
        assert announcement.lifecycle == "preview", "the frozen lifecycle changed"
        assert "Preview:" in mkt.announcement_subject(announcement, availability)

        # An unknown lifecycle is refused rather than defaulted to GA.
        assert _availability(db, "totally_shipped") is None
    finally:
        db.close()


def test_announcement_requires_approval(w):
    """"Only announce after approval."" """
    db = SessionLocal()
    try:
        address, _m, _u = w.subscriber([TOPIC_FEATURE_ANNOUNCEMENTS],
                                       organization_id=w.org_id)
        availability = _availability(db, LIFECYCLE_GA)
        announcement = mkt.create_announcement(db, availability, body="It is here.")
        assert announcement.status == "draft"
        cap, ctx = _capture()
        with ctx:
            assert mkt.notify_announcement(db, _Bg(), announcement) == 0
        assert cap.calls == []
        assert mkt.approve_announcement(db, announcement, approved_by=None) is False
        assert mkt.approve_announcement(db, announcement, approved_by=w.admin_id) is True
        cap2, ctx2 = _capture()
        with ctx2:
            assert mkt.notify_announcement(db, _Bg(), announcement) == 1
        assert announcement.status == "sent"
    finally:
        db.close()


def test_recipient_must_be_eligible(w):
    """7, 8 - subscribed AND eligible, both."""
    db = SessionLocal()
    try:
        enterprise, _m1, _u1 = w.subscriber([TOPIC_FEATURE_ANNOUNCEMENTS],
                                             organization_id=w.org_id)
        free, _m2, _u2 = w.subscriber([TOPIC_FEATURE_ANNOUNCEMENTS],
                                       organization_id=w.free_org_id)
        vague, _m3, _u3 = w.subscriber([TOPIC_FEATURE_ANNOUNCEMENTS],
                                        organization_id=w.vague_org_id)
        # Subscribed to a different topic entirely.
        unrelated, _m4, _u4 = w.subscriber([TOPIC_RELEASE_NOTES],
                                            organization_id=w.org_id)

        # Enterprise-only GA feature.
        availability = _availability(db, LIFECYCLE_GA,
                                     eligible_plans=[w.enterprise_slug])
        announcement = mkt.create_announcement(db, availability, body="Enterprise only.")
        mkt.approve_announcement(db, announcement, approved_by=w.admin_id)
        cap, ctx = _capture()
        with ctx:
            mkt.notify_announcement(db, _Bg(), announcement)
        subject = mkt.announcement_subject(announcement, availability)
        got = cap.to(subject)
        assert enterprise.lower() in got
        # 8 - a Free tenant is never told an enterprise feature is available to it.
        assert free.lower() not in got, "a Free tenant was told about an enterprise feature"
        assert unrelated.lower() not in got, "wrong topic"

        # Regional preview in Europe, granted only to the EU tenant.
        regional = _availability(db, "regional", eligible_regions=["eu"])
        mkt.grant_availability(db, regional, org_id=w.org_id, granted_by=w.admin_id)
        mkt.grant_availability(db, regional, org_id=w.free_org_id, granted_by=w.admin_id)
        mkt.grant_availability(db, regional, org_id=w.vague_org_id, granted_by=w.admin_id)
        ann2 = mkt.create_announcement(db, regional, body="Rolling out in Europe.")
        mkt.approve_announcement(db, ann2, approved_by=w.admin_id)
        cap2, ctx2 = _capture()
        with ctx2:
            mkt.notify_announcement(db, _Bg(), ann2)
        got2 = cap2.to(mkt.announcement_subject(ann2, regional))
        assert enterprise.lower() in got2, "the EU tenant is eligible"
        assert free.lower() not in got2, "an APAC tenant was told about a Europe rollout"
        # Fails closed: an unresolvable region is NOT eligible.
        assert vague.lower() not in got2, (
            "a tenant whose region cannot be resolved must not be told it is available")
    finally:
        db.close()


def test_targeted_feature_needs_an_explicit_grant(w):
    """5 - a restricted feature is never broadcast."""
    db = SessionLocal()
    try:
        subscriber, _m, _u = w.subscriber([TOPIC_FEATURE_ANNOUNCEMENTS],
                                           organization_id=w.org_id)
        no_org, _m2, _u2 = w.subscriber([TOPIC_FEATURE_ANNOUNCEMENTS])

        for lifecycle in TARGETED_LIFECYCLES:
            availability = _availability(db, lifecycle)
            announcement = mkt.create_announcement(db, availability, body="Limited.")
            mkt.approve_announcement(db, announcement, approved_by=w.admin_id)
            # No grant recorded -> nobody is eligible, including the subscriber with an org.
            assert mkt.announcement_recipients(db, availability) == [], lifecycle
            cap, ctx = _capture()
            with ctx:
                assert mkt.notify_announcement(db, _Bg(), announcement) == 0
            assert cap.calls == [], lifecycle

            # With a grant, exactly that organization's subscriber is reached.
            mkt.grant_availability(db, availability, org_id=w.org_id,
                                   granted_by=w.admin_id)
            recipients = [s.email for s in mkt.announcement_recipients(db, availability)]
            assert subscriber.lower() in recipients, lifecycle
            assert no_org.lower() not in recipients, (
                f"{lifecycle}: a subscriber with no organization cannot be eligible")

        # A GA feature with no plan or region restriction genuinely does reach everyone.
        ga = _availability(db, LIFECYCLE_GA)
        recipients = [s.email for s in mkt.announcement_recipients(db, ga)]
        assert no_org.lower() in recipients
    finally:
        db.close()


def test_announcement_dedup_and_availability_note(w):
    """9, 10."""
    db = SessionLocal()
    try:
        address, _m, _u = w.subscriber([TOPIC_FEATURE_ANNOUNCEMENTS],
                                        organization_id=w.org_id)
        availability = _availability(db, "preview")
        mkt.grant_availability(db, availability, org_id=w.org_id, granted_by=w.admin_id)
        announcement = mkt.create_announcement(db, availability, body="Try the preview.")
        mkt.approve_announcement(db, announcement, approved_by=w.admin_id)
        cap, ctx = _capture()
        with ctx:
            assert mkt.notify_announcement(db, _Bg(), announcement) == 1
            n = len(cap.calls)
            # 9 - no duplicate announcement.
            assert mkt.notify_announcement(db, _Bg(), announcement) == 0
        assert len(cap.calls) == n
        text = cap.of(mkt.announcement_subject(announcement, availability))["text"]
        # The message explains what preview MEANS, rather than implying availability.
        assert "still changing" in text and "may be withdrawn" in text
        assert "not covered by the standard service commitments" in text
        # 10 - unsubscribe available.
        assert "/unsubscribe?t=" in text
    finally:
        db.close()


# ══ MKT-003 ═════════════════════════════════════════════════════════════════════════════

def test_api_key_alone_does_not_enrol(w):
    """1, 2 - developer activity is not consent."""
    db = SessionLocal()
    try:
        org = db.get(Organization, w.org_id)
        org.api_keys = [{"id": str(uuid.uuid4()), "name": "prod", "prefix": "zs_live_x"}]
        db.add(WebhookEndpoint(org_id=org.id, url="https://example.test/hook",
                               secret="s" * 32, status="active", verified_at=_now(),
                               events=["event.started"]))
        db.commit()
        # An org_admin with a credential AND a verified webhook is still not enrolled.
        assert mkt.find(db, w.admin_email) is None
        assert db.query(DeveloperOnboardingEnrolment).count() == 0
        # And enrolment refuses without an active DEVELOPER_EDUCATION subscription.
        assert mkt.enrol_developer(db, email=w.admin_email, source="developer_signup_optin",
                                   organization_id=org.id) is None

        # Subscribed, but to the WRONG topic.
        address, _m, _u = w.subscriber([TOPIC_RELEASE_NOTES], organization_id=org.id)
        assert mkt.enrol_developer(db, email=address, source="developer_signup_optin",
                                   organization_id=org.id) is None

        # Only an explicit DEVELOPER_EDUCATION opt-in enrols.
        dev, _m2, _u2 = w.subscriber([TOPIC_DEVELOPER_EDUCATION], organization_id=org.id)
        enrolment = mkt.enrol_developer(db, email=dev, source="developer_signup_optin",
                                        organization_id=org.id)
        assert enrolment is not None and enrolment.status == "active"
        assert enrolment.opt_in_at is not None
        assert enrolment.opt_in_source == "developer_signup_optin"
        assert enrolment.steps_sent == []
    finally:
        db.close()


def test_onboarding_steps_follow_observable_milestones(w):
    """3, 4, 6, 8."""
    db = SessionLocal()
    try:
        org = db.get(Organization, w.org_id)
        org.api_keys = []
        db.commit()
        dev, _m, _u = w.subscriber([TOPIC_DEVELOPER_EDUCATION], organization_id=org.id)
        enrolment = mkt.enrol_developer(db, email=dev, source="preference_center",
                                        organization_id=org.id)

        # 3 - welcome is unlocked by the opt-in itself.
        assert mkt.next_step(db, enrolment) == "welcome"
        cap, ctx = _capture()
        with ctx:
            assert mkt.notify_onboarding_step(db, _Bg(), enrolment, "welcome") is True
            # 8 - no duplicate step.
            n = len(cap.calls)
            assert mkt.notify_onboarding_step(db, _Bg(), enrolment, "welcome") is False
        assert len(cap.calls) == n
        assert enrolment.steps_sent == ["welcome"]
        assert email_mod.MKT_003_SUBJECT["welcome"] in cap.subjects

        # 4 - the next step waits for a REAL milestone. No credential yet, so nothing.
        assert mkt.next_step(db, enrolment) is None
        cap2, ctx2 = _capture()
        with ctx2:
            counts = mkt.sweep(db, _Bg())
        assert counts["onboarding_steps"] == 0
        assert cap2.calls == []

        # Create a credential: now "build" unlocks.
        org.api_keys = [{"id": str(uuid.uuid4()), "name": "prod", "prefix": "zs_live_x"}]
        db.commit()
        assert mkt.next_step(db, enrolment) == "build"
        cap3, ctx3 = _capture()
        with ctx3:
            assert mkt.sweep(db, _Bg())["onboarding_steps"] == 1
        assert email_mod.MKT_003_SUBJECT["build"] in cap3.subjects
        assert "created an API credential" in cap3.blob()

        # Production readiness waits for a VERIFIED webhook, not merely a registered one.
        db.add(WebhookEndpoint(org_id=org.id, url="https://example.test/hook",
                               secret="s" * 32, status="pending_verification",
                               events=["event.started"]))
        db.commit()
        assert mkt.next_step(db, enrolment) is None, (
            "an unverified webhook must not unlock production readiness")
        endpoint = db.scalar(select(WebhookEndpoint).where(
            WebhookEndpoint.org_id == org.id))
        endpoint.verified_at = _now()
        endpoint.status = "active"
        db.commit()
        assert mkt.next_step(db, enrolment) == "production_readiness"
        cap4, ctx4 = _capture()
        with ctx4:
            assert mkt.sweep(db, _Bg())["onboarding_steps"] == 1
        db.refresh(enrolment)
        # 6 - the sequence state is durable and complete.
        assert enrolment.steps_sent == list(ONBOARDING_STEPS)
        assert enrolment.status == "completed"
        assert mkt.next_step(db, enrolment) is None
    finally:
        db.close()


def test_no_unobservable_milestone(w):
    """5 - API traffic is not observable here, so no step claims it."""
    import inspect

    # The milestone table contains only facts this platform records.
    from app.models import ONBOARDING_MILESTONES
    assert set(ONBOARDING_MILESTONES.values()) == {
        "opt_in", "credential_created", "webhook_verified"}
    for invented in ("first_api_call", "api_request", "successful_request",
                     "api_usage", "traffic"):
        assert invented not in ONBOARDING_MILESTONES.values(), invented

    # An unknown milestone is never "reached".
    db = SessionLocal()
    try:
        dev, _m, _u = w.subscriber([TOPIC_DEVELOPER_EDUCATION], organization_id=w.org_id)
        enrolment = mkt.enrol_developer(db, email=dev, source="preference_center",
                                        organization_id=w.org_id)
        for invented in ("first_api_call", "production_traffic", ""):
            assert mkt.milestone_reached(db, enrolment, invented) is False, invented
    finally:
        db.close()

    # And no onboarding copy claims to have seen a request.
    for step, body in email_mod.MKT_003_BODY.items():
        low = body.lower()
        for claim in ("your first api call", "we saw your request", "you called the api",
                      "your traffic", "requests you made"):
            assert claim not in low, f"{step} claims unobserved telemetry: {claim}"


def test_unsubscribe_cancels_the_series(w):
    """7 - and it is not resumed from another topic."""
    db = SessionLocal()
    try:
        org = db.get(Organization, w.org_id)
        org.api_keys = [{"id": str(uuid.uuid4()), "name": "p", "prefix": "zs_live_x"}]
        db.commit()
        dev, _m, _u = w.subscriber([TOPIC_DEVELOPER_EDUCATION, TOPIC_RELEASE_NOTES],
                                    organization_id=org.id)
        enrolment = mkt.enrol_developer(db, email=dev, source="preference_center",
                                        organization_id=org.id)
        cap, ctx = _capture()
        with ctx:
            mkt.notify_onboarding_step(db, _Bg(), enrolment, "welcome")

        subscription = mkt.find(db, dev)
        assert mkt.unsubscribe(db, subscription) is True
        db.refresh(enrolment)
        assert enrolment.status == "cancelled" and enrolment.cancelled_at is not None
        assert mkt.next_step(db, enrolment) is None
        cap2, ctx2 = _capture()
        with ctx2:
            assert mkt.notify_onboarding_step(db, _Bg(), enrolment, "build") is False
            assert mkt.sweep(db, _Bg())["onboarding_steps"] == 0
        assert cap2.calls == []

        # Dropping only the DEVELOPER_EDUCATION topic also stops it, even while the
        # subscriber keeps another topic.
        dev2, _m2, _u2 = w.subscriber([TOPIC_DEVELOPER_EDUCATION, TOPIC_RELEASE_NOTES],
                                       organization_id=org.id)
        e2 = mkt.enrol_developer(db, email=dev2, source="preference_center",
                                 organization_id=org.id)
        mkt.update_topics(db, mkt.find(db, dev2), [TOPIC_RELEASE_NOTES])
        cap3, ctx3 = _capture()
        with ctx3:
            assert mkt.notify_onboarding_step(db, _Bg(), e2, "welcome") is False
        assert cap3.calls == []
    finally:
        db.close()


# ══ MKT-004 ═════════════════════════════════════════════════════════════════════════════

def test_guide_fulfilment_creates_no_consent(w):
    """1, 2 - the guide is transactional."""
    db = SessionLocal()
    try:
        address = _new_email("prospect")
        guide_request, token = mkt.request_guide(
            db, email=address, guide="live_event_planning", name="Sam",
            marketing_opt_in=False)
        assert guide_request is not None and guide_request.status == "requested"
        # 2 - no consent record, and no token to confirm one.
        assert token is None
        assert guide_request.subscription_id is None
        assert mkt.find(db, address) is None
        for topic in MARKETING_TOPICS:
            assert mkt.eligible(db, topic, address) is False

        cap, ctx = _capture()
        with ctx:
            assert mkt.notify_guide(db, _Bg(), guide_request) is True
        assert guide_request.status == "fulfilled"
        text = cap.of(email_mod.MKT_004_GUIDE_SUBJECT.format(
            guide="Planning your first live event"))["text"]
        assert "not subscribed to any marketing emails" in text
        # Still no consent after fulfilment.
        assert mkt.find(db, address) is None

        # An unknown guide is refused.
        assert mkt.request_guide(db, email=_new_email(), guide="how_to_be_rich")[0] is None
    finally:
        db.close()


def test_guide_with_optin_creates_a_separate_pending_consent(w):
    """The opt-in is affirmative, separate, and still double opt-in."""
    db = SessionLocal()
    try:
        address = _new_email("prospect")
        guide_request, token = mkt.request_guide(
            db, email=address, guide="hybrid_events", marketing_opt_in=True)
        assert token, "an opt-in must produce a confirmation token"
        subscription = mkt.find(db, address)
        assert subscription is not None and subscription.status == "pending"
        assert subscription.source == "guide_request"
        assert subscription.topics == [TOPIC_LIVE_EVENT_EDUCATION]
        # PENDING, so still not eligible for anything until confirmed.
        assert mkt.eligible(db, TOPIC_LIVE_EVENT_EDUCATION, address) is False
        assert guide_request.subscription_id == subscription.id
        # The guide itself is delivered regardless.
        cap, ctx = _capture()
        with ctx:
            assert mkt.notify_guide(db, _Bg(), guide_request) is True
        assert "please confirm your address" in cap.blob().lower()
    finally:
        db.close()


def test_webinar_registration_lifecycle(w):
    """3, 4, 5, 6, 7, 14."""
    db = SessionLocal()
    try:
        webinar = mkt.schedule_webinar(
            db, title="Running your first live event",
            starts_at_utc=_now() + timedelta(days=7),
            description="A walkthrough of setup, rehearsal and go-live.",
            join_path="/webinars/live-1")
        assert webinar is not None and webinar.reference.startswith("WBN-")
        assert mkt.as_utc(webinar.starts_at_utc).utcoffset() == timedelta(0)

        address = _new_email("registrant")
        registration = mkt.register_for_webinar(db, webinar, email=address, name="Jo")
        assert registration is not None and registration.status == "registered"
        assert registration.consent_basis == "webinar_registration"
        # 3 - registering created NO marketing subscription.
        assert mkt.find(db, address) is None

        cap, ctx = _capture()
        with ctx:
            assert mkt.notify_webinar(db, _Bg(), webinar, variant="confirmation") == 1
            # 14 - dedup.
            n = len(cap.calls)
            assert mkt.notify_webinar(db, _Bg(), webinar, variant="confirmation") == 0
        assert len(cap.calls) == n
        text = cap.of(email_mod.MKT_004_CONFIRMATION_SUBJECT.format(
            title=webinar.title))["text"]
        assert "UTC" in text
        assert "Registering does not subscribe you to marketing" in text

        # 5 - reminder.
        cap2, ctx2 = _capture()
        with ctx2:
            assert mkt.notify_webinar(db, _Bg(), webinar, variant="reminder") == 1
        assert email_mod.MKT_004_REMINDER_SUBJECT.format(title=webinar.title) in \
            cap2.subjects

        # 6 - schedule change keeps the old time visible.
        changed, previous = mkt.reschedule_webinar(
            db, webinar, starts_at_utc=_now() + timedelta(days=9))
        assert changed is True and previous is not None
        assert webinar.previous_starts_at_utc is not None
        assert mkt.reschedule_webinar(db, webinar,
                                      starts_at_utc=webinar.starts_at_utc)[0] is False
        cap3, ctx3 = _capture()
        with ctx3:
            assert mkt.notify_webinar(db, _Bg(), webinar, variant="rescheduled",
                                      previous_start=previous) == 1
        rtext = cap3.of(email_mod.MKT_004_RESCHEDULED_SUBJECT.format(
            title=webinar.title))["text"]
        assert "Previous time (UTC)" in rtext and "New time (UTC)" in rtext

        # 7 - cancellation.
        assert mkt.cancel_webinar(db, webinar) is True
        cap4, ctx4 = _capture()
        with ctx4:
            assert mkt.notify_webinar(db, _Bg(), webinar, variant="cancelled") == 1
        assert mkt.cancel_webinar(db, webinar) is False
        # A cancelled session accepts no new registrations.
        assert mkt.register_for_webinar(db, webinar, email=_new_email()) is None
    finally:
        db.close()


def test_attendance_is_not_consent(w):
    """8 - marketing follow-up needs approval AND the recipient's own consent."""
    db = SessionLocal()
    try:
        webinar = mkt.schedule_webinar(db, title="Broadcast quality",
                                        starts_at_utc=_now() + timedelta(days=1))
        # One attendee who never opted in.
        attendee = _new_email("attendee")
        r1 = mkt.register_for_webinar(db, webinar, email=attendee)
        # One attendee who DID opt in to Live Events education.
        consented, _m, _u = w.subscriber([TOPIC_LIVE_EVENT_EDUCATION])
        r2 = mkt.register_for_webinar(db, webinar, email=consented)

        assert mkt.record_attendance(db, r1) is True
        assert mkt.record_attendance(db, r2) is True
        assert r1.attended_at is not None
        # Attending created no consent.
        assert mkt.find(db, attendee) is None

        # No approved follow-up yet -> nothing goes out even to the consented attendee.
        assert mkt.complete_webinar(db, webinar) is True
        cap, ctx = _capture()
        with ctx:
            assert mkt.notify_webinar_followup(db, _Bg(), webinar) == 0
        assert cap.calls == []

        # With an approved follow-up, ONLY the consented recipient is reached.
        webinar.followup_body = "Here are the three things people asked about."
        webinar.followup_approved_by = w.admin_id
        db.commit()
        cap2, ctx2 = _capture()
        with ctx2:
            assert mkt.notify_webinar_followup(db, _Bg(), webinar) == 1
        got = cap2.to(email_mod.MKT_004_FOLLOWUP_SUBJECT.format(title=webinar.title))
        assert got == [consented.lower()], got
        assert attendee.lower() not in got, (
            "attendance was treated as consent to a marketing follow-up")
        text = cap2.of(email_mod.MKT_004_FOLLOWUP_SUBJECT.format(
            title=webinar.title))["text"]
        assert "/unsubscribe?t=" in text
        assert "Attending a session does not subscribe you" in text
    finally:
        db.close()


def test_sensitive_participants_are_never_enrolled(w):
    """9, 10, 11, 12 - the suppression guards, stated one by one."""
    db = SessionLocal()
    try:
        # Each of these is the exact population the spec names. The behavioural proof is in
        # test_no_operational_relationship_creates_consent; this asserts the SOURCE
        # vocabulary makes enrolment on their behalf unrepresentable.
        from app.models import MARKETING_SOURCES

        for forbidden in ("event_registration", "event_participation", "contributor",
                          "contributor_grant", "support_ticket", "support_requester",
                          "security_contact", "status_subscription", "privacy_request",
                          "vulnerability_report", "memorial", "grantee",
                          "organization_membership", "api_key"):
            assert forbidden not in MARKETING_SOURCES, (
                f"'{forbidden}' is a marketing source - an operational relationship must "
                "never be a consent basis")
        # And an unknown source is refused outright, so no caller can invent one.
        assert mkt.subscribe(db, email=_new_email(), topics=[TOPIC_RELEASE_NOTES],
                             source="event_registration")[0] is None
        assert mkt.subscribe(db, email=_new_email(), topics=[TOPIC_RELEASE_NOTES],
                             source="memorial_participant")[0] is None
    finally:
        db.close()


def test_marketing_families_are_classified_separately(w):
    from app.services import notifications

    for family in ("MKT-001", "MKT-002", "MKT-003", "MKT-004"):
        assert notifications.message_class(family) == notifications.CLASS_D, family
        assert notifications.is_marketing(family) is True, family
        assert notifications.is_mandatory(family) is False, family
    # The consent lifecycle itself is transactional and mandatory: the receipt for an
    # unsubscribe must never be suppressible by a marketing preference.
    assert notifications.is_mandatory("MKT-000") is True
    assert notifications.is_marketing("MKT-000") is False


def test_provider_failure_does_not_lose_consent_or_state(w):
    """A Resend outage must not roll back consent, a digest, or a registration."""
    db = SessionLocal()
    try:
        address = _new_email("resilient")
        subscription, raw = mkt.subscribe(db, email=address,
                                           topics=[TOPIC_RELEASE_NOTES],
                                           source="website_form")
        capf, ctxf = _capture(fail=True)
        with ctxf:
            email_mod.send_marketing_verify_email(
                address, topics=["Release notes"], expires_at=None,
                confirm_url="https://example.test/x")
        db.expire_all()
        assert mkt.find(db, address) is not None, "consent was lost on a mail failure"

        release = _release(db, w)
        digest = mkt.create_digest(db, title="Sept",
                                    period_start=_now() - timedelta(days=30),
                                    period_end=_now(), release_ids=[release.id])
        mkt.approve_digest(db, digest, approved_by=w.admin_id)
        mkt.publish_digest(db, digest, published_by=w.admin_id)
        subscriber, _m, _u = w.subscriber([TOPIC_RELEASE_NOTES])
        capf2, ctxf2 = _capture(fail=True)
        with ctxf2:
            mkt.notify_digest(db, _Bg(), digest)
        db.expire_all()
        assert db.get(ReleaseDigest, digest.id).status == "published", (
            "a mail failure must not unpublish a digest")
        # The claim was released, so a retry is possible.
        cap, ctx = _capture()
        with ctx:
            assert mkt.notify_digest(db, _Bg(), digest) >= 1
    finally:
        db.close()


def test_marketing_sweep_runs_on_the_shared_ticker(w):
    import inspect

    from app.services import event_planning

    assert "marketing" in inspect.getsource(event_planning.sweep)
    assert not hasattr(mkt, "run_marketing_sweeper"), (
        "MKT must ride the leader-elected ticker, not add its own")
    # No reminder threshold is invented: reminders are operator-triggered.
    source = inspect.getsource(mkt.sweep)
    for invented in ("hours=24", "hours=1)", "timedelta(hours=24)"):
        assert invented not in source, f"fabricated reminder threshold: {invented}"


def test_no_marketing_sender_accepts_a_secret(w):
    import inspect

    senders = [n for n in dir(email_mod)
               if n.startswith(("send_marketing_", "send_release_", "send_feature_",
                                "send_developer_onboarding", "send_guide_",
                                "send_webinar_"))]
    assert len(senders) >= 8, senders
    banned = ("token", "secret", "api_key", "password", "credential", "internal_notes",
              "notes", "detail", "evidence", "ip")
    for name in senders:
        for param in inspect.signature(getattr(email_mod, name)).parameters:
            assert param.lower() not in banned, f"{name}({param})"


def test_idempotency_key_is_versioned_and_per_recipient(w):
    constraint = next(c for c in MarketingNotice.__table__.constraints
                      if c.__class__.__name__ == "UniqueConstraint")
    assert {c.name for c in constraint.columns} == {
        "kind", "subject_type", "subject_id", "version", "recipient"}


TESTS = [
    test_double_opt_in_is_required,
    test_one_topic_is_not_four,
    test_no_operational_relationship_creates_consent,
    test_marketing_service_reads_no_operational_table,
    test_marketing_preferences_are_not_operational_preferences,
    test_preference_change_and_unsubscribe_notices,
    test_unsubscribe_suppresses_immediately,
    test_unsubscribe_cannot_silence_mandatory_mail,
    test_every_marketing_sender_requires_unsubscribe,
    test_unsubscribe_token_grants_nothing_else,
    test_public_consent_endpoints,
    test_raw_release_does_not_send,
    test_digest_requires_approval_before_publication,
    test_internal_release_notes_never_ship,
    test_digest_reaches_only_subscribers_and_carries_links,
    test_lifecycle_wording_is_never_upgraded,
    test_announcement_requires_approval,
    test_recipient_must_be_eligible,
    test_targeted_feature_needs_an_explicit_grant,
    test_announcement_dedup_and_availability_note,
    test_api_key_alone_does_not_enrol,
    test_onboarding_steps_follow_observable_milestones,
    test_no_unobservable_milestone,
    test_unsubscribe_cancels_the_series,
    test_guide_fulfilment_creates_no_consent,
    test_guide_with_optin_creates_a_separate_pending_consent,
    test_webinar_registration_lifecycle,
    test_attendance_is_not_consent,
    test_sensitive_participants_are_never_enrolled,
    test_marketing_families_are_classified_separately,
    test_provider_failure_does_not_lose_consent_or_state,
    test_marketing_sweep_runs_on_the_shared_ticker,
    test_no_marketing_sender_accepts_a_secret,
    test_idempotency_key_is_versioned_and_per_recipient,
]

if __name__ == "__main__":
    for t in TESTS:
        run(t)
    assert not _LEAKS, f"email sent outside a capture context: {_LEAKS}"
    failed = [n for n, e in RESULTS if e is not None]
    print(f"\n{len(RESULTS) - len(failed)} passed, {len(failed)} failed")
    if failed:
        raise SystemExit(1)
