# ZoikoStream Frontend — Phase 0 Architecture Audit & Refactoring Plan

**Scope:** `client/src` only. React 19 + Vite 8 + Tailwind v4 + react-router 7 + recharts.
**Status:** AUDIT ONLY. Nothing has been moved, merged, renamed, or deleted. This document is the plan to be approved before any change.
**Method:** Deterministic import-graph analysis (every relative import resolved against every file) + full read of all 151 source files across 3 parallel deep-reads. Every "safe to delete" claim is backed by a zero-reference grep.

> **Spec note:** The Canonical Product Spec, UX/UI Engineering Spec, Homepage Wireframes, and Live Events Spec were referenced in the task but not attached. This audit is grounded in the *actual code*. If those docs define a target visual/IA that should override "consolidate onto what exists," attach them and this plan will be reconciled.

---

## 0. Headline findings

1. **Three overlapping "design systems" coexist** — `ui/*` (marketing + de-facto shared primitives), `components/admin/*` (the newest, richest app system), and `components/Dashboard/*` + `MainLayout` (legacy generic dashboard). They independently define Button, Badge, StatCard, tokens, sidebar, topbar, and chart cards.
2. **Two token files with colliding names** — `ui/tokens.js` and `components/admin/tokens.js` both export `ACCENT` and `surface` with *different types/meanings*. Pages import from both, sometimes in the same file. This is the root cause of the violet-vs-emerald brand inconsistency.
3. **An excellent `DataTable` exists but is used in only 4 places; raw `<table>` is hand-rolled in 10.**
4. **The org area is a half-finished migration** — 4 pages moved to the admin system + `useApi` + shared header/error/empty; 4 pages (Recordings, Analytics, Billing, Settings) never migrated and still hand-roll everything on mock state.
5. **6 confirmed dead code items, 8 empty scaffold folders, 2 unused dependencies.**
6. **`initials()` is defined ~9 times; number/currency/storage formatting is scattered ~20 places; the API-mutation-with-toast pattern is copy-pasted ~15 times.**

---

## 1. Complete frontend audit

### 1.1 Project & config
| Item | Value |
|---|---|
| App root | `client/` (Vite). Backend is separate (`server/`) — untouched. |
| Entry | `main.jsx` → `App.jsx` (all routes) |
| Config | `vite.config.js` (react + tailwind plugin, `envDir:'..'`), `eslint.config.js`, `vercel.json` (SPA rewrite), `index.html` |
| Deps (used) | react, react-dom, react-router-dom, recharts (6 files), react-hot-toast (5), react-icons (64), axios (1) |
| Deps (**dead**) | **`react-hook-form` (0 imports), `socket.io-client` (0 imports)** |
| Deps (near-dead) | `dayjs` — 1 trivial use (`PlatformStatus.jsx:48`) |
| Global CSS | `index.css` — Tailwind v4 `@theme`, brand remap (**emerald→violet, teal→pink**), `zk-*` keyframes/utilities, focus-ring, skeleton shimmer. Clean, shared. |

> ⚠️ **Do not "fix" green/teal classes:** `index.css:13-37` remaps the `emerald` and `teal` Tailwind scales to the brand purple/pink. Components written with `emerald-*`/`teal-*` render on-brand by design.

