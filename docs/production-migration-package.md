# Production migration package — ZoikoStream billing (Ledger 1)

Prepared for execution **after** Product/Finance approve P1, P2, P3 and P6. Nothing in this
document has been executed. It is the operational companion to
[`billing-maintenance-runbook.md`](billing-maintenance-runbook.md), which already covers the
scheduler in depth; this package covers the whole rollout and does not repeat what that file
says correctly.

Every command is labelled:

| Label | Meaning |
|---|---|
| `[READ-ONLY]` | Safe to run now, at any time, as often as you like |
| `[PRODUCTION WRITE — DO NOT EXECUTE YET]` | Changes the production database |
| `[STRIPE WRITE — DO NOT EXECUTE YET]` | Changes Stripe account state |
| `[DEPLOYMENT — DO NOT EXECUTE YET]` | Changes what production runs |

**Baseline facts** used throughout, all from the read-only audit of 2026-09-08:
production runs `origin/main` = `233d3bd`; the billing implementation is one commit ahead on
`origin/nagesh-changes` = `3da24f5`; 2 157 organizations, 1 subscription row, 2 055 eligible
and unprovisioned; plans are still `starter`/`pro`/`enterprise` with `price_monthly` NULL;
`subscriptions` is missing 7 columns and `commercial_overrides` is absent; the Stripe webhook
endpoint is registered, `livemode=false`, and subscribes to **none** of the
`customer.subscription.*` events.

---

## Before anything: the connection you run migrations through

The production `DATABASE_URL` points at
`aws-1-eu-west-2.pooler.supabase.com:6543`. Port **6543 is Supabase's transaction-mode
pooler**. That matters twice in this rollout:

* `pg_dump` is not reliable through a transaction pooler — take the backup over the **direct /
  session connection** (port `5432`), or use Supabase's own backup tooling.
* `create_tables.py` issues DDL and `migrate_provision_subscriptions.py` holds a transaction
  per batch. Both are safer over the session connection.

`[READ-ONLY]` Confirm which endpoint you actually hold before you start:

```bash
python - <<'PY'
import os, urllib.parse as u
p = u.urlparse(os.environ["DATABASE_URL"])
print("host:", p.hostname, "port:", p.port,
      "->", "TRANSACTION POOLER — use the direct/session URL for pg_dump and migrations"
            if p.port == 6543 else "session/direct connection")
PY
```

Do not paste the URL into a shell where it will be logged. Export it from your secret store.

---

## D1 — Production database backup

### Procedure

`[PRODUCTION WRITE — DO NOT EXECUTE YET]` *(a backup writes only to your backup target, but it
is the gate for everything after it, so it carries the label)*

Take **both** kinds of restore point. They fail differently, so one is not a substitute for the
other.

```bash
# 1. Supabase managed restore point (PITR). In the Supabase dashboard:
#    Project -> Database -> Backups -> confirm PITR is ENABLED and note the current timestamp.
#    Record that timestamp here: ____________________  (UTC)

# 2. An independent logical dump you control, over the DIRECT connection (port 5432).
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
pg_dump "$DIRECT_DATABASE_URL" \
  --format=custom \
  --no-owner --no-privileges \
  --file="zoikostream-preflight-${STAMP}.dump"

# 3. A schema-only dump as well — small, fast to diff, and it is what you will compare
#    against after D2 to prove exactly what changed.
pg_dump "$DIRECT_DATABASE_URL" --schema-only --no-owner --no-privileges \
  --file="zoikostream-preflight-schema-${STAMP}.sql"
```

### Verifying the backup is usable

A dump you have not restored is a hope, not a backup. Restore it to a scratch database and
compare counts against production.

```bash
# [PRODUCTION WRITE — DO NOT EXECUTE YET]  (writes only to the scratch database)
createdb zoikostream_restore_check
pg_restore --dbname=zoikostream_restore_check --no-owner --no-privileges \
  "zoikostream-preflight-${STAMP}.dump"
```

`[READ-ONLY]` Then compare the tables the rollout touches. The counts must match the
production baseline exactly:

```sql
SELECT 'organizations' AS t, count(*) FROM organizations
UNION ALL SELECT 'users',         count(*) FROM users
UNION ALL SELECT 'subscriptions', count(*) FROM subscriptions
UNION ALL SELECT 'plans',         count(*) FROM plans
UNION ALL SELECT 'audit_logs',    count(*) FROM audit_logs
UNION ALL SELECT 'provider_events', count(*) FROM provider_events
ORDER BY 1;
```

