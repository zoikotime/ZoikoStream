# ZoikoStream — Complete Architecture Audit

**Date:** 2026-07-29 · **Branch:** `harishreddy` · **Scope:** whole repo (240 tracked files + 11 untracked working files)
**Status:** READ-ONLY. Nothing was modified, generated, or refactored.
**Method:** full read of `server/app/**` (all routers, services, models, crud, schemas), `client/src/**` (routing, auth, layouts, hooks, data layer, design system), config, migrations, seeds, tests. Every claim below is grounded in a file:line reference. Two runtime defects were confirmed by executing an import against the real modules.

Prior art: `docs/frontend-audit-phase0.md` (frontend-only, Phase 0). Most of its consolidation plan has since been executed — this audit reflects the *current* state and supersedes its "3 design systems" headline.

---

## 1. Overall Architecture Diagram

```
                                 ┌──────────────────────────────────────┐
                                 │  Browser (Vite SPA, React 19)        │
                                 │  client/src                          │
                                 └──────────────────────────────────────┘
                                    │  axios (Bearer from localStorage)  │  raw WebSocket
                                    │  api.js                            │  useEventStream.js
                                    ▼                                    ▼
  ┌─────────────────────────────────────────────────────────────────────────────────────┐
  │  FastAPI  (server/app/main.py)                                                      │
  │  CORSMiddleware ─ OperationalError handler ─ lifespan(2 background tickers)          │
  ├───────────────┬───────────────┬──────────────┬──────────────┬──────────────┬────────┤
  │ auth.py       │ organization  │ events.py    │ admin.py     │ live.py      │streams │
  │ /auth/*       │ /organization │ /events/*    │ /admin/*     │ WS /live/... │channels│
  │               │ /*            │              │ (super only) │ + LK webhook │/dashbd │
  ├───────────────┴───────────────┴──────────────┴──────────────┴──────────────┴────────┤
  │  security.py  — HTTPBearer → JWT(HS256) → get_current_user → require_min_role ladder │
  │                 org_scoped()  (single point of tenant isolation)                     │
  ├──────────────────────────────────────────────────────────────────────────────────────┤
  │  crud/  (admin, organization, event)        services/  (admin, livekit, bus,          │
  │  pure queries + partial updates              broadcast, moderation)                   │
  ├──────────────────────────────────────────────────────────────────────────────────────┤
  │  models/  SQLAlchemy 2.0 DeclarativeBase (23 tables)   db.py: sync engine + Session   │
  └──────────────────────────────────────────────────────────────────────────────────────┘
        │                    │                     │                    │
        ▼                    ▼                     ▼                    ▼
  ┌───────────┐      ┌──────────────┐      ┌──────────────┐      ┌──────────────┐
  │ Postgres  │      │ Redis        │      │ LiveKit      │      │ Resend       │
  │ (Supabase │      │ pub/sub +    │      │ tokens, room │      │ (email HTTP) │
  │  pooler)  │      │ presence     │      │ ctl, egress  │      │              │
  │  REQUIRED │      │  OPTIONAL    │      │  OPTIONAL    │      │  OPTIONAL    │
  └───────────┘      └──────────────┘      └──────────────┘      └──────────────┘
                                                  │
                                            ┌─────▼──────┐
                                            │  Storage   │  ✗ NOT INTEGRATED
                                            │ (GCS/S3)   │  egress has no output bucket
                                            └────────────┘
```

**Realtime path (the architecturally interesting part):**

```
Console (host or moderator)
  │  1 WebSocket per client, multiplexing 12 logical channels
  ▼
routers/live.py  live_socket()
  ├─ _user_from_token(?token=jwt)          auth BEFORE accept
  ├─ mod.resolve_ctx(event_id, user)       org isolation + per-event assignment → Ctx
  ├─ bus.is_banned()                       ban outlives presence
  ├─ broadcast.ensure_state(ctx)           rehydrate live settings from DB
  ├─ bus.subscribe(event_id) → asyncio.Queue(200)   ← Redis pump when REDIS_URL set
  ├─ send snapshot (mod.snapshot + SNAPSHOT_EXTRAS)  ← whole page state in frame #1
  └─ loop: RateLimiter(30/10s) → mod.dispatch(ctx, action, payload)
                                     │
                    ACTIONS registry (moderation.ACTIONS + broadcast.ACTIONS)
                    VIEWER_ACTIONS / can_moderate / HOST_ONLY gates
                                     │
                    tx(fn) → asyncio.to_thread(short-lived Session)
                                     │
                    bus.publish(event_id, channel, type, data) → every subscriber
```

The design decision worth recording: **there is no REST surface for the live console.** ~50 mutations travel as socket actions through one dispatcher with one auth check, one permission table, one audit path. `routers/live.py:1-14` documents the rationale.

---

## 2. Current Folder Structure

```
ZoikoStream/
├─ .env                        (gitignored; DATABASE_URL, SECRET_KEY, LIVEKIT_*, REDIS_URL, RESEND_*)
├─ requirements.txt            17 pins, unversioned
├─ openapi.json                ⚠ STALE — 6 paths committed vs ~86 live
├─ docs/frontend-audit-phase0.md
├─ SETUP_GUIDE.md  README.md
│
├─ server/
│  ├─ alembic.ini              configured, but migrations/versions/ DOES NOT EXIST
│  ├─ migrations/              README, env.py (target_metadata = None), script.py.mako
│  ├─ create_tables.py         ← the REAL schema tool: create_all + idempotent ALTERs
│  ├─ seed.py                  plans, platform settings, super admin  ⚠ hardcoded password
│  ├─ update_stream_table.py   one-off ALTER script (superseded)
│  ├─ test.py                  DB connectivity smoke
│  ├─ test_authz.py            pure role-ladder + org_scoped self-check
│  ├─ test_events.py  test_invitations.py  test_org_settings.py
│  ├─ test_broadcast.py  test_moderation.py   (in-process bus, stubbed DB)
│  └─ app/
│     ├─ main.py               app factory, CORS, lifespan tickers, 503 handler, /health
│     ├─ config.py             pydantic-settings, single root .env
│     ├─ db.py                 engine (pool_pre_ping), SessionLocal, Base, get_db
│     ├─ security.py           hashing, JWT, get_current_user, require_min_role, org_scoped
│     ├─ auth.py               ⚠ a ROUTER living at package root, not in routers/
│     ├─ email.py              Resend HTTP + 3 inline-CSS templates + cid logo + self-check
│     ├─ api/dashboard.py      ⚠ second router package; legacy, partly hardcoded
│     ├─ routers/              admin, channels, events, live, organization, streams
│     ├─ crud/                 admin(460), event(160), organization(228)
│     ├─ services/             admin(188), bus(285), broadcast(840), moderation(991), livekit(180)
│     ├─ schemas/              admin, auth(⚠ also holds Channel schemas), channel, event,
│     │                        organization, stream
│     ├─ models/               23 tables across 13 modules (+live.py = 7 tables)
│     ├─ assets/zoiko-logo.png
│     └─ utils/                EMPTY
│
└─ client/
   ├─ vite.config.js           envDir:'..'  (shares the root .env)
   ├─ vercel.json              SPA rewrite
   └─ src/
      ├─ main.jsx  App.jsx(179)  api.js(24)  index.css
      ├─ auth/       AuthContext(37)  roleHome(17)  dummy(50)  ⚠ dummy still wired
      ├─ theme/      ThemeContext(18)  — class-based dark mode on <html>
      ├─ hooks/      useApi  useInterval  useSearch  useEventStream  useLiveEvent
      │              useMediaPreview  useSystemStats        [5 untracked in git]
      ├─ utils/      export.js                              [untracked]
      ├─ data/       10 mock modules (~1100 lines) — still the data layer for 8 screens
      ├─ ui/         THE design system: 22 primitives + charts/ + forms/ + tokens + motion
      ├─ layouts/    AppShell  AdminLayout  OrganizationLayout  AuthLayout
      │              MainLayout(160)  ⚠ legacy, own inline sidebar+topbar
      ├─ components/
      │   ProtectedRoute  RoleRoute
      │   admin/         11 primitives (index.js now re-exports ui/*) + sections/ (12)
      │   Dashboard/     6 legacy (Sidebar/Topbar used by OrganizationLayout)
      │   organization/  PageHeader / EmptyState / ErrorState
      │   home/          18 marketing sections across 14 folders
      │   host/ 6   moderation/ 6   watch/ 5
      │   common/ layout/   EMPTY
      └─ pages/      Home  Dashboard(legacy)  EventRegistration
                     admin/ 21   auth/ 4   organization/ 9   host/ 1   moderator/ 1   watch/ 1
      [empty scaffold dirs still present: routes/ services/ constants/ context/ styles/]
```

---

## 3. Authentication Flow

**Registration** (`auth.py:28`)
```
POST /auth/register {full_name, organization_name, email, username?, password}
  → username auto-derived from email prefix, uniquified by counter (N+1 queries)
  → 409 if email exists (email is GLOBALLY unique on users)
  → creates Organization, flush, then User in that org
  → role = "super_admin" if email == settings.SUPER_ADMIN_EMAIL else "org_admin"
  → BackgroundTasks: send_welcome_email (best-effort, never blocks)
  → 201 {access_token, user}
```

**Login** (`auth.py:79`)
```
POST /auth/login {identifier, password, remember}
  → lookup by email OR lower(username)
  → bcrypt.checkpw; 401 generic on either miss
  → 403 if not is_active
  → JWT HS256 {sub, role, exp}; exp = 24h, or 30d when remember=true
```

