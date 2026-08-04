# ZoikoStream — Complete Architecture Audit

**Date:** 2026-07-31 · **Branch:** `harishreddy` · **Scope:** whole repo (263 tracked files + 4 untracked working files)
**Status:** READ-ONLY. No code was modified, generated, or refactored. This document is the only artifact produced.
**Method:** full read of `server/app/**` (9 routers, 8 services, 17 model modules, 5 crud modules, 6 schema modules, config/security/db/email/ratelimit), `client/src/**` (routing, auth, layouts, 8 hooks, all pages, watch/host/moderation components), `create_tables.py`, `seed.py`, `.gitignore`, `openapi.json`, `requirements.txt`, `package.json`. All 10 backend self-check suites were executed. Every claim carries a `file:line`.

**Supersedes** `docs/architecture-audit.md` (2026-07-29, scored 68/100). That document is now stale in 9 places — §0 below lists exactly what changed, because a stale audit is more dangerous than none.

> **ADDENDUM (same day, post-audit):** the Enterprise Event Management module was implemented immediately after this audit was written, which closes some findings below. Superseded items, all verified by `server/test_event_module.py` (22 DB-backed checks) and `server/test_events.py`:
> Phase 2 — **Duplicate Events** 🔴→✅ (`POST /events/{id}/duplicate`) · **Edit Events** 🟡→✅ (`EventFormModal` covers every field) · **Cancel Events** 🟡→✅ (lifecycle action on the detail page).
> Phase 3 — event statuses now include **`paused`**; `EVENT_VISIBILITY` gained **`invite_only`**; `events` gained `replay_enabled`, `stream_quality`, `max_participants`, `access_password_hash`.
> Phase 4 — `ASSIGNMENT_ROLES` extended to six (**producer / cohost / panelist** added, credited-only by design); add/remove/replace all exposed; `GET /events/{id}/team` returns all roles in one request.
> Phase 13 — **viewer links now exist**: `event_access_links` (hash-only token, expiry, revoke, rotate) is the second revocable/opaque/expiring link in the platform, and the first event-facing one. The old `/e/{slug}` dead link is replaced by a server-generated `/events/{id}/watch?token=…`.
> Phase 16 — **P8** (client-side pagination truncating at 100 rows) fixed for events: `GET /events` filters/sorts/pages server-side and `DataTable` gained opt-in server mode. **P10** improved: the org console is now lazy-loaded (main bundle 1,029 kB → 811 kB). **F6** (the ~15× toast/try/catch block) now has `hooks/useMutation.js`.
> Still open and unchanged: the leaked `seed.py` credential, the broken invitation acceptance flow, the missing browser publisher, egress storage, the registrations table, and everything in the roadmap's Phase A/B.

**Counts (measured, not estimated):** 98 HTTP routes + 1 WebSocket + `/health` · 27 database tables · 49 socket actions (32 moderation + 17 broadcast) · 12 logical bus channels · 6 platform roles · 8 backend self-check suites.

---

## §0 — What changed since the 2026-07-29 audit

| Prior finding | Status today | Evidence |
|---|---|---|
| S2/S3 **Critical**: `GET /streams/` and `/streams/{id}/token` unauthenticated, leaked `stream_key` cross-tenant | ✅ **FIXED** | `routers/streams.py:60,124` both `Depends(get_current_user)`; list is owner-scoped; `stream_key` confined to single-stream owner reads |
| `GET /admin/roles` → 500 (`AttributeError`) | ✅ **FIXED** | `services/admin.py:170` `roles()` now exists, derived from `_ROLE_RANK` |
| `GET /admin/live-events` → 500 (`TypeError`) | ✅ **FIXED** | `services/admin.py:187` `live_events(db, state="live")` |
| S8: `/dashboard/*` returned 200 with `{"error":"Unauthorized"}` + hardcoded viewer counts | ✅ **FIXED** | `routers/dashboard.py:29,72,97` real `require_min_role`/`require_super_admin`; counts are `SUM(BroadcastSession.peak_viewers)` |
| S7: no HTTP rate limiting | ✅ **FIXED** | `ratelimit.py` + `routers/auth.py:31-34` (login 10/60s, OTP request 5/300s, OTP verify 10/300s, register 5/300s) |
| P1: DB pool at SQLAlchemy defaults (15 conns) vs unbounded `to_thread` executor | ✅ **FIXED** | `db.py:17-28` pool 20+10; `main.py:45` executor bounded to `DB_MAX_CONNECTIONS` |
| B1: `ChannelCreate` declared twice, silently shadowed by star-import | ✅ **FIXED** | `schemas/__init__.py` now explicit names + `__all__` |
| Three router locations (`app/auth.py`, `app/api/`, `app/routers/`) | ✅ **FIXED** | single `app/routers/` package |
| Dead components (`PlatformAnalytics`, `SecurityCenter`, `TrendStat`, `DashboardCard`), 7 empty scaffold dirs, `app/utils/` | ✅ **DELETED** | absent from `git ls-files`; `find -type d -empty` returns nothing |
| "Watch page is mock, no browser media path (viewer)" | ✅ **BUILT** | `components/watch/VideoPlayer.jsx:96` real `livekit-client` subscriber; `services/viewer.py` + `GET /events/{id}/viewer`, `/playback` |
| S15: `Event.visibility` stored and enforced nowhere | ✅ **ENFORCED** (viewer path) | `services/viewer.py:55-73` `access_for`; used by `routers/events.py:155` and `services/moderation.py:130` |
| S1 **Critical**: super-admin password committed | 🔴 **STILL LIVE** | `seed.py:127` `hash_password("NoxxMC26070%!LGM")`, echoed at `:137` |
| Invitation acceptance broken | 🔴 **STILL LIVE**, and now confirmed broken in three independent ways | §5 |
| No host publish path, no storage, no recordings API | 🔴 **STILL LIVE** | §9, §10 |

---

# PHASE 1 — PROJECT ARCHITECTURE

## 1. Overall system architecture

```
┌───────────────────────────────────────────────────────────────────────────────┐
│ BROWSER — Vite 8 SPA, React 19, Tailwind v4, react-router 7                   │
│  MarketingLayout │ AuthLayout │ AdminLayout │ OrganizationLayout │ MainLayout  │
│  standalone: /host/dashboard  /moderator/dashboard  /events/:id/watch  /e/:id  │
└───────────────────────────────────────────────────────────────────────────────┘
       │ axios + Bearer(localStorage)        │ raw WebSocket ?token=jwt   │ WebRTC
       │ src/api.js                          │ hooks/useEventStream.js    │ (viewer only)
       ▼                                     ▼                            ▼
┌──────────────────────────────────────────────────────────────┐      ┌──────────┐
│ FastAPI — server/app/main.py                                 │      │ LiveKit  │
│  CORSMiddleware → measure_requests → OperationalError→503     │      │  Cloud   │
│  lifespan: 3 background tickers + bounded thread executor     │◄─────┤ webhooks │
├──────────┬──────────┬─────────┬─────────┬────────┬───────────┤      └──────────┘
│ auth     │ organiza-│ events  │ admin   │ live   │ streams   │
│ /auth/*  │ tion/*   │ /events │ /admin/*│ WS +   │ channels  │
│ 6 routes │ 24       │ 13      │ 38      │ webhook│ dashboard │
├──────────┴──────────┴─────────┴─────────┴────────┴───────────┤
│ security.py — HTTPBearer → JWT HS256 → get_current_user       │
│               require_min_role ladder · org_scoped()          │
├───────────────────────────────────────────────────────────────┤
│ services/  admin · ops · org · viewer · livekit · bus ·        │
│            moderation (dispatcher) · broadcast (host domain)   │
│ crud/      admin · organization · event · channel · stream      │
├───────────────────────────────────────────────────────────────┤
│ models/  SQLAlchemy 2.0 DeclarativeBase — 27 tables            │
│ db.py    sync engine, pool 20+10, pre_ping, recycle 1800        │
└───────────────────────────────────────────────────────────────┘
   │                │                  │                 │
   ▼                ▼                  ▼                 ▼
┌────────┐   ┌──────────────┐   ┌──────────────┐   ┌──────────┐
│Postgres│   │ Redis        │   │ LiveKit API  │   │ Resend   │
│Supabase│   │ pub/sub,     │   │ token, room  │   │ email    │
│REQUIRED│   │ presence,    │   │ ctl, egress  │   │ OPTIONAL │
│        │   │ state, bans  │   │ OPTIONAL     │   │          │
└────────┘   │ OPTIONAL     │   └──────────────┘   └──────────┘
             └──────────────┘          │
                                 ┌─────▼──────┐
                                 │  Storage   │  ✗ NOT INTEGRATED
                                 │ (S3/GCS)   │  egress has no output bucket
                                 └────────────┘
```

**Every external service except Postgres degrades rather than fails.** `livekit.configured()`, `bus.redis()`, `email._send()` each return a falsy/no-op path when unset, and the degradation is *reported* (`livekit_enforced`, `enforced`, `encrypted`) rather than hidden. This is the single best architectural decision in the repo.

## 2. Folder structure

```
ZoikoStream/
├─ client/src/
│  ├─ api.js                  single axios instance + Bearer interceptor + errMsg()
│  ├─ App.jsx                 all routes, lazy boundaries, Placeholder stubs
│  ├─ auth/                   AuthContext.jsx (localStorage), roleHome.js, dummy.js ⚠
│  ├─ components/
│  │  ├─ admin/               + sections/ (10 Command Center sections), index.js barrel
│  │  ├─ organization/        console sections + orgScope.js (topbar filter context)
│  │  ├─ host/                ControlBar, StudioStage, HostPanel, HostHeader, FeatureModal
│  │  ├─ moderation/          ParticipantsPanel, ChatQAPanel, ModeratorSidebar
│  │  ├─ watch/               VideoPlayer, EventInfo, WatchPanel   ← real, LiveKit
│  │  ├─ home/                14 marketing sections
│  │  └─ Dashboard/           legacy shell parts (Sidebar, Topbar, QuickActions)
│  ├─ data/                   9 mock modules — still load-bearing for 6 screens
│  ├─ hooks/                  useApi, useEventStream, useLiveEvent, useViewerEvent,
│  │                          usePlaybackCheck, useMediaPreview, useInterval,
│  │                          useSearch, useSystemStats
│  ├─ layouts/                5 layouts (3 distinct shells — see F1)
│  ├─ pages/                  admin/(14) organization/(9) auth/(4) host/ moderator/
│  │                          watch/ Home/ Dashboard.jsx EventRegistration.jsx
│  ├─ ui/                     design system: tokens, 20 primitives, forms/, charts/
│  └─ theme/                  ThemeContext (class-based dark mode)
├─ server/
│  ├─ app/
│  │  ├─ main.py config.py db.py security.py email.py ratelimit.py
│  │  ├─ routers/   auth organization events admin live streams channels dashboard
│  │  ├─ services/  admin ops org viewer livekit bus moderation broadcast
│  │  ├─ crud/      admin organization event channel stream
│  │  ├─ models/    17 modules → 27 tables
│  │  └─ schemas/   auth admin event organization channel stream
│  ├─ migrations/   Alembic scaffolding — NO versions/ dir, target_metadata=None ⚠
│  ├─ create_tables.py   de-facto migration tool (append-only ALTER list)
│  ├─ seed.py            ⚠ hardcoded super-admin credential
│  └─ test_*.py          8 self-check suites (+ test.py DB probe)
├─ docs/            frontend-audit-phase0.md, architecture-audit.md, THIS FILE
└─ openapi.json     ⚠ 6 of 99 paths — 94% stale, no generation script
```

