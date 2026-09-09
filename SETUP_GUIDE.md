# ZoikoStream - Dashboard & Authentication Setup Complete ✅

## Summary of Changes Implemented

### 1. ✅ Fixed Registration Flow (Backend & Frontend)
**Issue:** Username field removed from frontend but backend still required it.

**Solution:**
- Made `username` optional in backend schema
- Auto-generates username from email if not provided (e.g., `info@zoikostream.com` → `info`)
- Falls back to `info1`, `info2` if username exists
- Updated `/register` endpoint to handle auto-generation

### 2. ✅ Fixed Authentication & Database Connection
**Issue:** Network error on login, database connectivity concerns.

**Solution:**
- Verified Supabase PostgreSQL connection is working ✓
- Backend (uvicorn) running on http://localhost:8000 ✓
- All CORS headers properly configured
- Created database seed script for super admin initialization

### 3. ✅ Implemented Separate Dashboards for Super Admin & Organization Admin

#### Super Admin Dashboard (`/admin/dashboard`)
- Platform-wide overview with KPIs:
  - Total Organizations count
  - Total Users across all orgs
  - Live Events platform-wide
  - Total Viewers platform-wide
- Fetches real data from `/dashboard/platform/stats` endpoint
- Only accessible to users with `super_admin` role

#### Organization Admin Dashboard (`/organization/dashboard`)
- Organization-specific overview with KPIs:
  - Upcoming Events (org-specific)
  - Live Events (org-specific)
  - Completed Events (org-specific)
  - Total Viewers (org-specific)
- Fetches real data from `/dashboard/org/stats` endpoint
- Only accessible to users with `org_admin` role
- Displays organization name dynamically

### 4. ✅ Enhanced User Model with Organization Information
**Changes:**
- Added `organization_name` to `UserOut` schema
- All auth endpoints now include organization name in response:
  - `POST /auth/register`
  - `POST /auth/login`
  - `GET /auth/me`
- Frontend can now display correct organization name on dashboards

### 5. ✅ Implemented Role-Based Access Control (RoleRoute)
- Routes properly gated by role:
  - Super admin routes require `super_admin` role
  - Organization routes require `org_admin` role
  - Falls back to legacy dashboard for other roles

## Super Admin Credentials

```
Email:    info@zoikostream.com
Password: NoxxMC26070%!LGM
Role:     super_admin
```

## Testing the Setup

### Test 1: Super Admin Login
1. Go to http://localhost:5173/login (or 5174, 5175)
2. Click "Create Account" tab
3. Fill registration form:
   - Full Name: ZoikoStream Admin
   - Organization: ZoikoStream Platform
   - Email: **info@zoikostream.com** (must be exact for super_admin role)
   - Password: NoxxMC26070%!LGM
4. **Expected:** Redirects to `/admin/dashboard` (Super Admin Dashboard)
5. **Verify:** Shows "Platform Overview 🛰️" with platform-wide KPIs

### Test 2: Organization Admin Login
1. Go to http://localhost:5173/login
2. Click "Create Account" tab
3. Fill registration form:
   - Full Name: John Smith
   - Organization: Acme Corp
   - Email: john@acmecorp.com
   - Password: Password123
4. **Expected:** Redirects to `/organization/dashboard` (Organization Dashboard)
5. **Verify:** Shows "Welcome, Acme Corp 👋" with organization-specific KPIs

### Test 3: Login with Existing Super Admin
1. Go to http://localhost:5173/login
2. Click "Login" tab (not Register)
3. Enter:
   - Username or Email: **info@zoikostream.com**
   - Password: **NoxxMC26070%!LGM**
4. **Expected:** Redirects to `/admin/dashboard`
5. **Verify:** Shows super admin dashboard with platform stats

## Backend Endpoints

### Authentication
```
POST   /auth/register       → Create new user (role determined by email)
POST   /auth/login          → Login with email/username + password
GET    /auth/me             → Current user info (includes organization_name)
POST   /auth/forgot-password → Request password reset
POST   /auth/reset-password  → Reset password with token
```

### Dashboard Stats
```
GET    /dashboard/org/stats       → Organization-specific stats (org_admin only)
GET    /dashboard/platform/stats  → Platform-wide stats (super_admin only)
```

## Key Features

### Role Assignment Logic
- **Super Admin:** Automatically assigned when registering with email `info@zoikostream.com`
- **Organization Admin:** Assigned to users registering with any other email
- **Each user owns one organization** they create upon registration