Expected against today's baseline: `organizations=2157`, `subscriptions=1`, `plans=3`,
`provider_events=62`. Drop the scratch database once the comparison passes.

### Rollback considerations

* The dump is the only thing that can undo **D4**, which is the one irreversible-by-design step
  (it creates ~2 055 commercial rows).
* PITR is the faster path for a same-day mistake; the logical dump is the path that survives a
  project-level problem.
* Restoring rolls back **everything**, including any real customer activity since the snapshot.
  That is why D4 is gated behind a reviewed dry run rather than treated as "just restore if
  wrong" — see the Rollback Plan at the end for the surgical alternative.

---

## D2 — Schema migration

### Read-only pre-check

`[READ-ONLY]`

```bash
DATABASE_URL="$PROD_DATABASE_URL" python verify_billing_readiness.py \
  --only V2 --base-url https://get.zoikostream.com
```

Expected **before** the migration: `V2 subscriptions columns` FAILS listing the 7 missing
columns, and `V2 billing tables` FAILS on `commercial_overrides`.

### The migration

`[PRODUCTION WRITE — DO NOT EXECUTE YET]`

```bash
cd server
DATABASE_URL="$DIRECT_DATABASE_URL" python create_tables.py
```

It takes no arguments. It is additive and idempotent — `CREATE TABLE IF NOT EXISTS` via
`create_all()`, then phased `ADD COLUMN IF NOT EXISTS` / `CREATE INDEX IF NOT EXISTS` — so it is
safe to re-run, and safe to run **while `main` is still serving traffic**: the old code simply
ignores columns it does not declare.

### Expected after the migration

| Object | Type |
|---|---|
| `subscriptions.stripe_customer_id` | `VARCHAR(120)` |
| `subscriptions.stripe_subscription_id` | `VARCHAR(120)`, unique index |
| `subscriptions.checkout_session_ref` | `VARCHAR(120)`, unique index |
| `subscriptions.billing_interval` | `VARCHAR(16)` |
| `subscriptions.pending_plan_id` | `UUID` → `plans.id` |
| `subscriptions.pending_billing_interval` | `VARCHAR(16)` |
| `subscriptions.plan_change_effective_at` | `TIMESTAMPTZ`, partial index where `pending_plan_id IS NOT NULL` |
| `commercial_overrides` | table (created by `create_all()`) |

The two unique indexes matter as much as the columns: they are what makes "one Stripe
subscription per row" a database guarantee rather than an application convention.

### Post-check

`[READ-ONLY]` Re-run the pre-check — `V2` must be all PASS. Then diff the schema against the
D1 snapshot so you can see precisely what changed and nothing else did:

```bash
pg_dump "$DIRECT_DATABASE_URL" --schema-only --no-owner --no-privileges \
  --file="zoikostream-post-d2-schema.sql"
diff -u "zoikostream-preflight-schema-${STAMP}.sql" "zoikostream-post-d2-schema.sql" | head -80
```

### Rollback considerations

**Do not roll D2 back.** It is purely additive; the new columns are nullable and unread by the
currently-deployed code, so leaving them costs nothing. Dropping them would destroy data
written by any step after this one. If D2 must be undone, that is a full D1 restore, not a
`DROP COLUMN`.

---

## D3 — Plan migration

### Dry runs first

`[READ-ONLY]` — both scripts support `--dry-run`, which rolls back rather than commits.

```bash
cd server
DATABASE_URL="$DIRECT_DATABASE_URL" python migrate_plan_names.py --dry-run
DATABASE_URL="$DIRECT_DATABASE_URL" python migrate_approved_prices.py --dry-run
```

Expected dry-run output: `starter -> developer`, `pro -> business`, enterprise untouched; then
`developer: price_monthly None -> 49 USD`, `business: price_monthly None -> 249 USD`, and
Enterprise set to `custom_pricing = TRUE`.

`migrate_plan_names.py` **refuses outright** if both an old and a new slug exist, rather than
merging two catalogs — so a half-applied state is caught, not compounded.

### The migration

`[PRODUCTION WRITE — DO NOT EXECUTE YET]`

