"""Authorization tests for the retirement of the "moderator" role.

The claim under test is narrow and worth stating precisely, because it is what made the
change safe to make at all: HOST WAS NEVER MISSING ANY MODERATOR CAPABILITY. An assigned
host has always resolved to can_moderate AND can_host together, so retiring moderator
granted host nothing new — the change is purely subtractive. These tests pin that down from
both directions:

  * host still reaches everything (audience management AND broadcast control),
  * nobody below host gained anything,
  * a LEGACY moderator assignment (models/event.LEGACY_ASSIGNMENT_ROLES — a row written
    before the retirement, deliberately still honoured so access was not revoked mid-flight)
    reaches audience management and is refused broadcast control,
  * the role is unreachable as a platform role, an assignable event role, an API surface,
    and a promotion target.

Hits a real database for the resolve_ctx cases — that function's whole job is a scoped query,
and mocking the query would test nothing. The rest is pure logic.
Run: `pytest test_moderator_retirement.py`
"""

import asyncio
import uuid
from contextlib import contextmanager

import pytest
from sqlalchemy import delete, select

# app.main FIRST, deliberately. app/services/org.py does `from .broadcast import
# engagement_score` while broadcast.py transitively imports org.py, so importing
# app.services.broadcast before the app package has been wired up hits that cycle and fails
# collection. This is PRE-EXISTING (test_broadcast.py fails the same way when run on its own,
# on unmodified code) and not something this change introduced — importing app.main resolves
# it the same way production does, via routers/live.py's ordered imports.
import app.main  # noqa: F401  - import order matters; see above

from app import security
from app.db import SessionLocal
from app.models import (
    ASSIGNMENT_ROLES,
    LEGACY_ASSIGNMENT_ROLES,
    LEGACY_USER_ROLES,
    ROLES,
    Event,
    EventAssignment,
    Organization,
    User,
)
from app.services import broadcast as bc  # noqa: F401 - registers broadcast.* into m.ACTIONS
from app.services import contributor  # noqa: F401 - registers contributor.* into m.ACTIONS
from app.services import moderation as m
from app.services import ops

LEGACY = "moderator"

# Every action that manages the audience rather than the broadcast. This is the set the
# retired role existed to perform, and the set a host must be able to reach.
AUDIENCE_ACTIONS = (
    "chat.approve", "chat.pin", "chat.delete", "chat.note", "chat.bulk",
    "qa.approve", "qa.answer", "qa.dismiss", "qa.pin", "qa.assign", "qa.delete",
    "poll.create", "poll.update", "poll.launch", "poll.close", "poll.delete",
    "announce.send", "announce.delete",
    "participant.mute", "participant.timeout", "participant.stage", "participant.role",
    "participant.ban", "participant.remove",
    "stage.admit", "stage.admit_all", "stage.mute_all", "stage.camera", "stage.share",
    "contributor.admit", "contributor.standby", "contributor.bring_live",
    "contributor.mute", "contributor.remove",
)

BROADCAST_ACTIONS = (
    "broadcast.golive", "broadcast.pause", "broadcast.resume", "broadcast.end",
    "broadcast.emergency_stop", "broadcast.preview", "broadcast.countdown",
    "broadcast.settings",
    "recording.start", "recording.pause", "recording.resume", "recording.stop",
)


def _ctx(*, can_moderate=False, can_host=False, can_contribute=False, role="viewer"):
    return m.Ctx(event_id=uuid.uuid4(), org_id=uuid.uuid4(), room="event_x",
                 user_id=uuid.uuid4(), name="Test Operator", identity="u1", role=role,
                 can_moderate=can_moderate, can_host=can_host, can_contribute=can_contribute)


