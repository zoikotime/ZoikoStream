"""One-shot data migration for the retirement of the "moderator" role.

There is NO schema migration to run. Both role columns (`users.role`, `event_assignments.role`)
are plain VARCHAR with no Postgres enum, no CHECK constraint and no lookup table — verified
against the live database — so removing the value from the application's role tuples cannot
break a row, and this script never alters a type or a constraint. It only rewrites DATA, and
only the rows it is explicitly asked to.

  DRY RUN IS THE DEFAULT. Nothing is written unless --apply is passed.

Two independent migrations, deliberately separate because they carry different risk:

  --users        users.role = 'moderator'  ->  'host'
                 Near-zero authority change, verified against the code rather than assumed:
                 a platform role of 'host' unlocks exactly one extra thing over 'moderator',
                 _can_edit in routers/events.py, which is itself double-gated on
                 (created_by == me OR an existing host EventAssignment) — so it grants nothing
                 on an event the person is not already attached to. It also carries the
                 commercial 'media_access' action (security._CUSTOMER_COMMERCIAL), whose two
                 endpoints are org-scoped through _get_event_or_404. It does NOT grant
                 can_host or can_moderate on any event: those come from event_assignments via
                 services/moderation.resolve_ctx, which this script does not touch here.
                 What it fixes: 'moderator' is no longer a rung in security._ROLE_RANK, so
                 such a row now resolves to rank -1 and roleHome()/RoleRoute treat it as an
                 unknown role — a real account holding a valid host EventAssignment would be
                 bounced off the console it is entitled to open.

  --assignments  event_assignments.role = 'moderator'  ->  DELETED
                 THIS ONE REVOKES ACCESS and is the reason the default is a dry run. Such a
                 row is currently the only thing granting its holder audience management
                 (chat, Q&A, polls, participant mute/remove/ban) on that event. Until it is
                 removed, services/moderation.resolve_ctx keeps honouring it as can_moderate —
                 never as can_host — via models/event.LEGACY_ASSIGNMENT_ROLES, precisely so
                 this cleanup can happen deliberately rather than as a side effect of a deploy.
                 Run it only once each affected event is over, or once the people involved
                 have been re-assigned as hosts.

                 It does NOT rewrite these rows to role='host'. That mapping is semantically
                 WRONG: 'host' carries broadcast control (go live, end, emergency stop,
                 recording) which a moderator assignment has never had, and a large share of
                 these rows are held by users whose platform role is 'speaker'. Promoting them
                 would hand the power to end a live broadcast to people the design explicitly
                 denies it. Re-assign anyone who genuinely should be a host through the normal
                 org-admin path (PATCH /events/{id}/hosts) so it is an audited, deliberate act.

Rollback:
  --users        Reversible: rerun with --revert-users to move 'host' rows back to 'moderator',
                 but ONLY those whose ids are listed in a backup file written by --apply. A
                 blind reverse UPDATE would demote every genuine host in the database, so the
                 revert refuses to run without that file.
  --assignments  Reversible ONLY from the backup file --apply writes before deleting. There is
                 no way to reconstruct which rows were moderator assignments after the fact.

Usage:
  python retire_moderator_role.py                              # report only, both migrations
  python retire_moderator_role.py --users --apply
  python retire_moderator_role.py --assignments                 # see exactly who loses what
  python retire_moderator_role.py --assignments --apply
  python retire_moderator_role.py --revert-users --backup <file>
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import text

from app.db import SessionLocal

LEGACY = "moderator"
REPLACEMENT_USER_ROLE = "host"
BACKUP_DIR = Path(__file__).parent / "_moderator_retirement_backups"


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _write_backup(kind: str, rows: list[dict]) -> Path:
    BACKUP_DIR.mkdir(exist_ok=True)
    path = BACKUP_DIR / f"{kind}-{_stamp()}.json"
    path.write_text(json.dumps(rows, indent=2, default=str), encoding="utf-8")
    return path


# ── users.role ────────────────────────────────────────────────────────────────────────────

def report_users(db) -> list[dict]:
    rows = db.execute(text("""
        select id, email, full_name, is_active, org_id
        from users
        where role = :legacy and deleted_at is null
        order by email
    """), {"legacy": LEGACY}).mappings().all()
    rows = [dict(r) for r in rows]
    print(f"\nusers.role = '{LEGACY}'  (excluding soft-deleted): {len(rows)}")
    if not rows:
        print("  nothing to migrate — this deployment is already clean.")
        return rows
    fixtures = [r for r in rows if str(r["email"]).endswith("@example.com")]
    print(f"  of which look like test fixtures (@example.com): {len(fixtures)}")
    print(f"  real accounts: {len(rows) - len(fixtures)}")
    for r in rows:
        if not str(r["email"]).endswith("@example.com"):
            print(f"    - {r['email']}  active={r['is_active']}")
    print(f"  -> would become role='{REPLACEMENT_USER_ROLE}'")
    return rows


def apply_users(db, rows: list[dict]) -> None:
    if not rows:
        return
    backup = _write_backup("users", rows)
    print(f"  backup written: {backup}")
    result = db.execute(text("""
        update users set role = :new
        where role = :legacy and deleted_at is null
    """), {"new": REPLACEMENT_USER_ROLE, "legacy": LEGACY})
    db.commit()
    print(f"  UPDATED {result.rowcount} users.role -> '{REPLACEMENT_USER_ROLE}'")


def revert_users(db, backup_path: Path) -> None:
    """Restore ONLY the ids recorded in a backup. Never a blind reverse UPDATE: 'host' is a
    live role held by many genuine accounts, and rewriting all of them to a retired value
    would be far more damaging than the thing being undone."""
    rows = json.loads(backup_path.read_text(encoding="utf-8"))
    ids = [r["id"] for r in rows]
    if not ids:
        print("backup lists no rows; nothing to revert.")
        return
    result = db.execute(
        text("update users set role = :legacy where id = any(:ids) and role = :new"),
        {"legacy": LEGACY, "new": REPLACEMENT_USER_ROLE, "ids": ids},
    )
    db.commit()
    print(f"REVERTED {result.rowcount} of {len(ids)} users.role -> '{LEGACY}'")


# ── event_assignments.role ────────────────────────────────────────────────────────────────

def report_assignments(db) -> list[dict]:
    rows = db.execute(text("""
        select ea.id, ea.event_id, ea.user_id, u.email, u.role as platform_role,
               e.title as event_title, e.status as event_status,
               exists (
                   select 1 from event_assignments h
                   where h.event_id = ea.event_id and h.user_id = ea.user_id and h.role = 'host'
               ) as also_host
        from event_assignments ea
        join users u on u.id = ea.user_id
        join events e on e.id = ea.event_id
        where ea.role = :legacy
        order by e.status, u.email
    """), {"legacy": LEGACY}).mappings().all()
    rows = [dict(r) for r in rows]
    print(f"\nevent_assignments.role = '{LEGACY}': {len(rows)}")
    if not rows:
        print("  nothing to clean up. The legacy read in "
              "services/moderation.resolve_ctx can be deleted for this deployment.")
        return rows

    losing = [r for r in rows if not r["also_host"]]
    print(f"  already ALSO assigned host on the same event (lose nothing): {len(rows) - len(losing)}")
    print(f"  would LOSE audience management on that event: {len(losing)}")

    by_status: dict[str, int] = {}
    by_role: dict[str, int] = {}
    for r in losing:
        by_status[r["event_status"]] = by_status.get(r["event_status"], 0) + 1
        by_role[r["platform_role"]] = by_role.get(r["platform_role"], 0) + 1
    print("    by event status: " + ", ".join(f"{k}={v}" for k, v in sorted(by_status.items())))
    print("    by platform role: " + ", ".join(f"{k}={v}" for k, v in sorted(by_role.items())))

    live_ish = [r for r in losing
                if r["event_status"] in ("live", "armed", "ready_to_arm", "rehearsal", "degraded")]
    if live_ish:
        print(f"\n  !! {len(live_ish)} of these are on events that are LIVE or being armed.")
        print("     Deleting their access now would take moderation away mid-event.")
        for r in live_ish[:20]:
            print(f"       - {r['email']} on {r['event_title']!r} ({r['event_status']})")
    return rows


def apply_assignments(db, rows: list[dict]) -> None:
    if not rows:
        return
    backup = _write_backup("assignments", rows)
    print(f"  backup written: {backup}  (the ONLY way to undo this — keep it)")
    result = db.execute(text("delete from event_assignments where role = :legacy"),
                        {"legacy": LEGACY})
    db.commit()
    print(f"  DELETED {result.rowcount} event_assignments rows with role='{LEGACY}'")


def revert_assignments(db, backup_path: Path) -> None:
    rows = json.loads(backup_path.read_text(encoding="utf-8"))
    restored = 0
    for r in rows:
        # ON CONFLICT DO NOTHING: uq_event_user_role means a re-inserted row that has since
        # been re-created by hand must not abort the whole restore.
        result = db.execute(text("""
            insert into event_assignments (id, event_id, user_id, role)
            values (:id, :event_id, :user_id, :legacy)
            on conflict do nothing
        """), {"id": r["id"], "event_id": r["event_id"], "user_id": r["user_id"], "legacy": LEGACY})
        restored += result.rowcount
    db.commit()
    print(f"RESTORED {restored} of {len(rows)} event_assignments rows with role='{LEGACY}'")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--users", action="store_true", help="migrate users.role -> host")
    ap.add_argument("--assignments", action="store_true", help="delete moderator event assignments")
    ap.add_argument("--apply", action="store_true",
                    help="actually write. Without this the script only reports.")
    ap.add_argument("--revert-users", action="store_true")
    ap.add_argument("--revert-assignments", action="store_true")
    ap.add_argument("--backup", type=Path, help="backup file for a --revert-* run")
    args = ap.parse_args()

    db = SessionLocal()
    try:
        if args.revert_users or args.revert_assignments:
            if not args.backup or not args.backup.exists():
                print("A --revert-* run requires --backup <file> written by an earlier --apply.",
                      file=sys.stderr)
                return 2
            if args.revert_users:
                revert_users(db, args.backup)
            else:
                revert_assignments(db, args.backup)
            return 0

        # Default: report on both, so a bare invocation is always safe and always informative.
        do_users = args.users or not args.assignments
        do_assignments = args.assignments or not args.users

        user_rows = report_users(db) if do_users else []
        assignment_rows = report_assignments(db) if do_assignments else []

        if not args.apply:
            print("\nDRY RUN — nothing was written. Re-run with --apply to commit.")
            return 0

        print("\n--apply given; writing.")
        if do_users:
            apply_users(db, user_rows)
        if do_assignments:
            apply_assignments(db, assignment_rows)
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