```bash
cd server
DATABASE_URL="$DIRECT_DATABASE_URL" python migrate_plan_names.py
DATABASE_URL="$DIRECT_DATABASE_URL" python migrate_approved_prices.py
```

Order matters: prices are keyed by the **new** slugs, so the rename must land first.

`migrate_plan_names.py` writes only `slug` and `name`. Every row `id`, every entitlement value
(`max_users`, `max_storage_gb`, `max_streaming_hours`, `features`) and `currency` are preserved
— so no subscription is re-planned and no tenant's limits move.

### Annual handling

**Annual amounts are deliberately never written to the database.** `plans` has a single
`price_monthly` column; putting $490 in it would misprice the catalog, and adding an annual
column would encode pricing this table is not the authority for. The annual cadence exists
**only** in `STRIPE_SUBSCRIPTION_PRICES` as `developer:annual=price_...`. Until Finance creates
those Prices (F2), `purchasable_intervals()` returns `["monthly"]` and the Billing page
correctly renders no cadence toggle. Nothing in D3 needs to change when annual arrives — only
D5 does.

### Post-check

`[READ-ONLY]`

```bash
DATABASE_URL="$PROD_DATABASE_URL" python verify_billing_readiness.py \
  --only V3 --base-url https://get.zoikostream.com
```

All of `V3 legacy slugs retired`, `V3 canonical slugs present`, `V3 developer price published`,
`V3 business price published`, `V3 enterprise is contract-priced` must PASS.
`V3 developer/business purchasable` will still FAIL until D5 supplies the price map — that is
expected at this point, not a problem.

### Rollback considerations

Fully reversible with no data loss: re-run `migrate_plan_names.py` with `RENAMES` inverted, and
set `price_monthly = NULL` to undo the price publish. Row ids never change, so no subscription
is affected either way.

---

## D4 — Subscription provisioning

> **This is the one step that creates commercial state at scale, and it is the step most likely
> to be wrong. Read the blocker below before scheduling it.**

### Blocker: exclusions are not implementable today

`eligible_org_ids()` in `migrate_provision_subscriptions.py` has **exactly three predicates**
and no exclusion mechanism of any kind:

* no subscription row of any kind
* at least one user with `email_verified IS TRUE`
* **every** organization status accepted — suspended organizations are deliberately included

That yields the audited **2 055**. If P3 excludes the 70 super-admin/staff organizations
(`Zoiko Staff …`, `cap-…`, `rc-…`, `lc-…`), **this script cannot express that**, and
`organizations.is_test` is `false` for all 2 157 so it cannot be used as the filter either.

Excluding anything therefore requires a **developer change first** — an `--exclude-org-ids` /
`--exclude-file` option, or a narrowed predicate — which is gated work, not a DevOps step.
Decide P3 before scheduling D4, and if the answer is "exclude staff", route it back to
development.

### Dry run

`[READ-ONLY]` — the dry run rolls back every batch and writes nothing.

```bash
cd server
DATABASE_URL="$DIRECT_DATABASE_URL" python migrate_provision_subscriptions.py --dry-run
```

Expected output to reconcile against the audit:

