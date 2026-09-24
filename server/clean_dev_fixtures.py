"""Remove test-fixture rows from a LOCAL DEVELOPMENT database. Dry-run unless --apply.

    python clean_dev_fixtures.py              # report only, changes nothing
    python clean_dev_fixtures.py --apply      # one transaction, commit or nothing

── WHY THIS EXISTS ─────────────────────────────────────────────────────────────────────
The suites write real rows and, before conftest's guard became .env-aware, a bare `pytest`
ran against whatever DATABASE_URL named — which on a developer box is the dev database. That
left 1,124 fixture accounts in zoiko_stream_dev alongside 4 genuine ones, and made Identity &
Access unusable: 623 "Org Admin" rows called Test Owner, Assignment Person and Auth Person.

A rebuild would be simpler and is the usual advice, but it destroys the genuine accounts and
their events too. This removes only what the fixtures created and leaves everything else
exactly as it is.

── HOW A ROW IS CLASSIFIED ─────────────────────────────────────────────────────────────
By PROVENANCE, never by "the address doesn't look real". A real user may be on gmail,
outlook, a company or a university domain, and none of that is evidence of anything.

  users          email @example.com. RFC 2606 reserves example.com for documentation and
                 testing; it can never receive mail, so no genuine account can hold one.
                 Every such row's full_name also matches a literal written by a file in
                 server/test_*.py (Test Owner, Assignment Person, Auth Person, Org Admin...).

  organizations  an org with NO non-example.com member. Deliberately not a name-prefix match
                 on "Asg Co"/"LVE Co"/"org-test-": a developer could name a real local org
                 anything, and membership is the fact that matters.

Anything that does not meet those tests is left alone and reported. Ambiguity is never
resolved by deleting.

── HOW IT DELETES ──────────────────────────────────────────────────────────────────────
The delete order is DERIVED from the live foreign-key graph in information_schema, not from
a hand-maintained list of tables. A hand-written order is wrong the moment someone adds a
table, and this database has 76 FKs into users/organizations with ON DELETE NO ACTION and a
second level behind them (42k analytics_snapshots, event_orders, commercial accounts...).
The sweep repeats until a pass deletes nothing, so children always go before parents whatever
the graph looks like today.

Everything runs inside ONE transaction. A failure rolls the whole thing back.
"""
from __future__ import annotations

import argparse
import os
import sys
import urllib.parse as up
from collections import defaultdict

from sqlalchemy import create_engine, text

# ── the only database this may touch ────────────────────────────────────────────────────
# Named explicitly rather than read from DATABASE_URL: this script deletes data, and pointing
# it at a database by editing an environment variable is exactly the accident to prevent.
ALLOWED_DB = "zoiko_stream_dev"
ALLOWED_HOSTS = ("localhost", "127.0.0.1")

SYNTHETIC_EMAIL_DOMAIN = "@example.com"


def guard(url: str) -> None:
    p = up.urlsplit(url)
    name = p.path.lstrip("/")
    problems = []
    if name != ALLOWED_DB:
        problems.append(f"database is {name!r}, not {ALLOWED_DB!r}")
    if p.hostname not in ALLOWED_HOSTS:
        problems.append(f"host is {p.hostname!r}, not local")
    if "supabase" in (p.hostname or "").lower():
        problems.append("host looks like Supabase (production)")
    if problems:
        sys.stderr.write("\nREFUSED — this script only ever runs against the local dev database:\n")
        for prob in problems:
            sys.stderr.write(f"  * {prob}\n")
        raise SystemExit(4)


