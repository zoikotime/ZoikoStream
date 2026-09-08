# Billing maintenance runbook — scheduled plan changes

Operational requirements for the subscription plan-change lifecycle. This document adds no
commercial rules; it states what must be running for the already-approved rules to take effect.

## The gap this closes

`apply_due_plan_changes` is registered in `services/maintenance.py::JOBS` and is invoked by
`run_all`. Nothing in this repository calls `run_all` on a schedule. **Until an external
scheduler exists, a scheduled plan change is recorded and never applied** — the customer stays
on their old plan past the effective date they were shown.

There is no cron, Cloud Scheduler config, GitHub Actions schedule, or worker process anywhere in
the repo. The only deployment artifact is `Dockerfile` (one Cloud Run service, one uvicorn
worker). This is not something that "already exists and needs enabling".

## Mechanism: the existing endpoint, called by an external scheduler

```
POST /api/commercial/maintenance/run
```

`routers/commercial.py::run_maintenance`. Reused, not replaced — its own docstring already names
the intended caller: *"The single entry point for an external scheduler — Cloud Scheduler, a
cron container, or an operator. Deliberately NOT an in-process background thread: on Cloud Run
that would run once per instance, die mid-sweep on a scale-down, and be unobservable."*

No new scheduler framework is introduced. The alternative in-process option (adding a ticker
beside the 13 leader-gated ones in `main.py`) is deliberately **not** taken: that endpoint's
docstring rejects in-process execution for these jobs, and adding a second invocation path would
duplicate the architecture.

Returns per-job results, including any job that failed, rather than a 500 that would hide the
jobs that succeeded. A scheduler needs to know "6 of 7 ran" and which one didn't.

### Two routes, two trust models

| Route | Caller | Auth |
|---|---|---|
| `POST /api/commercial/maintenance/run` | a **person** (operator) | `require_commercial("reconcile")` — ZoikoStream JWT, Finance authority |
| `POST /api/commercial/maintenance/scheduled-run` | **Cloud Scheduler** | `require_scheduler_identity` — Google-signed OIDC |

Both call the same `maintenance.run_all`. There is no maintenance behaviour only one of them can
trigger, and no duplicated logic. The operator route is unchanged.

A second route rather than widening the first because the trust models genuinely differ: one
authenticates a person holding our own JWT, the other a Google service identity. Fusing them
would create a single path where weakening either check opens the other.

### Scheduler authentication (OIDC)

**No long-lived token anywhere.** Cloud Scheduler mints a short-lived, Google-signed OIDC token
per invocation. `google-auth` is already available transitively via `google-cloud-storage`, so
this added no dependency.

Four checks, each closing a distinct hole:

1. **Configured?** Both `MAINTENANCE_SCHEDULER_SERVICE_ACCOUNT` and `MAINTENANCE_OIDC_AUDIENCE`
   must be set or the route answers **503** and runs nothing. The default is refusal — an
   unconfigured deployment never exposes an unauthenticated way to move customers between plans.
2. **Google-signed and unexpired?** `verify_oauth2_token` checks signature, issuer and `exp`.
   This is what keeps ordinary users out: ZoikoStream JWTs are signed with `SECRET_KEY` and fail
   Google's signature check outright — tested with a real org-admin and a real super-admin token.
3. **Right audience?** A token minted for a different Cloud Run service cannot be replayed here.
4. **Right identity?** A valid Google token only proves *some* Google principal called. The
   service-account email is compared constant-time, so any other project's token is refused.

Failures return **403** with no reason disclosed (telling a caller which check failed helps them
iterate toward a forgery) and are logged. Replay is bounded by the token's own short lifetime;
there is no nonce store, which would need shared state this deployment does not have — combined
with audience binding and an idempotent target, that is the practical protection.

### Frequency

**Daily is sufficient; hourly is safer.**

The effective date is always a subscription's `current_period_end`, so a change becomes due at a
period boundary — never mid-day-critical. The query is `plan_change_effective_at <= now`, so a
missed run is caught by the next one; nothing is lost, only delayed. Hourly bounds that delay to
an hour instead of a day, and the sweep is cheap (a partial-indexed query that usually returns
nothing).

Do not run more often than every few minutes: each run opens a transaction per due subscription
and calls Stripe once per applied change.

### Overlap safety

Safe to run repeatedly and concurrently. Verified by test, against real Postgres:

- `due_plan_changes` claims candidates with `FOR UPDATE SKIP LOCKED`.
- `claim_due_plan_change` **re-claims each row individually** before applying. This is the
  load-bearing protection: `crud.admin.create_audit_log` commits its own session, so the batch
  lock is released the moment the first subscription is applied. The per-row claim re-asserts
  `pending_plan_id IS NOT NULL` and the effective date against committed state.
- Two concurrent sweeps apply a change **exactly once** and write exactly one
  `subscription.plan_change_applied` row (`test_two_concurrent_sweeps_apply_the_change_exactly_once`).
- The Stripe call carries idempotency key `{sub.id}:{target_plan}:{interval}`, so a retry cannot
  swap or invoice twice.