| Figure | Expected |
|---|---|
| eligible (script's own count) | **2 055** |
| of which active organizations | 2 039 |
| of which suspended | 16 |
| excluded: no verified user | 102 unverified + 41 user-less |
| excluded: already provisioned | 1 |

It also prints a **MANUAL REVIEW** block naming the single legacy subscription whose
`trial_ends_at` is NULL (`8ebbe53c…`, org `762e1eaf…`). That row is reported and never touched
— it already has a subscription, so it is excluded from provisioning by definition. Its
handling is P4.

If the dry-run count is not 2 055, **stop**: the population moved since the audit, and Product
approved a number, not a query.

### Real run

`[PRODUCTION WRITE — DO NOT EXECUTE YET]`

```bash
cd server
DATABASE_URL="$DIRECT_DATABASE_URL" python migrate_provision_subscriptions.py --batch-size 200
```

Optional `--limit N` provisions only the first N in a stable `created_at, id` order, so a
cautious first pass (`--limit 25`) is resumable — the remainder stays in the same order for the
next run.

Each organization gets: plan `developer`, status `trialing`, 14-day trial from a single
timestamp per batch, **no card, no charge, no Stripe object**, and a `subscription.provisioned`
audit row. `provision_initial_subscription` is idempotent and returns `None` rather than
overwriting if a row appeared in the meantime, so a re-run cannot double-provision.

**Prerequisite that is easy to miss:** C2, the trial-expiry job, must be merged and deployed
first. `trial_expired` is a terminal §12 state that nothing currently writes, and `trialing`
**is** entitled — so provisioning 2 055 trials without C2 creates 2 055 plans that never end.

### Post-provisioning verification

`[READ-ONLY]`

```bash
DATABASE_URL="$PROD_DATABASE_URL" python verify_billing_readiness.py \
  --only V4 --base-url https://get.zoikostream.com
```

```sql
-- Coverage matches the approved figure, and nothing was provisioned twice.
SELECT (SELECT count(*) FROM organizations)                        AS organizations,
       (SELECT count(DISTINCT org_id) FROM subscriptions)          AS with_subscription,
       (SELECT count(*) FROM subscriptions)                        AS subscription_rows,
       (SELECT count(*) FROM (SELECT org_id FROM subscriptions
                              GROUP BY org_id HAVING count(*) > 1) d) AS duplicated;

-- Every new row is a 14-day Developer trial with a real end date.
SELECT p.slug, s.status, count(*),
       min(s.trial_ends_at) AS earliest_end, max(s.trial_ends_at) AS latest_end,
       count(*) FILTER (WHERE s.trial_ends_at IS NULL) AS null_end
FROM subscriptions s JOIN plans p ON p.id = s.plan_id
GROUP BY 1, 2 ORDER BY 3 DESC;

-- Nothing acquired a Stripe object: provisioning must never touch Stripe.
SELECT count(*) AS should_be_zero FROM subscriptions
WHERE stripe_subscription_id IS NOT NULL OR stripe_customer_id IS NOT NULL
   OR checkout_session_ref IS NOT NULL;

-- The audit trail matches the row count exactly.
SELECT count(*) FROM audit_logs WHERE action = 'subscription.provisioned';
```

### Rollback / recovery

The provisioned rows are precisely identifiable, so a surgical undo exists and is far safer
than a full restore:

`[PRODUCTION WRITE — DO NOT EXECUTE YET]`

```sql
-- 1. IDENTIFY first, in a read-only transaction. Never delete from an unreviewed set.
BEGIN; SET TRANSACTION READ ONLY;
SELECT s.id, s.org_id, s.created_at
FROM subscriptions s
JOIN audit_logs a ON a.target_id = s.id::text
                 AND a.action = 'subscription.provisioned'
WHERE s.stripe_subscription_id IS NULL      -- never touch anything that reached Stripe
  AND s.checkout_session_ref IS NULL
  AND a.created_at >= '<run start UTC>';
ROLLBACK;

-- 2. Only after reviewing that set, and only if Product asks for the undo.
--    The audit rows are retained deliberately — they are the evidence the run happened.
```

Do **not** undo by `DELETE FROM subscriptions WHERE created_at > …`: that would also catch a
genuine customer signup that happened during the window.

---

## D5 — Production Stripe configuration (Cloud Run)

No secret values appear here and none should be pasted into a shell history — set them from
Secret Manager or the Cloud Run console.

### The mode matrix — the thing that must not be got wrong

Three values must be in the **same mode**. A split configuration is the failure that returns
`No such price` at checkout, or has the `livemode_mismatch` guard quarantine every inbound
event as authentic-but-wrong-mode.

| Variable | Secret? | Must match mode | If P1 = LIVE | If P1 = TEST |
|---|---|---|---|---|
| `STRIPE_SECRET_KEY` | **yes** | ✅ | `sk_live_…` | `sk_test_…` |
| `STRIPE_WEBHOOK_SECRET` | **yes** | ✅ | signing secret of the **live** endpoint | signing secret of the **test** endpoint |
| `STRIPE_SUBSCRIPTION_PRICES` | no | ✅ | live Price IDs from F1/F2 | test Price IDs |
| `MAINTENANCE_SCHEDULER_SERVICE_ACCOUNT` | no | — | service-account email | same |
| `MAINTENANCE_OIDC_AUDIENCE` | no | — | service URL | same |

The two `MAINTENANCE_*` values are mode-independent and are **not secrets** — one is a
service-account email, the other a public URL — so plain env vars are correct and no Secret
Manager entry is needed.

### Price map format

```
STRIPE_SUBSCRIPTION_PRICES=developer:monthly=<price_id>,business:monthly=<price_id>
```

Add annual only once F2 exists:

```
STRIPE_SUBSCRIPTION_PRICES=developer:monthly=<id>,developer:annual=<id>,business:monthly=<id>,business:annual=<id>
```

`enterprise` is **deliberately absent** — an unpriced plan is what makes `self_service` false
and renders "Talk to an expert" instead of a checkout button. Do not add it.

### Applying

`[DEPLOYMENT — DO NOT EXECUTE YET]`

```bash
# Secrets via Secret Manager (preferred) — the values never enter your shell history.
gcloud run services update zoikostream --region=<region> \
  --update-secrets \
STRIPE_SECRET_KEY=zoiko-stripe-secret-key:latest,\
STRIPE_WEBHOOK_SECRET=zoiko-stripe-webhook-secret:latest

# Non-secret configuration.
gcloud run services update zoikostream --region=<region> \
  --update-env-vars \
STRIPE_SUBSCRIPTION_PRICES='developer:monthly=<id>,business:monthly=<id>',\
MAINTENANCE_SCHEDULER_SERVICE_ACCOUNT=<sa>@<project>.iam.gserviceaccount.com,\
MAINTENANCE_OIDC_AUDIENCE=https://<service-url>
```

`MAINTENANCE_OIDC_AUDIENCE` must equal the scheduler's `--oidc-token-audience` **exactly**.

### Verification

`[READ-ONLY]`

```bash
# Confirm the variables are present WITHOUT printing secret values.
gcloud run services describe zoikostream --region=<region> \
  --format='value(spec.template.spec.containers[0].env[].name)'

STRIPE_SECRET_KEY=… STRIPE_WEBHOOK_SECRET=… STRIPE_SUBSCRIPTION_PRICES=… \
  python verify_billing_readiness.py --only V5 --base-url https://get.zoikostream.com
```

V5 reports the key only as a mode and a length, and verifies every configured Price against the
approved book — amount, recurring interval, `livemode` agreement with the key, and `active`.

---

## D6 — Stripe webhook configuration

### Currently configured (audited 2026-09-08)

One endpoint: `https://get.zoikostream.com/api/commercial/webhooks/stripe`, `status=enabled`,
**`livemode=false`**, API version `2026-07-29.dahlia`, **11 events — every one of them
Ledger 2 (event-order payments)**:

```
charge.refunded                            payment_intent.canceled
checkout.session.async_payment_succeeded   payment_intent.payment_failed
checkout.session.completed                 payment_intent.processing
checkout.session.expired                   payment_intent.requires_action
payment_intent.amount_capturable_updated   payment_intent.succeeded
refund.updated
```

**All eleven must remain enabled.** They drive Ledger 2, which is live and unrelated to this
rollout. Removing any of them would break event-order payments.

### Must be added

`[STRIPE WRITE — DO NOT EXECUTE YET]`

| Event | Why it is required |
|---|---|
| `customer.subscription.created` | Drives `conversion_pending -> active`. **Without it a paying customer never activates.** |
| `customer.subscription.updated` | The same transition on the update path, plus period-end and plan reconciliation |
| `customer.subscription.deleted` | Cancellation reaching our ledger at all |
| `invoice.payment_failed` | The only route into `past_due`; without it there is no dunning signal |

`checkout.session.completed` is already subscribed and is what advances
`trialing -> conversion_pending` — which is exactly why the gap is dangerous rather than
obvious: checkout appears to work, the customer pays, and then stops in a state that grants
**no entitlement**.

Add them in the Stripe Dashboard on the endpoint matching P1's mode (create the live endpoint
first if P1 = LIVE, and take its signing secret for D5).