**Verification** (`security.py:39`)
```
Authorization: Bearer <jwt>  (HTTPBearer, auto_error=True)
  → jwt.decode → payload["sub"] → db.get(User, sub)
  → 401 if missing or not is_active   ← the only revocation mechanism
```
`role` is in the token but **never trusted** — every gate re-reads `user.role` from the DB row. That is correct and worth preserving.

**Password reset** (`auth.py:100-141`) — 4-digit OTP, 10-minute TTL, stored plaintext in `users.reset_token`, single-use, emailed via Resend. `/forgot-password` always 200 (no account enumeration). `/verify-otp` checks without consuming.

**WebSocket auth** (`live.py:66`) — the browser WS API cannot set headers, so the JWT rides in `?token=`. Same decode + `is_active` check, then org/assignment resolution, **all before `accept()`**.

**Frontend session** (`AuthContext.jsx`) — `{token, user}` in `localStorage`, hydrated synchronously (`loading` is permanently `false`). `api.js` interceptor attaches the Bearer. `logout()` clears both keys.

**Gaps:** no refresh tokens · no server-side revocation/blacklist (a password change does **not** invalidate issued JWTs) · no `/auth/me` call on boot, so a stale `localStorage.user` (e.g. role changed by an admin) persists until re-login · no rate limiting on `/auth/login` or `/auth/forgot-password` · no MFA (`Organization.security` JSON has toggles, nothing reads them).

---

## 4. Organization Flow

```
Sign-up creates the org  ────────────────────────────────────────────────┐
  POST /auth/register → Organization(name) + User(role=org_admin)         │
                                                                          │
Org admin self-service (/organization/*, org_id ALWAYS from JWT)          │
  get_my_org(user) ──────► the ONE binding point; org_id never read from  │
  get_my_org_admin  ────►  the request body (organization.py:46-61)       │
    GET/PATCH  /profile   /branding   /domain   /notifications  /security │
    GET        /me  /developer (read-only api_keys + webhook_urls)        │
    slug uniqueness: app-level via crud.slug_taken (NOT a DB constraint)  │
    domain change → domain_verified = False (no DNS flow exists yet)      │
                                                                          │
Members                                                                   │
  GET    /organization/users        paginated, sorted, soft-deleted hidden │
  PATCH  /organization/users/{id}   role ∈ ORG_ASSIGNABLE_ROLES;           │
                                    super_admin untouchable;              │
                                    self-demote / self-deactivate blocked │
  DELETE /organization/users/{id}   SOFT delete + is_active=False          │
                                                                          │
Invitations                                                               │
  POST /organization/invitations   → token_urlsafe(32); only sha256 stored │
                                     7-day TTL; one pending per email/org │
                                     email sent in BackgroundTasks         │
  PATCH .../{id} {action: resend|cancel|expire}   resend rotates the token │
  POST /organization/invitations/accept {token,...}  → creates User + JWT  │
  POST /organization/invitations/reject                                    │
                                                                          │
Super admin cross-org (/admin/organizations)  create/patch/delete + audit ┘
  delete blocked if any user still belongs to the org, or if it's your own
```

**⚠ The invitation loop is broken end to end.** `organization.py:233` builds `{base}/accept-invite?token={token}`, but `App.jsx:109` registers `/accept-invitation`, and `pages/auth/AcceptInvitation.jsx:26-28` reads `?email=&role=&org=` — never `?token=` — then calls `fakeSession()` (`AcceptInvitation.jsx:56`) instead of `POST /organization/invitations/accept`. An emailed invite lands on `*` → `RootRedirect` → `/login`. Additionally `_invite_url`'s base is `CORS_ORIGINS.split(",")[0]`, which defaults to `http://localhost:5173`.

---

## 5. Event Flow

```
        ┌────────────────────────── management layer (/events/*) ──────────────────────────┐
draft ──publish──► published ──► live ──► ended ──► archived
  │                scheduled ────┘                     ▲
  └──────────────────────── cancelled                   │
Guards (crud/event.py:22 status_transition_error, pure & unit-tested):
  • publish/schedule requires a non-blank title
  • live only from published|scheduled
  • ended only from live
  • archived never from live
Slug: unique per org (app-level), auto-derived from title with -1/-2 suffixes
Delete: soft (deleted_at); every read filters deleted_at IS NULL
Assignments: PATCH /events/{id}/{hosts|moderators|speakers} replaces the whole set;
             assignees must be live members of the same org (crud.valid_member_ids)
        └──────────────────────────────────────────────────────────────────────────────────┘
                                            │
        ┌──────────────────── broadcast layer (WS actions, host-only) ──────────────────────┐
preview ──golive──► live ⇄ paused ──end / emergency_stop──► ended
  │                                                              (ended_at set ONCE)
  ├─ broadcast.preview      livekit.ensure_room + issues a publish token; no session row
  ├─ broadcast.countdown    one server deadline, so every console counts down together
  ├─ broadcast.golive       idempotent; seeds settings from the Event's feature flags;
  │                         drives Event.status published|scheduled → live
  ├─ broadcast.pause/resume accumulates paused_ms; ONE session row survives a pause
  ├─ broadcast.settings     clean_settings() whitelist of 25 keys; persisted + bussed
  └─ broadcast.end          stops recording FIRST, then ends session, Event.status → ended
                            emergency_stop additionally livekit.close_room()
        └──────────────────────────────────────────────────────────────────────────────────┘
                                            │
        ┌──────────────────────────── recording lifecycle ─────────────────────────────────┐
idle ──start──► recording ⇄ paused ──stop──► stopped   (failed on egress error)
  start: RoomCompositeEgressRequest, MP4, filepath = zoikostream/{org}/{event}/{ts}.mp4
         enforced = bool(egress_id) — a failed egress is RECORDED as unenforced, not hidden
  pause: bookkeeping only — LiveKit egress has no pause API (documented at broadcast.py:460)
        └──────────────────────────────────────────────────────────────────────────────────┘
```

**Playback / replay: does not exist.** `pages/organization/Recordings.jsx` renders `data/recordings.js` mocks. There is no `GET /recordings` endpoint, no `Recording` read API, no signed URL issuance, and `LiveRecording.file_url` holds an egress *filepath*, not a fetchable URL.

**Viewer playback: does not exist.** `client/package.json` has **no `livekit-client`**. `components/watch/VideoPlayer.jsx` is a static surface. `broadcast.py:344` and `:746` both state plainly that a publish token is minted so wiring a real publisher is a drop-in — nothing publishes or plays media today.

---

## 6. User Role Flow

**Two orthogonal role systems, by design:**

| | Platform role (`users.role`) | Event role (`event_assignments.role`) |
|---|---|---|
| Values | super_admin, org_admin, host, moderator, speaker, viewer | host, moderator, speaker |
| Scope | global per user | per (event, user) |
| Set by | register / admin PATCH / org PATCH / invite | `PATCH /events/{id}/{role}s` (org admin only) |
| Used for | HTTP endpoint gates | live-console capability resolution |

**Ladder** (`security.py:64`) — `viewer 0 < speaker 1 < moderator 2 < host 3 < org_admin 4 < super_admin 5`. `require_min_role(m)` admits `m` and above; `super_admin` clears every gate and bypasses `org_scoped()`.

**Live-console capability resolution** (`moderation.py:118 resolve_ctx`) — this is the important nuance:
```
org_admin / super_admin      → can_moderate = True, can_host = True   (any event in their org)
moderator or host PLATFORM role → must be ASSIGNED to THIS event:
      assignment ∈ {moderator, host}  → can_moderate = True
      assignment == host              → can_host = True
speaker / viewer             → can_moderate = False, can_host = False
```
An org's `host` is *not* automatically host of every event. Correct.

**Action gating** (`moderation.py:927 dispatch`):
```
HOST_ONLY (broadcast.* + recording.*) ─► requires ctx.can_host
VIEWER_ACTIONS (chat.send/typing/react, qa.ask/vote, poll.vote,
                participant.hand, participant.state) ─► any authenticated attendee
everything else ────────────────────────────────────► requires ctx.can_moderate
```
Stage controls (`stage.camera/share/admit/admit_all/mute_all`) are deliberately available to moderators.

**Frontend gating** — `RoleRoute allow={["super_admin"]}` on `/admin/*`, `allow={["org_admin"]}` on `/organization/*`, `ProtectedRoute` on `/dashboard`. `roleHome()` maps role → landing page; `viewer` maps to `null` (no app home; viewers watch via event links).

**⚠ `/host/dashboard` and `/moderator/dashboard` have NO route guard** (`App.jsx:120,124`, flagged in-code as demo routes). The pages are not exploitable — the socket rejects a non-assigned user and the UI degrades to read-only — but any logged-in viewer can load the console chrome.

---

## 7. Database ER Overview

23 tables. Postgres (Supabase pooler). UUID v4 PKs everywhere except `platform_settings` (natural key `key`).