## 3. Layer-by-layer

| Layer | Implementation | Assessment |
|---|---|---|
| **Frontend arch** | Route-level code splitting for `Home`, `EventWatch`, and the 14 admin pages (`App.jsx:31-55`); `livekit-client` split again inside `VideoPlayer` | Good. Org console pages + recharts are still eager |
| **State management** | No global store. `useReducer` per live console (`useLiveEvent.js:57`), `useApi` for reads, React context for auth + theme + org scope | Correct call — the only shared mutable state is the live socket, and it has one reducer |
| **Routing** | `react-router-dom` 7, `ProtectedRoute` (session) + `RoleRoute` (role allowlist) as `<Outlet>` gates | Sound. Two routes bypass it — §11 |
| **Middleware** | `CORSMiddleware`, `measure_requests` (feeds `ops.request_stats`), `OperationalError → 503 with CORS headers` (`main.py:106`) | The 503 handler is a subtle, correct fix for a real "Network Error" bug |
| **Dependency injection** | FastAPI `Depends` throughout: `get_db`, `get_current_user`, `require_min_role(...)`, `require_super_admin`, `get_my_org`, `get_my_org_admin`, `rate_limit(...)` | Textbook. Router-level `dependencies=[Depends(require_super_admin)]` on `/admin/*` means a new admin route is gated by default |
| **Services** | 8 modules, ~4 700 lines. `bus` (transport), `moderation` (dispatcher), `broadcast` (host domain, registers *into* moderation), `livekit`, `viewer`, `org`, `ops`, `admin` | Import direction is one-way and acyclic (`broadcast → moderation → bus`) |
| **Repositories** | `crud/*` — pure queries + partial updates, no HTTP, no aggregation | Clean separation, consistently held |
| **DB layer** | Sync SQLAlchemy 2.0, `Mapped[]` annotations, `sessionmaker(autoflush=False, expire_on_commit=False)` | Sync under async is mitigated correctly via `tx()` → `to_thread` with short-lived sessions |
| **WebSocket arch** | 1 socket/client, 12 logical channels multiplexed, bounded queue (200, drop-oldest), ref-counted Redis pump, per-socket sliding window (30/10s), 90s idle reap | Best-engineered subsystem in the repo |
| **LiveKit** | Tokens, room control, egress, signature-verified webhooks. Subscriber side wired in the browser; **publisher side is not** | §9 |
| **Background workers** | 3 `asyncio` tickers in `lifespan`: `run_scheduler` (5s), `run_sampler` (15s), `run_metric_sampler` | **Per-process** — hard blocker on multi-worker (C1) |
| **Notifications** | ❌ None. `Organization.notifications` JSON toggles are stored; no dispatcher, no `Notification` table, no in-app feed | §12 |

---

# PHASE 2 — ORGANIZATION MANAGEMENT AUDIT

| Capability | Backend | Frontend | Verdict |
|---|---|---|---|
| Create Organization | `POST /auth/register` creates org + org_admin atomically (`auth.py:59-75`) | `auth/CreateOrganization.jsx` → real API | ✅ |
| Manage Organization | `GET/PATCH /organization/{profile,branding,domain,notifications,security}` (10 routes) | `organization/Settings.jsx`, `Profile.jsx` | ✅ |
| Manage Members | `GET/PATCH/DELETE /organization/users[/{id}]` with self-lockout + super-admin guards (`organization.py:243-263`) | `InviteMembers.jsx` | ✅ |
| Invite Members | `POST /organization/invitations` — sha256-only token, 7-day TTL, Resend email | `InviteMembers.jsx:43` | ✅ (send) |
| Remove Members | `DELETE /organization/users/{id}` → soft delete + `is_active=False` (kills tokens) | `InviteMembers.jsx:138` | ✅ |
| Change Member Roles | `PATCH /organization/users/{id}` restricted to `ORG_ASSIGNABLE_ROLES` | inline `<select>` (`InviteMembers.jsx:193`) | ✅ |
| Create Events | `POST /events` (org_admin only) + creation email | `CreateEventModal.jsx` | ✅ |
| Edit Events | `PATCH /events/{id}` — full field set, `_can_edit` allows assigned hosts | ❌ no edit form; only `PATCH {status}` | 🟡 backend-only |
| Delete Events | `DELETE /events/{id}` soft delete | `EventDetails.jsx:235`, `Events.jsx` | ✅ |
| Publish Events | `PATCH {status:"published"}` guarded by `status_transition_error` | `EventDetails.jsx:272` Publish button | ✅ |
| Cancel Events | `cancelled` is in `EVENT_STATUSES` and reachable via PATCH | ❌ no UI affordance | 🟡 |
| Duplicate Events | ❌ no endpoint, no UI | ❌ | 🔴 |
| Manage Settings | as above + `GET /organization/console-state` | ✅ | ✅ |
| Branding | `logo_url`, `primary_color`, `secondary_color`, `theme` persisted | ✅ wired | 🟡 stored but **not applied** to the rendered UI or to the attendee page |
| Analytics | `GET /organization/overview` (real: `services/org.py:612`, 400+ lines of real queries) | `organization/Dashboard.jsx` real; **`organization/Analytics.jsx` is 100% `data/analytics.js` mock** | 🟡 |
| Billing | `plans` + `subscriptions` tables; super-admin PATCH only; `services/org.entitlements` computes usage vs plan | `organization/Billing.jsx` 100% `data/billing.js` mock | 🔴 no provider, no invoices, no checkout, no metering |

**Tables:** `organizations` (34 columns), `users`, `invitations`, `plans`, `subscriptions`, `audit_logs`.
**Isolation:** `routers/organization.py:47` `get_my_org` is the single point where `org_id` is bound — always from the JWT, never from the body. Verified: no route in that module accepts an org id.

---

# PHASE 3 — EVENT MANAGEMENT AUDIT

## Model — `models/event.py`

`events` (39 columns): identity (`org_id`, `created_by`), content (title/slug/description/banner/thumbnail/category/tags/language/timezone/location), accessibility (`captions_enabled`, `translation_enabled` — **stored config only**, no caption pipeline exists), schedule, access (`visibility`, `registration_required`, `registration_limit`), 10 feature toggles, `status`, `impact` (standard|high|unrepeatable), timestamps + `deleted_at`.

`event_assignments`: `UniqueConstraint(event_id, user_id, role)`, role ∈ host|moderator|speaker.

## API (13 routes)

| Method | Path | Auth | Notes |
|---|---|---|---|
| GET | `/events` | any member | q, status, host, date_from/to, sort, order, page — org-scoped |
| POST | `/events` | org_admin | slug uniqueness per org, transition guard, creation email |
| GET/PATCH/DELETE | `/events/{id}` | member / `_can_edit` / org_admin | PATCH allows an assigned host |
| GET | `/events/{id}/viewer` | any session | **not org-scoped by design** → `viewer.access_for` |
| GET | `/events/{id}/playback` | any session | subscribe-only token, `can_publish` hard-false |
| GET/PATCH | `/events/{id}/{hosts,moderators,speakers}` | member / org_admin | PATCH replaces the whole set |

## Lifecycle diagram

```
                      POST /events (status ∈ draft|published|scheduled)
                                        │
                                        ▼
                                  ┌──────────┐
                        ┌─────────│  draft   │─────────┐
                        │         └──────────┘         │
              needs title ▼                            ▼ (any time)
                   ┌───────────┐   ┌───────────┐   ┌───────────┐
                   │ published │◄─►│ scheduled │   │ cancelled │
                   └───────────┘   └───────────┘   └───────────┘
                        │  │            │                (terminal in practice —
        broadcast.golive│  └────────────┤                 no UI, no re-open rule)
        OR PATCH status │               │
                        ▼               ▼
                    ┌──────┐  ◄── ONLY from published|scheduled
                    │ live │
                    └──────┘
                        │ broadcast.end / emergency_stop / PATCH status
                        ▼  ONLY from live
                    ┌───────┐
                    │ ended │
                    └───────┘
                        │
                        ▼  archive blocked while live
                   ┌──────────┐
                   │ archived │
                   └──────────┘

DELETE → deleted_at set (soft). Excluded from every listing and from viewer resolution.
```

**Guard:** `crud/event.py:22 status_transition_error` — 4 rules, pure, unit-tested. Enforced from *three* call sites: `POST /events`, `PATCH /events/{id}`, and `broadcast._golive`/`_end` (`broadcast.py:238,316` reuse it rather than writing `status = "live"` blind). That reuse is why the lifecycle can't be bypassed through the socket.

**Broadcast layer** (parallel to event status): `broadcast_sessions.status` ∈ preview|live|paused|ended. One row per go-live attempt; a pause does **not** create a second row (`models/live.py:113`). `paused_ms` accumulates so elapsed-live-time excludes pauses.

**Streaming / recording / replay:** see §9 and §10.
**Registration / audience:** ❌ no `registrations` table, no `attendance` table, no endpoint. `registration_required` and `registration_limit` are stored and read by nothing — and `services/viewer.py:63` and `:196` say so explicitly (`"registration_enforced": False`). Honest, and still a gap.

---

# PHASE 4 — EVENT TEAM AUDIT

