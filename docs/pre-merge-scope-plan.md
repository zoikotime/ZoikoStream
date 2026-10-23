# Pre-merge scope plan — branch `feature/organization-dashboard-sidebar-ui`

Status: **proposal for review**. Nothing here has been staged, committed, split or merged.
Prepared 2026-10-23 against base `5654378` (= local `main` = `origin/main` as last fetched on
2026-10-21; the remote was not refreshed for this plan). Every change listed is **uncommitted** in
the working tree; the branch (created at that base, no upstream yet; first drafted as `naveen`,
the same working tree) has no commits of its own.

Merging to `main` deploys to production (`.github/workflows/deployVM.yml` runs on every push to
`main`), so the scope of the first merge is the scope of the first production release.

## 1. Workstreams

| Id | Workstream | Production risk |
|---|---|---|
| **A** | Organization console and Event Workspace redesign, including the activity/audit feature (UI + API + `audit_logs` schema) | Schema change on `audit_logs`; new endpoints; console-wide visual change |
| **B** | Accessibility and contrast corrections (acceptance-audit fixes, final contrast pass, avatar contrast fix) | Low; colour/class changes plus two keyboard fixes |
| **C** | API-key expiration reminders (lifecycle states, inventory counts, reminder emails at 30/14/7/3/1 days) | Customer-facing email; key lifecycle logic |
| **D** | Sign-in reliability (session confirmation, duplicate submit, toast de-duplication) | Touches the login path |
| **E** | Local development port 8000 → 8001 and related dev configuration | Machine-specific; reverses a documented decision |
| **F** | Release safety: VM deploy migration gate and schema verification; production configuration safety (no development API address in a build; Guide leads delivered to the API's own port) | Changes the production deploy workflow; needs a new secret; a build now refuses output that names a loopback URL |

## 2. Recommended first release

**Include A + B + F. Exclude C, D and E** (each to its own later, separately reviewed change).

Why:
* **A** is the feature set being released; **F** is required for A to deploy safely (A adds
  `audit_logs` columns that the new code writes on every audited action).
* **B** corrects accessibility defects in A's own UI (most B changes live in A's new files and
  cannot ship without A) plus a handful of pre-existing shared components.
* **C** sends new customer-facing emails and changes key lifecycle reporting — it deserves its
  own review and a staging check against a non-production email provider.
* **D** changes the sign-in path; unrelated to the console, smallest blast radius on its own.
* **E** is specific to one development machine (port 8000 occupied by another local API). It
  changes defaults for every developer and reverses a regression guard (see §5, E).

### Prerequisites before the first merge

1. **`DATABASE_URL` repository secret** in GitHub Actions (the production database URL, as Cloud
   Build already has in Secret Manager). Without it the new `migrate` job fails closed and the
   production deploy does not run — by design.
2. **Network access** from GitHub-hosted runners to the production database (check any IP
   allowlist). Unverified.
3. **`/opt/zoikostream/deploy.sh` on the VM** (not in this repository) confirmed by its owner:
   which commit it deploys (it should deploy the pushed commit, or at least not a newer one than
   the `migrate` job applied), and whether it also runs `create_tables.py` (safe: idempotent and
   sequential after the `migrate` job).
4. **Which deploy path serves production** (`deployVM.yml`, Cloud Build `cloudbuild.yaml`, or
   both). If both run on one push, two schema runs can overlap: the statements are idempotent,
   but two concurrent `CREATE INDEX` on the same name can make one run fail with "already
   exists" — that path's deploy then stops (fail-closed) and a re-run succeeds.
5. **Staging measurement** of `python create_tables.py` on a production-size copy of
   `audit_logs`: the two new indexes are built without `CONCURRENTLY` (writes to `audit_logs`
   wait while each builds) and the backfills are single unbatched `UPDATE`s.
6. **Approvals** (none claimed here): the Inter typeface (`@fontsource-variable/inter`, OFL-1.1)
   and brand sign-off; the console action ramp colours; "faint" and "muted" text sharing a
   colour in light mode; the Audience two-column layout (changes the requirement pinned by
   `SingleColumnPages.test.jsx`); the Overview tile wording "Needs Action".
7. **How the VM builds the frontend** (`/opt/zoikostream/deploy.sh`): whether it runs `npm run build`
   or deploys an image, which env files that build reads, and what `VITE_API_URL` it receives. F's
   build guard now keeps a loopback `VITE_API_URL` out of any bundle, but the value and the build
   location are unverified. A failed Vite build empties its out dir first, so a build in the served
   `client/dist` should be made in a separate directory and swapped in.
