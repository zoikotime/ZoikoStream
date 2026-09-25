"""READ-ONLY production billing readiness verification (V1-V7).

WRITES NOTHING, ANYWHERE. Every database statement is a SELECT issued inside an explicit
`SET TRANSACTION READ ONLY`, so the server itself rejects a write even if one were introduced
by mistake. Every Stripe call is a retrieve or a list. Every HTTP call is a GET, except the
scheduler probe, which POSTs WITHOUT credentials precisely to prove it is refused.

Safe to run at any point in the rollout: before it starts (to capture a baseline), between
steps (to confirm the step landed), and after the smoke test (to confirm the lifecycle).
Each check prints PASS / FAIL / SKIP and the process exits non-zero if any check FAILED, so it
can gate a deployment step in CI or a shell.

USAGE
    # against production, read-only
    DATABASE_URL="<production url>" \
    STRIPE_SECRET_KEY="<key>" \
    python verify_billing_readiness.py --base-url https://get.zoikostream.com

    # a single check
    python verify_billing_readiness.py --only V2 --base-url https://get.zoikostream.com

NEVER PRINTS SECRETS. Keys and signing secrets are reported only as a mode ("test"/"live") and
a length; Price IDs and Product IDs are public identifiers and are shown in full.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

# ── expectations, from the approved documents ────────────────────────────────────────────
REQUIRED_SUBSCRIPTION_COLUMNS = (
    "billing_interval", "pending_plan_id", "pending_billing_interval",
    "plan_change_effective_at", "trial_ends_at", "current_period_end",
    "stripe_customer_id", "stripe_subscription_id", "checkout_session_ref",
)
REQUIRED_TABLES = (
    "subscriptions", "plans", "provider_events", "audit_logs", "organizations",
    "commercial_overrides", "payments", "invoices", "unmatched_settlements",
)
CANONICAL_PLAN_SLUGS = ("developer", "business", "enterprise")
LEGACY_PLAN_SLUGS = ("starter", "pro")
APPROVED_MONTHLY = {"developer": 4900, "business": 24900}      # minor units, USD
APPROVED_ANNUAL = {"developer": 49000, "business": 249000}
# Ledger 1 cannot complete a purchase without these. checkout.session.completed advances
# trialing -> conversion_pending; ONLY customer.subscription.created/updated can advance
# conversion_pending -> active.
REQUIRED_WEBHOOK_EVENTS = (
    "checkout.session.completed",
    "customer.subscription.created",
    "customer.subscription.updated",
    "customer.subscription.deleted",
    "invoice.payment_failed",
)
# Strings that must NOT survive in the deployed SPA — they are the pre-billing copy.
RETIRED_UI_STRINGS = ("Contact us to switch", "No payment provider connected",
                      "isn't set up yet")

results: list[tuple[str, str, str]] = []       # (check, status, detail)


def record(check: str, status: str, detail: str = "") -> None:
    results.append((check, status, detail))
    mark = {"PASS": "  PASS", "FAIL": "* FAIL", "SKIP": "  skip", "INFO": "  info"}[status]
    print(f"{mark}  {check}" + (f" — {detail}" if detail else ""))


# ── connections ──────────────────────────────────────────────────────────────────────────

def read_only_connection():
    """A connection pinned read-only at the SERVER, not by convention."""
    from sqlalchemy import create_engine, text
    url = os.environ.get("DATABASE_URL")
    if not url:
        return None, None
    engine = create_engine(url, connect_args={"connect_timeout": 30})
    conn = engine.connect()
    conn.execute(text("SET TRANSACTION READ ONLY"))
    return conn, text


def http_get(url: str, timeout: int = 30):
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:                                    # noqa: BLE001
        return None, str(e)


# ── V1 — the deployed build ──────────────────────────────────────────────────────────────

def v1_deployed_build(base_url: str) -> None:
    status, body = http_get(f"{base_url}/health")
    if status is None:
        record("V1 service health", "FAIL",
               f"{base_url} is unreachable ({body}) — every HTTP check below will also fail")
        return
    if status != 200:
        record("V1 service health", "FAIL", f"GET /health -> {status}")
        return
    record("V1 service health", "PASS", "GET /health -> 200")

    # The SPA shell plus its bundle. The retired copy lives in the JS chunk, not index.html,
    # so the asset is fetched and searched rather than the shell alone.
    status, html = http_get(base_url)
    if status != 200:
        record("V1 SPA reachable", "FAIL", f"GET / -> {status}")
        return
    import re
    assets = re.findall(r'/assets/[A-Za-z0-9._-]+\.js', html)
    if not assets:
        record("V1 deployed bundle", "SKIP", "no /assets/*.js referenced by index.html")
        return
    found: list[str] = []
    for asset in dict.fromkeys(assets):
        _, js = http_get(f"{base_url}{asset}")
        for needle in RETIRED_UI_STRINGS:
            if needle in (js or ""):
                found.append(f"{needle!r} in {asset}")
    if found:
        record("V1 deployed bundle is the new billing UI", "FAIL",
               "retired copy still served: " + "; ".join(found))
    else:
        record("V1 deployed bundle is the new billing UI", "PASS",
               f"none of the retired strings in {len(set(assets))} bundle(s)")


# ── V2 — schema ──────────────────────────────────────────────────────────────────────────

def v2_schema(conn, text) -> None:
    cols = {r[0] for r in conn.execute(text(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = 'subscriptions'")).all()}
    missing = [c for c in REQUIRED_SUBSCRIPTION_COLUMNS if c not in cols]
    record("V2 subscriptions columns", "FAIL" if missing else "PASS",
           f"missing: {missing}" if missing else f"all {len(REQUIRED_SUBSCRIPTION_COLUMNS)} present")

    absent = [t for t in REQUIRED_TABLES
              if conn.execute(text("SELECT to_regclass(:t)"),
                              {"t": f"public.{t}"}).scalar() is None]
    record("V2 billing tables", "FAIL" if absent else "PASS",
           f"missing: {absent}" if absent else f"all {len(REQUIRED_TABLES)} present")

    # Indexes the checkout path and the plan-change sweep rely on.
    idx = {r[0] for r in conn.execute(text(
        "SELECT indexname FROM pg_indexes WHERE tablename = 'subscriptions'")).all()}
    for wanted in ("uq_subscriptions_stripe_subscription", "uq_subscriptions_checkout_session"):
        record(f"V2 index {wanted}", "PASS" if wanted in idx else "FAIL",
               "" if wanted in idx else "absent — duplicate provider refs would be possible")


# ── V3 — plans and pricing ───────────────────────────────────────────────────────────────

def v3_plans(conn, text, base_url: str) -> None:
    rows = conn.execute(text(
        "SELECT slug, name, is_active, price_monthly, custom_pricing FROM plans ORDER BY slug"
    )).mappings().all()
    slugs = {r["slug"] for r in rows}

    leftover = sorted(slugs & set(LEGACY_PLAN_SLUGS))
    record("V3 legacy slugs retired", "FAIL" if leftover else "PASS",
           f"still present: {leftover}" if leftover else "no starter/pro remain")

    missing = [s for s in CANONICAL_PLAN_SLUGS if s not in slugs]
    record("V3 canonical slugs present", "FAIL" if missing else "PASS",
           f"missing: {missing}" if missing else "developer/business/enterprise all present")

    by_slug = {r["slug"]: r for r in rows}
    for slug, minor in APPROVED_MONTHLY.items():
        row = by_slug.get(slug)
        if row is None:
            record(f"V3 {slug} price published", "FAIL", "plan absent")
            continue
        expected = minor / 100
        actual = None if row["price_monthly"] is None else float(row["price_monthly"])
        record(f"V3 {slug} price published", "PASS" if actual == expected else "FAIL",
               f"price_monthly={actual} (approved {expected})")

    ent = by_slug.get("enterprise")
    if ent is not None:
        ok = bool(ent["custom_pricing"]) and ent["price_monthly"] is None
        record("V3 enterprise is contract-priced", "PASS" if ok else "FAIL",
               f"custom_pricing={ent['custom_pricing']} price_monthly={ent['price_monthly']}")

    # What the API actually serves — this is what the Billing page renders from.
    status, body = http_get(f"{base_url}/api/organization/plans")
    if status is None:
        record("V3 /api/organization/plans", "FAIL", f"{base_url} unreachable ({body})")
        return
    if status != 200:
        record("V3 /api/organization/plans", "FAIL", f"-> {status}")
        return
    try:
        served = json.loads(body)
    except ValueError:
        record("V3 /api/organization/plans", "FAIL", "response was not JSON")
        return
    served_by_slug = {p.get("slug"): p for p in served}
    for slug in ("developer", "business"):
        p = served_by_slug.get(slug)
        if p is None:
            record(f"V3 {slug} purchasable", "FAIL", "absent from the served catalog")
            continue
        record(f"V3 {slug} purchasable", "PASS" if p.get("self_service") else "FAIL",
               f"self_service={p.get('self_service')} "
               f"billing_intervals={p.get('billing_intervals')} "
               f"pricing_state={p.get('pricing_state')}")
    ent = served_by_slug.get("enterprise")
    if ent is not None:
        record("V3 enterprise not self-service",
               "PASS" if not ent.get("self_service") else "FAIL",
               f"self_service={ent.get('self_service')} pricing_state={ent.get('pricing_state')}")


# ── V4 — provisioning coverage ───────────────────────────────────────────────────────────

def v4_provisioning(conn, text) -> None:
    total = conn.execute(text("SELECT count(*) FROM organizations")).scalar()
    with_sub = conn.execute(text(
        "SELECT count(DISTINCT org_id) FROM subscriptions")).scalar()
    sub_rows = conn.execute(text("SELECT count(*) FROM subscriptions")).scalar()
    eligible_left = conn.execute(text(
        "SELECT count(DISTINCT o.id) FROM organizations o "
        "JOIN users u ON u.org_id = o.id AND u.email_verified = true "
        "WHERE NOT EXISTS (SELECT 1 FROM subscriptions s WHERE s.org_id = o.id)")).scalar()
    record("V4 organization coverage", "INFO",
           f"organizations={total} with_subscription={with_sub} subscription_rows={sub_rows}")
    record("V4 eligible organizations remaining", "PASS" if eligible_left == 0 else "FAIL",
           f"{eligible_left} verified organization(s) still have no subscription")

    dupes = conn.execute(text(
        "SELECT count(*) FROM (SELECT org_id FROM subscriptions "
        "GROUP BY org_id HAVING count(*) > 1) d")).scalar()
    record("V4 one subscription per organization", "PASS" if dupes == 0 else "FAIL",
           f"{dupes} organization(s) hold more than one subscription row")

    print("       subscriptions by status:")
    for r in conn.execute(text(
        "SELECT status, count(*) n FROM subscriptions GROUP BY status ORDER BY n DESC")).all():
        print(f"         {r[0]:<24} {r[1]}")

    legacy = conn.execute(text(
        "SELECT count(*) FROM subscriptions "
        "WHERE status IN ('trial','trialing') AND trial_ends_at IS NULL")).scalar()
    record("V4 trials all have an end date", "PASS" if legacy == 0 else "FAIL",
           f"{legacy} trial(s) with NULL trial_ends_at — these can never expire")

    orphan_plan = conn.execute(text(
        "SELECT count(*) FROM subscriptions s "
        "LEFT JOIN plans p ON p.id = s.plan_id WHERE p.id IS NULL")).scalar()
    record("V4 every subscription resolves a plan", "PASS" if orphan_plan == 0 else "FAIL",
           f"{orphan_plan} row(s) point at a missing plan")


# ── V5 — Stripe configuration ────────────────────────────────────────────────────────────

def v5_stripe() -> None:
    key = os.environ.get("STRIPE_SECRET_KEY", "")
    if not key:
        record("V5 Stripe configuration", "SKIP", "STRIPE_SECRET_KEY not present in this shell")
        return
    mode = "live" if key.startswith("sk_live") else "test" if key.startswith("sk_test") else "?"
    record("V5 Stripe key mode", "INFO", f"mode={mode} length={len(key)}")

    whsec = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
    record("V5 webhook secret present", "PASS" if whsec else "FAIL",
           f"length={len(whsec)}" if whsec else "STRIPE_WEBHOOK_SECRET is empty")

    raw = os.environ.get("STRIPE_SUBSCRIPTION_PRICES", "")
    if not raw:
        record("V5 price map configured", "FAIL", "STRIPE_SUBSCRIPTION_PRICES is empty")
        return
    pairs: dict[tuple[str, str], str] = {}
    for entry in raw.split(","):
        k, sep, price = entry.partition("=")
        if not sep:
            continue
        slug, _, interval = k.strip().partition(":")
        pairs[(slug.strip(), (interval.strip() or "monthly").lower())] = price.strip()
    record("V5 price map parsed", "INFO", f"{len(pairs)} entry/entries: {sorted(pairs)}")

    try:
        import stripe
    except ImportError:
        record("V5 Stripe API reachable", "SKIP", "stripe SDK not installed in this shell")
        return
    stripe.api_key = key
    mismatched: list[str] = []
    for (slug, interval), price_id in sorted(pairs.items()):
        try:
            p = stripe.Price.retrieve(price_id).to_dict()
        except Exception as exc:                              # noqa: BLE001
            mismatched.append(f"{slug}:{interval} unreadable ({type(exc).__name__})")
            continue
        want = (APPROVED_ANNUAL if interval == "annual" else APPROVED_MONTHLY).get(slug)
        want_interval = "year" if interval == "annual" else "month"
        recurring = (p.get("recurring") or {}).get("interval")
        problems = []
        if want is not None and p.get("unit_amount") != want:
            problems.append(f"amount={p.get('unit_amount')} approved={want}")
        if recurring != want_interval:
            problems.append(f"recurring={recurring} expected={want_interval}")
        if p.get("livemode") != (mode == "live"):
            problems.append(f"livemode={p.get('livemode')} but key is {mode}")
        if not p.get("active"):
            problems.append("price is inactive")
        if problems:
            mismatched.append(f"{slug}:{interval} ({price_id}) " + ", ".join(problems))
    record("V5 configured prices match the approved book",
           "FAIL" if mismatched else "PASS",
           "; ".join(mismatched) if mismatched else f"{len(pairs)} price(s) verified")


# ── V6 — webhook endpoint and delivery evidence ──────────────────────────────────────────

def v6_webhook(conn, text, base_url: str) -> None:
    key = os.environ.get("STRIPE_SECRET_KEY", "")
    if key:
        try:
            import stripe
            stripe.api_key = key
            expect_live = key.startswith("sk_live")
            endpoints = [e.to_dict() for e in stripe.WebhookEndpoint.list(limit=100).data]
            target = f"{base_url}/api/commercial/webhooks/stripe"
            match = [e for e in endpoints if (e.get("url") or "").rstrip("/") == target]
            if not match:
                record("V6 webhook endpoint registered", "FAIL",
                       f"no endpoint with url {target}; found "
                       f"{[e.get('url') for e in endpoints]}")
            else:
                ep = match[0]
                enabled = set(ep.get("enabled_events") or [])
                missing = [e for e in REQUIRED_WEBHOOK_EVENTS
                           if e not in enabled and "*" not in enabled]
                record("V6 webhook endpoint registered", "PASS",
                       f"status={ep.get('status')} livemode={ep.get('livemode')} "
                       f"events={len(enabled)}")
                record("V6 endpoint mode matches the key",
                       "PASS" if ep.get("livemode") == expect_live else "FAIL",
                       f"endpoint livemode={ep.get('livemode')}, key is "
                       f"{'live' if expect_live else 'test'}")
                record("V6 required subscription events subscribed",
                       "FAIL" if missing else "PASS",
                       f"MISSING {missing} — a paid customer would strand in "
                       f"conversion_pending" if missing else
                       "all lifecycle events present")
        except ImportError:
            record("V6 webhook endpoint", "SKIP", "stripe SDK not installed")
        except Exception as exc:                              # noqa: BLE001
            record("V6 webhook endpoint", "FAIL", f"could not list: {type(exc).__name__}")
    else:
        record("V6 webhook endpoint", "SKIP", "STRIPE_SECRET_KEY not present in this shell")

    # Delivery evidence in our own ledger.
    total = conn.execute(text("SELECT count(*) FROM provider_events")).scalar()
    rejected = conn.execute(text(
        "SELECT count(*) FROM provider_events WHERE signature_verified = false")).scalar()
    record("V6 provider events recorded", "INFO",
           f"{total} total, {rejected} with an unverified signature")
    print("       most recent inbound events:")
    for r in conn.execute(text(
        "SELECT event_type, signature_verified, processing_status, received_at "
        "FROM provider_events ORDER BY received_at DESC LIMIT 8")).all():
        print(f"         {r[3]}  {r[0]:<34} sig={r[1]} {r[2]}")

    unmatched = conn.execute(text(
        "SELECT count(*) FROM unmatched_settlements WHERE status = 'open'")).scalar()
    record("V6 no open unmatched settlements", "PASS" if unmatched == 0 else "FAIL",
           f"{unmatched} open — provider money attributed to nothing")


# ── V7 — scheduler ───────────────────────────────────────────────────────────────────────

def v7_scheduler(conn, text, base_url: str) -> None:
    """POSTs WITHOUT credentials on purpose: the only correct answers are 403 (configured and
    refusing an anonymous caller) or 503 (not configured). A 200 here would be a serious
    finding — an unauthenticated way to move customers between paid plans."""
    url = f"{base_url}/api/commercial/maintenance/scheduled-run"
    req = urllib.request.Request(url, data=b"", method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            code = r.status
    except urllib.error.HTTPError as e:
        code = e.code
    except Exception as exc:                                  # noqa: BLE001
        record("V7 scheduler route", "FAIL", f"unreachable: {exc}")
        return
    if code == 403:
        record("V7 scheduler route", "PASS",
               "403 without credentials — configured and refusing anonymous callers")
    elif code == 503:
        record("V7 scheduler route", "FAIL",
               "503 — MAINTENANCE_SCHEDULER_SERVICE_ACCOUNT / MAINTENANCE_OIDC_AUDIENCE unset, "
               "so scheduled plan changes will never run")
    else:
        record("V7 scheduler route", "FAIL",
               f"{code} without credentials — expected 403 (configured) or 503 (unconfigured)")

    ran = conn.execute(text(
        "SELECT count(*), max(created_at) FROM audit_logs "
        "WHERE action LIKE 'commercial.maintenance%'")).first()
    record("V7 maintenance has run", "INFO",
           f"{ran[0]} maintenance audit row(s), most recent {ran[1]}")

    due = conn.execute(text(
        "SELECT count(*) FROM subscriptions WHERE pending_plan_id IS NOT NULL "
        "AND plan_change_effective_at IS NOT NULL "
        "AND plan_change_effective_at <= now()")).scalar()
    record("V7 no overdue plan changes", "PASS" if due == 0 else "FAIL",
           f"{due} scheduled change(s) past their effective date and still unapplied")


# ── smoke-test evidence (run AFTER the manual purchase) ──────────────────────────────────

def smoke_evidence(conn, text, org_id: str) -> None:
    """Reads the audit chain a completed Developer -> Business purchase must leave behind."""
    rows = conn.execute(text(
        "SELECT created_at, action, meta FROM audit_logs "
        "WHERE org_id = :o AND (action LIKE 'subscription%' OR action LIKE '%checkout%') "
        "ORDER BY created_at"), {"o": org_id}).mappings().all()
    print(f"       {len(rows)} billing audit row(s) for org {org_id}:")
    for r in rows:
        print(f"         {r['created_at']}  {r['action']}")
        if r["meta"]:
            print(f"            {json.dumps(r['meta'])[:220]}")

    actions = [r["action"] for r in rows]
    transitions = [(r["meta"] or {}).get("from", "") + " -> " + (r["meta"] or {}).get("to", "")
                   for r in rows if r["action"] == "subscription.transition"]
    for needed, why in (
        ("subscription.checkout_started", "checkout was recorded"),
        ("subscription.checkout_completed", "the session completed"),
    ):
        record(f"SMOKE {needed}", "PASS" if needed in actions else "FAIL", why)
    record("SMOKE trialing -> conversion_pending",
           "PASS" if "trialing -> conversion_pending" in transitions else "FAIL",
           "Section 12's only route out of a trial")
    record("SMOKE conversion_pending -> active",
           "PASS" if "conversion_pending -> active" in transitions else "FAIL",
           "driven by customer.subscription.created — fails if V6 events are missing")

    sub = conn.execute(text(
        "SELECT s.status, p.slug, s.billing_interval, s.current_period_end, "
        "       s.stripe_subscription_id "
        "FROM subscriptions s LEFT JOIN plans p ON p.id = s.plan_id "
        "WHERE s.org_id = :o"), {"o": org_id}).mappings().all()
    for r in sub:
        record("SMOKE final subscription state",
               "PASS" if r["status"] == "active" and r["slug"] == "business" else "FAIL",
               f"status={r['status']} plan={r['slug']} interval={r['billing_interval']} "
               f"period_end={r['current_period_end']} stripe={bool(r['stripe_subscription_id'])}")


# ── entry point ──────────────────────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base-url", default=os.environ.get("APP_URL", "https://get.zoikostream.com"),
                    help="deployed service origin, no trailing slash")
    ap.add_argument("--only", action="append", default=None,
                    help="run only these checks (V1..V7, SMOKE); repeatable")
    ap.add_argument("--smoke-org-id", default=None,
                    help="after the manual purchase, read that organization's audit chain")
    args = ap.parse_args()
    base = args.base_url.rstrip("/")
    want = {w.upper() for w in (args.only or [])} or None

    def wanted(name: str) -> bool:
        return want is None or name in want

    print(f"READ-ONLY billing readiness verification against {base}")
    print("This script writes nothing to the database, Stripe, or Cloud Run.\n")

    conn = text = None
    if os.environ.get("DATABASE_URL"):
        conn, text = read_only_connection()
        print(f"database: connected read-only "
              f"({conn.execute(text('SELECT current_database()')).scalar()})\n")
    else:
        print("database: DATABASE_URL not set — database checks will be skipped\n")

    if wanted("V1"):
        v1_deployed_build(base)
    for name, fn in (("V2", v2_schema), ("V4", v4_provisioning)):
        if wanted(name):
            if conn is None:
                record(f"{name} database checks", "SKIP", "DATABASE_URL not set")
            else:
                fn(conn, text)
    if wanted("V3"):
        if conn is None:
            record("V3 database checks", "SKIP", "DATABASE_URL not set")
        else:
            v3_plans(conn, text, base)
    if wanted("V5"):
        v5_stripe()
    if wanted("V6"):
        if conn is None:
            record("V6 database checks", "SKIP", "DATABASE_URL not set")
        else:
            v6_webhook(conn, text, base)
    if wanted("V7"):
        if conn is None:
            record("V7 database checks", "SKIP", "DATABASE_URL not set")
        else:
            v7_scheduler(conn, text, base)
    if wanted("SMOKE") and args.smoke_org_id:
        if conn is None:
            record("SMOKE", "SKIP", "DATABASE_URL not set")
        else:
            smoke_evidence(conn, text, args.smoke_org_id)

    if conn is not None:
        conn.rollback()          # nothing to commit; ends the read-only transaction cleanly
        conn.close()

    failed = [r for r in results if r[1] == "FAIL"]
    print("\n" + "=" * 78)
    print(f"{len(results)} check(s): "
          f"{sum(1 for r in results if r[1] == 'PASS')} passed, "
          f"{len(failed)} failed, "
          f"{sum(1 for r in results if r[1] == 'SKIP')} skipped")
    if failed:
        print("\nFAILED:")
        for check, _, detail in failed:
            print(f"  - {check}: {detail}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