| Question | Answer |
|---|---|
| Is `EventAssignment` implemented? | ✅ `models/event.py:90`, table `event_assignments` |
| How are they assigned? | `PATCH /events/{id}/{hosts\|moderators\|speakers}` with `{user_ids:[...]}` — **replaces the whole set**, org_admin only (`routers/events.py:208-249`) |
| Where stored? | One table, three roles. No parallel host/moderator/speaker tables |
| Org-wide or event-specific? | **Both layers exist and both are checked.** `users.role` is the org-wide platform role (capability ceiling); `event_assignments.role` is the per-event grant. `services/moderation.py:138-150`: org_admin/super_admin moderate any event in their org; a `host`/`moderator` **must be assigned to that specific event** |
| How are permissions checked? | `resolve_ctx` computes `can_moderate` and `can_host` once at socket accept. `can_host` requires an `"host"` assignment specifically — a moderator assignment never grants it (`moderation.py:149`). `dispatch` then gates on `HOST_ONLY` / `VIEWER_ACTIONS` / `can_moderate` (`moderation.py:1037-1041`) |
| Updated? | Re-PATCH the role with the new full set (`crud/event.py:151 set_assignees` deletes then re-inserts) |
| Removed? | Omit the user from the PATCH body. No individual DELETE route |
| Validation | `crud.valid_member_ids` — assignee must be a live member of the *same org*; otherwise 400 naming the offending ids |
| Producer / Co-host / Panelist | ❌ Not modelled. "Producer" appears only as UI vocabulary in the host console. `ASSIGNMENT_ROLES` is exactly `("host","moderator","speaker")`. Runtime stage roles (`participant.role`) additionally allow `viewer`, but that is **presence state in Redis**, not an assignment |

**Gap:** an assignment grants no *notification* and no *email*. There is no `send_assignment_email`, and nothing tells a newly-assigned host that they are now responsible for an event. Combined with §5 this is the largest hole in the enterprise flow.

---

# PHASE 5 — INVITATION SYSTEM AUDIT

## What exists (backend — genuinely well built)

| Concern | Implementation |
|---|---|
| Model | `models/invitation.py` — `org_id`, `email`, `role`, `token_hash` (unique), `status`, `invited_by_id`, `expires_at`, `accepted_at` |
| Token security | `secrets.token_urlsafe(32)`; **only the sha256 hash is stored** (`crud/organization.py:126,174`); raw token returned exactly once in the create/resend response |
| Expiration | 7-day TTL; `_expire_stale` (`crud/organization.py:131`) flips lapsed rows on every list; accept re-checks `expires_at` |
| Validation | duplicate-pending guard per (org,email), existing-user guard, username collision guard, role allowlist |
| Lifecycle | pending → accepted \| rejected \| cancelled \| expired |
| APIs | `GET/POST/PATCH/DELETE /organization/invitations[/{id}]` (admin) + `POST /organization/invitations/{accept,reject}` (public, token-bearing) |
| Email | `send_invitation_email` — HTML-escaped, cid-embedded logo, best-effort, fired via `BackgroundTasks` |
| On accept | Creates the `User` with the invited role in the invited org and returns a **real `TokenOut`** — a working session (`organization.py:339-355`) |

## What is broken — three independent faults on one path

```
Admin clicks Invite
  POST /organization/invitations                                  ✅ works
  → email sent with link:  {origin}/accept-invite?token=<raw>     organization.py:270
                                        │
                                        ▼
  Frontend route registered:  /accept-invitation                  App.jsx:135
                              ────────────────  MISMATCH #1 → the emailed link 404s,
                                                falls to RootRedirect → /login
                                        │
  Even when reached manually, the page:
    • reads ?email / ?role / ?org from the query string —          AcceptInvitation.jsx:26-29
      it never reads ?token                        MISMATCH #2
    • calls fakeSession() from auth/dummy.js and    AcceptInvitation.jsx:56
      never calls POST /organization/invitations/accept  MISMATCH #3
                                        │
                                        ▼
  Result: a forged client-side session with an attacker-chosen role, and the
  invitation row stays `pending` forever. No user is created.
```

`?role=` is read straight from the URL and stored in the session (`AcceptInvitation.jsx:27`), so anyone can visit `/accept-invitation?role=org_admin` and get an `org_admin` client session. Server-side this is inert — every API call still carries a fake/absent token and is refused — but every client-side gate (`RoleRoute`) opens, so the entire org console and the admin console *render*. That is UI exposure of structure and copy, not data.

## Answers

- **Can an org admin invite host / moderator / viewer / speaker / org_admin?** ✅ Yes — `ORG_ASSIGNABLE_ROLES` (`organization.py:42`) covers all five; `super_admin` is excluded by design. Send works, **accept does not**.
- **Org-scoped or event-scoped?** **Org only.** There is no `event_invitations` table, no `POST /events/{id}/invitations`, no per-event invite token.
- **Is there an Event Invitation system?** 🔴 **No.** The two are conflated in the UI: `AcceptInvitation.jsx:29` reads an `?event=` param and routes viewers to `/events/:id/watch`, but nothing server-side ever issues such a link.
- **Do invitation emails exist?** ✅ One template, escaped and tested.
- **Notifications?** ❌ Email only. No in-app notification of any kind.

---

# PHASE 6 — HOST WORKFLOW AUDIT

```
Org Admin                    ✅ POST /auth/register → org_admin
   ↓
Create Event                 ✅ POST /events + CreateEventModal + creation email
   ↓
Assign Host                  ✅ PATCH /events/{id}/hosts + EventDetails "Hosts" tab
   ↓
Send Invitation              🟡 org invitation only; NO assignment email, NO event invite
   ↓
Host Accepts                 🔴 accept flow broken (§5) → host never gets an account
   ↓
Host Dashboard               🟡 /host/dashboard is real and socket-driven, but:
                                • UNGATED route (App.jsx:161) — any session loads it
                                • no link from anywhere in the org console
                                • resolves the event via GET /events?status=live, or ?event=<id>
   ↓
Preview                      🟡 broadcast.preview reserves the LiveKit room ✅ and mints a
                                publish token ✅; useMediaPreview shows a REAL local
                                camera/mic preview with measured resolution ✅ —
                                but the preview is never sent anywhere
   ↓
Go Live                      ✅ broadcast.golive — idempotent, advances event status,
                                ensures the room, audits, broadcasts to every console
   ↓
Publish Camera               🔴 **MISSING.** No publisher exists. publish_token flows
                                server → snapshot → useLiveEvent → StudioStage:161
                                where it is only DISPLAYED. grep: zero publish calls
   ↓
Recording                    🟡 recording.start calls real LiveKit egress ✅, records
                                enforced=false honestly when it fails ✅ — but egress has
                                NO output bucket, so on LiveKit Cloud the request is
                                rejected and no file is produced. pause/resume is
                                bookkeeping only (broadcast.py:460)
   ↓
End Stream                   ✅ broadcast.end stops recording first, then the session,
                                then the event; emergency_stop also deletes the room
```

**Verdict: the host console is a complete, real control room attached to a camera that is never connected.** Every control persists, audits and fans out correctly. `_golive`/`_end` correctly drive `events.status` through the shared guard. The one missing piece is ~80 lines of `livekit-client` publisher wiring — and both server call sites (`broadcast.py:346,747`) were written as drop-in points for exactly that.

---

# PHASE 7 — MODERATOR WORKFLOW AUDIT

| Step | Status | Evidence |
|---|---|---|
| Invite | 🟡 org invite only | §5 |
| Accept | 🔴 broken | §5 |
| Moderator Dashboard | 🟡 real & complete, **ungated route** (`App.jsx:165`), no entry link | `pages/moderator/Dashboard.jsx` |
| Join Event | ✅ WS `/live/events/{id}/ws`; auth + org isolation + per-event assignment + ban check all **before** `accept()` (`live.py:74-91`) | |
| Moderate Chat | ✅ send/typing/react/approve/pin/delete(soft)/note/bulk(≤100) | `moderation.py:986-994` |
| Moderate Q&A | ✅ ask/vote/approve/answer/dismiss/pin/assign(org-checked)/delete | `moderation.py:995-1002` |
| Manage Polls | ✅ create/update(vote-preserving edit)/launch/close/delete/vote + 5s scheduler | `moderation.py:1003-1008,1053` |
| Manage Participants | ✅ hand/state/mute/timeout/stage/role/ban(outlives presence)/remove — each returns whether LiveKit *enforced* it | `moderation.py:930-981` |
| Waiting Room | ✅ `stage.admit` / `admit_all`; staff never wait (`live.py:99`) — but a moderator can admit, which is arguably a host decision | `broadcast.py:502-536` |
| Announcements | ✅ send/schedule/delete, 3 priorities, `delivered_to` = real local connection count | `moderation.py:854` |
| End Session | ✅ host-only by design (`HOST_ONLY` blocks a moderator ending the stream) | `moderation.py:1037` |

**Verdict: fully implemented and the best-tested part of the system** (`test_moderation.py` PASSES). The only gaps are the entry path (invite/accept/link) and the ungated route.

---

# PHASE 8 — VIEWER WORKFLOW AUDIT

```
Viewer
  ↓
Registration      🔴 /e/:id renders pages/EventRegistration.jsx which resolves the id
                     against data/events.js MOCKS. A real event UUID or slug returns
                     "This event could not be found." The submit handler is
                     console.log + setDone(true).  ← EventDetails "Copy Link" (:197)
                     produces exactly this dead link.
  ↓
Invitation        🔴 no event invitations exist (§5)
  ↓
Waiting Room      🟡 server-side complete (presence.waiting, host admit/deny/admit_all);
                     NO attendee-facing waiting-room UI — the watch page has no
                     "waiting for the host" state
  ↓
Watch Page        ✅ /events/:eventId/watch — real. GET /events/{id}/viewer returns a
                     viewer-safe whole-page payload (event, organizer, stream, security,
                     support, access, viewer). services/viewer.py is a model of honest
                     reporting: `encrypted` = livekit.configured(), `edge_delivery`
                     claims LiveKit not a CDN, DRM omitted entirely.
  ↓
Playback          ✅ GET /events/{id}/playback → subscribe-only token (can_publish hard
                     false), 409 with a reason when not live / LiveKit unset.
                     VideoPlayer.jsx: real Room, adaptiveStream + dynacast, simulcast
                     quality picker, PiP, fullscreen, RTT from the real candidate pair.
                     ⚠ Nothing publishes, so a live event still shows an empty stage.
  ↓
Chat              🔴 not on the watch page. The server ALLOWS it — VIEWER_ACTIONS
                     includes chat.send/react, VIEWER_CHANNELS includes chat — and
                     useViewerEvent.js:29 handles no chat envelope and renders no
                     composer. The capability is built and unused.
  ↓
Q&A               🔴 same: qa.ask/qa.vote permitted server-side, no UI
  ↓
Replay            🔴 no VOD. No recordings read API, no signed URL, no player source
```

## Access validation — `services/viewer.py:55 access_for`