8. **`GUIDE_CONTACT_ENDPOINT`** wherever `GUIDE_ENABLED=true` (VM and Cloud Run): unset, so the API
   posts each lead to its own listening port, or a value that reaches this API's `/api/contact`. A
   value copied from the old `.env.example` (`http://127.0.0.1:8002/api/contact`) overrides that and
   loses every lead unless the API really listens on 8002.

### Resolved: A no longer depends on C's `key_summary`

`client/src/pages/organization/DeveloperPlatform.jsx` (A) reads `key_summary` from
`GET /organization/developer`, a field added by C. It used to branch the API-key tile's caption
on that field alone, so without C's backend the caption read **"Loading…" permanently**.

Fixed (option 1 below): `keyInventory()` uses the server's `key_summary` when present and
otherwise derives total/active from the key list by the same rule (revoked, or at/after
`expires_at`, is not active; a per-key `usable` from the server wins); "Loading…" shows only while
the request is outstanding; a response with no key list reads "Key inventory not reported".
Regression tests in `DeveloperPlatform.test.jsx` (verified to fail against the old logic).

A can therefore ship without C. The options considered were:

1. **(done)** Ship A without C, with the caption's fallback fixed and tested.
2. Ship C's read-only inventory pieces with A **without** the reminder sender.
3. Ship A and C together after reviewing C in full (customer emails included).

## 3. Mixed-purpose files (logical-change level)