### Verification

`[READ-ONLY]`

```bash
STRIPE_SECRET_KEY=… python verify_billing_readiness.py \
  --only V6 --base-url https://get.zoikostream.com
```

`V6 required subscription events subscribed` must PASS, and `V6 endpoint mode matches the key`
must PASS — that check exists specifically to catch a live key pointed at a test endpoint.

---

## D7 — Cloud Scheduler

The service account, IAM and job creation are already documented in
[`billing-maintenance-runbook.md`](billing-maintenance-runbook.md) §4–§6 and that guidance is
current. The configuration in short:

| Setting | Value |
|---|---|
| Endpoint | `https://<service-url>/api/commercial/maintenance/scheduled-run` |
| Method | `POST` |
| Auth | OIDC (`--oidc-service-account-email`, `--oidc-token-audience`) |
| Service account | `zoiko-billing-scheduler@<project>.iam.gserviceaccount.com` |
| Audience | `https://<service-url>` — must equal `MAINTENANCE_OIDC_AUDIENCE` exactly |
| Schedule | `0 * * * *` hourly, `Etc/UTC` |
| Deadline / retries | `600s`, 3 attempts, 30s–300s backoff |

`[DEPLOYMENT — DO NOT EXECUTE YET]` — the `gcloud scheduler jobs create http …` command is in
the runbook §6. Two amendments since it was written:

* The sweep now runs **eight** jobs, not seven — `reconcile_stripe_subscriptions` was added.
  It stays cheap: it reads only subscriptions that carry a `stripe_subscription_id` or an
  unlinked `checkout_session_ref` (a handful, not 2 055), plus one bounded `list`. The 600s
  deadline remains appropriate.
* The job must be created **after** D8, because the route ships with the new code. Creating it
  earlier produces failing invocations against a 404.

Grant the service account nothing beyond invoking the service — no `roles/editor`, no database
access, no Secret Manager. And note the `roles/run.invoker` binding is needed **only** if the
service is `--no-allow-unauthenticated`; this service also serves the public SPA, so it almost
certainly is not, and the application performs the identity check itself.

### Verification

`[READ-ONLY]`

```bash
DATABASE_URL="$PROD_DATABASE_URL" python verify_billing_readiness.py \
  --only V7 --base-url https://get.zoikostream.com
```

V7 POSTs to the route **without credentials** on purpose. The only correct answers are **403**
(configured, refusing an anonymous caller) or **503** (not configured). A 200 would be a
serious finding — an unauthenticated way to move customers between paid plans.

Then force one authenticated run and read the structured log lines:

```bash
gcloud scheduler jobs run zoikostream-billing-maintenance --location=<region>
gcloud run services logs read zoikostream --region=<region> --limit=50 \
  | grep -i maintenance
```

---

## D8 — Deployment

### 1. Merge

`[DEPLOYMENT — DO NOT EXECUTE YET]`

```bash
# Verify the exact commits first — do not trust a stale local `main`.
git fetch origin
git log --oneline origin/main..origin/nagesh-changes     # expect: 3da24f5 stripe changes (+ C3-C6 work)
git rev-list --left-right --count origin/main...origin/nagesh-changes
```

Open a PR `nagesh-changes -> main`, review, merge. **Do not build from the local `main` ref** —
it was last seen stale at `d325d1e` (2026-08-25).

### 2. Commit verification before building

`[READ-ONLY]`

```bash
git checkout main && git pull origin main
git rev-parse --short HEAD                               # record this: __________
# The build must contain the new billing UI and none of the retired copy.
git grep -c "Talk to an expert"        -- client/src/pages/organization/Billing.jsx   # expect > 0
git grep -c "Contact us to switch"     -- client/src                                  # expect 0
git grep -c "No payment provider connected" -- client/src/pages/organization/Billing.jsx  # expect 0
# And the server-side billing surface is present.
git grep -c "billing/checkout-session\|maintenance/scheduled-run" -- server/app/routers
```

### 3. Build and tag

`[DEPLOYMENT — DO NOT EXECUTE YET]`

```bash
SHA=$(git rev-parse --short HEAD)
REGION=<region>; PROJECT=<project>
IMAGE="${REGION}-docker.pkg.dev/${PROJECT}/zoikostream/app:${SHA}"

gcloud builds submit --tag "$IMAGE" .
```

Tag by **commit SHA**, never `latest`. The Dockerfile builds the SPA inside the image
(`npm run build` in a `node:22-alpine` stage, then `COPY --from=client /client/dist`), so the
deployed bundle is frozen at build time and the SHA is the only reliable identifier of what is
running. Record the currently-deployed image first — it is your rollback target:

```bash
# [READ-ONLY]
gcloud run services describe zoikostream --region=$REGION \
  --format='value(spec.template.spec.containers[0].image)'   # record: __________
```

### 4. Deploy

`[DEPLOYMENT — DO NOT EXECUTE YET]`

```bash
gcloud run deploy zoikostream --region=$REGION --image="$IMAGE"
```