```
super_admin                          → allowed, basis "platform_admin"
user.org_id == event.org_id          → allowed, basis "organization_member"
event.visibility ∈ public|unlisted   → allowed, basis "<visibility>_event"
otherwise                            → DENIED ("private to its organization")
```
`VIEWABLE_STATUSES = published|scheduled|live|ended|cancelled`. Missing, soft-deleted, draft/archived, and not-permitted **all collapse to the same 404** (`routers/events.py:148-158`) — the endpoint is deliberately not an existence oracle. The same function gates the WebSocket (`moderation.py:130`), so an attendee handed a public event's page cannot be refused the socket carrying its viewer count. That consistency is deliberate and correct.

| Viewer type | Supported |
|---|---|
| Authenticated viewer | ✅ |
| Organization member | ✅ (basis `organization_member`) |
| External viewer (signed in, other org) | ✅ for public/unlisted |
| **Anonymous viewer** | 🔴 **No.** `/events/:id/watch` sits behind `ProtectedRoute`; both endpoints require `get_current_user`; `useEventStream` starts in `unauthorized` without a localStorage token. `roleHome.js:5` promises "public viewers join a public event link with no account at all" — that path does not exist |
| Guest viewer (name-only) | 🔴 No |
| Private event | ✅ org-only, enforced |
| Public event | ✅ any signed-in user |

**Where things are generated:** playback token — `routers/events.py:194` (`livekit.create_stream_token(identity=user.id, room, can_publish=False)`). Watch page init — `hooks/useViewerEvent.js` (one REST read + the shared socket). Room name — `viewer.room_name()` / `Ctx.room`, both `event_<uuid>`, with a comment in each pointing at the other.

---

# PHASE 9 — LIVEKIT AUDIT

| Concern | Status | Location |
|---|---|---|
| Room creation | ✅ `ensure_room(empty_timeout=600)`; already-exists treated as success | `livekit.py:103` |
| Token generation | ✅ `create_stream_token(identity, room, can_publish)` | `livekit.py:20` |
| Publisher (server-side grant) | ✅ minted for hosts in `_preview` and `snapshot_extra` | `broadcast.py:346,747` |
| Publisher (browser) | 🔴 **MISSING — no publisher exists anywhere in the client** | grep: 0 publish calls |
| Subscriber | ✅ real `Room` + `TrackSubscribed` + attach | `VideoPlayer.jsx:96-133` |
| Ingress (RTMP/SRT) | 🔴 not integrated |
| Egress / Recording | 🟡 `RoomCompositeEgressRequest` with MP4 file output and 720p/1080p presets + 2K/4K advanced encoding — **but no S3/GCS/Azure output block**, so LiveKit Cloud rejects it. Failure is recorded as `enforced=False` + `error` and surfaced, never faked | `livekit.py:132-157` |
| Replay | 🔴 no read API, no signed URLs |
| Participant events | ✅ webhook handles participant_joined/left, track_published/unpublished, room_started/finished, egress_started/ended | `live.py:196-243` |
| Webhook security | ✅ `WebhookReceiver` + `TokenVerifier`; bad signature → 401; unconfigured → 503 (never accept-and-ignore) | `live.py:180-189` |
| Auto-recovery | ✅ `room_finished` while the session is still live → flag `recovering`, keep presence + waiting room, emit a health `down` — instead of tearing the broadcast down | `live.py:226-238` |
| Identity mapping | ✅ LiveKit identity == `str(user.id)` == presence key, so the console socket and the webhook update **one** record. Documented at `bus.py:161-164` |
| Host permissions | ✅ `can_host` from an `"host"` assignment; publish grant only from the host path |
| Moderator permissions | ✅ `can_moderate`; `HOST_ONLY` blocks broadcast/recording control |
| Viewer permissions | ✅ subscribe-only, `can_publish` hard-false |
| Publish permission control | ✅ `set_stage` flips `can_publish` server-side — the real lever |
| Token TTL | ⚠ no explicit `with_ttl`; SDK default, no revocation |
| Room naming | ⚠ **two conventions coexist**: `event_<uuid>` (events/live/viewer) and `stream_<uuid>` (legacy `/streams`). `live.py:173` flags it; the webhook ignores `stream_*` rooms entirely |

---

# PHASE 10 — AUTHENTICATION & AUTHORIZATION

## Auth flow

```
POST /auth/register  ──► create Organization + User(org_admin, or super_admin if
   (rate limit 5/5m)     email == SUPER_ADMIN_EMAIL) ──► JWT ──► welcome email (bg)

POST /auth/login     ──► identifier = email OR username (case-insensitive)
   (rate limit 10/60s)   bcrypt.checkpw ──► is_active check ──► JWT{sub, role, exp}
                                                   24h, or 30d when remember=true

Client: localStorage["token"] + ["user"]  ──►  axios interceptor: Bearer on every request
                                          ──►  ?token= on the WebSocket

Every request: HTTPBearer ──► jwt.decode(HS256) ──► db.get(User, sub) ──► is_active
               ⚑ the ROLE IS RE-READ FROM THE DATABASE, not trusted from the token
```

`security.py:49-51` re-loads the user row on every request. That single choice means a demotion or deactivation takes effect immediately for authorization purposes, even though the token itself is not revocable.

**Password reset:** `forgot-password` → 4-digit OTP, 10-min TTL, always 200 (no account enumeration) → `verify-otp` (optional, non-consuming) → `reset-password` (single-use, clears the OTP). ⚠ OTP is stored **plaintext** in `users.reset_token` with no attempt counter; the 5/300s + 10/300s IP limits and the short TTL are the only guards. The code says so at `auth.py:122`.

**Refresh tokens:** 🔴 none. No rotation, no revocation list, no invalidation on password change or role change. `is_active=False` is the only kill switch.

## Authorization

```
_ROLE_RANK (security.py:64)   viewer 0 < speaker 1 < moderator 2 < host 3 < org_admin 4 < super_admin 5

require_min_role("x")  →  rank(user) >= rank(x), else 403.  super_admin clears every gate.
require_super_admin    →  exact match.
org_scoped(stmt,model,user) → adds .where(model.org_id == user.org_id); super_admin bypasses.
```

Two isolation mechanisms, used consistently:
1. **`org_scoped()`** for query-level scoping (admin surfaces).
2. **Resolve-then-scope**: `_get_event_or_404(db, user.org_id, id)` and `get_my_org` — the id comes from the path, the org from the JWT, and a cross-tenant id resolves to `None` → 404.

Realtime authorization is a third, separate table: `VIEWER_ACTIONS` (8 unprivileged actions) → `HOST_ONLY` (12 broadcast/recording actions) → everything else needs `can_moderate` (`moderation.py:1028-1048`). Plus a **server-side output projection**: `viewer_snapshot` and `viewer_envelope` are allow-lists, so a future channel is invisible to attendees until deliberately added — the failure mode of forgetting is a missing panel, not a leak (`moderation.py:358-414`).

| Role | Reaches | Gate |
|---|---|---|
| super_admin | everything, all orgs | `require_super_admin` + rank 5 bypass |
| org_admin | own org: settings, members, invitations, events, assignments, any event's console | `require_org_admin`, `get_my_org_admin` |
| host | edit own/assigned events; broadcast control **on assigned events only** | `_can_edit`, `can_host` |
| moderator | audience control **on assigned events only** | `can_moderate` |
| speaker | rank 1 — assignable, routed to `/dashboard` (legacy mock page). No speaker surface | — |
| viewer | watch page + playback + the 8 viewer socket actions | `access_for` |

**Tenant isolation verdict:** structurally sound. Three defects sit outside it: `channels`/`streams` have no `org_id` at all (S4), and `Organization.status == "suspended"` is read by exactly one readiness gate (`ops.py:581`) and by no authorization check.

---

# PHASE 11 — ROUTING AUDIT

## Frontend

| Route | Guard | Access | Note |
|---|---|---|---|
| `/` | none | public / redirects | `LandingOrDashboard` |
| `/login` `/signup` `/forgot-password` | none | public | real API |
| `/accept-invitation` | none | public | 🔴 wrong path vs the email; dummy session |
| `/e/:id` | none | public | 🔴 mock-only registration page |
| `/events/:eventId/watch` | `ProtectedRoute` | any session | ✅ server decides via `access_for` |
| `/host/dashboard` | **none** | any visitor | 🟡 `App.jsx:161` — ungated by comment |
| `/moderator/dashboard` | **none** | any visitor | 🟡 `App.jsx:165` — ungated by comment |
| `/admin/*` (14 real + 5 stubs) | `RoleRoute(["super_admin"])` | super_admin | ✅ |
| `/organization/*` (9 real + 8 stubs) | `RoleRoute(["org_admin"])` | org_admin | ✅ — **hosts and moderators cannot see any organization page**, by design |
| `/dashboard` + 9 legacy stubs | `ProtectedRoute` | any session | speaker/unknown land here; 100% mock |
| `*` | — | `RootRedirect` → roleHome or /login | |

**22 `Placeholder` routes** exist so console navs never 404 — intentional, documented at `App.jsx:57`.

## Backend

| Prefix | Routes | Default authorization |
|---|---|---|
| `/auth` | 6 | public (4 rate-limited) except `/me` |
| `/organization` | 24 | member for reads; `require_org_admin` for writes + security/developer reads; 2 public token endpoints |
| `/events` | 13 | member read, org_admin write, `access_for` on the 2 attendee routes |
| `/admin` | 38 | `require_super_admin` at **router level** |
| `/live` | 1 WS + 1 webhook | JWT in query string; webhook signature-verified |
| `/streams` | 8 | `get_current_user`, owner-scoped via `Channel.owner_id` |
| `/channels` | 5 | `get_current_user`, owner-scoped |
| `/dashboard` | 3 | `require_min_role("org_admin")` / `require_super_admin` |
| `/health` | 1 | public, static `{"status":"ok"}` — does **not** touch the DB |

---

# PHASE 12 — EMAIL & NOTIFICATIONS

| Item | Status |
|---|---|
| SMTP | 🔴 none — HTTP API only |
| Email service | ✅ `app/email.py` via Resend REST, `httpx`, 10s timeout, best-effort with its own logging, `BackgroundTasks` so mail latency never delays a response |
| Templates | ✅ 4: welcome, password-reset OTP, invitation, **event-created**. Inline CSS, cid-embedded logo (`@lru_cache`), every interpolation `html.escape`d, offline `__main__` self-check with 12 assertions |
| Invitation emails | ✅ sent on create **and** resend — but the link path is wrong (§5) |
| Reminder emails | 🔴 none. No T-24h / T-1h event reminder anywhere |
| Assignment emails | 🔴 none — assigning a host sends nothing |
| Registration confirmation | 🔴 none (no registration system) |
| Notification service | 🔴 **does not exist.** No `Notification` model, no dispatcher, no queue |
| Push notifications | 🔴 none |
| In-app notifications | 🔴 none. `Organization.notifications` JSON toggles are writable via `GET/PATCH /organization/notifications` and **read by nothing** — the settings page implies delivery that cannot happen |
| Outbound webhooks | 🔴 `Organization.webhook_urls` stored, nothing dispatches |
| Deliverability | ⚠ `MAIL_FROM` defaults to `onboarding@resend.dev`, which Resend delivers **only to the account owner**. Flagged at `config.py:39` |