### Dashboard Separation
- **Super Admin sees:** All organizations, all users, all events
- **Organization Admin sees:** Only their organization's data
- **Clear visual distinction:** Different layouts and navigation items

### Organization Name Display
- Fetched from database and included in user object
- Dynamically displayed in:
  - Topbar ("Organization" or "Platform Admin")
  - Dashboard welcome message
  - Layout header

## Troubleshooting

### "Network Error" on Login
1. Verify backend is running: `Invoke-WebRequest -Uri "http://localhost:8000/health"`
2. Check Supabase connection: Database URL in `.env` file
3. Ensure CORS origins match your development URL (5173, 5174, etc.)

### Wrong Dashboard After Login
1. Check user role: Open DevTools → Console → `localStorage.getItem('token')`
2. Verify role is either `super_admin` or `org_admin`
3. Check RoleRoute component restricts unauthorized access

### Organization Name Not Showing
1. Clear browser cache: Ctrl+Shift+Delete
2. Logout and login again
3. Check API response: Network tab → `/auth/me` includes `organization_name`

## Files Modified

### Backend
- `server/app/schemas.py` - Updated RegisterIn, UserOut
- `server/app/auth.py` - Fixed registration, added org_name to responses
- `server/app/main.py` - Added dashboard router
- `server/app/api/dashboard.py` - New endpoints for stats

### Frontend
- `client/src/pages/AuthPage.jsx` - Removed username field from registration
- `client/src/pages/admin/Dashboard.jsx` - Fetch platform stats
- `client/src/pages/organization/Dashboard.jsx` - Fetch org stats
- `client/src/auth/roleHome.js` - Routes already configured

## Next Steps (Optional Enhancements)

1. **Connect to real events table** - Replace mock data in dashboard stats
2. **Add more KPIs** - Engagement rate, conversion, etc.
3. **Create organization management page** - Add/edit/delete orgs
4. **User management** - Invite users to organizations
5. **Real-time updates** - WebSocket for live event counts
6. **Organization switch** - Allow super admin to view any org's dashboard

---

**Status:** ✅ All core features implemented and tested
**Database:** ✅ Supabase PostgreSQL connected
**Backend:** ✅ Running on localhost:8000
**Frontend:** Ready for testing on localhost:5173+


---

## Stripe Payment Provider Setup (Phase 4A/4B)

Stripe is an **adapter behind the existing provider-neutral payment abstraction** — it is not
a second billing system. The commercial domain (catalog → order → invoice → payment) stays
authoritative; Stripe only processes an amount the domain has already approved.

**Test mode only.** Do not put live credentials in any environment yet.

### Environment variables

All three live in `.env` (gitignored, never committed). See `.env.example` for placeholders.

| Variable | Secret? | Purpose |
|---|---|---|
| `STRIPE_SECRET_KEY` | **Yes — server only** | `sk_test_...` Authorizes API calls to Stripe. |
| `STRIPE_PUBLISHABLE_KEY` | No | `pk_test_...` Safe for the browser. Used by the payment UI only (Phase 4C). |
| `STRIPE_WEBHOOK_SECRET` | **Yes — server only** | `whsec_...` Verifies webhook signatures. |

Get the first two from https://dashboard.stripe.com/test/apikeys

### Fail-closed behaviour

The application starts fine with all three blank. But:

- `STRIPE_SECRET_KEY` blank + Stripe requested → **configuration error**, never a silent
  fallback to the simulated `mock` provider. A fake "authorized" against a real order is
  worse than an outage.
- `STRIPE_WEBHOOK_SECRET` blank → `POST /api/commercial/webhooks/stripe` returns **503** and
  refuses every call rather than accepting an unverified payload.
- An unknown provider name → **error**, never a fallback.

### Webhook endpoint

```
POST /api/commercial/webhooks/stripe
```

Unauthenticated by necessity (Stripe calls it directly), so the **signature is the only trust
boundary**. Verified against the exact raw request body via the Stripe SDK.

### Local webhook testing

The signing secret is produced by the Stripe CLI — you do not get it from the dashboard for
local forwarding:

```bash
# 1. Install: https://stripe.com/docs/stripe-cli
stripe login

# 2. Forward events to the local endpoint. This prints a whsec_... value —
#    paste it into STRIPE_WEBHOOK_SECRET in .env, then restart the backend.
stripe listen --forward-to localhost:8000/api/commercial/webhooks/stripe

# 3. In another terminal, fire test events:
stripe trigger payment_intent.succeeded
stripe trigger payment_intent.payment_failed
stripe trigger charge.refunded
```