def fk_graph(conn) -> dict[str, list[tuple[str, str, str]]]:
    """{parent_table: [(child_table, child_column, parent_column), ...]} for every FK."""
    rows = conn.execute(text("""
        select tc.table_name   as child,
               kcu.column_name as child_col,
               ccu.table_name  as parent,
               rc.delete_rule  as rule
        from information_schema.table_constraints tc
        join information_schema.key_column_usage kcu
          on kcu.constraint_name = tc.constraint_name and kcu.table_schema = tc.table_schema
        join information_schema.constraint_column_usage ccu
          on ccu.constraint_name = tc.constraint_name and ccu.table_schema = tc.table_schema
        join information_schema.referential_constraints rc
          on rc.constraint_name = tc.constraint_name
        where tc.constraint_type = 'FOREIGN KEY' and tc.table_schema = 'public'
    """)).mappings().all()
    out = defaultdict(list)
    for r in rows:
        out[r["parent"]].append((r["child"], r["child_col"], r["rule"]))
    return out


def classify(conn) -> dict:
    users = conn.execute(text(f"""
        select id, full_name, email, role, is_active, created_at
        from users where email like '%{SYNTHETIC_EMAIL_DOMAIN}'
    """)).mappings().all()
    real_users = conn.execute(text(f"""
        select u.id, u.full_name, u.email, u.role, u.is_active, u.created_at, o.name as org
        from users u left join organizations o on o.id = u.org_id
        where u.email not like '%{SYNTHETIC_EMAIL_DOMAIN}'
        order by u.created_at
    """)).mappings().all()
    # An organization is synthetic only when NOTHING genuine belongs to it.
    orgs = conn.execute(text(f"""
        select o.id, o.name from organizations o
        where not exists (
            select 1 from users u
            where u.org_id = o.id and u.email not like '%{SYNTHETIC_EMAIL_DOMAIN}'
        )
    """)).mappings().all()
    return {"synthetic_users": users, "real_users": real_users, "synthetic_orgs": orgs}


def _topo_order(tables: set[str], graph) -> list[str]:
    """Tables ordered so every table comes BEFORE the tables it references.

    `graph` is keyed by parent, so a parent's entry names its children; a child must be
    deleted first. Kahn's algorithm over that, with any residual cycle appended in a stable
    order rather than raising — a self-referencing or mutually-referencing pair is deleted
    inside the same statement batch and Postgres resolves it, so refusing here would be worse
    than proceeding.
    """
    depends: dict[str, set[str]] = {t: set() for t in tables}
    for parent, edges in graph.items():
        if parent not in tables:
            continue
        for child, _col, rule in edges:
            if child in tables and child != parent and rule != "SET NULL":
                depends[parent].add(child)     # parent waits for child

    order, done = [], set()
    while True:
        ready = sorted(t for t in tables if t not in done and depends[t] <= done)
        if not ready:
            break
        order.extend(ready)
        done.update(ready)
    order.extend(sorted(tables - done))         # any cycle, stable
    return order


def collect(conn, targets: dict[str, list], graph) -> dict[str, set]:
    """Every row reachable from `targets`, gathered WITHOUT deleting anything.

    Collection and deletion are separate passes for a reason the first version of this script
    got wrong: deleting breadth-first removes `events` while its own `contributor_sessions`
    still point at it, and Postgres rejects the statement. Nothing can be ordered correctly
    until the whole set is known, so the whole set is gathered first and then deleted deepest
    table first (see _topo_order).
    """
    found: dict[str, set] = {t: set(map(str, ids)) for t, ids in targets.items() if ids}
    frontier = {t: set(ids) for t, ids in found.items()}

    for _ in range(16):
        nxt: dict[str, set] = defaultdict(set)
        for parent, ids in frontier.items():
            for child, child_col, rule in graph.get(parent, []):
                if rule == "SET NULL":
                    continue                    # nulled in place; the row survives
                kids = conn.execute(
                    text(f'select id from "{child}" where "{child_col}"::text = any(:ids)'),
                    {"ids": sorted(ids)},
                ).scalars().all()
                fresh = {str(k) for k in kids} - found.get(child, set())
                if fresh:
                    found.setdefault(child, set()).update(fresh)
                    nxt[child].update(fresh)
        if not nxt:
            break
        frontier = nxt
    return found


