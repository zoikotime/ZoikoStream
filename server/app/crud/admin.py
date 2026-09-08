"""Database access for the Super Admin API. Pure DB queries — no HTTP, no derived
business logic (that lives in services/admin.py). List helpers return (items, total)."""

import hashlib
import logging
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, or_, select, text
from sqlalchemy.orm import Session

from ..config import BILLING_INTERVALS, MONTHLY, TRIAL_DAYS, settings
from ..models import (
    AuditLog,
    Event,
    FeatureFlag,
    GovernanceRecord,
    Incident,
    Organization,
    Plan,
    Release,
    STAFF_COMMERCIAL_ROLES,
    CommercialOverride,
    COMMERCIAL_OVERRIDE_TARGETS,
    COMMERCIAL_OVERRIDE_TYPES,
    Subscription,
    SUBSCRIPTION_ENTITLED_STATES,
    SUBSCRIPTION_TERMINATED_STATES,
    normalize_subscription_state,
    subscription_transition_error,
    SupportTicket,
    User,
)
from ..schemas.admin import (
    AdminUserOut,
    ApiKeyCreated,
    ApiKeyOut,
    AuditLogOut,
    FeatureFlagOut,
    GovernanceRecordOut,
    IncidentOut,
    OrgOut,
    PlanOut,
    ReleaseOut,
    SubscriptionOut,
    SupportTicketOut,
)

# Non-terminated statuses rank above terminated when picking an org's "current" subscription.
# Sourced from the Section 12 vocabulary (models/subscription.py) so this cannot drift from the
# state machine; the set includes pre-Section-12 spellings so existing rows still rank correctly.
log = logging.getLogger(__name__)

_ACTIVE_SUB = SUBSCRIPTION_ENTITLED_STATES


# ── Organizations ────────────────────────────────────────────────────────────

def _user_counts(db: Session, org_ids: list[uuid.UUID]) -> dict[uuid.UUID, int]:
    if not org_ids:
        return {}
    rows = db.execute(
        select(User.org_id, func.count(User.id))
        .where(User.org_id.in_(org_ids))
        .group_by(User.org_id)
    ).all()
    return {oid: n for oid, n in rows}


def _event_counts(db: Session, org_ids: list[uuid.UUID]) -> dict[uuid.UUID, int]:
    """Events owned directly by the org."""
    if not org_ids:
        return {}
    rows = db.execute(
        select(Event.org_id, func.count(Event.id))
        .where(Event.org_id.in_(org_ids), Event.deleted_at.is_(None))
        .group_by(Event.org_id)
    ).all()
    return {oid: n for oid, n in rows}


def _current_subs(db: Session, org_ids: list[uuid.UUID]) -> dict[uuid.UUID, Subscription]:
    """Best subscription per org: newest non-cancelled, else newest overall."""
    if not org_ids:
        return {}
    subs = db.scalars(
        select(Subscription)
        .where(Subscription.org_id.in_(org_ids))
        .order_by(Subscription.started_at.desc())
    ).all()
    best: dict[uuid.UUID, Subscription] = {}
    for s in subs:
        cur = best.get(s.org_id)
        if cur is None:
            best[s.org_id] = s
        elif cur.status in SUBSCRIPTION_TERMINATED_STATES and s.status in _ACTIVE_SUB:
            best[s.org_id] = s
    return best


def list_organizations(db, q=None, status=None, plan=None, page=1, page_size=20):
    stmt = select(Organization)
    if q:
        like = f"%{q.lower()}%"
        stmt = stmt.where(or_(func.lower(Organization.name).like(like),
                              func.lower(Organization.domain).like(like)))
    if status:
        stmt = stmt.where(Organization.status == status)

    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    orgs = db.scalars(
        stmt.order_by(Organization.created_at.desc())
        .offset((page - 1) * page_size).limit(page_size)
    ).all()

    ids = [o.id for o in orgs]
    users, events, subs = _user_counts(db, ids), _event_counts(db, ids), _current_subs(db, ids)

    items = [_org_out(o, users, events, subs) for o in orgs]
    # Plan filter applies post-join (plan lives on the subscription, not the org row).
    if plan:
        items = [o for o in items if o.plan == plan]
    return items, total


def _org_out(o, users, events, subs) -> OrgOut:
    sub = subs.get(o.id)
    return OrgOut(
        id=o.id, name=o.name, domain=o.domain, status=o.status, region=o.region,
        storage_used_gb=o.storage_used_gb, bandwidth_gb=o.bandwidth_gb, created_at=o.created_at,
        users_count=users.get(o.id, 0), events_count=events.get(o.id, 0),
        plan=sub.plan.name if sub else None,
        subscription_status=sub.status if sub else None,
    )


def get_organization(db, org_id) -> OrgOut | None:
    o = db.get(Organization, org_id)
    if not o:
        return None
    ids = [o.id]
    return _org_out(o, _user_counts(db, ids), _event_counts(db, ids), _current_subs(db, ids))


# The plan every new organization starts on (approved Product decision). A slug, resolved
# against the catalog at call time, so this file never holds an entitlement or a price.
INITIAL_PLAN_SLUG = "developer"


def provision_initial_subscription(db, org: Organization, *, plan_slug: str | None = None,
                                    now: datetime | None = None,
                                    actor=None) -> Subscription | None:
    """Give `org` its initial Developer subscription on a 14-day trial. Returns it, or None.

    THE SINGLE implementation of the approved provisioning rule, shared by every path that
    creates an organization (self-registration, the admin console) and by the backfill
    migration. One place, so the plan, the state and the trial length cannot drift between
    them — they were previously inline in one path and absent from the other, which is why
    2149 organizations ended up with no subscription at all.

    THE APPROVED RULES, enforced here rather than described:
      * Developer plan, resolved by slug from the catalog.
      * Section 12 state `trialing`.
      * 14 days (`TRIAL_DAYS`), starting when the subscription is PROVISIONED — which is what
        `started_at` records, so the window and its origin are both on the row.
      * No card and no charge: nothing in this function imports, calls or reaches Stripe. There
        is no payment method to collect and no Stripe subscription to create.
      * No automatic conversion: reaching `trial_ends_at` charges nobody. Section 12 gives
        `trialing` no edge to `active` — the only route is CONVERSION_PENDING, which a
        deliberate checkout starts.

    IDEMPOTENT: returns None without writing if the organization already has ANY subscription
    row, whatever its state. That single guard is what makes re-running the migration, or a
    retried registration, unable to give one tenant two subscriptions.

    DOES NOT COMMIT. It adds the subscription and its audit row to the caller's session so
    they land in the SAME transaction as the organization and user being created — an
    organization must never be committed with a half-written subscription beside it. Note this
    is deliberately unlike `create_audit_log`, which commits internally; using that here would
    break the atomicity this function exists to preserve.
    """
    # Any existing subscription — not just an active one — makes this a no-op.
    if db.scalar(select(Subscription.id).where(Subscription.org_id == org.id)) is not None:
        return None

    plan = get_plan_by_slug(db, plan_slug or INITIAL_PLAN_SLUG)
    if plan is None or not plan.is_active:
        # FAIL SOFT, on purpose, and loudly. Raising here would make organization creation
        # depend on the plan catalog already carrying the Section 03 slugs — so deploying this
        # code before `migrate_plan_names.py` runs would break every signup. Degrading to
        # today's behaviour (no subscription) keeps the user-facing flow working while the log
        # says exactly what is wrong.
        log.error(
            "cannot provision an initial subscription for org %s: plan %r is missing or "
            "inactive. Run migrate_plan_names.py, then migrate_provision_subscriptions.py "
            "to backfill.",
            org.id, plan_slug or INITIAL_PLAN_SLUG,
        )
        return None

    started = now or datetime.now(timezone.utc)
    sub = Subscription(
        org_id=org.id,
        plan_id=plan.id,
        status="trialing",
        started_at=started,
        trial_ends_at=started + timedelta(days=TRIAL_DAYS),
    )
    db.add(sub)
    db.flush()

    # Added, not committed — see the docstring. Same shape as create_audit_log's row.
    db.add(AuditLog(
        actor_id=actor.id if actor else None,
        actor_email=actor.email if actor else None,
        action="subscription.provisioned",
        target_type="subscription", target_id=str(sub.id), org_id=org.id,
        meta={"plan_slug": plan.slug, "status": "trialing",
              "trial_days": TRIAL_DAYS,
              "started_at": started.isoformat(),
              "trial_ends_at": sub.trial_ends_at.isoformat(),
              "stripe": "none - no card required, no charge, no Stripe subscription"},
    ))
    return sub