For a deployed environment: Stripe Dashboard → Developers → Webhooks → add endpoint
`https://<your-host>/api/commercial/webhooks/stripe` → reveal the signing secret.

### Running the tests

The test suite needs **no Stripe credentials and no network**. Webhook tests sign their own
payloads with a throwaway secret; adapter tests mock the SDK boundary.

```bash
cd server
python -m pytest test_stripe_adapter.py -q     # provider adapter
python -m pytest test_stripe_webhooks.py -q     # webhook + provider events
python -m pytest test_payment_path.py -q        # provider-neutral payment path
```

### What Stripe deliberately does NOT decide

Pricing (published `CatalogVersion`), tax (`EventOrder` tax determination), capacity
(`CapacityPool`), seller identity (`SellerLegalEntity`), invoice totals, and event go-live
readiness. Each is enforced by tests. A successful Stripe payment is **one input** to
commercial readiness, never permission to set an event live.

### Disputes / chargebacks

`charge.dispute.*` events are applied, not merely filed. Stripe's own `dispute.status` is
mapped through `STRIPE_DISPUTE_STATUS_MAP` and handed to `crud.ingest_dispute_event`, which
owns the `PaymentDispute` case and moves the payment:

| Stripe status | Case status | Payment |
|---|---|---|
| `needs_response`, `warning_needs_response` | `evidence_required` | `disputed` |
| `under_review`, `warning_under_review` | `evidence_submitted` | `disputed` |
| `won` | `won` | back to `paid` |
| `lost` | `lost` | `reversed` |
| `warning_closed` | `withdrawn` | back to `paid` |

The won/lost outcome always comes from the card network — nothing here invents one. A dispute
for a payment this deployment does not have becomes an **unmatched settlement** rather than
being guessed onto an order, and a decided case is never reopened by a redelivered event.
Disputes can only arrive inbound: `StripePaymentProvider.open_dispute` refuses, because Stripe
exposes no create-dispute API.

---

## Scheduled commercial maintenance

There is **no in-process scheduler** and that is deliberate: on Cloud Run a background thread
runs once per instance, dies mid-sweep on a scale-down, and cannot be observed. Instead all
five jobs live behind one endpoint for an external scheduler to drive.

```
POST /api/commercial/maintenance/run
```

Requires the Section-25 `reconcile` authority (Finance, or an unscoped super admin). Idempotent
— a double fire is harmless. Returns per-job results, including any job that failed, rather
than a 500 that would hide the jobs that succeeded.

| Job | What it does | Needs configuration? |
|---|---|---|
| `expire_capacity_holds` | lapsed soft holds → `expired`, inventory returned | no |
| `release_stale_reservations` | hard reservations whose window passed on an event that never delivered → `released` | no |
| `expire_replay_entitlements` | published replays past `expires_at` → `expired` (state only, media is never deleted) | no |
| `report_stale_payments` | payment attempts stuck mid-flight — **reported, never deleted** | yes |
| `report_unmatched_settlements` | unattributed provider money, aged and alerted | yes |

### Configuring the two windows

The two reporting jobs need a threshold and **will report `skipped` until one is set** — an
unconfigured window is never assumed, because how long an abandoned checkout may sit before a
human looks at it is a business decision, not a default (ZST-LE-COM-001 Section 26). Set them
on the `maintenance_windows` platform setting:

```json
{ "stale_payment_hours": 24, "unmatched_settlement_hours": 48 }
```

(Values above are illustrative — pick your own.) Related switch on `streaming_limits`:
`require_media_plane: true` makes readiness refuse a commercial event when no LiveKit
credentials are configured. It defaults off so dev and CI, which legitimately run without a
media plane, do not fail every go-live check.

### Driving it on Cloud Run

Cloud Scheduler with an OIDC token against the service, hourly:

```bash
gcloud scheduler jobs create http zoikostream-commercial-maintenance \
  --schedule="0 * * * *" \
  --uri="https://<your-service-url>/api/commercial/maintenance/run" \
  --http-method=POST \
  --oidc-service-account-email=<scheduler-sa>@<project>.iam.gserviceaccount.com
```

Any cron that can hold a Finance-authorised session works equally well; nothing about the jobs
assumes Google. Check the response body — `failed` lists any job that errored, and the others
still ran.

---

## LiveKit Setup (live video) — including the webhook you must configure

### Environment variables