### 1.2 Directory map (files : lines)
```
src/
  main.jsx(11)  App.jsx(168)  api.js(24)  index.css(159)
  auth/         AuthContext(37)  roleHome(17)  dummy(50)
  theme/        ThemeContext(18)
  hooks/        useApi(49)                         [NEW, untracked in git]
  data/         12 mock files (~1150 lines total)
  ui/           30 files — design system + marketing primitives
  layouts/      MainLayout(160)  OrganizationLayout(45)  AdminLayout(22)  AuthLayout(118)
  components/
    ProtectedRoute(10)  RoleRoute(14)
    admin/      21 files (primitives + tokens + 12 sections/)   [newest system]
    Dashboard/  10 files (legacy/mid-gen chart cards + sidebar/topbar)
    organization/ 3 files (PageHeader, EmptyState, ErrorState)  [NEW, untracked]
    home/        18 files across 14 sub-folders (marketing sections)
    host/ 6   moderation/ 6   watch/ 5
    common/ layout/                                 [EMPTY]
  pages/
    Home/Home(47)  Dashboard(253)  EventRegistration(188)
    admin/ 3   auth/ 5   host/ 1   moderator/ 1   organization/ 9   watch/ 1
  routes/ services/ constants/ context/ styles/ utils/   [ALL EMPTY]
```

### 1.3 Routing (from `App.jsx`)
- Public: `/` (marketing Home, lazy), `/e/:id` (EventRegistration), `/events/:eventId/watch` (EventWatch)
- Auth (AuthLayout): `/login` `/signup` `/forgot-password` `/accept-invitation`
- Open demo routes (no role gate, flagged in code): `/host/dashboard`, `/moderator/dashboard`
- Super admin (RoleRoute `super_admin` → AdminLayout): `/admin/dashboard`, `/admin/organizations`, `/admin/live-events` + **13 `Placeholder` stubs**
- Org admin (RoleRoute `org_admin` → OrganizationLayout): dashboard, events, recordings, analytics, billing, settings, `events/:id`, `users`(→InviteMembers)
- Legacy (ProtectedRoute → MainLayout): `/dashboard` + **9 `Placeholder` stubs** (events, speakers, viewers, recordings, analytics, users, invitations, billing, settings)
- `*` → RootRedirect (role home or login)

Auth is fully **dummy** (`auth/dummy.js`): role inferred from email local-part, session in localStorage. No route is broken or unreachable. The 22 `Placeholder` stubs are intentional "coming soon" nav targets, not dead routes.

---

## 2. Duplicate code report