@contextmanager
def _stubbed_handlers():
    """Swap every handler for a no-op that records it ran, then restore.

    This tests the REAL gate in dispatch() -- the permission decision, the HOST_ONLY lookup and
    the VIEWER_ACTIONS lookup all execute unchanged -- while removing the handlers' database
    and Redis work. That matters for two reasons: an allowed action's handler would otherwise
    fail on an empty payload against a non-existent event (telling us nothing about
    permissions), and services/bus.py holds ONE lazily-created Redis client which breaks the
    moment a second asyncio.run() hands it a different event loop. Returning [] also means
    dispatch publishes nothing, so the bus is never touched at all.
    """
    reached: list[str] = []
    original = dict(m.ACTIONS)

    def make(name):
        async def handler(ctx, payload):
            reached.append(name)
            return []
        return handler

    try:
        for name in list(m.ACTIONS):
            m.ACTIONS[name] = make(name)
        yield reached
    finally:
        m.ACTIONS.clear()
        m.ACTIONS.update(original)


# ── the role is gone from every definition ────────────────────────────────────────────────

def test_moderator_is_not_a_platform_role():
    assert LEGACY not in ROLES
    assert LEGACY not in security._ROLE_RANK


def test_moderator_is_not_an_assignable_event_role():
    assert LEGACY not in ASSIGNMENT_ROLES


def test_retired_values_are_declared_as_legacy():
    """Not merely absent — named, so a reader can tell "we stopped issuing this" from
    "this was never valid", and so the grandfather read has a single source."""
    assert LEGACY in LEGACY_ASSIGNMENT_ROLES
    assert LEGACY in LEGACY_USER_ROLES


def test_require_moderator_dependency_no_longer_exists():
    """It was dead (no router referenced it) AND weaker than require_host (built as
    require_min_role("moderator"), so every host cleared it). Keeping it would have left a
    gate named after a role that no longer exists."""
    assert not hasattr(security, "require_moderator")


def test_removing_the_rung_changed_no_http_gate():
    """The only thresholds the codebase ever builds are "host" and "org_admin", and a
    moderator (rank 2) already failed both. A row still carrying the value resolves to -1,
    which fails them identically — so no endpoint's answer changed for anybody."""
    for threshold in ("host", "org_admin"):
        limit = security._ROLE_RANK[threshold]
        assert security._ROLE_RANK.get(LEGACY, -1) < limit
        assert security._ROLE_RANK.get("viewer", -1) < limit
        assert security._ROLE_RANK.get("speaker", -1) < limit
    # ...and the roles that SHOULD clear them still do.
    assert security._ROLE_RANK["host"] >= security._ROLE_RANK["host"]
    assert security._ROLE_RANK["org_admin"] >= security._ROLE_RANK["host"]
    assert security._ROLE_RANK["super_admin"] >= security._ROLE_RANK["org_admin"]


def test_moderators_endpoints_are_gone_from_the_api():
    from app.main import app
    paths = app.openapi()["paths"]
    assert not [p for p in paths if "moderator" in p.lower()]
    # The surviving assignment endpoints are untouched.
    assert "/api/events/{event_id}/hosts" in paths
    assert "/api/events/{event_id}/speakers" in paths


def test_readiness_gates_no_longer_require_a_moderator():
    """The gate was mandatory for unrepeatable-impact events. Left in place it could never
    pass again, permanently blocking readiness for exactly the events that matter most."""
    keys = [g[0] for g in ops._GATES]
    assert LEGACY not in keys
    assert "host" in keys
    from app.services import org as org_service
    assert LEGACY not in org_service._WORK_GATES


# ── the dispatch gate ─────────────────────────────────────────────────────────────────────

def test_host_reaches_every_audience_action_and_every_broadcast_action():
    """The core claim: host holds the union. If this fails, retiring the role DID cost a
    capability and the migration is wrong."""
    host = _ctx(can_moderate=True, can_host=True, role="host")
    every = AUDIENCE_ACTIONS + BROADCAST_ACTIONS
    for action in every:
        assert action in m.ACTIONS, f"{action} is not a registered action"
        assert action not in m.VIEWER_ACTIONS, f"{action} must not be open to plain viewers"

    async def run():
        with _stubbed_handlers() as reached:
            errors = {a: await m.dispatch(host, a, {}) for a in every}
            return errors, list(reached)

    errors, reached = asyncio.run(run())
    blocked = {a: e for a, e in errors.items() if e is not None}
    assert not blocked, f"host was refused: {blocked}"
    assert set(reached) == set(every), "an allowed action never reached its handler"