- Rows another worker held are reported as `contended` in the job result rather than hidden.

### Failure behaviour

Ordering is deliberate: **the local record moves first, then Stripe is asked to swap the price.**
If Stripe fails, the local change stands, `subscription.plan_change_provider_failed` is written,
and the job reports `provider_failed`. The customer is on the plan they asked for from the date
they were promised, and the provider is reconciled on the next run or the next
`customer.subscription.updated` webhook.

The reverse order was rejected: a provider timeout would leave a customer billed for a plan our
record denies them. ZoikoStream never *silently* disagrees with Stripe — a disagreement is always
audited.

## Deployment steps

Run in order. None of this touches customer data beyond the schema and the reconciliation below.

```bash
# 1. Schema (additive, idempotent — safe to re-run)
python create_tables.py

# 2. Plan catalog + approved display prices (both idempotent, both support --dry-run)
python migrate_plan_names.py --dry-run && python migrate_plan_names.py
python migrate_approved_prices.py --dry-run && python migrate_approved_prices.py

# 3. Backfill current_period_end for pre-existing paid subscriptions (idempotent)
python reconcile_subscription_periods.py --dry-run
python reconcile_subscription_periods.py
```

### 4. Create the scheduler service account and grant it nothing else

```bash
PROJECT=<your-project>
SA=zoiko-billing-scheduler

gcloud iam service-accounts create "$SA" \
  --project="$PROJECT" \
  --display-name="ZoikoStream billing maintenance scheduler"
```

The only IAM it needs is permission to invoke the service — and it needs that **only if** the
Cloud Run service is deployed `--no-allow-unauthenticated`. This service also serves the public
SPA, so it is almost certainly `--allow-unauthenticated`, in which case **no IAM binding is
required at all**: the application performs the identity check itself.

```bash
# ONLY if the service is --no-allow-unauthenticated:
gcloud run services add-iam-policy-binding zoikostream \
  --region=<region> \
  --member="serviceAccount:${SA}@${PROJECT}.iam.gserviceaccount.com" \
  --role="roles/run.invoker"
```

Grant it nothing else. It must not have `roles/editor`, database access, or Secret Manager
access — its only capability is calling one HTTP route.

### 5. Configure the service

```bash
gcloud run services update zoikostream --region=<region> \
  --update-env-vars \
MAINTENANCE_SCHEDULER_SERVICE_ACCOUNT=${SA}@${PROJECT}.iam.gserviceaccount.com,\
MAINTENANCE_OIDC_AUDIENCE=https://<service-url>
```

`MAINTENANCE_OIDC_AUDIENCE` must equal the `--oidc-token-audience` used below, exactly.
Neither value is a secret — one is a service-account email, the other a public URL — so plain
env vars are correct here and no Secret Manager entry is needed.

### 6. Create the scheduler job

```bash
gcloud scheduler jobs create http zoikostream-billing-maintenance \
  --project="$PROJECT" \
  --location=<region> \
  --schedule="0 * * * *" \
  --time-zone="Etc/UTC" \
  --uri="https://<service-url>/api/commercial/maintenance/scheduled-run" \
  --http-method=POST \
  --oidc-service-account-email="${SA}@${PROJECT}.iam.gserviceaccount.com" \
  --oidc-token-audience="https://<service-url>" \
  --attempt-deadline=600s \
  --max-retry-attempts=3 \
  --min-backoff=30s \
  --max-backoff=300s
```

| Setting | Value | Why |
|---|---|---|
| Method | `POST` | |
| Frequency | `0 * * * *` (hourly) | Effective dates are period boundaries; the query is `<= now`, so a missed run is caught by the next |
| Timeout | 600s | The sweep runs seven jobs and calls Stripe once per applied change |
| Retries | 3, 30s–300s backoff | **Safe** — every job is idempotent and each subscription is re-claimed under a row lock, so a retry cannot apply a plan twice, double-audit, or double-call Stripe |

### Verifying it works

```bash
# Force one run now
gcloud scheduler jobs run zoikostream-billing-maintenance --location=<region>

# Should show the two structured lines the route emits
gcloud run services logs read zoikostream --region=<region> --limit=50 \
  | grep "scheduled maintenance"
```

Expect `scheduled maintenance invoked by=<sa-email>` followed by
`scheduled maintenance complete by=… duration_ms=… failed_jobs=none plan_changes_applied=…
plan_changes_rejected=… plan_changes_contended=… plan_changes_provider_failed=…`.

A healthy idle run reports `plan_changes_applied=0` — that means "nothing was due", not a
failure.

### Manual trigger (safely)

Prefer `gcloud scheduler jobs run` above: it reuses the scheduler's own identity and needs no
credential handling. Failing that, an operator with Finance authority can call the **operator**
route with their own session token:

```bash
curl -X POST https://<service-url>/api/commercial/maintenance/run \
  -H "Authorization: Bearer <the operator's own ZoikoStream session token>"
```

Do not mint a long-lived token for this.

### Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| **503** "Scheduled maintenance is not configured" | One or both env vars unset | Set both, redeploy |
| **403** on every attempt | Audience mismatch, wrong service account, or clock skew | Confirm `--oidc-token-audience` matches `MAINTENANCE_OIDC_AUDIENCE` byte-for-byte and the email matches |
| **403** and you are using a user token | The scheduler route rejects ZoikoStream JWTs by design | Use the operator route instead |
| Runs, but `plan_changes_applied=0` when a change is expected | Not yet due, or `current_period_end` is NULL | Check `plan_change_effective_at`; run `reconcile_subscription_periods.py` |
| `plan_changes_provider_failed>0` | Stripe rejected the swap | The local change stands by design; check the `subscription.plan_change_provider_failed` audit row |
| `plan_changes_contended>0` | Two runs overlapped | Harmless — the other run applied it |
| `failed_jobs` non-empty | One job raised | A `commercial.maintenance.job_failed` audit row names it; the other jobs still ran |

### Required environment

Already-required values that the lifecycle depends on:

| Variable | Secret? | Why it matters here |
|---|---|---|
| `MAINTENANCE_SCHEDULER_SERVICE_ACCOUNT` | no | The only identity allowed to invoke the sweep. Unset → 503 |
| `MAINTENANCE_OIDC_AUDIENCE` | no | Must equal the scheduler's `--oidc-token-audience`. Unset → 503 |
| `DATABASE_URL` | **yes** | The sweep and the row locks |
| `STRIPE_SECRET_KEY` | **yes** | The price swap at the effective date. Absent → local change applies, provider sync skipped |
| `STRIPE_WEBHOOK_SECRET` | **yes** | Populates `current_period_end`, which **is** the effective date |
| `STRIPE_SUBSCRIPTION_PRICES` | no | Resolved again at the effective date, so a withdrawn price fails closed |
| `APP_URL` | no | Unrelated to the sweep; required in production for checkout return URLs |

The two new values are not secrets (a service-account email and a public URL), so they belong in
env vars rather than Secret Manager. Keep the existing secrets where they already live.

The Stripe webhook endpoint must be registered in the Stripe Dashboard pointing at
`/api/commercial/webhooks/stripe`. Without it `current_period_end` never populates and every
plan-change request is refused with `no_period_end`.

## Annual billing

Fail-closed and unconfigured. `purchasable_intervals()` reports `['monthly']` for both plans, the
Billing page renders no cadence toggle, and an annual request is refused naming the exact config
needed. Nothing is substituted at the monthly price.

To enable, Finance creates the two Stripe Prices ($490/yr Developer, $2,490/yr Business) and
extends the mapping:

```
STRIPE_SUBSCRIPTION_PRICES=developer:monthly=<id>,developer:annual=<id>,business:monthly=<id>,business:annual=<id>
```

Verified that forward resolution, reverse (price → plan) resolution, and the cadence toggle all
work as soon as those entries exist. No code change required.

Do not paste an elided example into `.env` — `price_…` with a literal ellipsis was accepted once
by a prefix-only check and broke every checkout. `config._is_stripe_price_id` now rejects it.

## Still outstanding — not code tasks

These block production regardless of the scheduler, and none of them was performed here.

### Finance must create the annual Stripe Prices

Annual billing is implemented and fails closed. Finance must, in the Stripe Dashboard:

1. Create a recurring **Developer** Price at **$490 / year**.
2. Create a recurring **Business** Price at **$2,490 / year**.
3. Supply both real Price IDs.
4. Extend the mapping to all four entries:

```
STRIPE_SUBSCRIPTION_PRICES=developer:monthly=<id>,developer:annual=<id>,business:monthly=<id>,business:annual=<id>
```

No code change is required — forward resolution, reverse price→plan resolution and the cadence
toggle all activate from configuration alone. Until then `purchasable_intervals()` reports
`['monthly']`, no toggle renders, and an annual request is refused naming the exact entry
needed. Nothing is ever substituted at the monthly price.

Never paste an elided example: `price_…` with a literal ellipsis was once accepted by a
prefix-only check and broke every checkout. `config._is_stripe_price_id` now rejects it.

### Production migrations are unrun

Production is still on the pre-Stripe schema — no `stripe_subscription_id`, no
`billing_interval`, no `pending_plan_id` — and its only subscription is a `trial`. Steps 1–3
above must run before any of this is live. Because there are no paid subscriptions there yet,
`reconcile_subscription_periods.py` will correctly report zero rows to reconcile.

### The Stripe webhook must be registered and verified

Register the production endpoint in the Stripe Dashboard:

```
https://<service-url>/api/commercial/webhooks/stripe
```

Subscribe at least `checkout.session.completed` and `customer.subscription.created|updated|deleted`,
then put the `whsec_…` signing secret in `STRIPE_WEBHOOK_SECRET`.

This is not optional for plan changes: the webhook is the only thing that populates
`current_period_end`, which **is** the approved effective date. Without it every plan-change
request is refused with `no_period_end`. Verify by making a test-mode change and confirming
`current_period_end` becomes non-NULL on that subscription.