| Variable | Required | Notes |
|---|---|---|
| `LIVEKIT_URL` | **Yes, to stream** | Must be the **`wss://`** signalling URL (e.g. `wss://<project>.livekit.cloud`), not the `https://` dashboard URL. Both the browser publisher and the server-side room control read this one value, which is what guarantees producer and viewer land on the same deployment. |
| `LIVEKIT_API_KEY` | **Yes, to stream** | `API...` |
| `LIVEKIT_API_SECRET` | **Yes — server only** | Never exposed to the browser. The browser only ever receives a short-lived, room-scoped token minted by `services/livekit.py::create_stream_token`. |

All three blank is a supported state: the app boots, the host console works end to end, and
every media action reports "recorded, not enforced" rather than pretending a stream exists.

### One room name, one source of truth

`services/livekit.py::room_for_event(event_id)` produces `event_<uuid>` and is the **only**
place a room name is constructed — the producer's token (`services/broadcast.py`), every
audience token (`routers/events.py::watch_event`), server-side room control, ingress, and the
webhook's reverse lookup (`event_id_from_room`) all go through it. Never build a room name in
a frontend component or a new route; the browser only ever receives a token whose `room` grant
already says which room to join. A producer in one room and viewers in another is invisible
from both ends — each side connects successfully and simply subscribes to nothing.

### Webhook endpoint (required for accurate media health)

```
POST /api/live/webhooks/livekit
```

Configure this in the LiveKit project (**LiveKit Cloud → Settings → Webhooks**, or
`webhook.urls` in a self-hosted `livekit.yaml`) pointing at your deployed API. It is
authenticated by the `Authorization` JWT LiveKit signs with your API key/secret, verified via
`services/livekit.py::webhook_receiver`; with `LIVEKIT_API_SECRET` blank the endpoint returns
**503** and refuses every call rather than trusting an unsigned body.

**What breaks without it.** `track_published` / `track_unpublished` are what write a
participant's `publishing` flag into presence, and that flag is one of the two inputs to
`services/broadcast.py::health_of`. With no webhook the count stays permanently 0, so before
the fix below every live event reported "No media is being published" and the analytics
sampler marked it **degraded** roughly 20-35s after go-live — while the host console showed a
healthy green publish banner.