Ordering requirement: **D2 → D3 → D5 → D6 must all be complete before this.** Deploying the new
code against the un-migrated schema produces an immediate `UndefinedColumn` 500 the first time
anyone starts a checkout, and deploying before D6 means the first real purchase strands in
`conversion_pending`.

### 5. Health checks

`[READ-ONLY]`

```bash
curl -fsS https://get.zoikostream.com/health                       # {"status":"ok"}
DATABASE_URL="$PROD_DATABASE_URL" STRIPE_SECRET_KEY=… \
  python verify_billing_readiness.py --base-url https://get.zoikostream.com
```

Watch the revision for errors before declaring success:

```bash
gcloud run services logs read zoikostream --region=$REGION --limit=100 \
  | grep -iE "error|traceback|undefinedcolumn|500"
```

### 6. Rollback

`[DEPLOYMENT — DO NOT EXECUTE YET]` — instant, and safe, because the schema is additive:

```bash
gcloud run services update-traffic zoikostream --region=$REGION \
  --to-revisions=<previous-revision>=100
```

The previous revision runs `main`'s old code, which simply ignores the added columns. Rolling
the **code** back does not require rolling the **schema** back — that asymmetry is the reason
D2 runs first and independently.

---

## V1–V7 — Verification

All checks are implemented in `server/verify_billing_readiness.py`. It writes nothing: every
statement is a SELECT inside `SET TRANSACTION READ ONLY`, every Stripe call is a retrieve or
list, every HTTP call is a GET except the deliberately-uncredentialed scheduler probe. It exits
non-zero if any check FAILs, so it can gate a step.

`[READ-ONLY]`

```bash
cd server
DATABASE_URL="$PROD_DATABASE_URL" \
STRIPE_SECRET_KEY=… STRIPE_WEBHOOK_SECRET=… STRIPE_SUBSCRIPTION_PRICES=… \
python verify_billing_readiness.py --base-url https://get.zoikostream.com

# or one at a time
python verify_billing_readiness.py --only V2 --base-url https://get.zoikostream.com
```

| Check | Proves | Run after |
|---|---|---|
| **V1** | `/health` is 200 and the served JS bundle contains none of the retired copy | D8 |
| **V2** | 9 subscription columns, 9 tables, both unique indexes | D2 |
| **V3** | Legacy slugs gone, canonical present, approved prices published, Enterprise contract-priced, and what `/api/organization/plans` actually serves | D3, D5 |
| **V4** | Coverage, zero eligible remaining, no duplicate rows, every trial has an end date, every subscription resolves a plan | D4 |
| **V5** | Key mode, webhook secret present, and every configured Price checked against the approved book for amount, interval, livemode and active | D5 |
| **V6** | Endpoint registered at the right URL, mode agrees with the key, all required subscription events subscribed, recent delivery evidence, zero open unmatched settlements | D6 |
| **V7** | Scheduler route answers 403 (not 200, not 503), maintenance has run, no overdue plan changes | D7 |

---

## Production smoke test

**Do not execute the payment until every step above is complete and V1–V7 pass.** In LIVE mode
this charges a real card; use a real card you control on a nominated internal organization, and
refund it afterwards through Stripe.

### Setup

`[READ-ONLY]` Choose one nominated organization and capture its baseline:

```sql
BEGIN; SET TRANSACTION READ ONLY;
SELECT s.id, s.org_id, o.name, p.slug AS plan, s.status, s.billing_interval,
       s.trial_ends_at, s.stripe_subscription_id, s.checkout_session_ref
FROM subscriptions s
JOIN organizations o ON o.id = s.org_id
LEFT JOIN plans p ON p.id = s.plan_id
WHERE s.org_id = '<nominated org id>';
ROLLBACK;
```

Expected baseline: `plan=developer`, `status=trialing`, `stripe_subscription_id` NULL.

### The test

`[STRIPE WRITE — DO NOT EXECUTE YET]`

1. Sign in as an `org_admin` of the nominated organization; open **Organization → Billing**.
2. Confirm the page shows **Developer / Business / Enterprise**, with real prices on Developer
   and Business, an **Upgrade** button on Business, and **Talk to an expert** on Enterprise.
   *(If Business shows "Talk to an expert", stop — D5's price map is wrong; nothing has been
   charged.)*
3. Click **Upgrade** on Business. You must be redirected to `checkout.stripe.com`.
4. **Before paying**, verify the session is the right one — the amount must read **$249.00 /
   month** and the product **Business**.