---

# PHASE 13 — VIEWER LINK GENERATION

| Link type | How it is produced | Expire | Revoke | Regenerate | Opaque |
|---|---|---|---|---|---|
| Share / public link | `EventDetails.jsx:197` `${origin}/e/${slug \|\| id}` — client-side string, **no server endpoint** | ❌ | ❌ | ❌ | ❌ slug or raw UUID |
| Watch link | `${origin}/events/${id}/watch` — hand-built in `Recordings.jsx:137` (over **mock** ids) | ❌ | ❌ | ❌ | ❌ raw UUID |
| Registration link | same as share link → mock page | ❌ | ❌ | ❌ | ❌ |
| Replay link | 🔴 does not exist | — | — | — | — |
| Private link | 🔴 no concept. Privacy is `visibility` + `access_for`, not a secret URL | — | — | — | — |
| Invite link | `organization.py:270` `${base}/accept-invite?token=<raw>` | ✅ 7 days | ✅ cancel/delete | ✅ resend rotates the token | ✅ `token_urlsafe(32)`, hash-only storage |

**Summary:** the only real, revocable, opaque, expiring link in the system is the **organization invitation** — and it is the one whose destination route does not exist. Every event-facing link is a client-side template over a raw UUID or slug with no token, no expiry and no revocation. `_invite_url` derives its base from `CORS_ORIGINS.split(",")[0]`, so in production the emailed origin is whatever happens to be first in that env var.

---

# PHASE 14 — DATABASE AUDIT

**27 tables.** Present: ✅ · Absent: 🔴

| Requested | Table | Notes |
|---|---|---|
| Organizations | ✅ `organizations` | 34 columns incl. 6 JSON blobs |
| Users | ✅ `users` | soft delete + `is_active` (distinct meanings, documented) |
| Roles | 🔴 | roles are **code-defined** (`_ROLE_RANK`) and exposed read-only via `GET /admin/roles`. Deliberate |
| Permissions | 🔴 | code-defined (`VIEWER_ACTIONS`/`HOST_ONLY`/`require_min_role`) |
| Invitations | ✅ `invitations` | org-scoped only |
| Events | ✅ `events` | + `event_assignments` |
| EventAssignments | ✅ `event_assignments` | `UNIQUE(event_id,user_id,role)` |
| Registrations | 🔴 **MISSING** | blocks registration, capacity, attendee lists |
| Attendance | 🔴 **MISSING** | presence is ephemeral (Redis); `analytics_snapshots` holds aggregates only |
| BroadcastSessions | ✅ `broadcast_sessions` | |
| Recording | ✅ `live_recordings` | ⚠ `size_bytes` is `Integer` → overflows at 2 GB |
| Replay | 🔴 **MISSING** | no VOD asset table |
| Analytics | ✅ `analytics_snapshots`, `platform_metrics` | |
| Notifications | 🔴 **MISSING** | |
| — | `live_messages`, `live_questions`, `live_polls`, `live_announcements`, `live_activity` | moderation domain |
| — | `channels`, `streams` | legacy subsystem, **no `org_id`** |
| — | `plans`, `subscriptions`, `audit_logs`, `platform_settings`, `feature_flags`, `releases`, `support_tickets`, `elevation_sessions`, `governance_records`, `incidents`, `session_alerts` | platform ops |

## ER diagram

```
                        ┌──────────────────┐
                        │  organizations   │◄──────────────┐
                        └────────┬─────────┘               │
              ┌──────────────────┼──────────────────┐      │
              ▼                  ▼                  ▼      │
        ┌──────────┐      ┌─────────────┐   ┌──────────────┴──┐
        │  users   │      │ invitations │   │ subscriptions   │──► plans
        └────┬─────┘      └─────────────┘   └─────────────────┘
             │  ▲
    owner_id │  │ created_by / user_id / invited_by_id
             ▼  │
        ┌──────────┐            ┌───────────────────┐
        │ channels │            │      events       │  org_id, created_by
        └────┬─────┘            └─────┬─────────────┘
             ▼                        │
        ┌──────────┐                  ├──► event_assignments (event_id, user_id, role)
        │ streams  │  ⚠ NO org_id     │
        │ room:    │  owner reached   ├──► broadcast_sessions ──► live_recordings
        │ stream_* │  via channel     │        (settings, peak,      (egress_id,
        └──────────┘                  │         paused_ms)            enforced, file_url)
                                      │
   ─── _EventScoped base (id, event_id, org_id, created_at) ───
                                      ├──► live_messages    (status, pinned, flags, reactions)
                                      ├──► live_questions   (votes, assigned_to)
                                      ├──► live_polls       (options JSON [{label,votes}])
                                      ├──► live_announcements
                                      ├──► live_activity    (append-only console timeline)
                                      └──► analytics_snapshots (1 row / 15s / live event)

  audit_logs  ── deliberately NO FK to users/orgs, so it outlives the actor
  platform_settings · feature_flags · releases · support_tickets
  platform_metrics · incidents · governance_records · elevation_sessions · session_alerts

  EPHEMERAL — Redis or process memory, never Postgres (bus.py):
    live:<id>:participants  presence hash   (identity → {name,role,muted,hand,quality,...})
    live:<id>:state         hot settings    (chat_enabled, slow_mode, status, countdown)
    live:<id>:bans          ban set         (outlives presence; ⚠ no TTL, no persistence)
```