def test_legacy_moderator_assignment_keeps_audience_control():
    """Grandfathered access, not a new grant: this is what stops the retirement silently
    revoking chat/Q&A/poll moderation on events that are already published."""
    legacy = _ctx(can_moderate=True, can_host=False, role="host")

    async def run():
        with _stubbed_handlers():
            return {a: await m.dispatch(legacy, a, {}) for a in AUDIENCE_ACTIONS}

    blocked = {a: e for a, e in asyncio.run(run()).items() if e is not None}
    assert not blocked, f"grandfathered access was revoked for: {blocked}"


def test_legacy_moderator_assignment_cannot_touch_the_broadcast():
    """The asymmetry that makes grandfathering safe. can_moderate without can_host must not
    reach go-live, end, emergency stop or recording — many of these rows are held by users
    whose platform role is speaker."""
    legacy = _ctx(can_moderate=True, can_host=False, role="host")
    for action in BROADCAST_ACTIONS:
        assert action in m.HOST_ONLY, f"{action} must be host-only"

    async def run():
        with _stubbed_handlers() as reached:
            errors = {a: await m.dispatch(legacy, a, {}) for a in BROADCAST_ACTIONS}
            return errors, list(reached)

    errors, reached = asyncio.run(run())
    for action, error in errors.items():
        assert error == "Only the event host can control the broadcast", f"{action}: {error!r}"
    assert not reached, f"a broadcast handler executed for a non-host: {reached}"


@pytest.mark.parametrize("role", ["viewer", "speaker"])
def test_viewer_and_speaker_gained_nothing(role):
    """Explicitly required: retiring a tier must not elevate anyone below it."""
    ctx = _ctx(role=role, can_contribute=(role == "speaker"))
    every = AUDIENCE_ACTIONS + BROADCAST_ACTIONS

    async def run():
        with _stubbed_handlers() as reached:
            errors = {a: await m.dispatch(ctx, a, {}) for a in every}
            return errors, list(reached)

    errors, reached = asyncio.run(run())
    allowed = [a for a, e in errors.items() if e is None]
    assert not allowed, f"{role} reached: {allowed}"
    assert not reached, f"a handler executed for {role}: {reached}"


def test_a_speakers_own_backstage_actions_are_still_theirs():
    """can_contribute is independent of can_moderate; the retirement must not have merged
    them. A speaker keeps self-service, and only self-service."""
    speaker = _ctx(role="speaker", can_contribute=True)
    own = ("contributor.consent", "contributor.toggle_mic", "contributor.toggle_camera")
    for action in own:
        assert action in m.VIEWER_ACTIONS

    async def run():
        with _stubbed_handlers():
            mine = {a: await m.dispatch(speaker, a, {}) for a in own}
            # ...and an OPERATOR contributor action is still refused to them.
            operator = await m.dispatch(speaker, "contributor.remove", {})
            return mine, operator

    mine, operator = asyncio.run(run())
    blocked = {a: e for a, e in mine.items() if e is not None}
    assert not blocked, f"a speaker was refused their own backstage actions: {blocked}"
    assert operator == "You are not authorized to run this event"


def test_unknown_action_is_refused_before_any_permission_question():
    async def run():
        with _stubbed_handlers():
            return await m.dispatch(_ctx(can_moderate=True, can_host=True), "moderator.anything", {})

    assert asyncio.run(run()) == "Unknown action: moderator.anything"


# ── promotion: the role must be unreachable as a target ───────────────────────────────────

def test_promotion_to_moderator_is_no_longer_possible():
    """participant.role was the ONLY socket path that could mint a moderator assignment.
    An old client still sending role="moderator" must not silently promote — the value is
    unrecognised, so it falls through to the same "viewer" demotion any junk input gets."""
    import inspect
    source = inspect.getsource(m._participant_action)
    # The accepted set is the authority; assert on it rather than on behaviour that would
    # need a live event row to observe.
    assert '("host", "speaker", "viewer")' in source
    assert '"moderator", "viewer")' not in source