```
                      ┌──────────────────┐
                      │  organizations   │  id, name, domain, status(active|trial|suspended),
                      │                  │  region, storage_used_gb, bandwidth_gb,
                      │                  │  slug*, website, description, industry,
                      │                  │  company_size, support_email, timezone, country,
                      │                  │  logo_url, primary_color, secondary_color, theme,
                      │                  │  domain_verified, notifications(JSON),
                      │                  │  security(JSON), api_keys(JSON), webhook_urls(JSON)
                      └────┬────┬────┬───┘   * indexed, uniqueness enforced in crud only
             ┌─────────────┘    │    └────────────────┬──────────────────┐
             │                  │                     │                  │
      ┌──────▼──────┐   ┌───────▼───────┐    ┌────────▼────────┐  ┌──────▼────────┐
      │   users     │   │ invitations   │    │  subscriptions  │  │support_tickets│
      │ org_id FK   │   │ org_id FK     │    │ org_id FK       │  │ org_id FK     │
      │ email UQ †  │   │ email, role   │    │ plan_id FK ─────┼─►│               │
      │ username UQ │   │ token_hash UQ │    │ status, seats   │  └───────────────┘
      │ password_h  │   │ invited_by FK │    │ periods         │
      │ role        │   │ expires_at    │    └─────────┬───────┘
      │ is_active   │   └───────────────┘              │        ┌────────┐
      │ reset_token │                                  └───────►│ plans  │
      │ deleted_at  │                                           │ slug UQ│
      └──┬───┬──────┘                                           └────────┘
         │   │
         │   └──────────────► channels (owner_id FK, slug UQ GLOBALLY ⚠ no org_id)
         │                        └──────► streams (channel_id FK, stream_key UQ,
         │                                          livekit_room UQ, is_live) ⚠ no org_id
         │
         └── created_by ──► ┌──────────────────────────────────────┐
                            │              events                  │
                            │ org_id FK(ix), created_by FK         │
                            │ title, slug(ix), description,        │
                            │ banner_image, thumbnail, category,   │
                            │ tags(JSON), language, timezone       │
                            │ start_time, end_time                 │
                            │ visibility, registration_required,   │
                            │ registration_limit                   │
                            │ 9 feature toggles (chat, qa, polls,   │
                            │   waiting_room, recording, hands,     │
                            │   screen_share, auto_start_rec,       │
                            │   auto_end)                           │
                            │ status, created_at/updated_at,        │
                            │ deleted_at (soft delete)              │
                            └───┬──────────────────────────────┬────┘
                                │                              │
                 ┌──────────────▼───────────┐    ┌─────────────▼─────────────────────────┐
                 │   event_assignments      │    │  7 live_* tables (models/live.py)     │
                 │ event_id FK(ix)          │    │  abstract base _EventScoped:          │
                 │ user_id FK               │    │    id, event_id FK(ix),               │
                 │ role: host|mod|speaker   │    │    org_id UUID(ix) ⚠ NO FK — denorm   │
                 │ UQ(event_id,user_id,role)│    │    created_at(ix)                     │
                 └──────────────────────────┘    ├───────────────────────────────────────┤
                                                 │ live_messages     status,pinned,flags,│
                                                 │                   reactions(JSON),    │
                                                 │                   reply_to, note,     │
                                                 │                   deleted_at/_by      │
                                                 │ live_questions    votes,status,pinned,│
                                                 │                   assigned_to/_name   │
                                                 │ live_polls        options(JSON        │
                                                 │                   [{label,votes}]),   │
                                                 │                   scheduled/closes/   │
                                                 │                   launched/closed_at  │
                                                 │ live_announcements priority,sent_at,  │
                                                 │                    delivered_to       │
                                                 │ broadcast_sessions status,settings,   │
                                                 │                    paused_ms,peak,    │
                                                 │                    ended_reason       │
                                                 │ live_recordings   session_id(ix),     │
                                                 │                   egress_id,quality,  │
                                                 │                   size_bytes ⚠ INT,   │
                                                 │                   file_url, enforced   │
                                                 │ analytics_snapshots viewers,parts,    │
                                                 │                   on_stage,messages,  │
                                                 │                   questions,reactions,│
                                                 │                   hands               │
                                                 │ live_activity     kind,text,actor_name│
                                                 └───────────────────────────────────────┘

 standalone / platform:  audit_logs (actor_id+actor_email, action, target_type/_id, org_id,
                                     meta JSON, ip, created_at(ix) — no FKs, must outlive actors)
                         feature_flags (key UQ)   releases   platform_settings (key PK)
```

**Tenant isolation model**
- `org_scoped(stmt, model, user)` (`security.py:93`) is the declared single point. `super_admin` bypasses.
- `/organization/*` binds via `get_my_org` from the JWT — never from the body.
- `/events/*` passes `user.org_id` into every crud call.
- `live_*` tables **denormalize `org_id`** so a query is org-isolated without a join (`models/live.py:1-13`); `moderation._scoped` filters on both `event_id` **and** `org_id`, and `_row()` re-scopes any id arriving over the wire.
- **`channels` and `streams` have no `org_id` at all** — isolation is `owner_id == user.id` only. `services/admin.live_events` reaches org via `stream → channel → owner → organization`.

**Constraint gaps**
| Intended rule | Where enforced | DB constraint? |
|---|---|---|
| org slug unique | `crud.slug_taken` | ✗ (index only) — race |
| event slug unique per org | `crud.event_slug_taken` | ✗ — race |
| one active subscription per org | "app logic" (`subscription.py:20`) | ✗ — and no code enforces it either |
| `users.email` unique | ✓ DB | ✓ globally † — one identity cannot join two orgs |
| `channels.slug` unique | ✓ DB | ✓ globally ⚠ — cross-tenant collision + enumeration |
| status/role enums | Python tuples | ✗ no CHECK / no PG ENUM |
| `live_*.org_id` → organizations | — | ✗ no FK |

**Migrations:** `alembic.ini` points at `migrations/`, but **`migrations/versions/` does not exist** and `env.py:22` still has `target_metadata = None`. The real schema tool is `create_tables.py` — `create_all()` plus 22 hand-written idempotent `ADD COLUMN IF NOT EXISTS` clauses on `organizations` and 1 on `users`. `models/live.py`'s 7 tables have no ALTER coverage (they're new, so `create_all` builds them) — **the next column added to any pre-existing table needs another hand-written ALTER**.

---

## 8. LiveKit Integration Flow

```
services/livekit.py — every function returns FALSE / (None,error) when unconfigured,
                      so the console works end-to-end in dev without LiveKit.
                      configured() = URL && API_KEY && API_SECRET all non-empty.

Room naming — TWO CONVENTIONS COEXIST:
   event_<event_id>    moderation.Ctx.room     ← the live console + webhooks
   stream_<stream_id>  routers/streams.py:261  ← the legacy stream API
   live.py:173 flags this: webhooks ignore any room not prefixed "event_".

Token generation (create_stream_token, livekit.py:20)
   VideoGrants(room_join=True, room=<name>, can_publish=<bool>, can_subscribe=True)
   ⚠ no TTL set on the AccessToken  ⚠ no can_publish_data, no room_admin
   Issued from:
     • broadcast.preview        → publish token, host only, if configured
     • snapshot_extra           → publish token, ctx.can_host && configured
     • POST /streams/{id}/start → publish token, channel owner
     • GET  /streams/{id}/token → subscribe-only, identity viewer-<uuid4>
                                  ⚠ NO AUTHENTICATION on this endpoint

Server-side room control (_with_room → short-lived LiveKitAPI, always aclose())
   mute_participant   get_participant → mute_published_track per track
   remove_participant
   set_stage          update_participant(ParticipantPermission(can_publish=on_stage))
   ensure_room        create_room(empty_timeout=600); already-exists = success
   close_room         delete_room

Recording (egress)
   start_recording  RoomCompositeEgressRequest → EncodedFileOutput(MP4, filepath)
                    720p/1080p via EncodingOptionsPreset; 2k/4k via explicit
                    EncodingOptions(width,height,framerate=30)
                    ⚠ NO s3/gcp/azure output block → on LiveKit Cloud the request is
                      rejected; surfaced honestly as (None, error) → enforced=False
   stop_recording   StopEgressRequest

Webhooks  POST /live/webhooks/livekit   (include_in_schema=False)
   WebhookReceiver(TokenVerifier(key, secret)).receive(body, Authorization)
     • unconfigured → 503 (never trusts an unsigned body)
     • bad signature → 401
   participant_joined / _left       → presence upsert/remove + participants channel + feed
   track_published / _unpublished   → presence.publishing = bool
   room_started                     → room.status{live:true} + persisted feed line
   room_finished                    → if session status ∈ (live,paused): DEGRADED, not ended
                                       → broadcast.health{level:down, recovering:true}
                                       → presence deliberately NOT cleared (waiting room)
                                      else: presence_clear + "Stream ended"
   egress_started / _ended          → recording.status + persisted feed line
```

**Permission matrix as actually implemented**

| | can_publish | can_subscribe | room control | recording control |
|---|---|---|---|---|
| Host (assigned) | ✓ token issued | ✓ | ✓ via `stage.*` / `participant.*` | ✓ `recording.*` |
| Moderator (assigned) | ✗ no token minted | ✓ | ✓ `participant.*`, `stage.*` | ✗ blocked by HOST_ONLY |
| Speaker | ✗ until `stage.on` grants publish | ✓ | ✗ | ✗ |
| Viewer | ✗ | ✓ | ✗ | ✗ |
| org_admin / super_admin | ✓ (treated as host) | ✓ | ✓ | ✓ |

**Confirmed defect:** `stage.camera` and `stage.share` (`broadcast.py:481`) both call `livekit.set_stage(room, identity, allowed)` — the same single `can_publish` bit — while writing to two different presence fields (`camera_allowed` / `share_allowed`). Disabling a speaker's camera also revokes their audio and screen share; re-allowing share silently restores the camera. The two controls are not independent at the media layer.