| File | Belongs to | Logical changes |
|---|---|---|
| `server/app/crud/admin.py` | A + C | **A:** `create_audit_log` gains `event_id`, `outcome`, `actor_role` (3 hunks). **C:** `from sqlalchemy import inspect as sa_inspect` (used by `_lock_keys`), `key_fingerprint`, key status constants, `key_status`, lifecycle/summary helpers, `_parse_ts`, `_lock_keys`, `_mutate_key`, `claim_key_notification`, `mark_key_expired`, `rotate_api_key`, `create_api_key(created_by=…)`, `revoke_api_key`. |
| `server/app/routers/organization.py` | A + C | **A:** imports `activity_log`, `event_workspace`; `_workspace_event`; `GET /events/{id}/readiness`, `/live-status`, `/insights`, `/activity`, `/activity/{entry}`; `GET /activity`, `/activity/{entry}`; `GET /team/access`; `90d` window on `/playback/qoe`. **C:** `get_developer` (one `now`, `list_api_keys`, `key_summary`, `generated_at`), `create_developer_api_key` and `create_my_api_key` (`created_by`). |
| `server/create_tables.py` | A + F | **A:** `_AUDIT_LOG_COLUMNS` (`event_id`, `outcome`, `actor_role`), `_AUDIT_LOG_STATEMENTS` (2 indexes, 3 backfills) and their execution in `ensure_schema`. **F:** `missing_schema`, `verify_schema`, `main` (`--check`), `sys.exit(main())`. |
| `client/src/pages/organization/DeveloperPlatform.jsx` | A (reads C, optional) | A redesign of the Developers page; uses C's `key_summary` when the server sends it and falls back to the key list otherwise (see §2). |
| `client/src/pages/organization/DeveloperPlatform.test.jsx` | A (mocks C) | Tests A's page; mocks C's `key_summary`. |
| `client/src/components/organization/ApiCredentials.jsx` | C (+ minor A styling) | Predominantly C (expiry states, inventory, filters, `data/apiKeyExpiry`); a few console-styling lines from A. Treat as C; review hunk by hunk if A's styling is wanted earlier. |
| `client/src/ui/tokens.js` | A + B | **A:** `consoleAction` (console primary ramp). **B:** `actionStops`, `actionStopsHover`, AA stops in `consoleAction`, `consoleChip`, `CONSOLE.faint` → slate-500. |
| `client/src/ui/Button.jsx` | A + B | **A:** console `primary` uses `consoleAction` (also affects super-admin, host and step-up console buttons). **B:** comment update (hover deepens). |
| `client/src/components/Dashboard/Sidebar.jsx` | A + B | **A:** NAV-001 rail (groups, children, badges, footer, workspace card). **B:** `actionStops` on the active row and Create event; rail section labels white/55; active-row hint backing; closed mobile drawer `max-lg:invisible`; `consoleChip` on the footer avatar and workspace badge. |
| `client/src/components/Dashboard/Topbar.jsx` | A + B | **A:** search, help, account menu, event back-link. **B:** `consoleChip` on both avatars. |
| `client/src/components/admin/Panel.jsx` | A + B | **A:** `id` anchor + `scroll-mt-24`. **B:** eyebrow uses `CONSOLE.faint`. |
| `client/src/components/organization/OrganizationPageHeader.jsx` | A + B | **A:** breadcrumbs, eyebrow. **B:** breadcrumb colour slate-500. |
| `client/src/pages/organization/Dashboard.jsx` | A + B | **A:** ORG-01 rebuild; tile label "Needs Action". **B:** table header muted; media-state 700 shades; CTAs on `actionStops`. |
| `client/src/pages/organization/Events.jsx` | A + B | **A:** list/calendar, tabs, row actions. **B:** tab counts slate-600; "today" cell on `actionStops`. |
| `client/src/pages/organization/Recordings.jsx` | A + B | **A:** ORG-04 rebuild. **B:** table header and "Storage used" label muted. |
| `client/src/pages/organization/Analytics.jsx` | A + B | **A:** ORG-05 rebuild. **B:** three literal slate-400 texts → `CONSOLE.faint`. |
| `client/src/pages/organization/AudienceAccess.jsx` | A + B | **A:** ORG-03 rebuild, plus the restored sort control (visibility, capacity). **B:** governance eyebrow white/60; policy strip body copy. |
| `client/src/pages/organization/OrganizationHub.jsx` (new) | A + B | **A:** ORG-06 hub. **B:** table header muted. |
| `client/src/components/organization/ActivityLedger.jsx` (new) | A + B | **A:** EVT-07 / Security ledger. **B:** header muted; tab count inherits; selected-row small print slate-600. |
| `client/src/components/organization/Freshness.jsx` (new) | A + B | **A:** freshness readout. **B:** "As of" in body colour. |
| `client/src/components/organization/console/ConsoleUI.jsx` (new) | A + B | **A:** console primitives. **B:** caption/subtitle/detail/footnote muted; banner body copy. |
| `client/src/components/organization/console/RowActionsMenu.jsx` (new) | A + B | **A:** row overflow menu. **B:** Tab hands focus back to the trigger. |
| `client/src/pages/organization/event/EventLiveControl.jsx` (new) | A + B | **A:** EVT-04. **B:** header muted; "Event time" body colour on the rose strip. |
| `client/src/pages/organization/event/EventRail.jsx` (new) | A + B | **A:** event rail. **B:** back-link label white/55. |
| `client/src/pages/organization/event/EventSectionHeader.jsx` (new) | A + B | **A:** section header. **B:** breadcrumb slate-500. |
| `.env.example` | E + F | **E:** `VITE_API_URL` → 8001 and its comment; Stripe forward → 8001. **F:** the Guide contact endpoint is left unset — a commented example and explanation replace the `127.0.0.1:8002` value. |
| `client/src/layouts/AppShell.jsx` | A | Inter import (**needs approval**) and the workspace `content` slot. |
| `client/src/index.css` | A | Inter on `.zk-console`; moves `text-wrap` rules into `@layer base` (an **app-wide** fix: they cancelled `truncate` everywhere, marketing pages included). |
| Test files with both A and B cases | A + B | `client/src/pages/organization/Dashboard.test.jsx`, `client/src/pages/organization/AudienceAccess.test.jsx`, `client/src/pages/organization/EventsRowActions.test.jsx`, `client/src/pages/organization/EventsViews.test.jsx`, `client/src/layouts/OrganizationShell.test.jsx`, `client/src/pages/organization/event/EventWorkspace.test.jsx` — B adds cases to A's suites; ship with A. |

## 4. File inventory

Every changed, deleted and untracked file. "(new)" = untracked; "(deleted)" = removed.

### A — console and Event Workspace redesign, activity/audit