def test_demotion_clears_a_legacy_grant_too():
    """A demoted participant who held a legacy moderator row must not keep audience
    management by reconnecting — so the revoke has to target that value as well."""
    assert LEGACY in m._ELEVATED_ASSIGNMENT_ROLES
    assert "host" in m._ELEVATED_ASSIGNMENT_ROLES


# ── resolve_ctx: the real object-level / event-level / org-level authority ─────────────────

@pytest.fixture
def scoped_event():
    """Two orgs, one event in the first, and users to attach to it. Everything created here
    is removed afterwards, so the suite leaves no rows behind."""
    db = SessionLocal()
    made = []
    try:
        org_a = Organization(name=f"authz-a-{uuid.uuid4().hex[:8]}")
        org_b = Organization(name=f"authz-b-{uuid.uuid4().hex[:8]}")
        db.add_all([org_a, org_b])
        db.flush()

        def mk_user(org, role):
            u = User(org_id=org.id, full_name=f"{role} user", role=role,
                     email=f"{role}-{uuid.uuid4().hex[:10]}@authz.test",
                     username=f"{role}{uuid.uuid4().hex[:10]}", password_hash="x",
                     is_active=True, email_verified=True)
            db.add(u)
            return u

        owner = mk_user(org_a, "org_admin")
        db.flush()
        event = Event(org_id=org_a.id, created_by=owner.id, title="Authz probe", status="published")
        db.add(event)
        db.flush()

        users = {
            "host": mk_user(org_a, "host"),
            "legacy": mk_user(org_a, "host"),
            "speaker": mk_user(org_a, "speaker"),
            "unassigned": mk_user(org_a, "host"),
            "viewer": mk_user(org_a, "viewer"),
            "org_admin": owner,
            "other_org_host": mk_user(org_b, "host"),
        }
        db.flush()
        db.add_all([
            EventAssignment(event_id=event.id, user_id=users["host"].id, role="host"),
            # The grandfathered row this whole migration hinges on.
            EventAssignment(event_id=event.id, user_id=users["legacy"].id, role=LEGACY),
            EventAssignment(event_id=event.id, user_id=users["speaker"].id, role="speaker"),
        ])
        db.commit()
        made = [event.id, org_a.id, org_b.id]
        yield event, {k: db.get(User, u.id) for k, u in users.items()}
    finally:
        event_id, org_a_id, org_b_id = (made + [None, None, None])[:3]
        if event_id:
            db.execute(delete(EventAssignment).where(EventAssignment.event_id == event_id))
            db.execute(delete(Event).where(Event.id == event_id))
            db.execute(delete(User).where(User.org_id.in_([org_a_id, org_b_id])))
            db.execute(delete(Organization).where(Organization.id.in_([org_a_id, org_b_id])))
            db.commit()
        db.close()


def test_assigned_host_holds_both_flags(scoped_event):
    """The single most important assertion in this file: host already had everything the
    retired role had, which is why nothing needed granting."""
    event, users = scoped_event
    ctx = m.resolve_ctx(event.id, users["host"])
    assert ctx is not None
    assert ctx.can_moderate is True
    assert ctx.can_host is True


def test_legacy_moderator_assignment_resolves_to_audience_only(scoped_event):
    event, users = scoped_event
    ctx = m.resolve_ctx(event.id, users["legacy"])
    assert ctx is not None
    assert ctx.can_moderate is True, "grandfathered access was revoked — this is the regression"
    assert ctx.can_host is False, "a legacy moderator row must never grant broadcast control"


def test_platform_host_without_an_assignment_gets_nothing(scoped_event):
    """Object-level authorization: being your org's "host" is not being THIS event's host."""
    event, users = scoped_event
    ctx = m.resolve_ctx(event.id, users["unassigned"])
    assert ctx is not None            # in-org, so the socket resolves...
    assert ctx.can_moderate is False  # ...with no authority at all
    assert ctx.can_host is False
    assert ctx.can_contribute is False


def test_assigned_speaker_gets_backstage_only(scoped_event):
    event, users = scoped_event
    ctx = m.resolve_ctx(event.id, users["speaker"])
    assert ctx.can_contribute is True
    assert ctx.can_moderate is False
    assert ctx.can_host is False