**Integrity assessment.** Strong instincts: `org_id` denormalized onto every live table with a stated reason (org-isolated queries without a join), `_EventScoped` base so a new live table inherits the isolation contract, soft deletes preserving compliance data, append-only audit with no FK. Weaknesses: **no working migrations** (`migrations/` has no `versions/`, `target_metadata = None`; truth lives in `create_tables.py`'s 40-clause append-only ALTER list), uniqueness rules (org slug, event slug per org, one active subscription) enforced only in Python, no enums or CHECK constraints for 8 tuples of magic status strings, `channels.slug` globally unique so tenants collide, `size_bytes` as `INTEGER`.

---

# PHASE 15 — API AUDIT

Format: `METHOD PATH — auth — purpose — request → response — frontend caller`.

## Auth (6)
| | |
|---|---|
| `POST /auth/register` — public, 5/5m | full_name, email, username?, password, organization_name → `{access_token, user}` · `auth/CreateOrganization.jsx` |
| `POST /auth/login` — public, 10/60s | identifier, password, remember → `{access_token, user}` · `auth/Login.jsx` |
| `POST /auth/forgot-password` — public, 5/5m | email → always-200 message · `auth/ForgotPassword.jsx` |
| `POST /auth/verify-otp` — public, 10/5m | email, otp → message · **no frontend caller** |
| `POST /auth/reset-password` — public, 10/5m | email, otp, password → message · `auth/ForgotPassword.jsx` |
| `GET /auth/me` — session | → `UserOut` · **no frontend caller** ⚠ session never revalidated |

## Organization (24)
`GET /overview` (member, range+workspace+include_test → whole-page payload · `organization/Dashboard.jsx`) · `GET /console-state` (member · `OrganizationLayout.jsx`) · `GET /me` · `GET|PATCH /profile` · `GET|PATCH /branding` · `GET /developer` (admin, read-only) · `GET|PATCH /notifications` · `GET|PATCH /security` (admin) · `GET|PATCH /domain` (PATCH resets `domain_verified`) · `GET /users` `GET|PATCH|DELETE /users/{id}` (admin; self-lockout + super-admin guards) · `GET|POST /invitations` `PATCH|DELETE /invitations/{id}` (admin; PATCH action ∈ resend|cancel|expire) · `POST /invitations/accept` **public** (token, full_name, password, username? → real `TokenOut`) — 🔴 **no frontend caller** · `POST /invitations/reject` **public** — 🔴 **no frontend caller**.
Frontend: `organization/Settings.jsx` (5 parallel GETs), `Profile.jsx`, `InviteMembers.jsx`.

## Events (13)
`GET /events` (member; q/status/host/date_from/date_to/sort/order/page → `Page[EventOut]` · `Events.jsx`, `useLiveEvent.js:202`) · `POST /events` (org_admin; `EventCreate` → `EventOut` + email · `CreateEventModal.jsx`) · `GET /events/{id}` · `PATCH /events/{id}` (`EventUpdate`; org_admin or assigned host) · `DELETE /events/{id}` (org_admin, soft) · `GET /events/{id}/viewer` (any session; `access_for` → viewer-safe payload · `useViewerEvent.js`) · `GET /events/{id}/playback` (any session; 409 with reason when not live → `{room, livekit_url, token}` · `VideoPlayer.jsx:95`) · `GET|PATCH /events/{id}/{hosts|moderators|speakers}` (6 routes; PATCH replaces the set · `EventDetails.jsx`).

## Admin (38, all `require_super_admin`, every mutation audited)
dashboard · command-center · console-state · search · POST|DELETE elevation · organizations CRUD (5) · users (3) · plans · subscriptions (2) · analytics · live-events · platform-health · audit-logs · settings (2) · roles · feature-flags CRUD (4) · releases (3) · support-tickets (4) · per-org api-keys (3). All 14 admin pages are wired to real endpoints.

## Live (2)
`WS /live/events/{id}/ws?token=` — snapshot-then-deltas, 49 actions, 12 channels · `useEventStream.js`.
`POST /live/webhooks/livekit` — signature-verified, `include_in_schema=False`.

## Streams (8) / Channels (5) / Dashboard (3)
Legacy. Owner-scoped via `Channel.owner_id`, **no org isolation**. `/streams` is read by `services/admin.live_events` and `_streaming_hours`, so `/admin/live-events` and `/admin/dashboard` depend on it — it cannot simply be deleted. `/dashboard/*` has **no frontend caller** (the org console uses `/organization/overview`).

## Coverage gaps
**Endpoints with no frontend caller (7):** `/auth/me`, `/auth/verify-otp`, `/organization/invitations/{accept,reject}`, all three `/dashboard/*`.
**UI needs with no endpoint:** recordings list/download/replay, org analytics, billing/invoices, event registration, asset upload, event duplicate, notifications, API-key self-service.

---

# PHASE 16 — CODE QUALITY AUDIT

## Dead code
| Item | Evidence |
|---|---|
| `client/src/auth/dummy.js` | only consumer is `AcceptInvitation.jsx:56` — which is itself the broken flow |
| `openapi.json` | 6 of 99 paths (94% stale), no generation script |
| `server/migrations/` | no `versions/`, `target_metadata = None` — non-functional |
| `server/update_stream_table.py` | superseded by the model + `create_all` |
| `POST /auth/verify-otp` | dead-by-design (the client goes straight to reset) |
| `/dashboard/*` (3 routes) | zero frontend callers |
| Dead deps — client | `react-hook-form` (0 refs), `socket.io-client` (0 refs), `dayjs` (**now 0 refs**) |
| Dead deps — server | `aiofiles`, `pillow`, `python-multipart` (0 refs — installed for an upload feature that does not exist) |
| Mock modules still load-bearing | `data/events.js`, `analytics.js`, `billing.js`, `recordings.js`, `host.js`, `moderation.js`, `orgSettings.js`, `home.js` |

## Duplicate code
**Backend:** `_base_url()` (`email.py:67`) and `_invite_url()` (`organization.py:268`) both derive a frontend origin from `CORS_ORIGINS.split(",")[0]` · `_iso` defined in `moderation.py:170` and wrapped in `broadcast.py:110` (reaching across a `_`-prefixed boundary) · the `paused_ms` accumulation block appears **4×** verbatim (`broadcast.py:232,282,312,391`) · the "newest not-ended" query pattern 2× (`broadcast.py:139,150`) · unique-username-by-counter loop 3× (`auth.py:50`, `crud/organization.py:114`, `seed.py:114`) · `_user_from_token` (`live.py:50`) re-implements `get_current_user` (necessary — query-string token — but the two must stay in step) · two role-hierarchy sources (`_ROLE_RANK` and `models/user.ROLES`, "keep in sync" by comment).

**Frontend:** three sidebars + three topbars (`MainLayout` inlines its own; `components/Dashboard/*`; `components/admin/Admin*`) · `initials()` ~6 definitions · `isEmail` regex 4 copies · `fmtDate` imported from the **mock** `data/events.js` by 6 real feature files · the `try{await api…; notify.success; reload()}catch{notify.error(errMsg(e))}` mutation block ~15× with no `useMutation` hook · hand-rolled `<table>` in ~7 places while `DataTable` exists · `data/moderation.js ACT_ICON` must stay in sync with `ACTIVITY_KINDS` in `models/live.py:30` — a cross-stack coupling with a comment and no test.

## Circular dependencies
None. Backend import direction is one-way (`routers → services → crud → models`; `broadcast → moderation → bus`). ⚠ `broadcast.py:838-840` performs **import-time registration** into `moderation.ACTIONS`/`HOST_ONLY`/`SNAPSHOT_EXTRAS`, so importing `routers/live.py` has side effects and the dispatcher's contents depend on import order. Clever, documented, and fragile.

## Security risks

| # | Sev | Finding | Location |
|---|---|---|---|
| S1 | 🔴 **Critical** | **Super-admin password committed to the repo** — `hash_password("NoxxMC26070%!LGM")`, and the same literal is `print()`ed to stdout. Anyone with repo read access owns the platform | `seed.py:127,137` |
| S2 | 🔴 **High** | **Client-side role forgery** — `/accept-invitation?role=org_admin` produces a `fakeSession` with an attacker-chosen role. Server-side inert; every client route gate opens | `AcceptInvitation.jsx:27,56` |
| S3 | 🔴 **High** | `channels` and `streams` have **no `org_id`** — the entire legacy streaming subsystem is outside `org_scoped()`. `channels.slug` is globally unique → tenants collide and are enumerable | `models/channel.py`, `models/stream.py` |
| S4 | 🔴 **High** | `SECRET_KEY` defaults to `"dev-secret-change-me"`. Only a `log.warning` (`config.py:46`) stands between a missing env var and forgeable JWTs for any user id and role | `config.py:14,21` |
| S5 | 🟡 High | **4-digit OTP stored plaintext, no attempt counter.** 10 000 combinations; IP rate limits (5/5m request, 10/5m verify) + 10-min TTL are the only guards. Acknowledged | `auth.py:118-124` |
| S6 | 🟡 High | `allow_origin_regex=r"https?://(localhost\|127\.0\.0\.1)(:\d+)?"` with `allow_credentials=True`, **unconditionally including production**. Any page on localhost can make credentialed calls to the prod API | `main.py:71` |
| S7 | 🟡 Medium | `/host/dashboard` and `/moderator/dashboard` have **no route guard** — any visitor loads the console shell. Data is correctly refused by the socket, so this is UI exposure | `App.jsx:161,165` |
| S8 | 🟡 Medium | **JWT in the WebSocket query string** — lands in proxy logs, browser history, `Referer`. Unavoidable with the browser WS API; a short-lived ticket exchange is the standard mitigation | `live.py:64`, `useEventStream.js:25` |
| S9 | 🟡 Medium | **LiveKit tokens have no explicit TTL** and no revocation | `livekit.py:34-44` |
| S10 | 🟡 Medium | Token in `localStorage` → any XSS reads it. No CSP in `index.html`, no `httpOnly` option | `api.js:9`, `AuthContext.jsx:21` |
| S11 | 🟡 Medium | **No invalidation on password or role change.** A 30-day token survives both. `is_active=False` is the only kill switch (role *authorization* is safe — it is re-read per request) | `security.py:25-36` |
| S12 | 🟡 Medium | `Organization.status == "suspended"` is documented as "blocks the org platform-wide" but is checked **only** by a readiness gate (`ops.py:581`). Suspended orgs keep full API access | `models/organization.py:16` |
| S13 | 🟡 Medium | `Organization.security` JSON (MFA, SSO, IP allowlist, session policy) is writable by org admins and **read by nothing** — the settings page implies enforcement that does not exist | `models/organization.py:68` |
| S14 | Low | `_ip()` uses `request.client.host`; `ratelimit.client_ip` deliberately ignores `X-Forwarded-For` (documented, correct without a verified proxy allowlist) — but behind a proxy **every audit row and every rate-limit bucket collapses to the proxy IP** | `admin.py:43`, `ratelimit.py:73` |
| S15 | Low | Bans live in Redis/memory with **no TTL and no persistence** — a Redis flush or restart clears every ban | `bus.py:266-285` |
| S16 | Low | No per-user Q&A vote ledger → trivial vote stuffing (acknowledged) | `moderation.py:648` |
| S17 | Low | `audit_logs.actor_email` receives `ctx.name` (a **display name**) for live actions, because `Ctx.actor` fakes a User with `email=self.name` | `moderation.py:112-115` |
| S18 | Info | No security headers (HSTS, CSP, X-Frame-Options, X-Content-Type-Options) on either tier |
| S19 | ✅ Info | `/live/webhooks/livekit` is signature-verified and 503s when unconfigured — **this one is done right**; noted so it is not "fixed" | `live.py:180-189` |
| S20 | ✅ Info | Org `api_keys` are hashed and prefixed by `crud/admin.create_api_key`, but **no endpoint authenticates with them** — an unused credential surface, not a live risk |

## Performance risks

| # | Issue | Location | Impact |
|---|---|---|---|
| P1 | **Analytics `_counts()` is O(messages) every 15 s.** 4 aggregates **plus** `SELECT reactions FROM live_messages WHERE reactions IS NOT NULL` — every reaction blob of the event, summed in Python — plus `SELECT * FROM live_polls`. Called **twice** in `analytics_now` and once per session per sampler tick | `broadcast.py:625-647,775` | An 8-hour event with 50 k messages re-reads 50 k JSON blobs every 15 s, forever. **Worst remaining hot spot** |
| P2 | Sampler is **sequential** — `for s in sessions:` with an `await tx()` per session | `broadcast.py:766-796` | 50 concurrent events = 50 serialized round trips inside one 15 s tick |
| P3 | `analytics_snapshots` grows unbounded — ~1 900 rows per 8 h event, **no pruning, no rollup, no partitioning** (acknowledged at `models/live.py:164`) | | index bloat over months |
| P4 | Every chat message `SELECT`s the author's last 10 messages before the `INSERT` | `moderation.py:495-499` | 2 queries per message |
| P5 | `_snapshot` issues 8 queries and `snapshot_extra` adds 4 + a full `analytics_now` **on every socket connect** | `moderation.py:307`, `broadcast.py:714` | ~12 queries × reconnects; a server blip → herd (mitigated by backoff+jitter, not eliminated) |
| P6 | First WS frame carries 200 messages + 200 questions + 50 polls + 50 announcements + 100 activity + 240 retention points | `moderation.py:43,318` | slow first paint on a busy event |
| P7 | `services/admin._streaming_hours` loads **every** stream row and sums in Python; called by two hot admin pages | `services/admin.py:44` | linear in total stream count |
| P8 | Frontend fetches `page_size=100` then filters/sorts/paginates **client-side** | `Events.jsx`, `InviteMembers.jsx:104`, `EventDetails.jsx:177` | silently truncates at 100 rows |
| P9 | `useApi` has no cache, dedupe or abort (documented). `Settings.jsx` fires 5 parallel GETs, `EventDetails.jsx` fires 5 | `hooks/useApi.js` | refetch storm per mount |
| P10 | **recharts + the org console are in the main bundle** — only `Home`, `EventWatch` and `/admin/*` are lazy | `App.jsx:11-28` | large first paint for org admins |
| P11 | `bus.state_set` is read-modify-write, not a Lua CAS (acknowledged at `bus.py:242`) | | lost update if a background writer ever touches state |

## Scalability risks

| # | Risk |
|---|---|
| C1 | **Background tickers run per process.** Three tickers in `lifespan` (`main.py:48`). With N workers a scheduled poll fires N times and N snapshot rows are written per interval. Acknowledged at `main.py:36-38` and `moderation.py:1057` with **no lock and no leader election** — the hard blocker on horizontal scaling |
| C2 | `bus.local_subscribers()` counts only **this** worker's connections, so announcement `delivered_to` is wrong the moment there is more than one worker |
| C3 | Redis Pub/Sub has no persistence or replay — a worker restart drops in-flight envelopes and triggers a full-snapshot stampede on reconnect |
| C4 | `QUEUE_MAX = 200` drop-oldest with **no counter, no metric, no log** — a dropped `message.delete` leaves a deleted message visible until reconnect |
| C5 | Presence is a single Redis hash per event; `HGETALL` on every connect and every 15 s sample. At 10 k participants that is a 10 k-field read |
| C6 | `LiveRecording.size_bytes` is `Integer` → **2 GB ceiling**. A 1080p multi-hour recording overflows |
| C7 | No connection cap and no admission control — `bus.subscribe` grows an unbounded set; nothing rejects the 10 001st socket |
| C8 | One LiveKit room per event, `empty_timeout=600`, no region selection (`Organization.region` stored and unused), no ABR ladder, no CDN/HLS egress |
| C9 | Recording is composite room egress only — no distributed egress pool, no queue, no retry. A failed egress is `enforced=False` and never retried |
| C10 | Unbounded growth with no lifecycle policy: `analytics_snapshots`, `live_activity`, soft-deleted `live_messages`, `audit_logs`, `platform_metrics` |
| C11 | **Schema evolution does not scale** — no Alembic versions; every column on an existing table needs a hand-written `ADD COLUMN IF NOT EXISTS` in `create_tables.py`, with no downgrade path and no history |
| C12 | **No observability** — no metrics, no tracing, no structured logging. `/health` returns a static literal and does not touch the DB (`main.py:114`) |
| C13 | Single region, single database, no read replicas, no read cache (Redis carries pub/sub + presence only) |

## Technical debt

**Architectural.** Two parallel streaming subsystems (`channels`+`streams` with `stream_*` rooms and owner auth vs `events`+`broadcast_sessions` with `event_*` rooms and org+assignment auth; flagged at `live.py:173`, and the legacy one still feeds two admin pages) · Alembic is scaffolding only · `openapi.json` 94% stale · **no CI** (no `.github/`, no test runner config; the 8 self-checks are `python test_x.py`) · **no version pins** in `requirements.txt` (17 unpinned, no lockfile) · zero frontend tests.

**Backend.** `routers/channels.py` is stylistically alien (no docstrings, no org scoping, `str` path params, raw `HTTPException(404, ...)`) · `services/broadcast.py` 840 lines and `services/moderation.py` 1 092 lines are the two largest files · `moderation.py:1090` imports `logging` **inside** an exception handler · `Ctx.actor` fakes a User for the audit writer (S17) · no DB-level enums for 8 tuples of status strings · `services/broadcast.py:346,747` comments claim "no livekit-client on the frontend" — **stale**, the dependency now exists and is used by the viewer.

**Frontend.** `AuthContext.jsx:7-9` still carries its "dummy auth, no backend" docstring even though login/register are fully real, and **never calls `/auth/me`** — a role change or deletion is invisible to the client until the token expires (up to 30 days) · `auth/dummy.js` still wired into a real page · `data/events.js` is load-bearing for 6 real screens · `MainLayout.jsx` hardcodes "245 GB / 1 TB", a "6" notification badge and an org name, and has **no `dark:` variants at all** · `pages/Dashboard.jsx` (all mock) is reachable at `/dashboard` for speakers · copyright years disagree (`AuthLayout` 2026 vs `EventRegistration.jsx:184` 2024) · `index.css` remaps Tailwind `emerald`/`teal` to the brand palette, so components written with `emerald-*` are on-brand **by design** — "fixing the green" breaks the brand everywhere · **4 files are untracked in git** (`hooks/usePlaybackCheck.js`, `hooks/useViewerEvent.js`, `server/app/services/viewer.py`, `server/test_viewer.py`) — the entire viewer feature's new code is uncommitted.

**Debt-avoidance worth preserving (do not undo):** `org_scoped()` as one isolation point · `require_min_role` as a composable ladder · role re-read from the DB per request · one socket, one dispatcher, one audit path · server-side allow-list projection for attendees · `enforced` booleans instead of pretending LiveKit succeeded · honest `null` + `note` instead of fabricated telemetry (`countries: None` with a reason) · soft deletes preserving compliance data · hashed invitation tokens · pure unit-testable guards (`status_transition_error`, `chat_gate`, `slow_mode_error`, `clean_settings`, `engagement_score`, `flag_text`) · `ponytail:` comments that name both the ceiling and the upgrade path.

## Test status (executed 2026-07-31)

| Suite | Result |
|---|---|
| `test_authz.py` | ✅ PASS |
| `test_broadcast.py` | ✅ PASS |
| `test_events.py` | ✅ PASS |
| `test_moderation.py` | ✅ PASS |
| `test_org_settings.py` | ✅ PASS |
| `test_viewer.py` | ✅ PASS |
| `test_invitations.py` | ❌ **FAIL** — `AssertionError: GET /organization/overview unprotected: 403`. The suite asserts an unauthenticated request returns 401; `HTTPBearer(auto_error=True)` returns **403** for a *missing* header (401 is for an invalid one). The request **is** refused, so this is a stale test expectation against a route added after the test was written — not a security hole. Still a red suite |
| `test_ops.py`, `test_org_overview.py` | ⚠ TIMEOUT — both require a live Postgres inside a rolled-back transaction (documented in their docstrings). Environmental, not a code defect |
| `test.py` | ❌ FAIL — `UnicodeEncodeError` printing `❌` to a cp1252 Windows console. Cosmetic; it is a DB connectivity probe, not a test |

---

# PHASE 17 — ENTERPRISE EVENT FLOW ANALYSIS

| # | Step | Verdict | Why — exactly |
|---|---|---|---|
| 1 | Organization Admin | ✅ **Fully** | `POST /auth/register` creates org + org_admin atomically; `CreateOrganization.jsx` is wired |
| 2 | Create Event | ✅ **Fully** | `POST /events` (org_admin), slug uniqueness per org, transition guard, creation email, `CreateEventModal.jsx` |
| 3 | Publish Event | ✅ **Fully** | `PATCH {status:"published"}` through `status_transition_error` (needs a title); Publish button at `EventDetails.jsx:272` |
| 4 | Assign Host | ✅ **Fully** | `PATCH /events/{id}/hosts`, org-membership validated, `event_assignments` row, Hosts tab UI |
| 5 | Assign Moderator | ✅ **Fully** | same path, `moderators` |
| 6 | Generate Viewer Link | 🟡 **Partial** | The **watch page and playback are real**, but no endpoint generates a link. `EventDetails.jsx:197` hand-builds `/e/{slug}` → the **mock registration page**, which cannot find a real event. The working URL (`/events/{id}/watch`) is never produced by the org console. No expiry, no revocation, no opaque id |
| 7 | Send Invitations | 🟡 **Partial** | Org invitations create, list, resend, cancel and email correctly. **No event invitation exists**, and **assigning a host or moderator sends nothing at all** |
| 8 | Host Accepts | 🔴 **Missing** | `POST /organization/invitations/accept` is complete and correct server-side and **has no frontend caller**. The emailed path (`/accept-invite`) is not a registered route; the page that exists ignores the token and fabricates a session (§5) |
| 9 | Moderator Accepts | 🔴 **Missing** | identical to #8 |
| 10 | Viewer Registers | 🔴 **Missing** | No `registrations` table, no `POST /events/{id}/register`. `/e/:id` is mock-only. `registration_required`/`registration_limit` are stored and enforced nowhere — `services/viewer.py:196` returns `"registration_enforced": False` |
| 11 | Host Starts Live | 🟡 **Partial** | `broadcast.golive` is correct, idempotent, audited, advances the event status, ensures the room, mints a publish token — **and nothing publishes media.** The event is "live" with an empty stage |
| 12 | Moderator Controls Event | ✅ **Fully** | 32 moderation actions across chat, Q&A, polls, announcements, participants, waiting room; each real, audited, broadcast, and honest about LiveKit enforcement |
| 13 | Audience Watches | 🟡 **Partial** | Real subscriber, real token, real access check, real viewer count over the socket. Blocked by #11 (nothing to watch), and requires an account — **no anonymous viewer path**. Chat and Q&A are permitted server-side but absent from the attendee UI |
| 14 | Recording | 🟡 **Partial** | Full lifecycle rows, real egress calls, honest `enforced=false` — but **no storage output configured**, so no durable file is produced. Pause/resume is bookkeeping only |
| 15 | Replay | 🔴 **Missing** | No VOD table, no recordings read API, no signed URLs, no player source. `organization/Recordings.jsx` is entirely `data/recordings.js`, and its "Watch Replay" links point at `/events/{mock-id}/watch` |

**Score: 6 fully · 6 partial · 3 missing.** The chain breaks in exactly two places, and both are narrow:
- **Steps 8–9** — a client that never calls a finished endpoint. This is the cheapest high-value fix in the repo: one route rename and one form submit.
- **Step 11** — no browser publisher. Everything on both sides of it is built.

---

# READINESS SCORES

| Module | Score | Rationale |
|---|---:|---|
| Authentication | **74** | Real JWT + bcrypt, DB-not-token role reads, rate-limited, no account enumeration, hashed invite tokens. −: default `SECRET_KEY`, plaintext 4-digit OTP, no refresh/revocation, no `/auth/me` on the client |
| Authorization & Multi-tenancy | **80** | One isolation point, composable ladder, resolve-then-scope, three-layer realtime permission table, server-side output projection. −: `channels`/`streams` outside it, suspension unenforced, 2 ungated frontend routes |
| Organization Management | **82** | Complete self-service surface, real overview aggregation, guarded member management. −: branding stored but never applied, billing/analytics pages mock |
| Event Management | **85** | Best-modelled domain: guarded lifecycle reused from 3 call sites, per-org slugs, soft delete, full filtering. −: no duplicate, no edit form, no cancel UI |
| Event Team / Assignments | **72** | One clean table, org-membership validation, per-event grants correctly distinguished from org roles. −: no notification on assignment, no producer/panelist, no individual remove |
| Invitations | **45** | Backend is genuinely well built (hashed tokens, TTL, resend rotation, real session on accept). Frontend does not call it, and the emailed path 404s. A finished engine with no ignition |
| Live Moderation | **90** | Highest in the repo. 32 audited actions, one dispatcher, honest enforcement booleans, filters that actually filter, passing self-check |
| Host / Broadcast Control | **68** | Complete control room: idempotent go-live, pause accounting, emergency stop, 25 validated settings, real local media preview. −: no publisher, recording not durable |
| Viewer Experience | **62** | Real access rules, real viewer-safe payload, real WebRTC subscriber, real readiness probes, honest security reporting. −: no anonymous access, no chat/Q&A UI, no waiting-room UI, no replay, no working share link |
| LiveKit Integration | **60** | Tokens, room control, egress, verified webhooks, auto-recovery, identity mapping all correct. −: publisher absent, no storage, no ingress, no TTL, two room conventions |
| Realtime Infrastructure | **86** | 12 channels on 1 socket, bounded queues, ref-counted Redis pump, bans outliving presence, backoff+jitter, snapshot resync, per-socket limiting. −: per-process tickers, silent drops, no admission control |
| Recording & Replay | **30** | Lifecycle rows and egress calls exist and report honestly. Nothing is stored, nothing can be read, nothing can be replayed. `size_bytes` overflows at 2 GB |
| Registration & Audience | **10** | No table, no endpoint, no UI. Two stored columns nothing reads. The one part of the flow with no foundation at all |
| Analytics | **65** | Real 15 s sampler, real retention graph, documented engagement formula, honest nulls, real org overview. −: O(messages) query shape, no per-org read API, org Analytics page mock, no retention policy |
| Email | **70** | 4 tested templates, escaped, best-effort, background-dispatched. −: no reminders, no assignment mail, dev sender domain, wrong invite path |
| Notifications | **5** | Stored toggles only. No model, no dispatcher, no in-app feed, no push, no webhooks out |
| Billing | **20** | Plans + subscriptions + entitlement computation exist. No provider, no invoices, no checkout, no metering; plan limits enforced nowhere |
| Database & Migrations | **55** | Thoughtful schema, denormalized `org_id` with a reason, `_EventScoped` base, soft deletes, append-only audit. −: no working migrations, Python-only uniqueness, no enums/CHECKs, `INTEGER` size_bytes |
| Frontend Architecture | **80** | One consolidated `ui/`, shared reducer for both consoles, clean route gates, real code splitting for the heavy areas. −: legacy shell, mocks load-bearing, `dummy.js` wired, org console eager |
| Code Quality & Docs | **90** | Unusually high. Nearly every non-obvious decision explains *why*; `ponytail:` markers name the ceiling and the upgrade path; the "no invented telemetry" rule is held consistently |
| Testing & CI | **25** | 6 of 8 suites pass and they are well-written pure checks. But one is red, two need a DB, **nothing runs any of them**, no CI, no frontend tests, no pinned deps |
| Scalability | **48** | Correct instincts everywhere (`tx()` off the loop, hot state in the bus, Redis-bridged fan-out) and every ceiling documented. −: per-process tickers block multi-worker, O(messages) analytics, no retention, no observability |
| Security posture | **58** | Good model, unshipped hardening. One committed credential, client-side role forgery, prod localhost CORS, default secret |

### Overall enterprise readiness: **63 / 100**

Weighted for a live-events SaaS: Security 20 · Streaming & LiveKit 20 · Event/Org domain 15 · Realtime 15 · Data & migrations 10 · Frontend 10 · Testing/ops 10.

The prior audit scored 68 on a *different* weighting (architecture-centric). Comparing like for like, real progress landed in the last two days — the two unauthenticated stream endpoints, the two 500ing admin endpoints, the 200-instead-of-403 denials, the undersized pool, the missing rate limits and the schema collision are all genuinely fixed, and the entire viewer experience is now real. The number does not rise further because this audit weights **the product's ability to stream and to onboard a team** more heavily, and both of those are still blocked by two narrow, well-understood gaps.

**The one-sentence read:** this is a codebase with senior-level design judgement and junior-level operational maturity — a finished, honest, well-documented control room and audience surface, connected to a camera that was never plugged in, guarded by a door whose handle is on the wrong side.

---

# PRIORITIZED ROADMAP

Based only on what exists. Each item names the file that already contains the seam.

### Phase A — live defects (hours)
1. **Rotate the leaked super-admin credential** and remove the literal from `seed.py:127,137` — read from env, or generate and print once.
2. **Close the invitation loop.** Three edits, one commit: align `_invite_url` (`organization.py:270`) with the route in `App.jsx:135`; make `AcceptInvitation.jsx` read `?token` and `POST /organization/invitations/accept`; delete `auth/dummy.js`. This alone unblocks steps 8–9 of the enterprise flow *and* closes S2.
3. Gate `/host/dashboard` and `/moderator/dashboard` with `RoleRoute` (`allow: ["host","org_admin","super_admin"]` / `["moderator","org_admin","super_admin"]`).
4. Refuse to boot with the default `SECRET_KEY` outside dev (`config.py:46` already detects it — raise instead of warn).
5. Gate the localhost CORS regex on an env flag (`main.py:71`).
6. **Commit the 4 untracked files** — the viewer feature's new code is not in git.

### Phase B — foundations (before any new feature)
7. **Adopt Alembic for real:** `target_metadata = Base.metadata`, one baseline revision stamping current state, then migrations only. Freeze `create_tables.py`'s ALTER list; retire `update_stream_table.py`.
8. **CI:** run the 6 green self-checks + `npm run lint && npm run build`. Fix or retire `test_invitations.py`'s 401/403 assertion. Regenerate `openapi.json` in CI or delete it.
9. Pin `requirements.txt`; drop `aiofiles`/`pillow`/`python-multipart` until upload lands; drop `react-hook-form`, `socket.io-client`, `dayjs`.
10. Add the DB constraints the code already claims: unique org slug, unique `(org_id, slug)` on events, one active subscription per org. Widen `LiveRecording.size_bytes` to `BigInteger`.
11. Hash `users.reset_token`; add an OTP attempt counter (lock after 5).
12. Enforce `Organization.status == "suspended"` in `get_current_user` — a stored-but-unenforced access rule is worse than an absent one.
13. Have the client call `GET /auth/me` on mount and rehydrate; delete the stale "dummy auth" docstring in `AuthContext.jsx`.

### Phase C — make it stream
14. **Storage.** Configure egress output (S3/GCS) in `livekit._egress_request` and make `file_url` fetchable. Nothing downstream works without this.
15. **Browser publisher.** Wire `livekit-client` into `StudioStage` using the `publish_token` already reaching it at `StudioStage.jsx:161`, driven by the `useMediaPreview` stream. Both server call sites (`broadcast.py:346,747`) were written as drop-in points. **This is the single highest-value change in the repo.**
16. **Recordings API.** `GET /recordings` (org-scoped over `live_recordings`), signed download URLs, replay source. Retire `data/recordings.js`; fix its `/events/{mock-id}/watch` links.
17. **Registration.** `registrations` table + `POST /events/{id}/register`, honouring `registration_limit`; then flip `viewer.access_for` to actually check it (`services/viewer.py:63` marks the exact spot). Rebuild `/e/:id` against real data.
18. **Anonymous viewer path** for `public` events — a scoped, short-lived token for an unauthenticated attendee. `roleHome.js:5` already promises it.
19. **Attendee chat + Q&A UI.** The server already permits `chat.send`, `chat.react`, `qa.ask`, `qa.vote` for viewers and already fans the `chat`/`qa` channels to them (`moderation.py:376,422`). This is UI-only work on a finished backend.
20. **Attendee waiting-room state** on the watch page — the host side is complete.
21. **Assignment + reminder emails** using the existing `email.py` pattern (a `_x_html()` builder + a `send_x_email()` + a self-check assertion).
22. Org analytics API over `analytics_snapshots`; retire `data/analytics.js`.

### Phase D — scale
23. Move the three tickers out of `lifespan` into one leader (Redis lock) or a dedicated worker. This unblocks multi-worker deployment (C1).
24. Fix the analytics query shape (P1): counter columns or incremental aggregation instead of re-summing every reaction blob every 15 s. Parallelise the sampler. Add retention/rollup.
25. Make `delivered_to` cross-worker (Redis counter, not `local_subscribers`).
26. Server-side pagination on the list screens; lazy-load the org console bundle and recharts.
27. Observability: structured logs, a metric on dropped bus envelopes and pool checkout waits, a `/health` that actually checks the DB.
28. Refresh tokens + a revocation list; invalidate on password and role change.

### Phase E — remaining surface
29. Billing provider + invoices + usage metering + plan-limit enforcement (`storage_used_gb`/`bandwidth_gb` are never written).
30. Asset upload (what `pillow`/`aiofiles`/`python-multipart` were installed for); apply org branding to the rendered UI and the attendee page.
31. Notification dispatcher + in-app feed; MFA/SSO/IP-allowlist enforcement; DNS domain verification; outbound webhooks; API-key authentication; feature-flag reads at request time.
32. Event duplicate, event edit form, cancel affordance.
33. Retire `MainLayout` + `pages/Dashboard` + `components/Dashboard/*` once speaker and viewer have real homes; move `fmtDate`/`initials`/`isEmail` out of `data/*` into `utils/` **before** deleting the mocks; consolidate the three sidebars/topbars.

---

# FILES THAT SHOULD NOT BE CASUALLY MODIFIED

`server/app/security.py` (the entire authN/authZ surface + `org_scoped`) · `server/app/db.py` · `server/app/config.py` · `.env` · `server/app/models/*.py` (schema truth with **no migration path**) · `server/app/services/bus.py` (dual-mode contract must hold in both branches or it breaks only in production) · `services/moderation.py` — `dispatch`/`resolve_ctx`/`tx`/`_row`/`_scoped` (the only thing stopping a wire id reaching another tenant's row) · `services/viewer.py` — `access_for` (the attendee authorization rule, shared with the socket) · `crud/event.py` — `status_transition_error` · `routers/organization.py` — `get_my_org`/`get_my_org_admin` · `server/create_tables.py` (append-only history; reordering breaks existing databases) · `client/src/api.js` · `client/src/auth/AuthContext.jsx`, `RoleRoute.jsx`, `ProtectedRoute.jsx` · `client/src/index.css` (the emerald→brand remap) · `client/src/hooks/useEventStream.js` (reconnect/backoff/jitter/resync).

# FILES SAFE TO EXTEND

**Backend:** `routers/admin.py` (add route + `_audit`) · `routers/events.py` (`_get_event_or_404` + `require_org_admin`/`_can_edit`) · `routers/organization.py` (`get_my_org*`) · `crud/*` (pure queries) · `services/admin.py`, `services/org.py`, `services/ops.py` (derived readers — keep the no-fabricated-numbers rule) · `services/broadcast.py` (new host action → local `ACTIONS`; `HOST_ONLY` picks up `broadcast.*`/`recording.*` automatically) · `services/moderation.py` (new action → `ACTIONS`, and `VIEWER_ACTIONS` only if truly unprivileged) · `services/viewer.py` (new viewer-safe field → the relevant `_x_out`) · `services/livekit.py` (follow `_with_room`, always return whether it was enforced) · `models/live.py` (subclass `_EventScoped` and inherit the isolation contract) · `email.py` (a `_x_html()` + `send_x_email()` + a `__main__` assertion) · `test_*.py`.

**Frontend:** `ui/` + `ui/tokens.js` · `hooks/` (`useMutation` is the obvious next one — ~15 duplicated call sites) · `utils/export.js` (the home for `initials`/`isEmail`/formatters) · `pages/**` (follow `organization/Events.jsx`) · `components/{admin,organization,host,moderation,watch}/` · `hooks/useLiveEvent.js` + `useViewerEvent.js` reducers (`default: return state` makes an unhandled envelope inert, so new server channels are safe) · `App.jsx` (replace `Placeholder`s one at a time) · `data/*.js` — **delete-only**.

---

*End of audit. No code was modified. No components were generated. No APIs were added. No schema was changed.*