### 2.1 Duplicate design systems (the big one)
| Concept | `ui/*` | `components/admin/*` | `components/Dashboard/*` / legacy |
|---|---|---|---|
| Tokens | `ui/tokens.js` (`ACCENT` = object, `surface` = object, `STATUS`) | `admin/tokens.js` (`ACCENT` = `"violet"`, `surface` = string, `TONE`, `type`, `focusRing`, `SERIES`; re-imports `cx` from ui) | — |
| Button | `ui/Button.jsx` (emerald, `rounded-xl`, hover-lift, polymorphic Link/a) | `admin/Button.jsx` (violet, `rounded-lg`, `loading`/`leftIcon`/`iconOnly`) | raw `<button>` in `MainLayout`, `pages/Dashboard` |
| Badge | `ui/Badge.jsx` (prop `status`, `STATUS` map) | `admin/Badge.jsx` (prop `tone`, `TONE` map) | `Dashboard/RecentEvents` own STATUS map |
| Stat tile | `ui/StatsCard.jsx` | `admin/StatCard.jsx`, `admin/StatStrip.jsx`, `admin/TrendStat.jsx`(dead) | inline in `pages/Dashboard.jsx:84-100` |
| Card/surface | `ui/Card.jsx` (canonical, shadowed) | `admin/Panel.jsx` (flat), `admin/SectionCard.jsx` (wraps ui/Card) | local `Card` in `pages/Dashboard.jsx:66` |
| Table | — | `admin/DataTable.jsx` (sort/paginate/skeleton/empty) | `Dashboard/RecentEvents` hand-rolled |
| Sidebar | — | `admin/AdminSidebar.jsx` | `Dashboard/Sidebar.jsx` **+ inline copy in `MainLayout.jsx:47-109`** |
| Topbar | — | `admin/AdminTopbar.jsx` | `Dashboard/Topbar.jsx` **+ inline copy in `MainLayout.jsx:116-152`** |
| Section heading | `ui/Typography.jsx` `SectionHeading` (marketing) | `admin/SectionHeading.jsx` (**name collision**, dead) | — |
| Panel | — | `admin/Panel.jsx` | `moderation/Panel.jsx` (same idea, `rounded-2xl`+shadow), `host/HostPanel.jsx` (misnamed — it's a tab sidebar) |
| Animated number | `ui/Counter.jsx` (count-up on scroll) | `admin/LiveCounter.jsx` `useLiveValue` (sine drift) | — |
| Chart wrappers | — | `admin/AreaTrend.jsx`, `admin/Sparkline.jsx` | `Dashboard/AreaChartCard`, `BarChartCard`, `DonutChartCard`, `RadialCard` + inline chart in `pages/Dashboard.jsx:158` |

### 2.2 Duplicate tables (10 hand-rolled vs 1 shared)
`DataTable` used by: `admin/Organizations.jsx:165`, `org/Events.jsx:160`, `org/InviteMembers.jsx:255,273`.
Hand-rolled `<table>` (no sort/paginate/skeleton/empty): `admin/LiveEvents.jsx:44`, `admin/sections/Infrastructure.jsx`, `admin/sections/LiveEventActivity.jsx`, `admin/sections/OrgsAttention.jsx`, `Dashboard/RecentEvents.jsx`, `pages/Dashboard.jsx:196-249`, `org/Analytics.jsx:176`, `org/Billing.jsx:223`, `org/Settings.jsx:345`.

### 2.3 Duplicate helpers / logic
- **`initials()` — ~9 definitions:** `admin/format.js:11`(canonical), `admin/AdminTopbar.jsx:15`, `admin/LiveEvents.jsx:41`, `Dashboard/Topbar.jsx:6`, `MainLayout.jsx:25`, `data/host.js:7`, `data/moderation.js:5`, `data/watch.js:5`, `org/EventDetails.jsx:36` (+ re-inlined `InviteMembers.jsx:174`).
- **`isEmail` regex — 4 copies:** `auth/Login.jsx:10`, `auth/CreateOrganization.jsx:10`, `auth/ForgotPassword.jsx:9`, `EventRegistration.jsx:37`.
- **Number/currency/storage formatting — fragmented:** `.toLocaleString()` in ~20 files; `admin/format.js` `compact/money` used only in admin; **two storage formatters** `data/recordings.js:21` `fmtStorage` vs `data/billing.js:55` `fmtGB`; inline `$`+`toFixed(2)` in `Billing.jsx:99,131,239,275`.
- **API-mutation-with-toast** `try{await api…; notify.success; reload()}catch{notify.error(errMsg(e))}` — ~15 copies across `org/Events`, `org/EventDetails`(×2), `org/InviteMembers`(×4), `CreateEventModal`, all 3 real auth pages.
- **Client-side search filter** (`query.trim().toLowerCase().includes`) — `Events.jsx:42`, `Recordings.jsx:119`, `InviteMembers.jsx:111` (no debounce anywhere).
- **Blob→object-URL→download** — `org/Analytics.jsx:71` (CSV), `org/Billing.jsx:92` (invoice), `admin/Organizations.jsx` (CSV).
- **`setInterval` timer effect** — `HostHeader.jsx:16`, `StudioStage.jsx:20`, `ModeratorHeader.jsx:24`, `AuthLayout.jsx:16`, plus viewer-count drift `moderator/Dashboard.jsx:35` & `EventWatch.jsx:27` (same code, ranges 13 vs 15).
- **Poll %-bar / poll-option builder / chat-send / Q&A sort** — duplicated across `moderation/ModeratorSidebar`, `watch/WatchPanel`, `host/HostPanel`, `host/FeatureModal`.
- **Tone/status color maps — 5 declarations of the same 6 colors:** `admin/tokens.TONE`, `ui/tokens.STATUS`, `admin/HealthDot.jsx:6` (own TONE+HEX), `ControlBar.jsx:11`, `moderation/Panel.jsx:6`.

### 2.4 Duplicate Tailwind strings (recurring 3+ places — exact strings in the deep-read appendices below)
Input control (~6 copies, 2 accent variants) · label class (4×) · page `<h1>` header (4×) · KPI grid wrapper (4×) · leading search-icon (3×) · trailing chevron select (4×) · table `th` (3 conventions) · table `td` (3×) · rounded-2xl card surface (6×) · theme-toggle icon button (3×) · emerald send button (3×) · emerald text link (7×) · progress-bar track (3×) · LIVE pulse dot (hand-rolled 4×, `ui/Badge` already does it) · banner accent→gradient map (byte-identical 2× + 2 variants) · radial-glow overlay (4×) · footer (2×, "© 2024" while `AuthLayout` says 2026).

### 2.5 Duplicate charts
5 recharts wrappers + 1 hand-rolled SVG + 1 inline chart. `AreaTrend` ≈ `AreaChartCard` minus grid/legend; `Sparkline` ≈ `AreaTrend` minus axes. Recharts tooltip `contentStyle` object re-declared in 5 files.

---

## 3. Unused files report

### 3.1 Confirmed dead — zero references (safe to delete now)
| File | Lines | Evidence |
|---|---|---|
| `components/admin/sections/PlatformAnalytics.jsx` | 88 | not in `admin/Dashboard.jsx` section list; grep = self only |
| `components/admin/sections/SecurityCenter.jsx` | 57 | same |
| `components/admin/TrendStat.jsx` | 28 | never imported, not in `admin/index.js`; duplicates `StatCard` |
| `components/Dashboard/DashboardCard.jsx` | 3 | pure re-export shim of `ui/StatsCard`; never imported |

### 3.2 Conditional dead (become safe once §3.1 is removed)
| Item | Note |
|---|---|
| `components/admin/SectionHeading.jsx` (13) | imported **only** by PlatformAnalytics + SecurityCenter |
| `LiveStat` export in `components/admin/LiveCounter.jsx:22-35` | dead export; **keep `useLiveValue`** (used by `LivePlatform`) |

### 3.3 Empty scaffold folders (delete; recreate when actually needed — YAGNI)
`src/routes/` · `src/services/` · `src/constants/` · `src/context/` · `src/styles/` · `src/utils/` · `src/components/common/` · `src/components/layout/`

### 3.4 Dead / decorative code (not whole files)
- `react-hook-form`, `socket.io-client` — uninstall (0 imports).
- `CreateOrganization.jsx` `slug` state + `slugify` (`:11,22,82`) — feeds a placeholder only; never validated or sent.
- `StudioStage.jsx:23` — redundant `setTimeout(()=>setSecs(0),0)` reset.
- `moderator/Dashboard.jsx:35` drift guard on `currentEvent.status` — always true (`data/moderation.js:11` hardcodes `"Live"`).
- `admin/StatStrip`, `admin/SectionHeading` used by admin only; **not** by any org page (org pages hand-roll equivalents instead).

**All 12 `data/*.js` files are used.** No page/layout/context/hook file is orphaned besides the above.

---

## 4. Safe-to-delete list (ranked, with guardrails)

**Tier 1 — delete immediately, zero risk:**
1. `components/admin/sections/PlatformAnalytics.jsx`
2. `components/admin/sections/SecurityCenter.jsx`
3. `components/admin/TrendStat.jsx`
4. `components/Dashboard/DashboardCard.jsx`
5. Empty dirs: `routes/ services/ constants/ context/ styles/ utils/ components/common/ components/layout/`
6. `npm uninstall react-hook-form socket.io-client`

**Tier 2 — delete in the same commit as Tier 1 (they only feed Tier 1):**
7. `components/admin/SectionHeading.jsx`
8. `LiveStat` export block in `LiveCounter.jsx`

**Do NOT delete (reachable / in use), even though they look redundant:**
- `pages/Dashboard.jsx`, `MainLayout.jsx`, all `components/Dashboard/*` — reachable via `/dashboard` for `speaker`/unknown roles (`roleHome.js:13,17`). These are *consolidation* targets (§6/§7), not deletions, until their roles get real pages.
- `data/*` mock files — the entire data layer until the API lands.

---

## 5. Safe-to-merge components list

| Merge these | Into | Keep the richer behavior of |
|---|---|---|
| `ui/tokens.js` + `admin/tokens.js` | **one token module** | resolve `ACCENT`/`surface` collisions (rename admin's → `BRAND`/`panelSurface`); fold `TONE`→`STATUS` |
| `ui/Button` + `admin/Button` | **one `Button`** | admin's `loading`/`leftIcon`/`iconOnly` + ui's polymorphic Link/variants |
| `ui/Badge` + `admin/Badge` | **one `Badge`** | one status/tone map, `dot`/`live` props |
| `ui/StatsCard` + `admin/StatCard` + `admin/StatStrip` | **one `StatCard`** (+ optional `strip` layout) | loading skeleton + Counter + delta + optional Sparkline |
| `admin/Panel` + `moderation/Panel` (+ `SectionCard`) | **one `Panel`/`Section`** | flat vs shadowed as a `variant` prop |
| `admin/AreaTrend` + `admin/Sparkline` + `Dashboard/AreaChartCard` | **one `<AreaChart>`** (props: axes/grid/legend/carded) | + shared `chartTooltip` style, one `SERIES` palette |
| `Dashboard/BarChartCard`, `DonutChartCard`, `RadialCard` | **`ui/charts/`** | as-is, just relocated + shared tooltip |
| `host/SummaryCards` + `moderation/SummaryCards` | **one `SummaryCards`** (data via props) | — |
| `Dashboard/QuickActions` + `admin/sections/QuickActions` | data-driven `QuickActions` | admin's (already data-driven) |
| 3 sidebars (`AdminSidebar`, `Dashboard/Sidebar`, `MainLayout` inline) | **one prop-driven `Sidebar`** | `Dashboard/Sidebar` is already prop-driven; add admin's grouping |
| 3 topbars (`AdminTopbar`, `Dashboard/Topbar`, `MainLayout` inline) | **one `Topbar`** | admin's is the superset |
| tab strips in `HostPanel`/`WatchPanel`/`ChatQAPanel` | **one `Tabs`** | — |
| 10 hand-rolled `<table>` | **`DataTable`** | already feature-complete |
| input/label/toggle classes in org+host+mod forms | **promote `auth/fields.jsx`** to `ui/forms/` (`Input`,`Field`,`PasswordField`,`Toggle`,`SubmitButton`) | — |
| empty states, page headers, error/loading | **already exist** in `components/organization/*` — enforce use on the 4 unmigrated org pages | — |

Shared utilities to extract (new `src/lib/`): `initials`, `isEmail`, `formatNumber/compact`, `formatCurrency`, `formatStorage` (merge `fmtGB`/`fmtStorage`/`fmtSize`), `downloadBlob`, `fmtElapsed`. Shared hook: `useMutation` (the toast/try/catch/reload wrapper) alongside `useApi`.

---

## 6. Folder restructuring plan

Principles: feature-based, shallow, one design-system home. Proposed target:
```
src/
  main.jsx  App.jsx  index.css  api.js
  auth/            AuthContext, roleHome, dummy
  theme/           ThemeContext            (or fold into ui/)
  lib/             formatters, initials, isEmail, downloadBlob, validators   ← replaces empty utils/
  hooks/           useApi, useMutation
  ui/              ← THE design system (merge admin/* primitives + Dashboard/* charts in)
    tokens.js  Button  Badge  Card  Panel  Modal  Drawer  StatCard  DataTable
    Spinner  Skeleton  Toast  Typography  Logo  Counter  Tabs  HealthDot  motion
    forms/         Input  Field  PasswordField  Toggle  SubmitButton
    charts/        AreaChart  BarChart  DonutChart  RadialGauge  Sparkline
  layouts/         AppShell (nav via props)  AuthLayout          ← collapse Main/Org/Admin shells
  pages/
    marketing/     Home + sections/         ← was pages/Home + components/home
    auth/          Login  CreateOrganization  ForgotPassword  AcceptInvitation  fields→ui/forms
    admin/         Dashboard  Organizations  LiveEvents  sections/
    organization/  Dashboard  Events  Recordings  Analytics  Billing  Settings  EventDetails  Members
    host/  moderator/  watch/  public/(EventRegistration)
  data/            mock data (quarantined; delete per-file as endpoints land)
```
Deleted along the way: 8 empty dirs; `components/admin`, `components/Dashboard`, `components/home`, `components/organization`, `components/{host,moderation,watch}` folders re-homed under `ui/` (primitives) or `pages/<feature>/` (feature components). `routes/services/constants/context/styles` never existed as code — drop them.

> Ponytail note: this is a *relocation + merge*, not a new abstraction layer. No new framework, no barrel-of-barrels, no config for values that never change.

---

## 7. Component restructuring plan (execution order)

1. **Tokens first** — merge to one module, fix `ACCENT`/`surface`/`STATUS`/`TONE` collisions. Nothing else can consolidate cleanly until brand vocabulary is single. *(touches ~40 import sites, mechanical)*
2. **Primitives** — one Button, Badge, StatCard, Card/Panel. Codemod imports.
3. **DataTable everywhere** — convert the 10 hand-rolled tables.
4. **Forms** — promote `fields.jsx` → `ui/forms/`; replace hand-rolled inputs in org/host/moderation.
5. **Charts** — one `<AreaChart>` + shared tooltip; relocate bar/donut/radial to `ui/charts/`.
6. **Shell** — one `Sidebar`, one `Topbar`, one `AppShell`; retire `MainLayout` inline copies.
7. **Org pages parity** — bring Recordings/Analytics/Billing/Settings onto `useApi` + shared header/empty/error + shared table/filter/search (match Dashboard/Events).
8. **Utilities** — extract `lib/` helpers; delete the 9 `initials`, 4 `isEmail`, 2 storage formatters, etc.
9. **Feature dedup** — merge SummaryCards, QuickActions, Tabs, poll/chat logic.
10. **Delete dead code** (§4 Tier 1+2) — do this last so nothing references it mid-refactor.

Each step is independently shippable and behavior-preserving.

---

## 8. Routing cleanup plan
- No broken/unreachable/duplicate routes. Keep the route table.
- The 22 `Placeholder` stubs are intentional; leave until real pages land, but track them.
- Minor: `/organization/users` renders `InviteMembers` — rename the component to `Members`/`OrganizationMembers` for clarity (route stays).
- The 9 legacy root-level stubs (`/events`, `/recordings`, `/analytics`, `/billing`, `/settings`, …) conceptually duplicate the `/organization/*` routes. Once `speaker`/viewer get real homes, retire `MainLayout` + these stubs. Not now (reachable).
- Consider `App.jsx` route-config extraction into `routes.jsx` only if it grows; currently fine as one file.

## 9. Performance improvement opportunities (behavior-preserving)
- **Module-scope side effects:** several `admin/sections/*` compute derived state at import time (e.g. `LiveEventActivity.jsx:6-9` filter/sort/slice; `PlatformStatus.jsx:8`). Move into the component/`useMemo` so it's not recomputed on module eval and is testable.
- **Marketing already code-split** (`Home` + sections via `lazy`). Consider the same `lazy` treatment for the heavy admin/org dashboards + recharts (recharts is large; today it's in the main bundle for anyone hitting `/dashboard`).
- **Dead effect deps:** `moderator/Dashboard.jsx:35` guard is always-true; the two viewer-drift intervals are duplicated — one shared `useLiveViewers(base,range)` hook.
- **No prop-drilling crises found.** `moderator/Dashboard.jsx` holds ~10 handlers + 7 state slices in one component — extract a `useModeration()` hook (readability + testability, not a perf issue).
- **Search inputs** re-filter on every keystroke with no debounce (fine at mock scale; add debounce in the shared search when lists hit the API).
- **`StudioStage.jsx:23`** redundant `setTimeout` — remove.

## 10. Naming improvements
Rename generic files to feature-scoped, descriptive names (Step 11 of brief):
| Now | Proposed |
|---|---|
| `components/host/HostPanel.jsx` (it's a tab sidebar) | `HostSidebarTabs.jsx` |
| `moderation/Panel.jsx`, `admin/Panel.jsx` | merge → `ui/Panel.jsx` |
| `host/SummaryCards.jsx`, `moderation/SummaryCards.jsx` | merge → `SummaryCards.jsx` (shared) |
| `Dashboard/QuickActions.jsx`, `admin/sections/QuickActions.jsx` | merge → `QuickActions.jsx` |
| `admin/SectionHeading.jsx` vs `ui/Typography.SectionHeading` | resolve collision — one name |
| `pages/organization/Dashboard.jsx` etc. (5 files named `Dashboard.jsx`) | keep folder-scoped; ensure imports use path not bare name |
| `pages/organization/InviteMembers.jsx` (route is `/users`) | `Members.jsx` |
| `data/recordings.fmtStorage` + `data/billing.fmtGB` | one `lib/formatStorage` |
Avoid bare `Header.jsx`/`Card.jsx`/`Table.jsx`/`index.jsx` for feature files; primitives keep short names inside `ui/`.

## 11. Final proposed folder structure
→ see **§6** (the target tree). Feature-based, ≤3 levels deep, single `ui/` design system, `lib/` for pure helpers, `data/` quarantined.

## 12. Refactoring checklist
- [ ] **Approve this plan** (and attach the 4 specs if they should steer target visuals)
- [ ] Merge `ui/tokens` + `admin/tokens` → one module; resolve `ACCENT`/`surface`/`STATUS`/`TONE` collisions
- [ ] One `Button`, `Badge`, `StatCard`, `Card`/`Panel`; codemod imports
- [ ] Convert 10 hand-rolled `<table>` → `DataTable`
- [ ] Promote `auth/fields.jsx` → `ui/forms/`; replace hand-rolled inputs/toggles
- [ ] One `<AreaChart>` + shared tooltip; relocate bar/donut/radial → `ui/charts/`
- [ ] One `Sidebar` + `Topbar` + `AppShell`; retire `MainLayout` inline copies
- [ ] Bring Recordings/Analytics/Billing/Settings to `useApi` + shared header/empty/error
- [ ] Extract `lib/`: `initials`, `isEmail`, `formatNumber`, `formatCurrency`, `formatStorage`, `downloadBlob`, `fmtElapsed`; add `useMutation` hook
- [ ] Merge `SummaryCards`, `QuickActions`, `Tabs`; extract `useModeration`, `useLiveViewers`
- [ ] Move `admin/sections/*` module-scope work into components/`useMemo`
- [ ] Delete Tier 1 + Tier 2 dead code; `npm uninstall react-hook-form socket.io-client`
- [ ] Delete 8 empty scaffold folders
- [ ] Rename per §10; fix `/organization/users`→`Members`
- [ ] Fix footer year `2024`→consistent; remove decorative `slug`; remove `StudioStage` redundant timeout
- [ ] `npm run lint && npm run build` green after each step

---

*End of Phase 0 audit. No code changed. Awaiting approval before Step-by-step execution.*