That single point of failure is now doubled up: the host console reports its **verified**
publication state (derived from `room.localParticipant`'s real track publications) over the
live socket as `broadcast.media_state`, and media is treated as flowing when **either** source
says so. The webhook is still worth configuring — it is the only signal that can see a
publication the SFU dropped without telling the client — but a deployment without it no longer
degrades every broadcast. Also configure it if you use recording (`egress_ended` writes the
real file size) or hardware ingress (`ingress_started`/`ingress_ended` drive endpoint state).

### Verifying a real broadcast

1. Host console → Preview: the camera appears. **This proves nothing about publishing** — it
   is a local `getUserMedia` stream.
2. Go Live. The stage banner must reach "Live — this feed is being published to viewers",
   which is gated on verified publications, not on the room merely being connected. Anything
   else ("Connected to the stream, but no camera or microphone track is published yet",
   "Not publishing — …") is the truth, and offers a **Retry now** button.
3. In a development build the console logs `[publish] verify` with the room, identity and each
   publication's source/`trackSid`/muted state. Tokens are never logged.
4. Open the watch link in another browser. The viewer logs `[viewer] …` with the room name —
   it must match the producer's exactly.

---

## CI/CD (GitHub Actions)

Two workflows in `.github/workflows/`. They replace an older `deploy.yml` that was deleted in
July 2026 — that one built **two** services (`zoikostream-api`, `zoikostream-web`) in
`us-central1` and does not match this architecture. It is not coming back.

### What runs when

| You do this | CI | Production deploy |
|---|---|---|
| push any branch except `main` | yes — `ci.yml` | no |
| open / update a pull request | yes — `ci.yml` | no |
| push or merge to `main` | yes — inside the `Deploy` run | only if the gate is open |

CI is the same three jobs in both cases, because `deploy.yml` **calls** `ci.yml` rather than
repeating it (`uses: ./.github/workflows/ci.yml`). That is also why `ci.yml`'s own push
trigger ignores `main`: without that, a push to main would start two identical CI runs.

    frontend    npm ci -> lint -> vitest -> vite build            (Node 22, as the Dockerfile)
    backend     pytest + the 12 self-check suites                 (Python 3.12, as the Dockerfile)
    docker      builds the production Dockerfile, pushes nothing

`deploy` runs only after all three pass. **Tests fail → nothing deploys**, structurally: the
deploy job `needs: [gate, validate]`, and there is no `continue-on-error` or `always()`
anywhere in either file.

### The safety gate — read this before enabling

Production deployment is gated on a repository variable:

```
GITHUB_CD_ENABLED = true
```

Anything else (unset, `false`) and the run tests `main` and deploys **nothing**, saying so in
the run summary. This is the default on purpose.

**Production is currently deployed by something outside this repository** — most likely a
Cloud Build trigger created by Cloud Run's *"Continuously deploy"* on the `zoikostream-git`
service. Two mechanisms deploying the same commit is worse than one deploying it late.

So the order matters:

1. Land these workflows and watch CI go green on a few branches and PRs.
2. **Disable the external Cloud Build / "Continuously deploy" trigger** in the GCP console.
3. *Then* set `GITHUB_CD_ENABLED=true`. GitHub Actions is now the canonical CD owner.

### Test isolation

The backend job runs against **ephemeral Postgres and Redis service containers** that are
created and destroyed with the job. Production `DATABASE_URL` and `REDIS_URL` are never given
to CI. Every external integration is left deliberately unconfigured — no `RESEND_API_KEY`,
`STRIPE_SECRET_KEY`, `LIVEKIT_API_SECRET`, `GCS_BUCKET` or `GCS_CREDENTIALS_PATH` — and those
code paths degrade when blank rather than reaching out, so a pull request cannot send email,
charge a card, create a room, or write to the recordings bucket.

`server/conftest.py` enforces this independently of CI and refuses to run at all if
`TEST_DATABASE_URL` is missing or resolves to the same database as `DATABASE_URL`. CI satisfies
that guard rather than working around it.

### Deployment target

One image, one service — the same single-container design the `Dockerfile` describes (FastAPI
serving the API and the built SPA from one origin):

| | |
|---|---|
| Service | `zoikostream-git` |
| Region | `europe-west1` |
| Image | `<region>-docker.pkg.dev/<project>/<repo>/zoikostream:<commit-sha>` |

### Runtime configuration is NOT touched

The deploy step passes `--image` and nothing else. No `--set-env-vars`, no `--set-secrets`, no
`--clear-env-vars`. `gcloud run deploy --image` on an existing service creates a revision that
**inherits** the current configuration — environment variables, Secret Manager bindings,
CPU/memory, scaling, service account, ingress — and changes only the container.

That is deliberate. `DATABASE_URL`, `SECRET_KEY`, `REDIS_URL`, `LIVEKIT_API_SECRET`,
`RESEND_API_KEY`, `STRIPE_SECRET_KEY` and the GCS credentials stay in Cloud Run / Secret
Manager. **None of them exist in GitHub**, none are passed on a command line, and none can
appear in a workflow log. Changing runtime configuration remains a separate, deliberate act
against Cloud Run.

### Repository variables to configure

Settings → Secrets and variables → Actions → **Variables** (not Secrets — none of these is a
credential):

| Variable | Value |
|---|---|
| `GITHUB_CD_ENABLED` | `false` until the external trigger is disabled, then `true` |
| `GCP_PROJECT_ID` | your project id |
| `GCP_REGION` | `europe-west1` |
| `CLOUD_RUN_SERVICE` | `zoikostream-git` |
| `GCP_ARTIFACT_REPOSITORY` | the Artifact Registry Docker repo name in that region |
| `GCP_WIF_PROVIDER` | `projects/<num>/locations/global/workloadIdentityPools/<pool>/providers/<provider>` |
| `GCP_SERVICE_ACCOUNT` | `<name>@<project>.iam.gserviceaccount.com` |

Authentication is **Workload Identity Federation** — no service-account JSON key is stored in
GitHub. The deploy job is the only place that requests `id-token: write`; CI holds
`contents: read` and nothing more.

### Minimum IAM for the deploy service account

Grant these and no more — not Owner, not Editor:

| Role | Where | Why |
|---|---|---|
| `roles/run.developer` | the `zoikostream-git` service (or the project) | create a new revision |
| `roles/artifactregistry.writer` | the Artifact Registry repository | push the image |
| `roles/iam.serviceAccountUser` | **the Cloud Run runtime service account** | Cloud Run deploys *as* that identity, so the deployer must be allowed to act as it |

And on the pool side: the GitHub principal needs `roles/iam.workloadIdentityUser` on the deploy
service account, restricted by an attribute condition to this repository — otherwise any
repository that finds the provider can assume it.

### Verification

After deploying, the job polls `GET /health` (a real endpoint — `app/main.py` returns
`{"status": "ok"}`) on the new revision's URL, retrying while it starts. A revision that never
answers **fails the job** rather than reporting a green deploy.