Backend:
`server/app/main.py` (request reference middleware, `X-Request-Id` exposed) ·
`server/app/models/audit_log.py` (`event_id`, `outcome`, `actor_role`, insert listener) ·
`server/app/request_context.py` (new) ·
`server/app/services/activity_log.py` (new) ·
`server/app/services/event_audit.py` (new) ·
`server/app/services/event_workspace.py` (new) ·
`server/app/routers/events.py` (audit receipts, readiness on list, attention counts) ·
`server/app/services/ops.py` (`readiness_for`, `readiness_many`) ·
`server/app/services/org.py` (on-air badge, rail role/features, `developer_access_in_use`) ·
`server/app/services/audience.py` (`whole_event` insights) ·
`server/app/services/moderation.py` (actor role on live-console audit rows) ·
`server/app/services/playback_qoe.py` (`90d` window, UTC series) ·
`server/app/crud/commercial.py` (`actor_role` on audit rows) ·
`server/app/crud/event.py` (`on_air` / `upcoming` display filters) ·
`server/app/schemas/event.py` (display filters, readiness fields) ·
`server/create_tables.py` (A part — see §3) ·
`server/app/crud/admin.py`, `server/app/routers/organization.py` (A parts — see §3).

Backend tests:
`server/test_event_activity_audit.py` (new) · `server/test_team_access.py` (new) ·
`server/test_ops.py` · `server/test_playback_qoe.py`.