---

## 9. Existing Features

**Working, real, backed by the API**

| Area | Detail |
|---|---|
| Auth | register (creates org), login (email or username), 24h/30d JWT, 4-digit OTP reset, `/auth/me` |
| Org self-service | profile, branding, domain, notifications, security JSON — all 5 GET+PATCH, wired to `pages/organization/Settings.jsx` |
| Members | list/get/patch/soft-delete with self-lockout guards, wired to `InviteMembers.jsx` |
| Invitations | create/list/resend/cancel/delete, sha256-only token storage, 7-day TTL, email delivery (accept step broken — §4) |
| Events | full CRUD + soft delete, guarded lifecycle, per-org slugs, filter/sort/paginate, host/moderator/speaker assignment. Wired to `Events.jsx`, `EventDetails.jsx`, `CreateEventModal.jsx` |
| Super admin | 34 endpoints: orgs, users, plans, subscriptions, analytics, platform health, audit logs, platform settings, feature flags, releases, support tickets, per-org API keys. **Every mutation writes an audit row.** 14 admin pages wired |
| Live moderation | chat (send/react/typing/approve/pin/delete/note/bulk), Q&A (ask/vote/approve/answer/dismiss/pin/assign/delete), polls (create/update/launch/close/delete/vote + scheduler), announcements (send/schedule/delete), participants (hand/state/mute/timeout/stage/role/ban/remove) |
| Host console | preview, countdown, go-live (idempotent), pause/resume, end, emergency stop, 25 validated live settings, recording start/pause/resume/stop, stage camera/share, waiting-room admit/admit-all, mute-all |
| Realtime infra | 12 logical channels over 1 WS, bounded queues with drop-oldest, Redis pub/sub bridge (ref-counted pump), Redis-or-memory presence, bans that outlive presence, per-socket sliding-window rate limit, 90s idle reap, exponential backoff + jitter reconnect, ping/pong latency, full-snapshot resync |
| Analytics | 15s sampler → `analytics_snapshots` (this IS the retention graph), peak viewers, engagement score (documented formula), avg watch time from real join stamps, device/platform/browser distribution from the handshake UA, honest `null` + note for GeoIP |
| Content moderation | profanity/link/spam/duplicate heuristics with a TLD allowlist (so "React.js" isn't flagged), host-toggleable filters, optional auto-moderation, slow mode, emoji-only, subscriber-only, staff bypass |
| Audit | `audit_logs` from admin mutations AND every live moderator action; `live_activity` as the in-console timeline (two readers, one writer helper) |
| Email | 3 Resend templates (welcome, OTP, invitation), inline CSS, cid-embedded logo, HTML-escaped, best-effort with its own logging, offline self-check |
| Design system | one `ui/` — tokens, Button, Badge, Card, Panel, Modal, Drawer, DataTable, StatCard, Spinner, Skeleton, Toast, Typography, motion, `ui/forms/` (11 exports), `ui/charts/` (shared recharts wrappers + theme-aware tooltip). `components/admin/index.js` now re-exports from `ui/*` |
| Theming | class-based dark mode on `<html>`, persisted; Tailwind v4 `@theme` with a brand remap of the emerald/teal scales |
| Marketing site | 14 homepage sections, lazy-loaded, code-split |

**Present but mock-only (UI complete, no backend)**

`pages/organization/Recordings.jsx` · `Analytics.jsx` · `Billing.jsx` · `pages/watch/EventWatch.jsx` (+ 5 watch components) · `pages/EventRegistration.jsx` · `pages/Dashboard.jsx` (legacy) · `components/admin/AdminSidebar/AdminTopbar` (nav/search/alerts from `data/platform.js`) · `components/host/*` and `components/moderation/*` constants from `data/host.js` / `data/moderation.js`.

---

## 10. Missing Features

Ordered by how much they block a shippable product.

**Blocking — the streaming product cannot function**
1. **No browser media path.** No `livekit-client` dependency. Nothing publishes; nothing plays. Publish tokens are minted and discarded. This is the single largest gap.
2. **No storage integration.** Egress has no S3/GCS/Azure output; `platform_health` reports "Google Cloud Storage not yet integrated". Recordings are not durably written.
3. **No recording read API.** No `GET /recordings`, no signed URLs, no download, no replay, no VOD. `file_url` is an egress filepath.
4. **Invitation accept is broken** (path mismatch + `fakeSession`) — §4.
5. **Two admin endpoints return 500** — §12.

**High**
6. Public event registration — `/e/:id` renders mocks; no `registrations` table, no `POST /events/{id}/register`, and `registration_required` / `registration_limit` are stored but read by nothing.
7. Anonymous viewer access — no way to issue a scoped token to an unauthenticated attendee for a `public` event. `Event.visibility` is stored and never enforced anywhere.
8. Org analytics API — `pages/organization/Analytics.jsx` is mocks; `analytics_snapshots` exists but has no per-org read endpoint.
9. Billing — `plans` + `subscriptions` tables exist and super admin can patch them, but there is no payment provider, no invoices, no checkout, no usage metering. `storage_used_gb` / `bandwidth_gb` are never written.
10. Plan-limit enforcement — `max_users`, `max_storage_gb`, `max_streaming_hours` are stored and enforced nowhere.
11. Asset/image upload — `logo_url`, `banner_image`, `thumbnail`, `avatar_url`, `banner_url` are all `String(500)` with no upload endpoint.
12. Org-scoped API keys are super-admin-only to create (`/admin/organizations/{id}/api-keys`); `GET /organization/developer` is read-only, and **no endpoint authenticates using an API key** — they are decorative.
13. Feature flags have full CRUD but **nothing reads them** at request time.

**Medium**
14. Notification system — `Organization.notifications` JSON toggles are stored; no notification dispatcher, no in-app notifications (the bell in `MainLayout.jsx:130` is hardcoded "6").
15. Security settings — MFA, SSO, IP allowlist, session policy stored in `Organization.security`; nothing reads them.
16. DNS domain verification — `domain_verified` only ever set to `False`.
17. Per-user Q&A vote ledger (`moderation.py:547` flags the vote-stuffing hole).
18. GeoIP (honestly reported as `null` with a note).
19. Chat/transcript export, compliance export (soft deletes preserve the data for it; no endpoint).
20. Refresh tokens, token revocation, password-change invalidation.
21. Speaker and viewer dashboards (`roleHome` maps speaker → legacy `/dashboard`, viewer → `null`).
22. `analytics_snapshots` retention/pruning.
23. Simulcast / adaptive bitrate / transcoding ladder — `resolution`/`bitrate_kbps`/`adaptive` are stored targets only.
24. Live captions, breakout rooms, RTMP ingest/egress, multi-destination restreaming.
25. Webhooks *out* (`webhook_urls` stored, nothing dispatches).

---

## 11. Duplicate Code

**Backend**

| # | Duplication | Locations |
|---|---|---|
| B1 | **Channel Pydantic schemas defined twice, differently** — `ChannelCreate` in both; `schemas/auth.py` also has `ChannelUpdate`/`ChannelOut`, `schemas/channel.py` has `ChannelResponse`. `schemas/__init__` does `from .auth import *` then `from .channel import *`, so **`channel.py`'s `ChannelCreate` silently wins** and the `auth.py` copy (with `min_length=3`) is shadowed | `schemas/auth.py:51-79`, `schemas/channel.py` |
| B2 | **Two dashboard/stats surfaces** — `api/dashboard.py` (`/dashboard/*`, partly hardcoded) vs `services/admin.dashboard_summary` (`/admin/dashboard`, real). Both count orgs/users, both exclude `"ZoikoStream Platform"` by name string | `api/dashboard.py:77,98` vs `services/admin.py:22,54` |
| B3 | Two router *packages* — `app/auth.py` is a router at package root while every other router lives in `app/routers/`, and `app/api/dashboard.py` is a third location | `app/auth.py`, `app/api/`, `app/routers/` |
| B4 | Two role-hierarchy sources — `security._ROLE_RANK` and `models/user.ROLES` (comment says "keep in sync"). `services/admin.py:14` imports `_ROLE_RANK` and **never uses it** | `security.py:64`, `models/user.py:18` |
| B5 | Unique-username-by-counter loop written three times | `auth.py:37-42`, `crud/organization.py:114`, `seed.py:100-104` |
| B6 | `_base_url()` / `_invite_url()` both derive a frontend URL from `CORS_ORIGINS.split(",")[0]` | `email.py:66`, `routers/organization.py:233` |
| B7 | `_iso()` defined in `moderation.py:166` and re-exported through a wrapper in `broadcast.py:110` |
| B8 | The pause/resume `paused_ms` accumulation block, byte-for-byte, in four places | `broadcast.py:232, 282, 312, 390` |
| B9 | `_current_session` / `_current_recording` "newest not-ended" query pattern | `broadcast.py:139,150` |
| B10 | Platform-org exclusion by literal name string in two modules | `api/dashboard.py:77`, `services/admin.py:17` |
| B11 | Idempotent-schema logic split between `create_tables.py` and the now-superseded `update_stream_table.py` |
| B12 | `_user_from_token` (`live.py:66`) re-implements `get_current_user`'s decode+lookup+is_active — genuinely necessary (query-string token), but the two must stay in step |

**Frontend**

| # | Duplication | Locations |
|---|---|---|
| F1 | **Legacy dashboard shell duplicated** — `MainLayout.jsx:47-109` inlines its own sidebar and `:116-152` its own topbar, while `components/Dashboard/Sidebar.jsx` + `Topbar.jsx` (prop-driven) serve `OrganizationLayout`, and `components/admin/AdminSidebar/AdminTopbar` serve `AdminLayout`. Three sidebars, three topbars |
| F2 | `initials()` — still ~7 definitions | `admin/format.js`, `admin/AdminTopbar.jsx`, `Dashboard/Topbar.jsx`, `MainLayout.jsx:25`, `data/host.js`, `data/moderation.js`, `data/watch.js` |
| F3 | `isEmail` regex — 4 copies | `auth/Login.jsx:10`, `auth/CreateOrganization.jsx`, `auth/ForgotPassword.jsx`, `EventRegistration.jsx` |
| F4 | Two storage formatters | `data/recordings.js fmtStorage`, `data/billing.js fmtGB` |
| F5 | `fmtDate` imported from `data/events.js` by 6 unrelated feature files — a mock module is doing library duty | `Analytics`, `Billing`, `InviteMembers`, `Recordings`, `Settings`, `watch/WatchHeader` |
| F6 | API-mutation-with-toast (`try{await api…; notify.success; reload()}catch{notify.error(errMsg(e))}`) — ~15 copies; no `useMutation` hook exists |
| F7 | Client-side search filter, no debounce — `Events.jsx`, `Recordings.jsx`, `InviteMembers.jsx` (+ `hooks/useSearch.js` exists and is barely used) |
| F8 | Blob → object-URL → download — `utils/export.js` now exists, but `org/Billing.jsx` still hand-rolls it |
| F9 | Viewer-count drift interval — `EventWatch.jsx:28` (range 15) and a sibling in the mock consoles (range 13) |
| F10 | `data/*` re-declare mock constants that the real socket snapshot now supplies (`data/host.js` QUALITY, accents; `data/moderation.js` ACT icons keyed to `ACTIVITY_KINDS` in `models/live.py:30` — a cross-stack coupling with no test) |
| F11 | Hand-rolled `<table>` still in ~7 places while `DataTable` exists (`admin/LiveEvents.jsx`, 3 `admin/sections/*`, `Dashboard/RecentEvents.jsx`, `pages/Dashboard.jsx`, `org/Analytics.jsx`, `org/Billing.jsx`, `org/Settings.jsx`) |

---

## 12. Dead Code

**Confirmed dead — zero references (verified by grep)**

| Item | Lines | Evidence |
|---|---|---|
| `client/src/components/admin/sections/PlatformAnalytics.jsx` | 88 | 0 refs |
| `client/src/components/admin/sections/SecurityCenter.jsx` | 57 | 0 refs |
| `client/src/components/admin/TrendStat.jsx` | 28 | 0 refs, not in `admin/index.js` |
| `client/src/components/Dashboard/DashboardCard.jsx` | 3 | re-export shim of `ui/StatsCard`; only self-references |
| `server/app/api/dashboard.py:16-30` `OrgStats` / `PlatformStats` | 15 | plain classes, never instantiated or referenced |
| `server/app/services/admin.py:14` `from ..security import _ROLE_RANK` | 1 | imported, never used — residue of a deleted `roles()` |
| `server/app/utils/` | — | empty package |
| `client/src/{routes,services,constants,context,styles}/`, `components/{common,layout}/` | — | 7 empty scaffold dirs |

**Dead dependencies**

| Package | Imports |
|---|---|
| `react-hook-form` ^7.82.0 | **0** |
| `socket.io-client` ^4.8.3 | **0** (the app uses a raw `WebSocket`; `useEventStream.js:13` documents why) |
| `dayjs` ^1.11.21 | 1 trivial use |
| `aiofiles`, `pillow`, `python-multipart` (requirements.txt) | **0** — no file upload/processing exists yet |

**Superseded / orphaned scripts**

- `server/update_stream_table.py` — its two ALTERs are now covered by the model + `create_all`.
- `server/migrations/` — Alembic scaffolding with **no versions directory** and `target_metadata = None`. Non-functional.
- `openapi.json` — 6 paths committed vs ~86 live. Stale artifact, not generated by any script in the repo.

**⚠ Not dead — BROKEN. Two admin endpoints raise on every call (confirmed by executing an import):**

```
$ python -c "import app.services.admin as s; print(hasattr(s,'roles')); import inspect; print(inspect.signature(s.live_events))"
has roles: False
live_events sig: (db: Session) -> list[dict]
```

| Endpoint | Call site | Failure |
|---|---|---|
| `GET /admin/roles` | `routers/admin.py:255` → `svc.roles()` | `AttributeError` — `services.admin` has no `roles`. Only 9 top-level functions exist and `roles` is not one of them. → 500 |
| `GET /admin/live-events` | `routers/admin.py:203` → `svc.live_events(db, state=state)` | `TypeError: live_events() got an unexpected keyword argument 'state'`. → 500 |

Both are consumed by live pages: `pages/admin/Roles.jsx:8` and `pages/admin/LiveEvents.jsx:32-33` (which calls it **twice**, `state=live` and `state=recent`), plus `pages/admin/Dashboard.jsx:41`. Three admin screens are broken.

**Dead-by-design (intentional, keep)**

`app/auth.py:128` `/auth/verify-otp` (frontend goes straight to reset) · 22 `Placeholder` routes in `App.jsx` (intentional nav targets) · `pages/Dashboard.jsx` + `MainLayout` + `components/Dashboard/*` (reachable via `/dashboard` for `speaker`/unknown roles) · all 10 `data/*` mocks (still the data layer for 8 screens).

---

## 13. Performance Bottlenecks

Ranked by when they bite.

| # | Issue | Location | Impact |
|---|---|---|---|
| P1 | **DB pool undersized for socket traffic.** `create_engine` sets only `pool_pre_ping` → SQLAlchemy defaults `pool_size=5, max_overflow=10` = 15 connections. Every socket action goes through `tx()` → `asyncio.to_thread` (default executor: `min(32, cpu+4)` threads) → a fresh `SessionLocal()`. Beyond ~15 concurrent actions, threads block on `QueuePool` checkout until timeout (30s default) | `db.py:7`, `moderation.py:217-232` | **First thing that breaks under load.** Symptom: socket actions hang, then 500 |
| P2 | **Analytics sampler is O(events × messages) every 15 seconds.** `_counts()` runs 4 aggregate queries **plus** `SELECT reactions FROM live_messages WHERE reactions IS NOT NULL` — every reaction row of the event, summed in Python — and `SELECT * FROM live_polls`. Called twice in `analytics_now` (total + 1-minute window) and once per session per tick | `broadcast.py:625-647`, `:757-796` | An 8-hour event with 50k messages re-reads 50k JSON blobs every 15s, forever |
| P3 | Sampler is **sequential**: `for s in sessions:` with an `await mod.tx(write)` per session. 50 concurrent events = 50 serialized round trips inside one 15s tick | `broadcast.py:765-796` | Tick overruns its interval; snapshots drift |
| P4 | **`analytics_snapshots` grows unbounded** — 1 row/15s/live event ≈ 1,900 rows per 8h event (acknowledged at `models/live.py:164`), with **no pruning, no partitioning, no rollup** | `models/live.py:161` | Table and index bloat over months |
| P5 | Every chat message does a `SELECT` of the author's last 10 messages (duplicate + slow-mode check) before the `INSERT` | `moderation.py:392-398` | 2 queries per message; at 100 msg/s = 200 qps against a 15-connection pool |
| P6 | `_snapshot` issues **8 queries** (event, host, speakers, ×5 history windows at 200/200/50/50/100 rows) on **every socket connect**, and `snapshot_extra` adds 4 more plus a full `analytics_now` | `moderation.py:265-299`, `broadcast.py:714-749` | ~12 queries × N reconnects. A server blip → thundering herd (mitigated by backoff+jitter, not eliminated) |
| P7 | `_streaming_hours` loads **every** stream row and sums in Python (acknowledged at `services/admin.py:48`). Called by both `/admin/dashboard` and `/admin/analytics` | `services/admin.py:44` | Linear in total stream count, on two hot admin pages |
| P8 | `len(o.users)` in a loop → **N+1** lazy loads, one query per org | `api/dashboard.py:102` | 10 orgs = 11 queries |
| P9 | `bus.state_set` is a read-modify-write, not a Lua CAS (acknowledged at `bus.py:242`) | `bus.py:240` | Lost update if a background writer ever touches state |
| P10 | Frontend fetches `page_size=100` then filters/sorts/paginates **client-side** | `org/Events.jsx:31`, `org/InviteMembers.jsx:104-105` | Silently truncates at 100 rows; wasteful payloads |
| P11 | `useApi` has no cache, no dedupe, no abort; `Settings.jsx:102-106` fires 5 parallel GETs on mount, `EventDetails.jsx:66-69` fires 4 | `hooks/useApi.js` | Refetch storm on every mount |
| P12 | **recharts is in the main bundle.** Only `Home` is lazy-loaded; every admin/org dashboard route is eagerly imported in `App.jsx:11-42` | `App.jsx` | Large first paint for logged-in users |
| P13 | No search debounce anywhere; `useSystemStats` runs a `requestAnimationFrame` loop + 2s interval whenever the host console is open | `useSystemStats.js:57` | Minor; the rAF loop correctly pauses when hidden |
| P14 | `SNAPSHOT_EXTRAS` + `analytics_now` mean the **first WS frame is large** (200 messages + 200 questions + 50 polls + 50 announcements + 100 activity + 240 retention points) | `moderation.py:308` | Slow first paint on a busy event |

---

## 14. Security Risks

| # | Sev | Finding | Location |
|---|---|---|---|
| S1 | **Critical** | **Hardcoded super-admin password committed to the repo** — `hash_password("NoxxMC26070%!LGM")`, and the same literal is `print()`ed to stdout. Anyone with repo read access owns the platform account | `seed.py:111,122` |
| S2 | **Critical** | **`GET /streams/` requires no authentication** and returns **every stream on the platform across all tenants**, including `stream_key` (`StreamResponse.stream_key`, `schemas/stream.py:30`). A stream key is a publish credential | `routers/streams.py:80-110` |
| S3 | **Critical** | **`GET /streams/{id}/token` requires no authentication** and mints a LiveKit subscribe token for any live stream. Anyone who can enumerate a stream id can watch any tenant's private broadcast | `routers/streams.py:283-326` |
| S4 | **High** | `channels` and `streams` have **no `org_id`** — the entire legacy streaming subsystem is outside `org_scoped()`. `channels.slug` is globally unique, so tenants collide and are enumerable | `models/channel.py`, `models/stream.py` |
| S5 | **High** | `SECRET_KEY` defaults to `"dev-secret-change-me"`. If `.env` is absent or the key is unset in prod, **anyone can forge a valid JWT for any user id and role** | `config.py:13` |
| S6 | **High** | **4-digit OTP = 10,000 combinations, no attempt counter, no lockout, no rate limit.** The 10-minute TTL is the only guard (acknowledged at `auth.py:112`). Reset tokens are stored **plaintext** in `users.reset_token` | `auth.py:100-141` |
| S7 | **High** | **No rate limiting on `/auth/login`** (or any HTTP endpoint). The only limiter in the codebase is per-WebSocket-connection | `auth.py:79`, `live.py:49` |
| S8 | **High** | `/dashboard/*` endpoints return **HTTP 200 with `{"error": "Unauthorized"}`** instead of raising 403. Any client that checks status codes rather than body shape treats the denial as success | `api/dashboard.py:41-45,75,96` |
| S9 | **High** | `CORSMiddleware` `allow_origin_regex=r"https?://(localhost\|127\.0\.0\.1)(:\d+)?"` with `allow_credentials=True`, **applied in production too**. Any page served from localhost may make credentialed cross-origin calls to the prod API | `main.py:53` |
| S10 | Medium | `/host/dashboard` and `/moderator/dashboard` have **no `RoleRoute`** — any authenticated user loads the console shell. Data access is correctly refused by the socket, so this is UI exposure, not data exposure | `App.jsx:120,124` |
| S11 | Medium | **JWT in the WebSocket query string** — ends up in proxy/access logs, browser history, and `Referer`. Unavoidable with the browser WS API, but a short-lived ticket exchange is the standard mitigation | `live.py:80`, `useEventStream.js:25` |
| S12 | Medium | **LiveKit tokens have no TTL** — `AccessToken` is built with no `with_ttl`, so it uses the SDK default rather than a deliberate short lifetime, and there is no revocation | `services/livekit.py:34-44` |
| S13 | Medium | **Token in `localStorage`** → readable by any XSS. No `httpOnly` cookie option, no CSP in `index.html` | `api.js:9`, `AuthContext.jsx:21` |
| S14 | Medium | **No password-change or role-change invalidation.** A 30-day "remember me" JWT survives a password reset and a demotion. `is_active=False` is the only kill switch | `security.py:39` |
| S15 | Medium | `Event.visibility` (`public`/`private`/`unlisted`) is stored and **enforced nowhere** | `models/event.py:52` |
| S16 | Medium | `Organization.status == "suspended"` is documented as "blocks the org platform-wide" but **no code checks it** — suspended orgs keep full access | `models/organization.py:16` |
| S17 | Medium | `Organization.security` JSON (MFA, SSO, IP allowlist, session policy) is writable by org admins and **read by nothing** — the settings page implies enforcement that does not exist | `models/organization.py:59` |
| S18 | Low | `_ip(request)` uses `request.client.host` — **behind a proxy every audit row records the proxy IP**; no `X-Forwarded-For` handling | `routers/admin.py:41` |
| S19 | Low | No per-user Q&A vote ledger → trivial vote stuffing (acknowledged at `moderation.py:547`) |
| S20 | Low | Bans are keyed on LiveKit `identity` (= user id) held in Redis/memory with **no TTL and no persistence** — a Redis flush or restart clears every ban | `bus.py:266-285` |
| S21 | Low | `email.py` templates escape user input (verified by the module's own self-check), but `_invite_url` interpolates the token into HTML unescaped — safe today because `token_urlsafe` is alphanumeric+`-_` | `email.py:120-136` |
| S22 | Low | `/live/webhooks/livekit` is correctly signature-verified and 503s when unconfigured — **this one is done right**, noted so it isn't "fixed" | `live.py:214-224` |
| S23 | Info | No security headers (HSTS, CSP, X-Frame-Options, X-Content-Type-Options) on either tier |
| S24 | Info | Org `api_keys` are hashed and prefixed by `crud/admin.create_api_key`, but **no endpoint authenticates with them** — an unused credential surface |

---

## 15. Scalability Risks

| # | Risk | Detail |
|---|---|---|
| C1 | **Background tickers run per process.** `main.py:32` starts `run_scheduler()` and `run_sampler()` in `lifespan`. With N uvicorn workers, a scheduled poll fires N times and N analytics rows are written per interval. Acknowledged at `main.py:30-31` and `moderation.py:955-957`, with **no lock and no leader election** — this is the hard blocker on horizontal scaling |
| C2 | **Sync SQLAlchemy under an async socket server.** Mitigated correctly via `tx()` → `to_thread` with short-lived sessions (the reasoning at `moderation.py:10-13` is sound), but the thread pool and the 15-connection default pool cap concurrency well below the stated 10k-viewer / 100-moderator target |
| C3 | **`bus.local_subscribers()` counts only THIS worker's connections.** Announcement `delivered_to` is therefore wrong the moment there is more than one worker — honestly labelled ("the real connection count at send time", `models/live.py:95`) but incorrect at scale |
| C4 | **Redis Pub/Sub has no persistence and no replay.** A worker restart drops in-flight envelopes; recovery relies entirely on the client reconnecting and pulling a fresh snapshot. That works, but it means every restart triggers a full-snapshot stampede |
| C5 | **`QUEUE_MAX = 200` with drop-oldest.** A slow client silently loses envelopes with no counter, no metric, and no log line — a dropped `message.delete` leaves a deleted message visible until reconnect |
| C6 | **The presence store is a single Redis hash per event** (`HGETALL` on every snapshot). At 10k participants that is a 10k-field hash read on every connect and on every 15s sample |
| C7 | **`LiveRecording.size_bytes` is `Integer`** → Postgres `INTEGER`, max 2,147,483,647 ≈ **2 GB**. A 1080p multi-hour recording overflows. `BroadcastSession.paused_ms` and `LiveRecording.paused_ms` are also `Integer` (~24 days — fine) |
| C8 | **No connection cap per event and no admission control.** `bus.subscribe` grows an unbounded set; nothing rejects the 10,001st socket |
| C9 | **No horizontal-scale story for LiveKit.** One room per event, `empty_timeout=600`, no region selection (`Organization.region` is stored and unused), no simulcast/ABR ladder, no CDN or edge distribution (`platform_health` reports CDN "not yet integrated") |
| C10 | **Recording is composite room egress only** — one process per room, no distributed egress pool, no queue, no retry. A failed egress is recorded as `enforced=False` and never retried |
| C11 | **Unbounded table growth with no lifecycle policy**: `analytics_snapshots`, `live_activity` (append-only), `live_messages` (soft-deleted rows retained for compliance), `audit_logs` (append-only, indexed on `created_at` only) |
| C12 | **Schema evolution does not scale.** No Alembic versions; every column on a pre-existing table needs a hand-written `ADD COLUMN IF NOT EXISTS` in `create_tables.py`. There is no downgrade path and no migration history |
| C13 | **Frontend list screens fetch `page_size=100` and paginate client-side** — the UI silently misrepresents any org with >100 events or members |
| C14 | **No observability.** No metrics, no tracing, no structured logging, no health check beyond `/health` returning a static `{"status":"ok"}` (it does not touch the DB). `platform_health` does ping the DB, but it is super-admin-only |
| C15 | **Single-region, single-database.** No read replicas, no caching layer for reads (Redis is used only for pub/sub and presence) |

---

## 16. Technical Debt

**Architectural**

1. **Three router locations** — `app/auth.py` (root), `app/api/dashboard.py`, `app/routers/*`. Two of the three are one-file exceptions.
2. **`api/dashboard.py` is a legacy parallel implementation** with hardcoded values (`upcoming_events: 5`, `live_events: 1`, `completed_events: 28`, `total_viewers: 12530` at `:60-64`; `live_events: 3`, `total_viewers: 84120` at `:83-84`) — and `pages/organization/Dashboard.jsx:27` renders them as if real. `services/admin.py` proves the team knows how to do this honestly; this module predates that standard.
3. **Two streaming subsystems** — `channels`+`streams` (`stream_<id>` rooms, owner-based auth, no org_id) vs `events`+`broadcast_sessions` (`event_<id>` rooms, org+assignment auth). `live.py:173` explicitly flags the overlap. The `streams` API is what `/admin/live-events` and `services/admin._streaming_hours` read, so it cannot simply be deleted.
4. **Alembic is scaffolding only** — no versions, `target_metadata = None`. Schema truth lives in `create_tables.py`'s hand-written ALTER list.
5. **`openapi.json` is committed and 93% stale** (6 of ~86 paths) with no generation script.
6. **No CI** — no `.github/`, no test runner config. The 8 `test_*.py` files are `python test_x.py` self-checks (well-written, genuinely useful, but nothing runs them automatically). Frontend has **zero** tests.
7. **`requirements.txt` has no version pins** — 17 unpinned packages, no lockfile. `aiofiles`, `pillow`, `python-multipart` are unused.

**Backend**

8. `routers/streams.py` and `routers/channels.py` are stylistically alien to the rest of the codebase — no docstrings, no org scoping, unusual formatting, `str` path params instead of `uuid.UUID`, raw `HTTPException(403, "...")` instead of `status.HTTP_*`.
9. `schemas/auth.py` holds Channel schemas that are then shadowed by `schemas/channel.py` (B1). Star-imports in `schemas/__init__.py` make the shadowing invisible.
10. Stale comment: `main.py:44-45` says "create_all here would miss the Stream model, which models/__init__.py doesn't register" — `models/__init__.py:4` **does** import `Stream`.
11. `services/broadcast.py` is 840 lines spanning lifecycle + recording + stage + analytics + sampler + UA parsing; `services/moderation.py` is 991 lines spanning detection + context + serializers + tx + 30 handlers + dispatcher + scheduler. Both are coherent and well-documented, but they are the two largest files in the repo.
12. `broadcast.py:111` `_iso` wraps a private from another module (`mod._iso`) — reaching across a `_`-prefixed boundary.
13. `moderation.py:989` imports `logging` **inside** an exception handler.
14. `SNAPSHOT_EXTRAS` / `mod.ACTIONS.update()` **import-time registration** — clever and documented (`broadcast.py:812-840`), but it means importing `routers/live.py` has side effects and the dispatcher's contents depend on import order.
15. `Ctx.actor` returns a `SimpleNamespace` faking a `User` for `create_audit_log` (`moderation.py:112-115`), and passes `email=self.name` — **audit rows for live actions carry a display name in the `actor_email` column**.
16. No status/role enums at the DB level — 8 tuples of magic strings kept in sync by convention.

**Frontend**

17. **`auth/dummy.js` is still wired into a real page** — `AcceptInvitation.jsx:56`. `AuthContext.jsx:7-9` still carries its "dummy auth, no backend" docstring even though login/register are fully real.
18. **`data/events.js` is load-bearing** — six real feature files import `fmtDate` from a mock module. Deleting the mocks will break working screens.
19. `MainLayout.jsx` duplicates the shell and hardcodes "245 GB / 1 TB", a "6" notification badge, and "Zoiko Industries / Organization Admin" (`:81-83,133,146-147`).
20. `pages/Dashboard.jsx` (253 lines, all mock) is reachable at `/dashboard` for `speaker` and unknown roles.
21. `useApi` has no cache/dedupe/abort (deliberate, documented at `hooks/useApi.js:10-11`) — fine now, a real constraint as screens multiply.
22. 5 hooks + `utils/export.js` + 6 backend files are **untracked in git** — a substantial amount of working code is uncommitted.
23. `data/moderation.js` `ACT_ICON` must stay in sync with `ACTIVITY_KINDS` in `server/app/models/live.py:30` — a cross-stack coupling documented in a comment with no test.
24. `AuthLayout` says © 2026 while `EventWatch.jsx:89` says © 2024.
25. Dark mode is inconsistent: `MainLayout` and `pages/Dashboard` have **no `dark:` variants** at all.
26. 7 empty scaffold directories remain.
27. `index.css` remaps the Tailwind `emerald`/`teal` scales to the brand purple/pink — components written with `emerald-*` render on-brand **by design**. Anyone "fixing the green" breaks the brand. Documented in `docs/frontend-audit-phase0.md:35`; worth a comment in `index.css` itself.

**Positive debt-avoidance worth preserving** (these are the codebase's real strengths — do not undo them):
`security.org_scoped()` as a single isolation point · `require_min_role` as a composable ladder · one WS + one dispatcher + one audit path · `enforced` booleans everywhere instead of pretending LiveKit succeeded · honest `null` + `note` instead of fabricated telemetry · soft deletes that preserve compliance data · invitation tokens stored hashed · pure, unit-testable lifecycle guards (`status_transition_error`, `chat_gate`, `slow_mode_error`, `clean_settings`, `engagement_score`) · `ponytail:` comments that name the ceiling *and* the upgrade path.

---

## 17. Recommended Build Order

Correctness first, then the product's reason to exist, then scale.

**Phase A — stop the bleeding (hours, not days)**
1. Rotate the leaked super-admin credential; remove the literal from `seed.py` (read from env, or generate and print once).
2. Fix `GET /admin/roles` and `GET /admin/live-events` — three admin screens are 500ing.
3. Fix the invitation loop: align the emailed path with the route, and make `AcceptInvitation.jsx` post the token to `POST /organization/invitations/accept`. Delete `auth/dummy.js` in the same commit.
4. Authenticate + org-scope `GET /streams/` and `GET /streams/{id}/token`; stop returning `stream_key` in list responses.
5. Make `/dashboard/*` raise 403 instead of returning 200-with-error.
6. Require a non-default `SECRET_KEY` at startup when not in dev.
7. Drop the localhost CORS regex in production (gate it on an env flag).
8. Commit the 11 untracked working files.

**Phase B — foundations (before any new feature)**
9. Adopt Alembic for real: `target_metadata = Base.metadata`, one baseline revision stamping current state, then migrations only. Retire `update_stream_table.py` and freeze `create_tables.py`'s ALTER list.
10. Set `pool_size` / `max_overflow` deliberately in `db.py` and cap the `to_thread` executor. This is P1.
11. Add CI: run the 8 `test_*.py` self-checks + `npm run lint && npm run build`. Regenerate `openapi.json` in CI or delete it.
12. Rate-limit `/auth/login`, `/auth/forgot-password`, `/auth/reset-password`; add an OTP attempt counter (5 tries) and hash `reset_token`.
13. Add DB constraints for the rules the code claims: unique org slug, unique `(org_id, slug)` on events, one active subscription per org.
14. Enforce `Organization.status == "suspended"` and `Event.visibility` — stored-but-unenforced access rules are worse than absent ones.

**Phase C — make it a streaming product**
15. **Storage.** Configure egress output (GCS/S3) and add a `file_url` that is actually fetchable. Nothing downstream works without this.
16. **Add `livekit-client` to the frontend.** Wire the publisher into `StudioStage` using the `publish_token` the server already mints. Both call sites in `broadcast.py` say this is a drop-in — collect on that.
17. **Wire the viewer.** Replace `components/watch/VideoPlayer.jsx`'s dummy surface with a real subscriber; give `EventWatch` the socket instead of `data/watch.js`.
18. **Recordings API.** `GET /recordings` (org-scoped), signed download URLs, replay. Retire `data/recordings.js`.
19. **Anonymous/public viewer tokens** for `public` events, respecting `visibility` and `registration_required`.
20. **Public registration** — `registrations` table + `POST /events/{id}/register`, honouring `registration_limit`. Retire `pages/EventRegistration.jsx`'s mocks.
21. **Org analytics API** over `analytics_snapshots`. Retire `data/analytics.js`.
22. Split `stage.camera` from `stage.share` properly (they currently share one `can_publish` bit).
23. Widen `LiveRecording.size_bytes` to `BigInteger`.

**Phase D — scale**
24. Move the two tickers out of `lifespan` into a single leader (Redis lock) or a dedicated worker process. This unblocks multi-worker deployment (C1).
25. Fix the analytics query shape (P2): counter columns or incremental aggregation instead of re-summing every reaction blob every 15s. Add retention/rollup for `analytics_snapshots`.
26. Make `delivered_to` cross-worker (Redis counter, not `local_subscribers`).
27. Server-side pagination on the frontend list screens; lazy-load the admin/org route bundles and recharts.
28. Observability: structured logs, metrics on dropped bus envelopes and pool checkout waits, a `/health` that actually checks the DB.
29. Refresh tokens + a revocation list; invalidate on password and role change.

**Phase E — remaining product surface**
30. Billing + payment provider + usage metering + plan-limit enforcement.
31. Asset upload (this is what `pillow` / `aiofiles` / `python-multipart` were installed for).
32. Notification dispatcher; MFA/SSO/IP-allowlist enforcement; DNS domain verification; outbound webhooks; API-key authentication.
33. Frontend cleanup: retire `MainLayout` + `pages/Dashboard` + `components/Dashboard/*` once speaker/viewer have real homes; extract `lib/` helpers; delete the mocks as each endpoint lands; remove dead deps and the 7 empty dirs.

---

## 18. Files That Should NEVER Be Modified (without a very deliberate, reviewed reason)

These are load-bearing single points of truth. A careless edit here is a security or data-integrity incident, not a bug.

| File | Why |
|---|---|
| `server/app/security.py` | The **entire** authN/authZ surface: hashing, JWT issue/verify, `get_current_user`, the role ladder, and `org_scoped()` — the one place tenant isolation lives. Every endpoint depends on it. Changes require the full `test_authz.py` pass plus review. |
| `server/app/db.py` | Engine, session factory, `Base`. Every model and every request depends on it. (Pool tuning is a *deliberate* change — see Phase B10 — not a casual one.) |
| `server/app/config.py` | Settings contract for both tiers (`vite.config.js` shares the same `.env`). Adding a field is safe; changing a name or a default silently breaks deploys. |
| `.env` | Live secrets, gitignored. Never commit, never paste, never log. |
| `server/app/models/*.py` | Schema truth. With no working Alembic, an edit here has **no migration path** — a changed column is a manual production ALTER. Treat every change as a schema migration proposal. |
| `server/app/services/bus.py` | The realtime substrate. Every console, every webhook, and both tickers publish through it. Its dual-mode (Redis / in-process) contract must hold in both branches or the app breaks only in production. |
| `server/app/services/moderation.py` — `dispatch`, `resolve_ctx`, `tx`, `_row`, `_scoped` | The permission gate and the org-isolation gate for ~50 realtime mutations. `_row`/`_scoped` are the only thing stopping an id from the wire reaching another tenant's row. |
| `server/app/crud/event.py` — `status_transition_error` | The event lifecycle contract, relied on by `events.py`, `broadcast._golive` and `broadcast._end`. Pure and tested — keep it that way. |
| `server/app/routers/organization.py` — `get_my_org` / `get_my_org_admin` | The single binding point where `org_id` comes from the JWT rather than the request. Bypassing this is a cross-tenant write. |
| `server/create_tables.py` | The de-facto migration tool. Its ALTER list is append-only history; **reordering or removing a clause breaks existing databases.** |
| `server/seed.py` | Bootstrap identity. Needs the credential fix (Phase A1) and then should be left alone. |
| `client/src/api.js` | The single axios instance + auth interceptor + `errMsg`. Its docstring records a real crash (non-string `detail` → blank screen). |
| `client/src/auth/AuthContext.jsx`, `components/RoleRoute.jsx`, `components/ProtectedRoute.jsx` | Client-side session and route gating. Small files, total blast radius. |
| `client/src/index.css` | Tailwind v4 `@theme` + the **emerald/teal → brand remap**. "Fixing the green" breaks the brand everywhere. |
| `client/src/hooks/useEventStream.js` | Reconnect/backoff/jitter/heartbeat/resync. This file is why "never refresh the page" is true; naive edits cause reconnect storms. |
| `openapi.json` | Do not hand-edit. Either regenerate it from the app or delete it. |

---

## 19. Files Safe to Extend

**Backend — additive by design**

| File | How to extend |
|---|---|
| `server/app/routers/admin.py` | Add a route + a `_audit(...)` call. Router-level `require_super_admin` already gates everything. |
| `server/app/routers/events.py` | Add routes using `_get_event_or_404` + `require_org_admin` / `_can_edit`. |
| `server/app/routers/organization.py` | Add routes using `get_my_org` / `get_my_org_admin`. Isolation is inherited. |
| `server/app/crud/{admin,event,organization}.py` | Pure query/update functions. No HTTP, no aggregation. Add freely. |
| `server/app/services/admin.py` | Add derived/aggregate readers. **Also where the missing `roles()` belongs.** Keep the "no fabricated numbers" rule. |
| `server/app/services/broadcast.py` | New host actions: write a handler, add it to the local `ACTIONS` dict; `HOST_ONLY` picks up `broadcast.*`/`recording.*` prefixes automatically. New snapshot data → extend `snapshot_extra`. |
| `server/app/services/moderation.py` | New moderator/viewer actions: add to `ACTIONS`, and to `VIEWER_ACTIONS` only if truly unprivileged. New content heuristics go behind `flag_text`. |
| `server/app/services/livekit.py` | New room/egress helpers following the `_with_room` pattern — always return whether it was enforced. |
| `server/app/schemas/*.py` | Add models. (Fix the `auth.py`/`channel.py` collision first — B1.) |
| `server/app/models/live.py` | New live tables: subclass `_EventScoped` and you inherit id/event_id/org_id/created_at and the isolation contract for free. |
| `server/app/email.py` | New template: a `_x_html()` builder using `_shell` + `_header`, a `send_x_email()`, and an assertion in the `__main__` self-check. Always `html.escape` user input. |
| `server/test_*.py` | Add assertions. Pure-logic, no-DB, no-fixture style — the cheapest safety net in the repo. |

**Frontend — additive by design**

| File / dir | How to extend |
|---|---|
| `client/src/ui/` (+ `forms/`, `charts/`) | The design system. Add a primitive, export it from `index.js`. |
| `client/src/ui/tokens.js` | Add an accent/status entry — one place, propagates everywhere. |
| `client/src/hooks/` | Add hooks. `useMutation` (the ~15× toast/try/catch pattern) is the obvious next one. |
| `client/src/utils/export.js` | The home for the `lib/` helpers Phase E33 calls for (`initials`, `isEmail`, formatters, `downloadBlob`). |
| `client/src/pages/**` | New screens. Follow `organization/Events.jsx`: `useApi` + `OrganizationPageHeader` + `EmptyState` / `ErrorState` + `DataTable`. |
| `client/src/components/{admin,organization,host,moderation,watch}/` | Feature components. `admin/index.js` is the shared barrel. |
| `client/src/hooks/useLiveEvent.js` — the `reducer` | Add a `"channel/type"` case. The `default: return state` means an unhandled envelope is inert, so adding server channels is safe. |
| `client/src/App.jsx` | Add routes. Replace `Placeholder` stubs with real pages one at a time. |
| `client/src/data/*.js` | **Delete-only.** Remove a mock as its endpoint lands — but move `fmtDate`/`initials` into `utils/` first (F5). |
| `docs/` | Add documents. This file and `frontend-audit-phase0.md` live here. |

---

## 20. Overall Architecture Score

# 68 / 100

| Dimension | Weight | Score | Rationale |
|---|---:|---:|---|
| Architecture & separation of concerns | 15 | **13** | Clean router → service → crud → model layering. FastAPI DI used properly (`get_db`, `get_current_user`, `require_*`, `get_my_org`). The one-socket/one-dispatcher realtime design is genuinely well-reasoned and documented. −2 for three router locations and two parallel streaming subsystems. |
| Security | 20 | **9** | The *model* is good: single isolation point, composable role ladder, DB-not-token role reads, hashed invitation tokens, signature-verified webhooks, no account enumeration. But a **committed super-admin password**, **two unauthenticated stream endpoints leaking cross-tenant publish keys and tokens**, a **default `SECRET_KEY`**, a 4-digit unthrottled OTP, and 200-instead-of-403 denials are all live. Great design, unshipped hardening. |
| Data model & integrity | 15 | **10** | Thoughtful: denormalized `org_id` on live tables with a stated reason, soft deletes for compliance, append-only audit with no FK so it outlives actors, `_EventScoped` base. −5 for **no working migrations**, uniqueness rules enforced only in Python, no enums/CHECKs, no `org_id` on channels/streams, `size_bytes` as `INTEGER`. |
| Realtime & streaming | 15 | **10** | The realtime layer is the best-engineered part of the repo: multiplexed channels, bounded queues, ref-counted Redis pump, presence keyed to LiveKit identity, bans that outlive presence, backoff+jitter, full-snapshot resync, per-socket rate limiting, honest `enforced` booleans. But **there is no browser media path and no storage** — the streaming product does not stream. |
| Scalability | 10 | **5** | Correct instincts throughout (`tx()` off the event loop, hot state in the bus not Postgres, Redis-bridged fan-out) and every ceiling is *documented*. But per-process tickers block multi-worker deploys, the pool is at defaults, the analytics query shape is O(messages) per 15s, and no table has a retention policy. |
| Frontend architecture | 10 | **8** | One consolidated `ui/` design system with tokens/forms/charts, `AppShell` render-prop pattern, a single well-designed reducer shared by both consoles, clean `RoleRoute`/`ProtectedRoute`. −2 for the surviving legacy shell, `data/*` still load-bearing, `dummy.js` still wired, no code splitting beyond `Home`. |
| Code quality & documentation | 10 | **9** | Unusually high. Nearly every non-obvious decision carries a comment explaining *why*, `ponytail:` markers name both the shortcut and its upgrade path, and the "no invented telemetry" rule is held consistently (`countries: null` with a reason rather than a plausible map). −1 for two 800+ line services and a handful of stale comments. |
| Testing & tooling | 5 | **2** | 8 well-written pure self-checks covering the role ladder, org isolation, content detection, the dispatcher permission table, and the bus. But **nothing runs them**, there is no CI, no frontend tests, no pinned dependencies, and a stale committed `openapi.json`. |
| **Total** | **100** | **68** | |

**Read of the number.** This is a codebase with **senior-level design judgement and junior-level operational maturity.** The hard parts — tenant isolation, the realtime substrate, the permission model, honest failure reporting — are done thoughtfully and documented better than most production systems. What is missing is the unglamorous half: migrations, CI, secret hygiene, pinned deps, rate limits, and the two authentication checks that were never added to the oldest router.

Two facts dominate any plan:

1. **The product does not yet stream.** There is no `livekit-client` in the browser and no storage backend for egress. Everything around media — tokens, room control, recording rows, health, analytics, the entire host console — is built and waiting. The gap is narrow but absolute.
2. **Five defects are live right now**: a leaked super-admin credential, two unauthenticated cross-tenant stream endpoints, two 500ing admin endpoints, and a fully broken invitation acceptance flow.

Fix Phase A, adopt Alembic and CI in Phase B, and this scores in the low 80s without a single new feature.

---

*End of audit. No code was modified. No components were generated. Nothing was refactored.*