def create_organization(db, data) -> Organization:
    org = Organization(
        name=data.name, domain=data.domain, region=data.region or "US East",
        status=data.status or "active",
    )
    db.add(org)
    db.flush()
    # `plan_slug` still selects the plan when the console names one; omitting it no longer
    # means "no subscription at all" but "the approved default", so an admin-created
    # organization is provisioned on the same terms as a self-registered one.
    provision_initial_subscription(db, org, plan_slug=data.plan_slug or None)
    db.commit()
    db.refresh(org)
    return org


def update_organization(db, org: Organization, data) -> Organization:
    for field in ("name", "domain", "region", "status", "storage_used_gb", "bandwidth_gb"):
        val = getattr(data, field, None)
        if val is not None:
            setattr(org, field, val)
    if data.plan_slug:
        plan = get_plan_by_slug(db, data.plan_slug)
        if plan:
            # Was: db.add(Subscription(...)) — which INSERTED a second row on every plan
            # change, contradicting this model's own documented invariant ("One active row per
            # org") and leaving both readers to disambiguate by `started_at desc`. Orgs
            # accumulated rows and none was authoritative, which is exactly what
            # ZST-COM-PLAN-001 COM-ENT-003 ("One authoritative commercial phase per tenant")
            # exists to prevent. Now updates the current subscription in place, creating one
            # only when the org genuinely has none.
            current = _current_subs(db, [org.id]).get(org.id)
            if current is None:
                db.add(Subscription(org_id=org.id, plan_id=plan.id, status="active"))
            else:
                current.plan_id = plan.id
    db.commit()
    db.refresh(org)
    return org


def _delete_legacy_memberships(db, column: str, value) -> None:
    """Clear the legacy `memberships` rows that FK-reference a user/org being hard-deleted.

    The table predates the current org_id-on-User model and is NOT SQLAlchemy-mapped, which
    has a consequence beyond needing raw SQL: `Base.metadata.create_all()` never creates it,
    so it exists on databases carried forward from the older schema and does NOT exist on a
    freshly-provisioned one. An unconditional DELETE therefore raised UndefinedTable and made
    every hard delete of a user or organization a 500 on any new deployment.

    `to_regclass` returns NULL for a missing relation, so this is a no-op there and byte-for-byte
    the previous behaviour wherever the table is still present. `column` is never caller-supplied
    — both call sites pass a literal — but it is validated anyway so this can never become an
    injection point if that changes.
    """
    if column not in ("org_id", "user_id"):
        raise ValueError(f"Unsupported memberships column {column!r}")
    if db.scalar(text("SELECT to_regclass('public.memberships')")) is None:
        return
    db.execute(text(f"DELETE FROM memberships WHERE {column} = :v"), {"v": value})


def delete_organization(db, org: Organization) -> None:
    db.query(Subscription).filter(Subscription.org_id == org.id).delete()
    # `memberships` predates the current org_id-on-User model and isn't SQLAlchemy-mapped,
    # but it still FK-references organizations — clean it up raw or the delete 500s.
    _delete_legacy_memberships(db, "org_id", org.id)
    db.delete(org)
    db.commit()


# ── Users ──────────────────────────────────────────────────────────────────