Frontend (new):
`client/src/components/Dashboard/orgNav.js` · `client/src/components/Dashboard/orgNav.test.js` ·
`client/src/components/organization/console/consoleStyles.js` ·
`client/src/data/activity.js` · `client/src/data/eventCalendar.js` · `client/src/data/eventCalendar.test.js` ·
`client/src/data/eventTime.js` · `client/src/data/eventTime.test.js` ·
`client/src/pages/organization/EventsCalendar.jsx` · `client/src/pages/organization/EventsViews.test.jsx` ·
`client/src/pages/organization/OrganizationHub.test.jsx` · `client/src/pages/organization/SecurityAudit.jsx` ·
`client/src/pages/organization/event/EventActivity.jsx` · `client/src/pages/organization/event/EventAudienceAccess.jsx` ·
`client/src/pages/organization/event/EventInsights.jsx` · `client/src/pages/organization/event/EventLifecycleActions.jsx` ·
`client/src/pages/organization/event/EventOverview.jsx` · `client/src/pages/organization/event/EventPreparation.jsx` ·
`client/src/pages/organization/event/EventReplay.jsx` · `client/src/pages/organization/event/EventWorkspace.test.jsx` ·
`client/src/pages/organization/event/EventWorkspaceLayout.jsx` · `client/src/pages/organization/event/PeoplePanel.jsx` ·
`client/src/pages/organization/event/ReportsPanel.jsx` · `client/src/pages/organization/event/workspace.js` ·
`client/src/pages/organization/DeveloperPlatform.test.jsx` ·
`client/src/data/keyInventory.js` · `client/src/data/keyInventory.test.js` (the Developers page's
API-key figures with or without C's `key_summary` — §2) ·
plus the A+B new files in §3 (`OrganizationHub.jsx`, `ActivityLedger.jsx`, `Freshness.jsx`, `ConsoleUI.jsx`,
`RowActionsMenu.jsx`, `EventLiveControl.jsx`, `EventRail.jsx`, `EventSectionHeader.jsx`).

Frontend (modified):
`client/src/App.jsx` (workspace routes, Hub, Security & audit) ·
`client/src/pages/organization/EventDetails.jsx` (deleted — replaced by the Event Workspace) ·
`client/src/components/admin/DataTable.jsx` (`itemLabel`, uppercase sortable headers) ·
`client/src/components/organization/dashboard/useDashboardData.js` ·
`client/src/layouts/OrganizationLayout.jsx` · `client/src/layouts/AppShell.jsx` · `client/src/index.css` ·
`client/src/pages/organization/Billing.jsx` · `client/src/pages/organization/Settings.jsx` ·
`client/src/pages/organization/InviteMembers.jsx` · `client/src/pages/organization/LiveInputs.jsx` ·
`client/src/pages/organization/PlaybackAccess.jsx` · `client/src/pages/organization/StreamingSessions.jsx` ·
`client/src/pages/organization/SupportStatus.jsx` · `client/src/pages/organization/TrustEvidence.jsx` (page headers) ·
`client/src/components/organization/SessionsTable.jsx` (link to Live Control) ·
`client/src/components/organization/RecordingPanel.jsx` · `client/src/data/timezones.js` ·
`client/src/hooks/useMutation.js` · `client/src/pages/organization/AssignPeopleModal.jsx` (comments only) ·
`client/src/pages/organization/DeveloperPlatform.jsx` (see §2) ·
`client/package.json` · `client/package-lock.json` (Inter; manifest and lock agree) ·
plus the A+B modified files in §3.

Frontend tests (modified):
`client/src/components/organization/QuickActionsMenu.test.jsx` · `client/src/pages/organization/Analytics.cards.test.jsx` ·
`client/src/pages/organization/Analytics.engagement.test.jsx` · `client/src/pages/organization/EventStatusLifecycle.test.jsx` ·
`client/src/pages/organization/EventsMissedStatus.test.jsx` · `client/src/pages/organization/EventsRequestLiveEvent.test.jsx` ·
`client/src/pages/organization/EventsViewerLinkColumn.test.jsx` · `client/src/pages/organization/Recordings.test.jsx` ·
`client/src/pages/organization/RecordingsCategoryFilter.test.jsx` ·
`client/src/pages/organization/SingleColumnPages.test.jsx` (**requirement change**: Audience is now two columns — needs product sign-off).

### B — accessibility and contrast

B-only files (independent of A; could ship on their own):
`client/src/components/organization/EventCommercial.jsx` (table header colour) ·
`client/src/components/admin/StatCard.jsx` · `client/src/ui/StatsCard.jsx` (hint colour) ·
`client/src/components/organization/ViewingSessionsPanel.jsx` · `client/src/components/organization/ViewingSessionsPanel.test.jsx` ·
`client/src/components/organization/dashboard/MetricCard.jsx` (non-link tiles become named groups) ·
`client/src/pages/organization/InviteMembersStepUp.test.jsx` (test race: waits for focus).

B tests (new/modified):
`client/src/ui/contrast.test.js` (new; computes ratios from Tailwind's palette) ·
`client/src/components/organization/console/consoleContrast.test.jsx` (new) ·
`client/src/components/Dashboard/topbarAccountMenu.test.jsx` (avatar chips).

B changes inside A files: see §3.

### C — API-key expiration reminders (not in the first release)

`server/app/services/credential_lifecycle.py` ·
`server/app/email.py` (reminder subjects and copy; also fixes the key-created email's CTA, which pointed at
`/organization/developer`, not a console route, to `/organization/settings?tab=developer`) ·
`server/app/routers/admin.py` · `server/app/schemas/admin.py` · `server/app/schemas/organization.py` ·
`server/test_api_key_expiry.py` (new) · `server/test_developer_lifecycle.py` · `server/test_developer_platform.py` ·
`client/src/components/organization/ApiCredentials.jsx` · `client/src/components/organization/ApiCredentials.expiry.test.jsx` (new) ·
`client/src/data/apiKeyExpiry.js` (new) · `client/src/data/apiKeyExpiry.test.js` (new) ·
C parts of `server/app/crud/admin.py` and `server/app/routers/organization.py` (§3).

### D — sign-in reliability (not in the first release)

`client/src/auth/AuthContext.jsx` (`setSession` returns null when `/auth/me` refuses the new session) ·
`client/src/pages/auth/Login.jsx` (in-flight guard, one toast slot) ·
`client/src/pages/auth/LoginFlow.test.jsx` (new).

### E — local development port (not in the first release; keep local)

`.env.example` (E part — §3) · `SETUP_GUIDE.md` · `server/README.md` · `server/run-dev.ps1` (new) ·
`client/e2e/env.js` · `client/playwright.config.js` ·
`client/src/apiBase.js` (dev fallback 8000 → 8001; HEAD's comment documents why it was set to 8000) ·
`client/src/api.js` (development-only "the request went to …" hint) ·
`client/src/apiConfig.test.js` (new; pins 8001) ·
`client/src/pages/organization/Billing.loadError.test.jsx` (**assertion reversed**: HEAD asserted the fallback is
*not* 8001; it now asserts it *is*).

`server/app/guide/settings.py` is no longer in E: its port-specific `GUIDE_CONTACT_ENDPOINT` default (8000 at
HEAD, 8001 here) was replaced by F's port-independent one, so the first release carries neither port.

### F — release safety

`.github/workflows/deployVM.yml` (`migrate` job before `deploy`; fail-closed on a missing `DATABASE_URL`
secret; serialised, never cancelled mid-run; the SSH step itself unchanged) ·
`server/create_tables.py` (F part — §3) ·
`server/test_deploy_migration_gate.py` (new) ·
`docs/pre-merge-scope-plan.md` (this document).

Production configuration safety (from the port audit):
`client/vite.config.js` (`envDir` made absolute; a build does not embed a loopback `VITE_API_URL`, and refuses
output that names any loopback URL with a port; `ZOIKOSTREAM_ALLOW_LOOPBACK_API=1` for a deliberate local build) ·
`client/src/loopbackApiGuard.js` (new) · `client/src/loopbackApiGuard.test.js` (new) ·
`server/app/guide/settings.py` (`GUIDE_CONTACT_ENDPOINT` defaults to empty) ·
`server/app/guide/adapters.py` (unset → the port the lead's request arrived on, over loopback; no TCP port →
`failed`/`unconfigured`, nothing sent) · `server/app/guide/leads.py` (passes the request's server address) ·
`server/guide_tests/test_leads.py` (fake adapter accepts it; one new case) ·
`server/test_guide_contact_endpoint.py` (new) · `.env.example` (F part — §3).

## 5. Dependencies

* **A → F.** A writes new `audit_logs` columns on every audited action; F guarantees the schema step
  completes (and verifies) before the VM deploy starts. Cloud Build already gates on its `migrate` step.
* **A → C (`key_summary`): optional.** Resolved — the page works with or without the field; both paths are tested (§2).
* **B → A.** Most B changes are in A's new files or recolour A's new styles (`consoleAction`, the rail,
  console primitives). Ship B with A. The B-only files in §4 have no dependency on A.
* **E ↔ tests.** Excluding E means keeping HEAD's `apiBase.js` *and* HEAD's `Billing.loadError.test.jsx`
  together, and leaving out `apiConfig.test.js`; they encode the port and must move as one.
* **F ↔ E: independent.** The build guard keys on loopback hosts, not on a port, and the Guide endpoint
  no longer names a port, so both behave the same with E in or out.
* **D** has no dependency on A, B, C, E or F.
* **F** has no code dependency on A (its verification covers every model); it needs the prerequisites in §2.

## 6. Tests by workstream

| Workstream | Tests |
|---|---|
| A (backend) | `test_event_activity_audit.py`, `test_team_access.py`, `test_ops.py`, `test_playback_qoe.py`; related existing suites `test_org_overview.py`, `test_audience_insights.py`, `test_event_missed_status.py`, `test_event_status_lifecycle.py` |
| A (frontend) | `Dashboard.test.jsx`, `EventStatusLifecycle.test.jsx`, `EventsMissedStatus.test.jsx`, `EventsRequestLiveEvent.test.jsx`, `EventsRowActions.test.jsx`, `EventsViewerLinkColumn.test.jsx`, `EventsViews.test.jsx`, `event/EventWorkspace.test.jsx`, `OrganizationShell.test.jsx`, `orgNav.test.js`, `OrganizationHub.test.jsx`, `Recordings.test.jsx`, `RecordingsCategoryFilter.test.jsx`, `Analytics.cards.test.jsx`, `Analytics.engagement.test.jsx`, `AudienceAccess.test.jsx`, `SingleColumnPages.test.jsx`, `QuickActionsMenu.test.jsx`, `DeveloperPlatform.test.jsx`, `eventCalendar.test.js`, `eventTime.test.js` |
| B | `ui/contrast.test.js`, `console/consoleContrast.test.jsx`, `topbarAccountMenu.test.jsx`, `ViewingSessionsPanel.test.jsx`, `InviteMembersStepUp.test.jsx`, plus the B cases inside the A suites listed in §3 |
| C | `test_api_key_expiry.py`, `test_developer_lifecycle.py`, `test_developer_platform.py`, `ApiCredentials.expiry.test.jsx`, `apiKeyExpiry.test.js` |
| D | `LoginFlow.test.jsx` |
| E | `apiConfig.test.js`, `Billing.loadError.test.jsx` (reversed assertion) |
| F | `test_deploy_migration_gate.py`, `test_guide_contact_endpoint.py`, `loopbackApiGuard.test.js`; one case in `guide_tests/test_leads.py` (that suite needs a Postgres with pgvector) |

## 7. Open items requiring investigation

* `ApiCredentials.jsx` — if any of A's styling is wanted in the first release, review its 36 hunks
  individually; otherwise leave the file entirely to C.
* The VM deploy script (including how it builds the frontend), the active deploy path(s), the GitHub secret,
  runner network access and each environment's `GUIDE_CONTACT_ENDPOINT` (§2, items 1–4, 7–8).
* Remote `main` was last fetched 2026-10-21; refresh it and re-check shared files (Button, tokens, routing,
  `crud/admin.py`, events router) for conflicts before building the release change.