5. Complete the payment.
6. Return to the Billing page.

### Verification of the chain

`[READ-ONLY]`

```bash
DATABASE_URL="$PROD_DATABASE_URL" python verify_billing_readiness.py \
  --only SMOKE --smoke-org-id '<nominated org id>' \
  --base-url https://get.zoikostream.com
```

That reads the audit chain and asserts each link:

| Expected | Meaning |
|---|---|
| `subscription.checkout_started` | The session was recorded against our row |
| `subscription.transition trialing -> conversion_pending` | §12's only route out of a trial, from `checkout.session.completed` |
| `subscription.checkout_completed` | Stripe's ids bound to our row |
| `subscription.transition conversion_pending -> active` | **Driven by `customer.subscription.created` — this is the link that fails if D6 was skipped** |
| final state `status=active`, `plan=business`, `current_period_end` populated | The purchased plan took effect, resolved from the price Stripe actually bills |

Then confirm entitlement moved with it:

```sql
BEGIN; SET TRANSACTION READ ONLY;
SELECT p.slug, p.max_users, p.max_storage_gb, s.status, s.billing_interval, s.current_period_end
FROM subscriptions s JOIN plans p ON p.id = s.plan_id
WHERE s.org_id = '<nominated org id>';
ROLLBACK;
```

### If it stops at `conversion_pending`

That is the D6 signature, and it is the expected symptom if the subscription events were not
added. The customer has paid and holds **no entitlement** (`conversion_pending` is not in
`SUBSCRIPTION_ENTITLED_STATES`). Recovery: add the events (D6), then replay the delivery from
the Stripe Dashboard — the webhook path is idempotent and will apply the transition correctly.
Do **not** hand-edit the subscription row.

### Duplicate-guard check (optional, no charge)

With the subscription now active, click **Upgrade** again. The backend must refuse with a 409
naming the plan-change path — proving the C4 guard holds — and must create no second Stripe
subscription. Confirm in Stripe that the customer still has exactly one.

---

## Rollback plan

| Step | Reversible? | How |
|---|---|---|
| **D1** | n/a | It *is* the rollback |
| **D2** schema | Do not reverse | Additive and unread by old code. Reversal = full D1 restore, never `DROP COLUMN` |
| **D3** plans | Yes, cleanly | Re-run the rename inverted; set `price_monthly = NULL`. Row ids unchanged |
| **D4** provisioning | Surgically | Delete only rows joined to a `subscription.provisioned` audit row in the run window **and** with no Stripe reference. Never by `created_at` alone |
| **D5** env | Yes | `gcloud run services update --update-env-vars` back, or roll the revision |
| **D6** webhook | Yes | Remove the four added events. The 11 Ledger 2 events must stay |
| **D7** scheduler | Yes | `gcloud scheduler jobs pause` — the sweep is idempotent, so pausing loses nothing |
| **D8** deploy | Yes, instantly | `update-traffic --to-revisions=<previous>=100`. Old code ignores the new columns |

**Order of unwind**, if the whole rollout must be abandoned after the smoke test: pause the
scheduler (D7) → roll traffic back (D8) → leave the schema (D2) → decide on provisioning (D4)
with Product, since 2 055 organizations now hold trials they can see. Leave D3 in place; the
renamed catalog is correct regardless.

---

## Remaining Product / Finance decisions

Nothing in this package can start until these land.

| | Decision | Gates |
|---|---|---|
| **P1** | Production Stripe **LIVE or TEST** | F1, F2, D5, D6 — the root decision |
| **P2** | What `trial_expired` stops | C2, and therefore D4 |
| **P3** | Provisioning scope — and note **exclusions need a code change first** | D4 |
| **P6** | Monthly only, or monthly + annual | F2, and V3's expected output |
| P4 | The one legacy `trial` row with NULL `trial_ends_at` | D4 post-check |
| P5 | Enforcement policy for organizations with no subscription row | — |
| **F1** | LIVE monthly Prices, $49 / $249 | D5 |
| **F2** | LIVE annual Prices, $490 / $2,490 | D5, only if P6 includes annual |
| **F3** | Confirm Enterprise stays contract-priced | D3 |
| **F4** | Triage the 18 open unmatched settlements (4 × $249) | V6 will FAIL until this is cleared |