def null_out(conn, targets: dict[str, list], graph, apply: bool) -> dict[str, int]:
    """Clear the nullable references instead of deleting the rows that hold them.

    organizations.owner_user_id is ON DELETE SET NULL, and the schema saying so is the schema
    saying "losing the owner does not destroy the organization". Deleting it would take out a
    genuine org whose owner happened to be a fixture account — none exists here today, which
    is luck, not a guarantee.
    """
    out: dict[str, int] = {}
    for parent, ids in targets.items():
        if not ids:
            continue
        for child, child_col, rule in graph.get(parent, []):
            if rule != "SET NULL":
                continue
            where = f'"{child_col}"::text = any(:ids)'
            params = {"ids": [str(i) for i in ids]}
            n = conn.execute(text(f'select count(*) from "{child}" where {where}'), params).scalar()
            if not n:
                continue
            if apply:
                conn.execute(text(f'update "{child}" set "{child_col}" = null where {where}'), params)
            out[f"{child}.{child_col} (nulled)"] = n
    return out


def resolve_url(explicit: str | None) -> str:
    """The connection string, from --url or DATABASE_URL. Never a built-in default.

    It used to default to a full local URL with a developer's own password baked in. That is
    a credential in version control — harmless against localhost, but developer passwords get
    reused, and a default also makes it possible to run this script by accident with no
    thought about what it is pointed at. Requiring the value means the target is always an
    explicit decision.

    Nothing here echoes the URL: an error that helpfully prints the connection string is how
    a password reaches a terminal, a CI log or a screenshot. The caller sees the variable
    name, never its value, and guard() below reports only the database name and hostname.
    """
    url = explicit or os.environ.get("DATABASE_URL", "").strip()
    if not url:
        sys.stderr.write(
            "\nREFUSED — no database was named.\n"
            "  Set DATABASE_URL to the LOCAL development database, or pass --url.\n"
            f"  Only {ALLOWED_DB!r} on localhost is accepted; everything else is refused.\n\n"
        )
        raise SystemExit(4)
    return url


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="commit; without it nothing changes")
    ap.add_argument("--url", default=None,
                    help="connection string; defaults to $DATABASE_URL. Required either way.")
    args = ap.parse_args()

    url = resolve_url(args.url)
    guard(url)
    engine = create_engine(url)

    with engine.begin() as conn:            # ONE transaction; any error rolls everything back
        found = classify(conn)
        graph = fk_graph(conn)

        print(f"database        {ALLOWED_DB} (local)")
        print(f"mode            {'APPLY — will commit' if args.apply else 'DRY RUN — no changes'}")
        print()
        print(f"synthetic users {len(found['synthetic_users'])}   ({SYNTHETIC_EMAIL_DOMAIN}, RFC 2606 reserved)")
        print(f"synthetic orgs  {len(found['synthetic_orgs'])}   (no genuine member)")
        print()
        print("PRESERVED — genuine accounts:")
        for u in found["real_users"]:
            print(f"  {u['id']}  {u['full_name']!r:<22} {u['email']:<38} "
                  f"{u['role']:<12} org={u['org']!r} joined={u['created_at']:%Y-%m-%d}")
        print()

        targets = {"users": [u["id"] for u in found["synthetic_users"]],
                   "organizations": [o["id"] for o in found["synthetic_orgs"]]}

        counts = null_out(conn, targets, graph, apply=args.apply)
        reachable = collect(conn, targets, graph)
        order = _topo_order(set(reachable), graph)

        for table in order:
            ids = sorted(reachable[table])
            if not ids:
                continue
            if args.apply:
                conn.execute(text(f'delete from "{table}" where id::text = any(:ids)'), {"ids": ids})
            counts[table] = len(ids)

        print(f"{'REMOVED' if args.apply else 'WOULD REMOVE'}:")
        for key in sorted(counts, key=lambda k: -counts[k]):
            if counts[key]:
                print(f"  {counts[key]:>7}  {key}")
        print(f"\n  total rows: {sum(counts.values())}")
        print(f"  delete order: {' -> '.join(order)}")

        if not args.apply:
            print("\nDry run — rolling back. Re-run with --apply to commit.")
            conn.rollback()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