def test_viewer_gets_nothing(scoped_event):
    event, users = scoped_event
    ctx = m.resolve_ctx(event.id, users["viewer"])
    assert ctx.can_moderate is False and ctx.can_host is False and ctx.can_contribute is False


def test_org_admin_still_runs_any_event_in_its_own_org(scoped_event):
    """Explicitly required: org_admin authority must not have changed."""
    event, users = scoped_event
    ctx = m.resolve_ctx(event.id, users["org_admin"])
    assert ctx.can_moderate is True and ctx.can_host is True


def test_a_host_from_another_organization_is_refused(scoped_event):
    """Org isolation: resolves to None, so routers/live.py refuses the socket before any
    application data is sent."""
    event, users = scoped_event
    assert m.resolve_ctx(event.id, users["other_org_host"]) is None


def test_host_of_one_event_cannot_control_another(scoped_event):
    """Cross-event containment, within the same org. The assignment is per-event, so the
    same user resolves with no authority on an event they are not attached to."""
    event, users = scoped_event
    db = SessionLocal()
    try:
        other = Event(org_id=event.org_id, created_by=users["org_admin"].id,
                      title="Unrelated event", status="published")
        db.add(other)
        db.commit()
        other_id = other.id
    finally:
        db.close()
    try:
        ctx = m.resolve_ctx(other_id, users["host"])
        assert ctx is not None                # same org, so visible
        assert ctx.can_host is False          # but not its host
        assert ctx.can_moderate is False
    finally:
        db = SessionLocal()
        try:
            db.execute(delete(Event).where(Event.id == other_id))
            db.commit()
        finally:
            db.close()


def test_snapshot_reports_the_same_two_flags_it_enforces(scoped_event):
    """The client renders controls from these fields, so a snapshot that disagreed with the
    dispatch gate would either hide a legitimate control or offer one that always fails."""
    event, users = scoped_event
    contexts = {key: m.resolve_ctx(event.id, users[key]) for key in ("host", "legacy")}

    # ONE event loop for both snapshots: services/bus.py caches a single Redis client, and a
    # second asyncio.run() would hand that cached client a loop it was not created on. The
    # cache is reset first so this test does not inherit a client from an earlier module.
    from app.services import bus
    bus._redis = None

    # SNAPSHOT_EXTRAS (broadcast's and contributor's additions) are suspended for this test.
    # They are not what carries the authorization flags — can_moderate/can_host come from
    # moderation's own snapshot — and broadcast's extra queries LiveRecording, whose ORM model
    # declares columns this database does not have (hold_category, health_state, ...). That
    # drift is pre-existing and unrelated to the role change: models/live.py is untouched by it
    # and the columns are already missing at HEAD. Including the extras here would make an
    # authorization test fail for a schema reason, which tests nothing useful.
    extras = list(m.SNAPSHOT_EXTRAS)
    m.SNAPSHOT_EXTRAS.clear()
    try:
        async def run():
            return {key: await m.snapshot(ctx) for key, ctx in contexts.items()}

        snaps = asyncio.run(run())
    finally:
        m.SNAPSHOT_EXTRAS.extend(extras)
    for key, expect_host in (("host", True), ("legacy", False)):
        snap = snaps[key]
        assert snap["can_moderate"] is True
        assert snap["you"]["can_moderate"] is True
        assert snap["you"]["can_host"] is expect_host, f"{key}: can_host should be {expect_host}"


def test_no_event_assignment_row_uses_the_retired_role_as_a_writer():
    """Belt-and-braces on the retirement itself: the value must appear in the codebase only
    as a legacy READ. If a writer comes back, this catches it."""
    import pathlib
    app_dir = pathlib.Path(__file__).parent / "app"
    offenders = []
    for path in app_dir.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#") or LEGACY not in line:
                continue
            # A write looks like assigning the literal to a role field or inserting it.
            if 'role="moderator"' in line or "role='moderator'" in line:
                offenders.append(f"{path.name}:{lineno}: {stripped}")
    assert not offenders, "moderator is being WRITTEN again:\n" + "\n".join(offenders)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