def list_users(db, q=None, role=None, org_id=None, is_active=None, page=1, page_size=20):
    stmt = select(User)
    if q:
        like = f"%{q.lower()}%"
        stmt = stmt.where(or_(func.lower(User.full_name).like(like),
                              func.lower(User.email).like(like),
                              func.lower(User.username).like(like)))
    if role:
        stmt = stmt.where(User.role == role)
    if org_id:
        stmt = stmt.where(User.org_id == org_id)
    if is_active is not None:
        stmt = stmt.where(User.is_active == is_active)

    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    users = db.scalars(
        stmt.order_by(User.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    ).all()
    return [_user_out(u) for u in users], total


def user_stats(db) -> dict:
    """Dataset-wide counts for the Users console's KPI row — one query instead of the four
    page_size=1 round trips the page used to make per load."""
    row = db.execute(
        select(
            func.count(),
            func.count().filter(User.is_active.is_(True)),
            func.count().filter(User.is_active.is_(False)),
            func.count().filter(User.role == "super_admin"),
        )
    ).one()
    total, active, inactive, super_admins = row
    return {"total": total, "active": active, "inactive": inactive, "super_admins": super_admins}


def _user_out(u: User) -> AdminUserOut:
    out = AdminUserOut.model_validate(u)
    out.organization_name = u.organization.name if u.organization else None
    return out


def update_user(db, user: User, data) -> AdminUserOut:
    for field in ("role", "is_active", "full_name"):
        val = getattr(data, field, None)
        if val is not None:
            setattr(user, field, val)
    # "" clears it back to unscoped full-access super_admin (see schemas.admin.UserUpdate);
    # None means "not included in this PATCH", matching every other field's convention here.
    staff_role = getattr(data, "staff_commercial_role", None)
    if staff_role is not None:
        target_role = data.role if data.role is not None else user.role
        if staff_role == "":
            user.staff_commercial_role = None
        elif target_role != "super_admin":
            raise ValueError("staff_commercial_role only applies to super_admin users")
        elif staff_role not in STAFF_COMMERCIAL_ROLES:
            raise ValueError(f"staff_commercial_role must be one of {STAFF_COMMERCIAL_ROLES}")
        else:
            user.staff_commercial_role = staff_role
    db.commit()
    db.refresh(user)
    return _user_out(user)


def delete_user(db, user: User) -> None:
    # `memberships` predates the current org_id-on-User model and isn't SQLAlchemy-mapped,
    # but it still FK-references users — clean it up raw or the delete 500s.
    _delete_legacy_memberships(db, "user_id", user.id)
    # Identity tables added for IDN-001/IDN-003/IDN-007 also FK-reference users, and this
    # is a HARD delete (unlike the org-level soft delete), so every one of them would block
    # it. Removing them is correct: a spent verification challenge or a sign-in history row
    # has no meaning once the identity is gone. The lasting record of the deletion lives in
    # audit_logs and account_state_events, neither of which is FK-bound to the user.
    for table in ("identity_challenges", "sign_in_events", "account_recoveries",
                  "step_up_grants"):
        db.execute(text(f"DELETE FROM {table} WHERE user_id = :uid"), {"uid": user.id})

    # Governance records added for ZST-EC-001 ORG-007/008/009 also FK-reference users, but
    # they must NOT be deleted with the identity: a support session that touched a tenant,
    # an ownership transfer, or an access-review decision are exactly the records that have
    # to survive the person leaving. The references are nulled instead, and each row already
    # stores the human-readable identity alongside the id (engineer_display,
    # approved_by_email, current_owner_email, reviewer_email), so the record stays readable.
    for stmt in (
        "UPDATE support_access_requests SET engineer_id = NULL WHERE engineer_id = :uid",
        "UPDATE support_access_requests SET approved_by_id = NULL WHERE approved_by_id = :uid",
        "UPDATE support_access_requests SET emergency_authorizer_id = NULL "
        "WHERE emergency_authorizer_id = :uid",
        "UPDATE ownership_transfers SET current_owner_id = NULL WHERE current_owner_id = :uid",
        "UPDATE ownership_transfers SET proposed_owner_id = NULL WHERE proposed_owner_id = :uid",
        "UPDATE ownership_transfers SET initiated_by_id = NULL WHERE initiated_by_id = :uid",
        "UPDATE access_reviews SET created_by_id = NULL WHERE created_by_id = :uid",
        "UPDATE access_review_assignments SET reviewer_id = NULL WHERE reviewer_id = :uid",
        "UPDATE access_review_assignments SET member_id = NULL WHERE member_id = :uid",
        "UPDATE access_review_assignments SET decided_by_id = NULL WHERE decided_by_id = :uid",
        "UPDATE access_review_escalations SET escalated_to_id = NULL "
        "WHERE escalated_to_id = :uid",
        "UPDATE organizations SET owner_user_id = NULL WHERE owner_user_id = :uid",
    ):
        db.execute(text(stmt), {"uid": user.id})

    # Elevation sessions are ended rather than kept dangling — an elevation belonging to a
    # deleted account should not read as still open — and then removed, since the durable
    # record of what was done under it lives in audit_logs.
    db.execute(text("DELETE FROM elevation_sessions WHERE user_id = :uid"), {"uid": user.id})

    db.delete(user)
    db.commit()


# ── Plans & Subscriptions ────────────────────────────────────────────────────

def get_plan_by_slug(db, slug) -> Plan | None:
    return db.scalar(select(Plan).where(Plan.slug == slug))


def list_plans(db) -> list[PlanOut]:
    # price_monthly is nullable (no approved price published yet), and ordering by a column
    # that is NULL for every row is non-deterministic — fall back to name so the console and
    # the Billing page always render the tiers in a stable order.
    plans = db.scalars(select(Plan).order_by(Plan.price_monthly.nullslast(), Plan.name)).all()
    return [PlanOut.model_validate(p) for p in plans]


def list_subscriptions(db, status=None, page=1, page_size=20):
    stmt = select(Subscription)
    if status:
        stmt = stmt.where(Subscription.status == status)
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    subs = db.scalars(
        stmt.order_by(Subscription.started_at.desc()).offset((page - 1) * page_size).limit(page_size)
    ).all()
    return [_sub_out(s) for s in subs], total


def _sub_out(s: Subscription) -> SubscriptionOut:
    return SubscriptionOut(
        id=s.id, org_id=s.org_id,
        organization_name=s.organization.name if s.organization else None,
        plan=s.plan.name if s.plan else None,
        # Guard the price separately from the plan: price_monthly is nullable now, and
        # float(None) raises. None here means "no approved price", which SubscriptionOut
        # already renders as "—".
        price_monthly=(
            float(s.plan.price_monthly)
            if s.plan and s.plan.price_monthly is not None
            else None
        ),
        status=s.status, seats=s.seats, started_at=s.started_at,
        current_period_end=s.current_period_end, trial_ends_at=s.trial_ends_at,
    )


def get_subscription(db, sub_id) -> Subscription | None:
    return db.get(Subscription, sub_id)


def update_subscription(db, sub: Subscription, data) -> SubscriptionOut:
    """A status change is now a validated Section 12 transition, not a field assignment.

    Previously this wrote `data.status` verbatim: any string up to the column width persisted,
    including nonsense and including a jump straight from `canceled` back to `active`. That
    contradicts ZST-COM-PLAN-001 Section 12, whose whole purpose is that commercial state be
    explicit and never inferred. Raises ValueError; the router turns that into a 400.
    """
    if data.status is not None:
        error = subscription_transition_error(sub.status, data.status)
        if error:
            raise ValueError(error)
        sub.status = normalize_subscription_state(data.status)
    if data.seats is not None:
        sub.seats = data.seats
    if data.current_period_end is not None:
        sub.current_period_end = data.current_period_end
    if data.plan_slug:
        plan = get_plan_by_slug(db, data.plan_slug)
        if plan is None:
            raise ValueError(f"Unknown plan '{data.plan_slug}'")
        if plan.id != sub.plan_id:
            # THE GOVERNANCE FIX. This used to be `sub.plan_id = plan.id; db.commit()` — an
            # immediate, unvalidated, UNAUDITED swap sitting right beside the carefully guarded
            # status field above. On a paying customer that silently moved their entitlement
            # and their billing basis apart: the plan on record changed, Stripe kept invoicing
            # the old price, and no audit row recorded who did it or when.
            #
            # A PAID subscription now goes through the same lifecycle a customer's own request
            # does — scheduled to `current_period_end`, no proration, entitlements unchanged
            # until then. Support gets no power here that the approved rules deny a customer,
            # which is the point: an administrative shortcut that bypasses the commercial rules
            # is how the record and the money drift apart.
            if normalize_subscription_state(sub.status) in ("active", "plan_change_scheduled"):
                interval = data.billing_interval or sub.billing_interval or MONTHLY
                request_plan_change(db, sub, plan=plan, billing_interval=interval,
                                    actor=getattr(data, "_actor", None))
            else:
                # Not a live paid subscription — there is no period boundary to schedule
                # against and no active billing to keep consistent (a trial, a pending
                # activation, or a terminated row). Immediate assignment is correct here, but
                # it is AUDITED now, which it never was.
                previous = str(sub.plan_id)
                sub.plan_id = plan.id
                create_audit_log(
                    db, actor=getattr(data, "_actor", None),
                    action="subscription.plan_set_by_admin",
                    target_type="subscription", target_id=sub.id, org_id=sub.org_id,
                    meta={"from": previous, "to": str(plan.id), "to_slug": plan.slug,
                          "state": sub.status,
                          "reason": "not a live paid subscription — applied immediately"},
                )
    db.commit()
    db.refresh(sub)
    return _sub_out(sub)


# ── Audit logs ───────────────────────────────────────────────────────────────

def create_audit_log(db, *, actor, action, target_type=None, target_id=None,
                     org_id=None, meta=None, ip=None) -> None:
    db.add(AuditLog(
        actor_id=actor.id if actor else None,
        actor_email=actor.email if actor else None,
        action=action, target_type=target_type,
        target_id=str(target_id) if target_id is not None else None,
        org_id=org_id, meta=meta, ip=ip,
    ))
    db.commit()


def list_audit_logs(db, action=None, target_type=None, page=1, page_size=50):
    stmt = select(AuditLog)
    if action:
        stmt = stmt.where(AuditLog.action == action)
    if target_type:
        stmt = stmt.where(AuditLog.target_type == target_type)
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    logs = db.scalars(
        stmt.order_by(AuditLog.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    ).all()
    return [AuditLogOut.model_validate(x) for x in logs], total


# ── Feature flags ────────────────────────────────────────────────────────────

def list_feature_flags(db) -> list[FeatureFlagOut]:
    flags = db.scalars(select(FeatureFlag).order_by(FeatureFlag.key)).all()
    return [FeatureFlagOut.model_validate(f) for f in flags]


def get_feature_flag(db, flag_id) -> FeatureFlag | None:
    return db.get(FeatureFlag, flag_id)


def get_feature_flag_by_key(db, key) -> FeatureFlag | None:
    return db.scalar(select(FeatureFlag).where(FeatureFlag.key == key))


def create_feature_flag(db, data, updated_by: str) -> FeatureFlag:
    flag = FeatureFlag(key=data.key, name=data.name, description=data.description,
                       enabled=data.enabled, updated_by=updated_by)
    db.add(flag)
    db.commit()
    db.refresh(flag)
    return flag


def update_feature_flag(db, flag: FeatureFlag, data, updated_by: str) -> FeatureFlag:
    for field in ("name", "description", "enabled"):
        val = getattr(data, field, None)
        if val is not None:
            setattr(flag, field, val)
    flag.updated_by = updated_by
    db.commit()
    db.refresh(flag)
    return flag


def delete_feature_flag(db, flag: FeatureFlag) -> None:
    db.delete(flag)
    db.commit()


# ── Release center ───────────────────────────────────────────────────────────

def list_releases(db, page=1, page_size=50):
    stmt = select(Release)
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    releases = db.scalars(
        stmt.order_by(Release.released_at.desc()).offset((page - 1) * page_size).limit(page_size)
    ).all()
    return [ReleaseOut.model_validate(r) for r in releases], total


def get_release(db, release_id) -> Release | None:
    return db.get(Release, release_id)


def create_release(db, data, released_by: str) -> Release:
    release = Release(version=data.version, title=data.title, notes=data.notes,
                      channel=data.channel or "production", released_by=released_by)
    db.add(release)
    db.commit()
    db.refresh(release)
    return release


def delete_release(db, release: Release) -> None:
    db.delete(release)
    db.commit()


# ── Support tickets ──────────────────────────────────────────────────────────

def _ticket_out(t: SupportTicket) -> SupportTicketOut:
    out = SupportTicketOut.model_validate(t)
    out.organization_name = t.organization.name if t.organization else None
    return out


def list_support_tickets(db, status=None, org_id=None, page=1, page_size=50):
    stmt = select(SupportTicket)
    if status:
        stmt = stmt.where(SupportTicket.status == status)
    if org_id:
        stmt = stmt.where(SupportTicket.org_id == org_id)
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    tickets = db.scalars(
        stmt.order_by(SupportTicket.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    ).all()
    return [_ticket_out(t) for t in tickets], total


def get_support_ticket(db, ticket_id) -> SupportTicket | None:
    return db.get(SupportTicket, ticket_id)


def create_support_ticket(db, data) -> SupportTicket:
    ticket = SupportTicket(
        org_id=data.org_id, subject=data.subject, message=data.message,
        priority=data.priority or "normal", requester_email=data.requester_email,
    )
    db.add(ticket)
    db.commit()
    db.refresh(ticket)
    return _ticket_out(ticket)


def update_support_ticket(db, ticket: SupportTicket, data) -> SupportTicket:
    if data.status is not None:
        ticket.status = data.status
        if data.status in ("resolved", "closed") and ticket.resolved_at is None:
            ticket.resolved_at = datetime.now(timezone.utc)
        elif data.status in ("open", "in_progress"):
            ticket.resolved_at = None
    if data.priority is not None:
        ticket.priority = data.priority
    db.commit()
    db.refresh(ticket)
    return _ticket_out(ticket)


def delete_support_ticket(db, ticket: SupportTicket) -> None:
    db.delete(ticket)
    db.commit()


# ── Incidents (Trust & Safety console) ───────────────────────────────────────

def _incident_out(i: Incident) -> IncidentOut:
    out = IncidentOut.model_validate(i)
    out.organization_name = i.organization.name if i.organization else None
    return out


def _incident_ref(now: datetime) -> str:
    # INC-20260814-0630-a1b2 — date+time makes it human-scannable, the hex tail keeps
    # concurrent opens from colliding on the same second.
    return f"INC-{now:%Y%m%d}-{now:%H%M}-{secrets.token_hex(2)}"


def list_incidents(db, status=None, kind=None, org_id=None, page=1, page_size=50):
    stmt = select(Incident)
    if status:
        stmt = stmt.where(Incident.status == status)
    if kind:
        stmt = stmt.where(Incident.kind == kind)
    if org_id:
        stmt = stmt.where(Incident.org_id == org_id)
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = db.scalars(
        stmt.order_by(Incident.started_at.desc()).offset((page - 1) * page_size).limit(page_size)
    ).all()
    return [_incident_out(i) for i in rows], total


def get_incident(db, incident_id) -> Incident | None:
    return db.get(Incident, incident_id)


def create_incident(db, data, commander_default: str | None) -> Incident:
    now = datetime.now(timezone.utc)
    incident = Incident(
        ref=_incident_ref(now), title=data.title, detail=data.detail,
        severity=data.severity, kind=data.kind, org_id=data.org_id,
        commander=data.commander or commander_default, started_at=now,
    )
    db.add(incident)
    db.commit()
    db.refresh(incident)
    return _incident_out(incident)


def update_incident(db, incident: Incident, data) -> Incident:
    if data.status is not None:
        incident.status = data.status
        if data.status == "resolved" and incident.resolved_at is None:
            incident.resolved_at = datetime.now(timezone.utc)
        elif data.status != "resolved":
            incident.resolved_at = None
    if data.severity is not None:
        incident.severity = data.severity
    if data.commander is not None:
        incident.commander = data.commander
    db.commit()
    db.refresh(incident)
    return _incident_out(incident)


# ── Governance records ────────────────────────────────────────────────────────

def _governance_record_out(r: GovernanceRecord) -> GovernanceRecordOut:
    out = GovernanceRecordOut.model_validate(r)
    out.organization_name = r.organization.name if r.organization else None
    return out


def legal_hold_event_ids(db, event_ids) -> set:
    """Which of these events currently have an OPEN legal-hold governance record —
    resolved_at IS NULL is the same "still active" signal services/ops.py already uses to
    count active legal holds for the admin dashboard. Only a super_admin can open/resolve
    one (routers/admin.py's create/update_governance_record), matching who actually has
    authority to place a legal hold — never the org itself. Used to block recording
    deletion (routers/organization.py's delete_recording) in addition to the recording's
    own `legal_hold` column, which nothing currently sets directly."""
    if not event_ids:
        return set()
    return set(db.scalars(
        select(GovernanceRecord.event_id).where(
            GovernanceRecord.event_id.in_(event_ids),
            GovernanceRecord.kind == "legal_hold",
            GovernanceRecord.resolved_at.is_(None),
        )
    ).all())


def event_under_legal_hold(db, event_id) -> bool:
    return event_id in legal_hold_event_ids(db, [event_id])


def list_governance_records(db, kind=None, status=None, org_id=None, page=1, page_size=50):
    stmt = select(GovernanceRecord)
    if kind:
        stmt = stmt.where(GovernanceRecord.kind == kind)
    if status:
        stmt = stmt.where(GovernanceRecord.status == status)
    if org_id:
        stmt = stmt.where(GovernanceRecord.org_id == org_id)
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = db.scalars(
        stmt.order_by(GovernanceRecord.opened_at.desc()).offset((page - 1) * page_size).limit(page_size)
    ).all()
    return [_governance_record_out(r) for r in rows], total


def get_governance_record(db, record_id) -> GovernanceRecord | None:
    return db.get(GovernanceRecord, record_id)


def create_governance_record(db, data) -> GovernanceRecord:
    record = GovernanceRecord(
        kind=data.kind, org_id=data.org_id, event_id=data.event_id,
        detail=data.detail, due_at=data.due_at,
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return _governance_record_out(record)


def update_governance_record(db, record: GovernanceRecord, data) -> GovernanceRecord:
    if data.status is not None:
        record.status = data.status
        if data.status == "resolved" and record.resolved_at is None:
            record.resolved_at = datetime.now(timezone.utc)
        elif data.status != "resolved":
            record.resolved_at = None
    if data.detail is not None:
        record.detail = data.detail
    if data.due_at is not None:
        record.due_at = data.due_at
    db.commit()
    db.refresh(record)
    return _governance_record_out(record)


# ── Developer / API keys (stored on Organization.api_keys JSON) ─────────────

def key_fingerprint(record: dict) -> str:
    """Non-secret short identifier for one credential (ZST-EC-001 DEV-002).

    Derived from the stored sha256 VERIFIER, never from the secret itself. The existing
    `prefix` field would have been the obvious candidate, but it is `raw[:12]` - eight
    characters of literal banner plus four characters of the actual token - so publishing it
    in an email would disclose real key material for no benefit.

    Eight hex characters of a hash carry no reconstruction risk and are enough for a human
    to match an email against a console row.
    """
    digest = (record.get("key_hash") or record.get("id") or "").replace("-", "")
    short = digest[:8].upper() or "UNKNOWN"
    return f"{short[:4]}-{short[4:]}" if len(short) == 8 else short


def list_api_keys(db, org: Organization) -> list[ApiKeyOut]:
    records = org.api_keys or []
    return [ApiKeyOut(id=r["id"], label=r["label"], prefix=r["prefix"],
                      fingerprint=key_fingerprint(r),
                      created_at=r["created_at"], expires_at=r.get("expires_at"),
                      revoked=r.get("revoked", False))
            for r in records]


def get_api_key_record(org: Organization, key_id: str) -> dict | None:
    for r in (org.api_keys or []):
        if r.get("id") == key_id:
            return r
    return None


# Credential lifecycle states (ZST-EC-001 DEV-003/DEV-004). Explicit state, not inferred
# from which notification markers happen to be set.
KEY_ACTIVE = "active"
KEY_EXPIRED = "expired"
KEY_REVOKED = "revoked"
KEY_STATUSES = (KEY_ACTIVE, KEY_EXPIRED, KEY_REVOKED)

# How far ahead of expiry the warning fires.
KEY_EXPIRY_WARNING_DAYS = 7


def key_status(record: dict) -> str:
    """Effective state of one credential record.

    Derived so that records written before `status` existed still report correctly: a
    revoked flag or a lapsed expiry is authoritative even without the field.
    """
    if record.get("revoked"):
        return KEY_REVOKED
    stored = record.get("status")
    if stored in KEY_STATUSES:
        return stored
    expires = _parse_ts(record.get("expires_at"))
    if expires is not None and expires <= datetime.now(timezone.utc):
        return KEY_EXPIRED
    return KEY_ACTIVE


def _parse_ts(value):
    if not value:
        return None
    if hasattr(value, "tzinfo"):
        return value
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _mutate_key(db, org: Organization, key_id: str, changes: dict) -> dict | None:
    """Apply changes to one credential record in the JSON column, atomically enough for a
    single-writer path. Returns the updated record."""
    records, updated, found = org.api_keys or [], [], None
    for r in records:
        if r.get("id") == key_id:
            r = {**r, **changes}
            found = r
        updated.append(r)
    if found is not None:
        org.api_keys = updated
        db.commit()
    return found


def claim_key_notification(db, org: Organization, key_id: str,
                           column: str = "dev_002_notified_at") -> bool:
    """Claim the right to send ONE notification for one credential. True for exactly one
    caller, per (credential, marker).

    The marker lives on the credential record itself, so a retried request that somehow
    reached this point twice cannot produce two security emails for one credential.
    """
    records = org.api_keys or []
    updated, claimed = [], False
    for r in records:
        if r.get("id") == key_id and not r.get(column):
            r = {**r, column: datetime.now(timezone.utc).isoformat()}
            claimed = True
        updated.append(r)
    if claimed:
        org.api_keys = updated
        db.commit()
    return claimed


def mark_key_expired(db, org: Organization, key_id: str) -> dict | None:
    """Move a lapsed credential into the EXPIRED state.

    This records lifecycle state. It does NOT block access, because no Zoiko Steam endpoint
    authenticates an API key - see the reported DEV-003 gap, and note that the expiry email
    is worded accordingly.
    """
    record = get_api_key_record(org, key_id)
    if record is None or key_status(record) != KEY_ACTIVE:
        return None
    return _mutate_key(db, org, key_id,
                       {"status": KEY_EXPIRED,
                        "expired_at": datetime.now(timezone.utc).isoformat()})


def rotate_api_key(db, org: Organization, key_id: str, actor_id=None,
                   expires_in_days: int | None = None) -> tuple[dict | None, ApiKeyCreated | None]:
    """Issue a replacement and retire the original, recording the relationship both ways.

    There is NO overlap window: the previous credential is revoked immediately. An overlap
    could only be honoured by an authentication path that checks expiry, and none exists —
    promising one would be promising behaviour nothing enforces. The email says so.
    """
    old = get_api_key_record(org, key_id)
    if old is None or key_status(old) == KEY_REVOKED:
        return None, None

    label = old.get("label") or "Rotated credential"
    replacement = create_api_key(db, org, label, expires_in_days=expires_in_days)
    now = datetime.now(timezone.utc).isoformat()

    _mutate_key(db, org, replacement.id,
                {"rotated_from": key_id,
                 "rotated_from_fingerprint": key_fingerprint(old),
                 "rotated_at": now,
                 "rotated_by": str(actor_id) if actor_id else None})
    retired = _mutate_key(db, org, key_id, {
        "revoked": True, "status": KEY_REVOKED, "revoked_at": now,
        "revoked_by": str(actor_id) if actor_id else None,
        "revoke_reason": "rotated",
        "rotated_to": replacement.id,
        "rotated_to_fingerprint": replacement.fingerprint,
    })
    return retired, replacement


def create_api_key(db, org: Organization, label: str, expires_in_days: int | None = None) -> ApiKeyCreated:
    now = datetime.now(timezone.utc)
    raw = f"zk_live_{secrets.token_urlsafe(32)}"
    record = {
        "id": str(uuid.uuid4()),
        "label": label,
        "prefix": raw[:12],
        "key_hash": hashlib.sha256(raw.encode()).hexdigest(),
        "created_at": now.isoformat(),
        # An expiry makes credential rotation a real, surfaceable obligation (the org
        # console's "expires in N days" item reads this). None = non-expiring, which is
        # what every key created before this field existed remains.
        "expires_at": (now + timedelta(days=expires_in_days)).isoformat() if expires_in_days else None,
        "revoked": False,
        "status": KEY_ACTIVE,
    }
    org.api_keys = [*(org.api_keys or []), record]
    db.commit()
    return ApiKeyCreated(id=record["id"], label=label, prefix=record["prefix"],
                         fingerprint=key_fingerprint(record),
                         created_at=record["created_at"], revoked=False, key=raw,
                         expires_at=record["expires_at"])


def revoke_api_key(db, org: Organization, key_id: str, actor_id=None,
                   reason: str = "administrative") -> bool:
    """Revoke one credential, recording WHO and WHY.

    Previously this set a bare `revoked: True`, so a revocation could be seen but never
    attributed. The record is retained rather than deleted, which is what keeps the audit
    history intact (ZST-EC-001 DEV-004).
    """
    records = org.api_keys or []
    found = False
    updated = []
    for r in records:
        if r["id"] == key_id:
            found = True
            r = {**r, "revoked": True, "status": KEY_REVOKED,
                 "revoked_at": datetime.now(timezone.utc).isoformat(),
                 "revoked_by": str(actor_id) if actor_id else None,
                 "revoke_reason": reason}
        updated.append(r)
    if not found:
        return False
    org.api_keys = updated
    db.commit()
    return True


# ── Commercial overrides (ZST-COM-PLAN-001 Section 19 / Section 20) ──────────────────────
#
# Section 20 is the requirement these functions exist to satisfy: "No employee may grant a
# paid feature through a feature flag, database edit or support impersonation. Use an
# approved entitlement override record."

def request_commercial_override(db, *, org, actor, override_type: str, target_type: str,
                                 target_key: str, reason: str, expires_at,
                                 target_value=None, audit_ref: str | None = None) -> CommercialOverride:
    """Raise an override request. Never self-approving — approval is a separate call by a
    different person (approve_commercial_override), matching the maker-checker posture the
    commercial ledger already uses for exceptions and refunds.

    Every refusal here is a Section 19 control, not a house rule:
      * the type must be one of the seven the document names;
      * "reasoned" -> a non-empty reason is mandatory;
      * "time-bound" / "automatic expiry" -> a FUTURE expiry is mandatory. An override that
        is already expired at creation, or has no end, is the permanent shadow plan Section 19
        exists to prevent.
    """
    if override_type not in COMMERCIAL_OVERRIDE_TYPES:
        raise ValueError(
            f"Unknown override type '{override_type}' "
            f"(allowed: {', '.join(COMMERCIAL_OVERRIDE_TYPES)})"
        )
    if target_type not in COMMERCIAL_OVERRIDE_TARGETS:
        raise ValueError(
            f"Unknown override target '{target_type}' "
            f"(allowed: {', '.join(COMMERCIAL_OVERRIDE_TARGETS)})"
        )
    if not (reason and reason.strip()):
        raise ValueError(
            "A commercial override requires a reason — an unexplained exception is not "
            "auditable (ZST-COM-PLAN-001 Section 19)"
        )
    if not (target_key and target_key.strip()):
        raise ValueError("A commercial override must name what it overrides")
    if expires_at is None:
        raise ValueError(
            "A commercial override must expire — Section 19 requires exceptions be time-bound "
            "with automatic expiry, so that they cannot become permanent shadow plans"
        )
    if expires_at <= datetime.now(timezone.utc):
        raise ValueError("Override expiry must be in the future")

    override = CommercialOverride(
        org_id=org.id, override_type=override_type, target_type=target_type,
        target_key=target_key.strip(), target_value=target_value, reason=reason.strip(),
        requested_by=actor.id if actor else None, status="requested",
        expires_at=expires_at, audit_ref=audit_ref,
    )
    db.add(override)
    db.flush()
    create_audit_log(
        db, actor=actor, action="commercial.override.requested",
        target_type="commercial_override", target_id=override.id, org_id=org.id,
        meta={"override_type": override_type, "target_type": target_type,
              "target_key": override.target_key, "reason": override.reason,
              "expires_at": expires_at.isoformat(), "audit_ref": audit_ref},
    )
    db.refresh(override)
    return override


def approve_commercial_override(db, override: CommercialOverride, approver) -> CommercialOverride:
    """Two-person approval. Reuses the commercial ledger's fail-closed maker-checker helper
    rather than re-implementing the comparison, so a NULL requester cannot self-approve."""
    from .commercial import assert_distinct_maker_checker

    if override.status != "requested":
        raise ValueError(f"Cannot approve an override in status '{override.status}'")
    if override.expires_at <= datetime.now(timezone.utc):
        raise ValueError("This override has already expired and cannot be approved")
    assert_distinct_maker_checker(
        override.requested_by, approver,
        violation="the approver must differ from the requester",
        missing_maker="this override records no requester, so approval cannot be separated from it",
    )
    override.status = "approved"
    override.approver_id = approver.id
    override.decided_at = datetime.now(timezone.utc)
    create_audit_log(
        db, actor=approver, action="commercial.override.approved",
        target_type="commercial_override", target_id=override.id, org_id=override.org_id,
        meta={"override_type": override.override_type, "target_key": override.target_key,
              "requested_by": str(override.requested_by),
              "expires_at": override.expires_at.isoformat(), "audit_ref": override.audit_ref},
    )
    db.refresh(override)
    return override


def active_overrides(db, org_id, *, target_type: str | None = None,
                     now: datetime | None = None) -> list[CommercialOverride]:
    """Overrides that are APPROVED and NOT YET EXPIRED, for one tenant.

    Expiry is evaluated at READ time, so a lapsed override stops granting the moment it
    expires — it does not wait for a sweep to run. The maintenance job below only tidies the
    stored status for reporting; correctness never depends on it having run. This is the same
    lazy-expiry posture crud.commercial.active_exception uses.
    """
    now = now or datetime.now(timezone.utc)
    stmt = select(CommercialOverride).where(
        CommercialOverride.org_id == org_id,
        CommercialOverride.status == "approved",
        CommercialOverride.expires_at > now,
    )
    if target_type is not None:
        stmt = stmt.where(CommercialOverride.target_type == target_type)
    return list(db.scalars(stmt.order_by(CommercialOverride.expires_at)).all())


def expire_lapsed_overrides(db, *, now: datetime | None = None) -> int:
    """Mark approved overrides whose expiry has passed as `expired`.

    Section 19 requires "automatic expiry". Access control already honours expiry lazily
    (active_overrides above); this makes the stored state agree, so a console listing does not
    show a lapsed grant as still approved. Idempotent: a second run matches nothing.
    """
    now = now or datetime.now(timezone.utc)
    lapsed = db.scalars(
        select(CommercialOverride).where(
            CommercialOverride.status == "approved",
            CommercialOverride.expires_at <= now,
        )
    ).all()
    for override in lapsed:
        override.status = "expired"
        create_audit_log(
            db, actor=None, action="commercial.override.expired",
            target_type="commercial_override", target_id=override.id, org_id=override.org_id,
            meta={"override_type": override.override_type, "target_key": override.target_key,
                  "expired_at": now.isoformat()},
        )
    return len(lapsed)


# ── Ledger 1 subscription checkout + provider correlation (ZST-COM-PLAN-001 §13/§18) ─────

def resolve_subscription_price_id(plan, billing_interval: str = MONTHLY) -> str:
    """The approved Stripe Price ID for a plan, or refuse.

    THE price-authority boundary. The browser sends a plan identifier; this is the only place
    that turns it into something chargeable, and it can only ever return a value an operator
    configured against an approved price book (settings.subscription_price_map).

    Fails closed when unmapped. ZST-COM-PLAN-001 Section 24 states numeric prices "are
    intentionally not supplied" by the specification, and Section 23 makes ZST-COM-PRICE-001 a
    prerequisite "before public numeric pricing and live charging" — so an unmapped plan is not
    a bug to paper over with a default, it is an unpriced plan that must not be sold.

    The CADENCE fails closed the same way and for the same reason. The approved price book
    publishes both a monthly and an annual price per self-service plan, but each cadence is a
    DISTINCT Stripe Price; if the annual one is not configured, an annual request is refused
    rather than quietly billed at the monthly price. Substituting one cadence for the other
    would charge an amount the customer did not agree to.
    """
    interval = (billing_interval or MONTHLY).strip().lower()
    if interval not in BILLING_INTERVALS:
        raise ValueError(
            f"Unknown billing interval '{billing_interval}' "
            f"(allowed: {', '.join(BILLING_INTERVALS)})"
        )
    price_id = settings.subscription_price_map().get((plan.slug, interval))
    if not price_id:
        raise ValueError(
            f"No approved Stripe price is configured for the '{plan.slug}' plan on {interval} "
            "billing. A plan cannot be sold on a cadence Finance has not published: add "
            f"'{plan.slug}:{interval}=price_...' to STRIPE_SUBSCRIPTION_PRICES once the Stripe "
            "Price exists."
        )
    return price_id


def resolve_plan_for_provider_price(db, price_ids) -> tuple[Plan | None, str | None]:
    """The Plan a verified Stripe Price belongs to, or (None, refusal_reason).

    The INVERSE of resolve_subscription_price_id, and deliberately built on the same operator
    configuration. That symmetry is the point: a subscription can only ever be resolved onto a
    plan whose price an operator approved, so a Stripe subscription billing some other price
    (created by hand in the dashboard, or against a retired Price) resolves to nothing and is
    recorded as evidence instead of silently moving a tenant onto a plan.

    Why the PRICE and not `metadata.plan_slug`: metadata is a free-text label. Our own server
    set it at checkout creation, so it is not attacker-controlled, but it is still a label
    ABOUT the sale rather than the sale itself. The price is what the customer is actually
    charged, so resolving from it means the plan on record and the money collected can never
    disagree — including when a subscription is later edited in the Stripe dashboard.

    Refuses rather than guesses in every ambiguous case; the reason is returned for the audit
    trail. Section 18 forbids granting entitlement that the commercial record does not support,
    and a guessed plan is exactly that.
    """
    ids = [p for p in (price_ids or []) if isinstance(p, str) and p.strip()]
    if not ids:
        return None, "no_price_on_subscription"

    # {(plan_slug, interval): price_id}. The INTERVAL is deliberately collapsed here: monthly
    # and annual are two prices for the SAME plan, so either one resolves to that plan. The
    # cadence is a property of the Stripe subscription, not of which entitlements apply.
    approved = settings.subscription_price_map()
    slugs = sorted({slug for (slug, _interval), price_id in approved.items() if price_id in ids})
    if not slugs:
        return None, f"unapproved_price:{','.join(sorted(ids))}"
    if len(slugs) > 1:
        # One subscription billing two approved plans' prices. Which plan the tenant is on is
        # then a commercial question, not an inference this code may make.
        return None, f"ambiguous_price_mapping:{','.join(slugs)}"

    plan = get_plan_by_slug(db, slugs[0])
    if plan is None:
        # Configuration names a plan slug this deployment's catalog does not contain.
        return None, f"no_plan_for_slug:{slugs[0]}"
    if not plan.is_active:
        return None, f"plan_not_active:{slugs[0]}"
    return plan, None


def record_subscription_checkout_started(db, sub: Subscription, *, checkout_session_ref: str,
                                          billing_interval: str | None = None,
                                          actor=None) -> Subscription:
    """Bind a Checkout Session to the subscription so the eventual webhook can find it.

    Correlation ONLY. The Section 12 state is deliberately untouched: starting a checkout is
    not a purchase, and Section 18 is explicit that "No feature may be unlocked because a card
    authorization succeeded if the subscription/order activation did not complete."

    `billing_interval` is recorded for the same correlation reason. It was previously resolved
    to a Price ID and then thrown away, so nothing downstream could say which cadence a tenant
    was on — Section 16 requires the console to show it. Recording it changes no state and
    grants no entitlement; the amount still comes only from the Stripe Price.
    """
    sub.checkout_session_ref = checkout_session_ref
    if billing_interval:
        sub.billing_interval = billing_interval
    create_audit_log(
        db, actor=actor, action="subscription.checkout_started",
        target_type="subscription", target_id=sub.id, org_id=sub.org_id,
        meta={"checkout_session_ref": checkout_session_ref, "plan_id": str(sub.plan_id),
              "billing_interval": billing_interval},
    )
    return sub


class PlanChangeError(ValueError):
    """A plan change was refused. Carries a machine-readable `code` so the router can map it to
    a status without string-matching a message."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def request_plan_change(db, sub: Subscription, *, plan: Plan, billing_interval: str,
                        actor=None) -> Subscription:
    """Schedule a paid subscription's move to `plan` on `billing_interval`.

    THE APPROVED RULES, each enforced here rather than described:

      * NO PRORATION. Nothing is charged or credited now. This function moves no money at all;
        it records an intention and a date.
      * EFFECTIVE AT `current_period_end`. The date is copied from the subscription's own period
        boundary. It is never computed, never "now + a month", and never defaulted — a
        subscription with no known period end cannot be scheduled, because there is no honest
        date to promise (see the refusal below).
      * ENTITLEMENTS UNCHANGED. `plan_id` and `billing_interval` are deliberately NOT touched.
        The tenant keeps the plan they are paying for until the effective date; only
        `pending_*` is written.

    The Section 12 move is `active -> plan_change_scheduled`, applied through the state machine
    like every other transition. An immediate `active -> active` plan swap is not representable
    in the graph, which is precisely why this path exists.
    """
    interval = (billing_interval or "").strip().lower()
    if interval not in BILLING_INTERVALS:
        raise PlanChangeError(
            "invalid_interval",
            f"Unknown billing interval '{billing_interval}' "
            f"(allowed: {', '.join(BILLING_INTERVALS)})")

    if not plan.is_active:
        raise PlanChangeError("invalid_plan", f"The '{plan.slug}' plan is not available.")

    # The target must be PRICED on the requested cadence. Resolved now so an unpurchasable
    # combination is refused at request time rather than discovered at the effective date —
    # and re-resolved again when applied, so a price withdrawn in between also fails closed.
    try:
        resolve_subscription_price_id(plan, interval)
    except ValueError as e:
        raise PlanChangeError("unpriced", str(e)) from e

    current = normalize_subscription_state(sub.status)
    if current == "plan_change_scheduled":
        # Idempotency and honesty: re-requesting the SAME change is a no-op rather than an
        # error (a double-clicked button must not fail), but silently overwriting a DIFFERENT
        # pending change would discard a scheduled commitment without a trace.
        if sub.pending_plan_id == plan.id and sub.pending_billing_interval == interval:
            return sub
        raise PlanChangeError(
            "change_already_scheduled",
            "This subscription already has a different plan change scheduled. Cancel it "
            "before scheduling another.")

    if current != "active":
        # Only a live paid subscription can schedule a change. A trial converts through
        # checkout (that is the CONVERSION_PENDING path, not this one), and a past_due,
        # suspended, canceled or closed subscription has no period boundary to change at.
        raise PlanChangeError(
            "not_active",
            f"A plan change can only be scheduled for an active subscription; this one is "
            f"'{sub.status}'.")

    if sub.plan_id == plan.id and (sub.billing_interval or MONTHLY) == interval:
        raise PlanChangeError(
            "no_change", "This organization is already on that plan and billing interval.")

    if sub.current_period_end is None:
        # The approved effective date IS current_period_end. With no period boundary on record
        # there is no date to schedule for, and inventing one would invent the commercial term.
        raise PlanChangeError(
            "no_period_end",
            "This subscription has no known billing period end yet, so a change cannot be "
            "scheduled. It is set from Stripe's own subscription record; please retry once the "
            "current period is confirmed.")

    error = subscription_transition_error(sub.status, "plan_change_scheduled")
    if error:
        raise PlanChangeError("illegal_transition", error)

    from_plan_id, from_interval = str(sub.plan_id), sub.billing_interval
    sub.pending_plan_id = plan.id
    sub.pending_billing_interval = interval
    sub.plan_change_effective_at = sub.current_period_end
    sub.status = "plan_change_scheduled"

    create_audit_log(
        db, actor=actor, action="subscription.plan_change_scheduled",
        target_type="subscription", target_id=sub.id, org_id=sub.org_id,
        meta={"from": from_plan_id, "from_interval": from_interval,
              "to": str(plan.id), "to_slug": plan.slug, "to_interval": interval,
              "effective_at": sub.plan_change_effective_at.isoformat(),
              "proration": "none", "state_from": "active",
              "state_to": "plan_change_scheduled"},
    )
    return sub


def cancel_plan_change(db, sub: Subscription, *, actor=None) -> Subscription:
    """Abandon a scheduled change and return to ACTIVE on the UNCHANGED plan.

    This is Section 12's second `PLAN_CHANGE_SCHEDULED -> ACTIVE` outcome — the document's
    "ACTIVE(old version)" row. Nothing about the current plan moved while the change was
    pending, so there is nothing to roll back: clearing `pending_*` and returning to active
    leaves the tenant exactly where they have been billed all along.
    """
    if normalize_subscription_state(sub.status) != "plan_change_scheduled":
        raise PlanChangeError(
            "no_change_scheduled", "This subscription has no scheduled plan change to cancel.")

    was = {"plan": str(sub.pending_plan_id), "interval": sub.pending_billing_interval,
           "effective_at": sub.plan_change_effective_at.isoformat()
           if sub.plan_change_effective_at else None}
    sub.pending_plan_id = None
    sub.pending_billing_interval = None
    sub.plan_change_effective_at = None
    sub.status = "active"

    create_audit_log(
        db, actor=actor, action="subscription.plan_change_cancelled",
        target_type="subscription", target_id=sub.id, org_id=sub.org_id,
        meta={"cancelled": was, "kept_plan": str(sub.plan_id),
              "kept_interval": sub.billing_interval,
              "state_from": "plan_change_scheduled", "state_to": "active"},
    )
    return sub


def due_plan_changes(db, *, now: datetime | None = None) -> list[Subscription]:
    """Subscriptions whose scheduled change has reached its effective date, LOCKED for this
    worker only.

    `FOR UPDATE SKIP LOCKED` is the concurrency protection, and it is load-bearing rather than
    defensive. The maintenance runner is an HTTP endpoint an external scheduler calls, and it
    has no mutual exclusion of its own — a Cloud Scheduler double-fire, a retry, or an operator
    running it by hand while the schedule fires all execute the sweep concurrently. Without a
    lock both workers would read the same due row, both would pass the `pending_plan_id is not
    None` check on their own session copy, and both would write a
    `subscription.plan_change_applied` audit row. The final state would be correct and Stripe
    would be protected by its idempotency key, but the requirement that the transition be
    recorded EXACTLY ONCE would not hold.

    SKIP LOCKED rather than plain FOR UPDATE so the second worker moves on to other due rows
    instead of blocking behind the first — the standard queue-claim pattern, and the reason
    this needs no Redis: the lock lives in the transaction that is already open.

    IMPORTANT: this batch lock alone is NOT sufficient, because `create_audit_log` commits its
    own session — so the first subscription the caller applies releases the lock on every other
    row in the batch. `claim_due_plan_change` below re-claims each row individually, and that is
    what actually makes the sweep safe. This function is the cheap candidate query.
    """
    moment = now or datetime.now(timezone.utc)
    return list(db.scalars(
        select(Subscription).where(
            Subscription.pending_plan_id.isnot(None),
            Subscription.plan_change_effective_at.isnot(None),
            Subscription.plan_change_effective_at <= moment,
        ).with_for_update(skip_locked=True)
    ).all())


def claim_due_plan_change(db, subscription_id, *, now: datetime | None = None):
    """Re-read ONE subscription under a fresh row lock, or None if it is no longer claimable.

    The atomic claim, and the reason the sweep cannot double-apply. Each iteration re-asserts
    the full due-ness predicate against COMMITTED state rather than trusting the batch read:

      * `FOR UPDATE SKIP LOCKED` — if another worker holds this row, we get None and move on
        rather than blocking or duplicating.
      * `pending_plan_id IS NOT NULL` — if another worker already applied it (and cleared the
        pending fields), the predicate no longer matches and we get None. This is the case the
        batch lock could not cover, because `create_audit_log`'s internal commit drops the
        batch's locks as soon as the first subscription is applied.
      * the effective date is re-checked, so a change is never applied early even if the
        candidate list was built moments before the boundary.

    Returns the locked, freshly-read Subscription — the caller must apply and commit promptly,
    since the lock is held for the life of the transaction.
    """
    moment = now or datetime.now(timezone.utc)
    return db.scalar(
        select(Subscription).where(
            Subscription.id == subscription_id,
            Subscription.pending_plan_id.isnot(None),
            Subscription.plan_change_effective_at.isnot(None),
            Subscription.plan_change_effective_at <= moment,
        ).with_for_update(skip_locked=True)
    )


def apply_plan_change(db, sub: Subscription, *, actor=None) -> tuple[bool, str | None]:
    """Put a due scheduled change into effect: `plan_change_scheduled -> active` on the new plan.

    Returns (applied, error). This is the document's "ACTIVE(new version)" outcome.

    IDEMPOTENT by construction: `pending_*` is cleared as part of applying, so a second run
    finds nothing to do and reports (False, None). Re-running the sweeper — or running two of
    them — cannot transition twice or bill twice, because this function moves no money either.

    The Stripe side is deliberately separate. Nothing here calls Stripe: the provider swap is
    performed by the caller and CONFIRMED by webhook, so a Stripe outage cannot leave our
    record claiming a change that Stripe never made.
    """
    if sub.pending_plan_id is None:
        return False, None                      # already applied, or never scheduled

    plan = db.get(Plan, sub.pending_plan_id)
    if plan is None or not plan.is_active:
        create_audit_log(
            db, actor=actor, action="subscription.plan_change_rejected",
            target_type="subscription", target_id=sub.id, org_id=sub.org_id,
            meta={"reason": "pending plan is missing or inactive",
                  "pending_plan_id": str(sub.pending_plan_id)},
        )
        return False, "The scheduled plan is no longer available."

    interval = sub.pending_billing_interval or MONTHLY
    # Re-resolve rather than trusting a stored price: a cadence an operator has withdrawn since
    # the change was scheduled must fail closed here, not be charged from a stale copy.
    try:
        resolve_subscription_price_id(plan, interval)
    except ValueError as e:
        create_audit_log(
            db, actor=actor, action="subscription.plan_change_rejected",
            target_type="subscription", target_id=sub.id, org_id=sub.org_id,
            meta={"reason": "no approved price at the effective date",
                  "detail": str(e), "to_slug": plan.slug, "to_interval": interval},
        )
        return False, str(e)

    error = subscription_transition_error(sub.status, "active")
    if error:
        create_audit_log(
            db, actor=actor, action="subscription.plan_change_rejected",
            target_type="subscription", target_id=sub.id, org_id=sub.org_id,
            meta={"reason": "illegal transition at the effective date", "detail": error,
                  "from_state": sub.status},
        )
        return False, error

    from_plan_id, from_interval = str(sub.plan_id), sub.billing_interval
    effective = sub.plan_change_effective_at
    sub.plan_id = plan.id
    sub.billing_interval = interval
    sub.status = "active"
    sub.pending_plan_id = None
    sub.pending_billing_interval = None
    sub.plan_change_effective_at = None

    create_audit_log(
        db, actor=actor, action="subscription.plan_change_applied",
        target_type="subscription", target_id=sub.id, org_id=sub.org_id,
        meta={"from": from_plan_id, "from_interval": from_interval,
              "to": str(plan.id), "to_slug": plan.slug, "to_interval": interval,
              "effective_at": effective.isoformat() if effective else None,
              "proration": "none",
              "state_from": "plan_change_scheduled", "state_to": "active"},
    )
    return True, None


def subscription_by_provider_ref(db, *, checkout_session_ref: str | None = None,
                                  stripe_subscription_id: str | None = None) -> Subscription | None:
    """Find a subscription by a Stripe reference — never by amount, email or customer name.

    Same rule the Ledger 2 webhook path already enforces: fuzzy matching on a financial
    mutation is how one tenant's money lands on another's ledger. Both columns are unique, so a
    match is exact or absent.
    """
    if stripe_subscription_id:
        found = db.scalar(select(Subscription).where(
            Subscription.stripe_subscription_id == stripe_subscription_id))
        if found is not None:
            return found
    if checkout_session_ref:
        return db.scalar(select(Subscription).where(
            Subscription.checkout_session_ref == checkout_session_ref))
    return None


def apply_subscription_provider_event(db, sub: Subscription, *, new_state: str,
                                       stripe_customer_id: str | None = None,
                                       stripe_subscription_id: str | None = None,
                                       plan: Plan | None = None,
                                       current_period_end: datetime | None = None,
                                       reason: str | None = None,
                                       actor=None) -> tuple[bool, str | None]:
    """Apply a provider-driven lifecycle change THROUGH the Section 12 state machine.

    Returns (applied, error). This is the only path by which a Stripe event may move a
    subscription, and it exists because Section 12 requires that "UI, API, support and billing
    never infer lifecycle from a payment event": the provider reports a fact, and the state
    machine decides whether that fact corresponds to a legal transition.

    An illegal transition is REFUSED and audited, never forced — matching
    crud.commercial.apply_payment_state. Provider references are still recorded when supplied,
    because correlating the row is correct regardless of whether the transition was legal.

    `plan`, when supplied, is the plan the verified Stripe price resolved to
    (resolve_plan_for_provider_price). It is applied ONLY as part of a successful transition
    INTO `active`, and never on a refused one. Binding the two together is what keeps the
    commercial record honest in both directions: the tenant cannot end up entitled to a plan
    the provider is not billing, and cannot be moved onto a purchased plan while the
    activation that pays for it has not completed (Section 18).
    """
    if stripe_customer_id and not sub.stripe_customer_id:
        sub.stripe_customer_id = stripe_customer_id
    if stripe_subscription_id and not sub.stripe_subscription_id:
        sub.stripe_subscription_id = stripe_subscription_id

    # Stripe's period boundary, taken from the verified event. Unlike the ids above this is
    # updated on EVERY event rather than only when unset: the boundary moves at each renewal,
    # and a stale value here would schedule a plan change for a date that has already passed.
    #
    # It is also the reason this is synced at all. The column existed but only the admin PATCH
    # ever wrote it, so every Stripe-paid subscription carried NULL — and the approved effective
    # date for a plan change IS current_period_end, which left that rule with no anchor.
    if current_period_end is not None:
        sub.current_period_end = current_period_end

    previous = sub.status
    error = subscription_transition_error(previous, new_state)
    if error:
        create_audit_log(
            db, actor=actor, action="subscription.transition_rejected",
            target_type="subscription", target_id=sub.id, org_id=sub.org_id,
            meta={"from": previous, "to": new_state, "error": error, "reason": reason},
        )
        return False, error

    canonical = normalize_subscription_state(new_state)
    if canonical == normalize_subscription_state(previous):
        # The STATE is a replay — but the PRICE may not be. This branch used to return here
        # unconditionally, which silently dropped a real change: an already-ACTIVE subscription
        # whose Stripe price moved (a dashboard edit, or a change made through Stripe by
        # support) kept its old plan_id forever, with applied=False, error=None and no audit
        # row. That directly contradicted this module's own claim that resolving the plan from
        # the price means "the plan on record and the money collected can never disagree —
        # including when a subscription is later edited in the Stripe dashboard". They could,
        # and nothing said so.
        #
        # Reconciling here is NOT the undefined "scheduled plan change": nothing is being
        # scheduled and no policy is being chosen. Stripe is ALREADY billing the new price, so
        # the money has already moved; refusing to follow it would leave the tenant paying for
        # one plan and entitled to another. Recording the fact is the safe direction, and
        # Section 12's "commercial state must be explicit" requires the record to say so.
        #
        # Audited under its OWN action, never as a transition: no Section 12 edge was taken.
        if canonical == "active" and plan is not None and sub.plan_id != plan.id:
            reconciled_from = str(sub.plan_id)
            sub.plan_id = plan.id
            create_audit_log(
                db, actor=actor, action="subscription.plan_reconciled",
                target_type="subscription", target_id=sub.id, org_id=sub.org_id,
                meta={"state": canonical, "plan_from": reconciled_from,
                      "plan_to": str(plan.id), "plan_slug": plan.slug, "reason": reason,
                      "stripe_subscription_id": sub.stripe_subscription_id},
            )
            return True, None
        return False, None                      # idempotent replay: nothing to record

    sub.status = canonical
    if canonical == "canceled" and sub.cancelled_at is None:
        sub.cancelled_at = datetime.now(timezone.utc)

    # The purchased plan takes effect at ACTIVATION and nowhere else. `conversion_pending` is
    # deliberately excluded: an abandoned conversion must leave the tenant on the plan they
    # already had, not on the one they started paying for and did not finish.
    plan_changed_from = None
    if plan is not None and canonical == "active" and sub.plan_id != plan.id:
        plan_changed_from = str(sub.plan_id)
        sub.plan_id = plan.id

    create_audit_log(
        db, actor=actor, action="subscription.transition",
        target_type="subscription", target_id=sub.id, org_id=sub.org_id,
        meta={"from": previous, "to": canonical, "reason": reason,
              "stripe_subscription_id": sub.stripe_subscription_id,
              **({"plan_from": plan_changed_from, "plan_to": str(plan.id),
                  "plan_slug": plan.slug} if plan_changed_from else {})},
    )
    return True, None
