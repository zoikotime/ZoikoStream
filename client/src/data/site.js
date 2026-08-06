// Content for the public site pages (everything under / that isn't the homepage).
//
// Kept in one module for the same reason data/home.js exists: the pages stay layout-only, so a
// copy change is a data edit rather than a JSX edit.
//
// HOUSE RULE, and it is load-bearing here: a figure with no source in this repo is `null` and
// renders as an em dash with the reason on hover — the convention DeveloperOps and
// SecuritySupport established in the console. Headcount, customer names, funding, uptime
// history and case-study numbers are all things this repo does not know, so none of them is
// asserted. Plan tiers and limits are the exception: they come from data/billing.js, which the
// org console's Billing page already presents, so the public page and the in-app page cannot
// disagree.

import {
  FiServer, FiVideo, FiShield, FiBarChart2, FiRadio, FiCode, FiBookOpen, FiTerminal,
  FiLayers, FiUsers, FiGlobe, FiHeart, FiBriefcase, FiMic, FiMonitor, FiLock,
  FiFileText, FiActivity, FiZap, FiGitBranch, FiPackage, FiPlayCircle,
} from "react-icons/fi";

// ── Company ──────────────────────────────────────────────────────────────────

export const COMPANY_INTRO =
  "ZoikoStream is the video platform Zoiko Group builds for organisations that cannot afford a " +
  "broadcast to fail. One API covers contribution, ingest, production, secure delivery and " +
  "playback — with managed Live Events for the moments that only happen once.";

// What the company is for, stated as principles rather than milestones — the repo has no
// dated history to draw a timeline from, and an invented one would be the worst kind of filler.
export const COMPANY_PRINCIPLES = [
  {
    icon: FiShield,
    accent: "violet",
    title: "Unrepeatable moments set the bar",
    body:
      "A graduation, a shareholder meeting, a service — these do not get a second take. Every " +
      "readiness gate, every redundancy decision and every escalation path in the platform is " +
      "designed backwards from that constraint.",
  },
  {
    icon: FiLock,
    accent: "indigo",
    title: "Access is a decision, not a default",
    body:
      "Visibility, registration, passphrase and watch window are separate gates, enforced on the " +
      "media rather than the page, so an audience is never accidentally larger than intended.",
  },
  {
    icon: FiActivity,
    accent: "blue",
    title: "Measured, or marked unmeasured",
    body:
      "Our own consoles show an em dash where nothing is being observed, never a zero. A number " +
      "that looks like a clean bill of health when nothing is watching is worse than a blank.",
  },
  {
    icon: FiUsers,
    accent: "emerald",
    title: "Operators are the users",
    body:
      "Hosts, moderators, speakers and support staff each get a surface built for the job they do " +
      "under time pressure — not one dashboard with their features hidden behind tabs.",
  },
];

export const COMPANY_FACTS = [
  { label: "Part of", value: "Zoiko Group" },
  { label: "Platform focus", value: "Live and on-demand video infrastructure" },
  { label: "Headquarters", value: null, reason: "Not recorded in this repository" },
  { label: "Team size", value: null, reason: "Not recorded in this repository" },
  { label: "Founded", value: null, reason: "Not recorded in this repository" },
];

export const CAREERS_PRINCIPLES = [
  ["Write the reason down", "Every non-obvious decision in our codebase carries the why. Reviews ask for the reasoning, not just the diff."],
  ["Own it end to end", "The person who builds a surface handles its failures. Handoffs to a separate ops team are not how this platform is run."],
  ["Small diffs, real tests", "The change that ships is the smallest one that works, with a check that fails if the logic breaks."],
  ["No invented numbers", "If we cannot measure it, we say so. That applies to dashboards, to status pages, and to each other."],
];

// ── Solutions ────────────────────────────────────────────────────────────────

export const SOLUTIONS = [
  {
    id: "enterprise",
    icon: FiBriefcase,
    accent: "violet",
    title: "Enterprise",
    body:
      "All-hands, results calls and regulated internal comms, with governed access and an audit " +
      "trail behind every privileged action.",
    points: ["Scoped roles and elevation", "Audit log on every high-risk action", "Registration and passphrase gating", "Retention and legal-hold support"],
  },
  {
    id: "media",
    icon: FiMic,
    accent: "indigo",
    title: "Media & Broadcast",
    body:
      "Multi-contributor production with a real control room: stage management, backstage, and " +
      "per-source publish grants rather than one shared key.",
    points: ["Host control room", "Moderator console", "Source-scoped speaker grants", "Replay published from the same asset"],
  },
  {
    id: "education",
    icon: FiBookOpen,
    accent: "blue",
    title: "Education",
    body:
      "Lectures and cohort sessions where attendance matters — registration is enforced on the " +
      "media, and captions and transcripts travel with the recording.",
    points: ["Registration enforced on playback", "Captions and transcript assets", "Q&A and polls", "Replay for the same audience"],
  },
  {
    id: "worship",
    icon: FiHeart,
    accent: "amber",
    title: "Worship",
    body:
      "Weekly services that must simply work, run by volunteers. Pre-flight checks and a device " +
      "check before anyone goes live.",
    points: ["Pre-live checks", "Contributor device check", "Simple host controls", "Unlisted and passphrase access"],
  },
  {
    id: "events",
    icon: FiRadio,
    accent: "rose",
    title: "Events",
    body:
      "Conferences and launches, with readiness gates that block an unrepeatable event when a " +
      "mandatory gate is failing rather than discovering it on air.",
    points: ["Readiness gates per impact class", "Operator rostering", "Capacity and access links", "Live incident surfacing"],
  },
];

// ── Platform pillars (the Footer's Platform column, anchored on this page) ────

export const PLATFORM_PILLAR_DETAIL = [
  {
    id: "infrastructure",
    icon: FiServer,
    accent: "violet",
    title: "Infrastructure",
    body: "Globally distributed ingest, transcode and edge delivery you don't operate. Stage-by-stage availability is reported per region, worst region first.",
    points: ["RTMPS and SRT contribution", "Regional ingest", "Stage-level availability reporting", "Managed transcode"],
  },
  {
    id: "streaming",
    icon: FiVideo,
    accent: "indigo",
    title: "Streaming",
    body: "Sub-second live and adaptive on-demand from one API, with the same access model applied to both.",
    points: ["Low-latency live", "Adaptive VOD", "Single access model", "Replay from the recorded asset"],
  },
  {
    id: "security",
    icon: FiShield,
    accent: "blue",
    title: "Security",
    body: "Signed, short-lived playback credentials; separate visibility, registration and passphrase gates; audit on every privileged action.",
    points: ["Short-lived playback tokens", "Hashed access links, revealed once", "Scoped elevation with step-up auth", "Full audit trail"],
  },
  {
    id: "analytics",
    icon: FiBarChart2,
    accent: "emerald",
    title: "Analytics",
    body: "Concurrency, engagement and usage metering per session, event and workspace — and an explicit blank wherever nothing is being observed.",
    points: ["Live concurrency", "Engagement and Q&A", "Usage metering", "Unmeasured marked as unmeasured"],
  },
];

// ── Pricing ──────────────────────────────────────────────────────────────────
// Tiers, prices and limits are imported from data/billing.js by the page, so the public
// pricing table and the in-app Billing page are the same numbers. What lives here is only
// what billing.js does not carry.

export const PRICING_NOTES = [
  "Prices are per organization, per month, billed in USD.",
  "Streaming hours are measured on published output, not on time spent in a control room.",
  "Every plan includes the full API — tiers differ by capacity and support, never by endpoint.",
  "Managed Live Events are quoted per event on top of any plan.",
];

export const PRICING_FAQ = [
  {
    q: "What happens when we exceed a plan limit?",
    a: "The workspace keeps working. Usage above an entitlement is surfaced on the Usage & Entitlements page in your console and reconciled on the next invoice — a broadcast is never cut off mid-session for a quota.",
  },
  {
    q: "Is the API restricted on lower tiers?",
    a: "No. Every plan has the same API surface. Tiers differ by capacity, retention and support response, so an integration built on Starter does not have to be rewritten on Enterprise.",
  },
  {
    q: "Can we run a managed event without a plan?",
    a: "Yes. Managed Live Events are quoted per event, and are the usual route for a one-off unrepeatable broadcast.",
  },
  {
    q: "Do you offer non-profit or education pricing?",
    a: "Talk to us. Worship and education workloads are a large share of what runs on this platform and are priced accordingly.",
  },
  {
    q: "How is an SLA credit calculated?",
    a: "Against measured availability for the stages your traffic actually used, per region. The calculation is written into the contract rather than left to interpretation.",
  },
];

// ── Docs ─────────────────────────────────────────────────────────────────────

export const DOC_SECTIONS = [
  { slug: "", label: "Documentation", icon: FiBookOpen },
  { slug: "api", label: "API Reference", icon: FiCode },
  { slug: "sdks", label: "SDKs", icon: FiPackage },
  { slug: "sandbox", label: "Sandbox", icon: FiTerminal },
  { slug: "architecture", label: "Architecture", icon: FiLayers },
  { slug: "guides", label: "Guides", icon: FiFileText },
];

// The lifecycle, used as the docs table of contents. Same eight stages the console rails use.
export const DOC_LIFECYCLE = [
  ["contribute", "Contribute", "Get a source into the platform — encoders, browsers, and contributor device checks."],
  ["ingest", "Ingest", "Live inputs, publish keys, RTMPS and SRT, and confirming ingest health."],
  ["produce", "Produce", "Stage management, layouts, and who is allowed to publish which source."],
  ["secure", "Secure", "Visibility, registration, passphrase, watch windows and signed playback."],
  ["deliver", "Deliver", "Playback, access links, and what an audience can actually reach."],
  ["understand", "Understand", "Concurrency, engagement, usage metering and export."],
  ["preserve", "Preserve", "Recordings, transcripts, retention and replay."],
  ["platform", "Platform", "Credentials, webhooks, errors and versioning."],
];

// Endpoint groups, transcribed from the routers in this repository. Deliberately grouped the
// way the API is actually organised rather than alphabetically.
export const API_GROUPS = [
  {
    id: "events",
    title: "Events",
    base: "/events",
    body: "Create, schedule, staff and govern an event. Team assignment is per role; access links are per event.",
    endpoints: [
      ["GET", "/events", "List events — filtered, sorted and paginated server-side."],
      ["POST", "/events", "Create an event."],
      ["GET", "/events/{id}", "Read one event, including its access posture."],
      ["PATCH", "/events/{id}", "Update an event."],
      ["POST", "/events/{id}/duplicate", "Duplicate configuration — never audience or history."],
      ["GET", "/events/{id}/team/{role}", "Read assignees for a role."],
      ["PATCH", "/events/{id}/team/{role}", "Replace the assignee set for a role."],
      ["GET", "/events/{id}/access-links", "List viewer access links."],
      ["POST", "/events/{id}/access-links", "Issue a link. The raw token is returned once."],
      ["POST", "/events/{id}/access-links/{linkId}/rotate", "Re-secret an existing link."],
      ["POST", "/events/{id}/access-links/{linkId}/revoke", "Revoke without deleting the record."],
    ],
  },
  {
    id: "streams",
    title: "Live inputs",
    base: "/streams",
    body: "Ingest endpoints. A list never returns a publish key; a single read does, because that is the only shape that exposes one credential at a time.",
    endpoints: [
      ["GET", "/streams", "List your live inputs, paginated."],
      ["POST", "/streams", "Create an input on a channel you own."],
      ["GET", "/streams/{id}", "Read one input, including its publish key."],
      ["PUT", "/streams/{id}", "Update title, description or category."],
      ["POST", "/streams/{id}/start", "Mark the input live."],
      ["POST", "/streams/{id}/stop", "Mark the input idle."],
      ["DELETE", "/streams/{id}", "Delete the input and invalidate its key."],
    ],
  },
  {
    id: "media",
    title: "Media",
    base: "/media",
    body: "Recordings, transcripts, folders and retention. Organization-scoped: a read resolves the caller's own library.",
    endpoints: [
      ["GET", "/media/library", "List recordings with filters."],
      ["GET", "/media/stats", "Library totals by state."],
      ["GET", "/media/recordings/{id}", "Read one recording."],
      ["PATCH", "/media/recordings/{id}", "Rename, move or retag."],
      ["GET", "/media/recordings/{id}/transcript.vtt", "Captions as WebVTT."],
      ["POST", "/media/recordings/{id}/archive", "Archive without purging."],
      ["DELETE", "/media/recordings/{id}/purge", "Permanent deletion, subject to holds."],
      ["GET", "/media/settings", "Read retention configuration."],
    ],
  },
  {
    id: "analytics",
    title: "Analytics",
    base: "/analytics",
    body: "Concurrency, engagement, attendance and usage. Windowed reads take a range parameter.",
    endpoints: [
      ["GET", "/analytics/overview", "Headline figures for a window."],
      ["GET", "/analytics/live", "Current concurrency across live sessions."],
      ["GET", "/analytics/events/{id}", "One event's performance."],
      ["GET", "/analytics/engagement", "Chat, Q&A, polls and reactions."],
      ["GET", "/analytics/attendees", "Attendance and return behaviour."],
      ["GET", "/analytics/export", "Export a window for offline analysis."],
    ],
  },
  {
    id: "organization",
    title: "Organization",
    base: "/organization",
    body: "Workspace posture, members, invitations and security configuration.",
    endpoints: [
      ["GET", "/organization/overview", "Lifecycle, sessions, entitlements and attention items."],
      ["GET", "/organization/developer", "Credential and webhook inventory."],
      ["GET", "/organization/users", "Members and their roles."],
      ["POST", "/organization/invitations", "Invite a member."],
      ["GET", "/organization/security", "Security configuration."],
      ["PATCH", "/organization/security", "Update security configuration."],
    ],
  },
  {
    id: "attendee",
    title: "Attendee",
    base: "/attendee",
    body: "The audience side: registration, reminders, bookmarks and preferences.",
    endpoints: [
      ["GET", "/attendee/events", "Events this viewer can see or has registered for."],
      ["POST", "/attendee/events/{id}/register", "Register for an event."],
      ["POST", "/attendee/events/{id}/reminder", "Set a reminder."],
      ["GET", "/attendee/preferences", "Read playback and accessibility preferences."],
      ["PATCH", "/attendee/preferences", "Update them."],
    ],
  },
];

export const SDKS = [
  {
    id: "node",
    icon: FiZap,
    accent: "emerald",
    name: "Node.js",
    install: "npm install @zoikostream/node",
    status: "v3 current · v2 deprecated Oct 2026",
    body: "Server-side client for events, live inputs, media and analytics. Webhook signature verification included.",
  },
  {
    id: "python",
    icon: FiCode,
    accent: "indigo",
    name: "Python",
    install: "pip install zoikostream",
    status: "v3 current",
    body: "Typed client with the same surface as Node, for data and automation workloads.",
  },
  {
    id: "browser",
    icon: FiMonitor,
    accent: "blue",
    name: "Browser player",
    install: 'import { Player } from "@zoikostream/player";',
    status: "v3 current",
    body: "Playback with captions, quality selection and reduced-motion support out of the box.",
  },
  {
    id: "cli",
    icon: FiTerminal,
    accent: "violet",
    name: "CLI",
    install: "npm install -g @zoikostream/cli",
    status: "v3 current",
    body: "Scriptable access to the same endpoints — useful in CI and for one-off operational tasks.",
  },
];

export const GUIDES = [
  ["contribute", "Send your first stream", "Create a live input, connect an encoder over RTMPS or SRT, and confirm ingest health."],
  ["secure", "Gate playback correctly", "Choose between visibility, registration, passphrase and a watch window — and understand why each is enforced on the media."],
  ["platform", "Verify webhook signatures", "Validate authenticity, reject replays, and handle retries idempotently."],
  ["preserve", "Publish a replay", "Turn a finished session into a governed on-demand asset with captions intact."],
  ["understand", "Read analytics via the API", "Pull concurrency, engagement and usage metering for a session or workspace."],
  ["deliver", "Issue and rotate access links", "Share a broadcast with people who have no account, and withdraw that access cleanly."],
  ["produce", "Run a multi-contributor broadcast", "Stage management, source-scoped publish grants, and the moderator's authority."],
  ["ingest", "Choose RTMPS or SRT", "Trade-offs under packet loss, and what each protocol needs from your encoder."],
];

export const ARCHITECTURE_LAYERS = [
  {
    icon: FiRadio,
    accent: "violet",
    title: "Contribution and ingest",
    body: "Encoders and browsers publish into a live input. A publish key authorises exactly one input, and a list read never returns one.",
  },
  {
    icon: FiVideo,
    accent: "indigo",
    title: "Production",
    body: "A real-time room where the host controls the stage. Publish grants are issued per source and per participant, so a speaker cannot take over a feed that isn't theirs.",
  },
  {
    icon: FiShield,
    accent: "blue",
    title: "Access decision",
    body: "Visibility, registration, passphrase and watch window are evaluated when media is requested — not when the page loads, which is what keeps a landing page reachable to someone who still needs to register.",
  },
  {
    icon: FiGlobe,
    accent: "emerald",
    title: "Delivery",
    body: "Short-lived subscribe-only credentials are minted per viewer per session, and re-minted on recovery rather than cached.",
  },
  {
    icon: FiPlayCircle,
    accent: "amber",
    title: "Preservation",
    body: "A finished session becomes an asset with its transcript, governed by retention rules and legal holds, and optionally republished as a replay to the same audience.",
  },
  {
    icon: FiGitBranch,
    accent: "rose",
    title: "Observation",
    body: "Sampled concurrency, engagement events and usage metering feed the consoles. Anything with no producer is reported as unmeasured rather than zero.",
  },
];

// ── Trust and legal ──────────────────────────────────────────────────────────

export const SECURITY_PRACTICES = [
  {
    icon: FiLock,
    accent: "violet",
    title: "Credentials",
    body: "API keys are stored hashed; only a prefix is ever displayed. Access-link tokens are sha256-hashed and shown exactly once, at issue or rotation. Nothing in the platform can return a stored secret.",
  },
  {
    icon: FiShield,
    accent: "indigo",
    title: "Playback authorisation",
    body: "Playback credentials are subscribe-only, short-lived, and minted per session. An attendee endpoint can never issue a publish grant — that path is the host console's and is separately gated.",
  },
  {
    icon: FiUsers,
    accent: "blue",
    title: "Privileged access",
    body: "High-risk platform actions require scoped elevation, an impact preview, step-up authentication and an audit entry. An elevated session is stated in the interface, never silent.",
  },
  {
    icon: FiFileText,
    accent: "emerald",
    title: "Audit",
    body: "Privileged actions are written to an append-only log with actor, target, and origin. The log is searchable by action and target type.",
  },
  {
    icon: FiActivity,
    accent: "amber",
    title: "Webhooks",
    body: "Inbound provider webhooks are rejected unless signed, with a five-minute replay tolerance and constant-time comparison. An unconfigured secret returns an error rather than accepting silently.",
  },
  {
    icon: FiServer,
    accent: "rose",
    title: "Tenancy",
    body: "Reads resolve the caller's organization server-side. A missing row and another tenant's row return the same response, so ids cannot be probed for existence.",
  },
];

export const COMPLIANCE_POSTURE = [
  { label: "SOC 2 Type II", value: null, reason: "No certification record exists in this repository" },
  { label: "ISO 27001", value: null, reason: "No certification record exists in this repository" },
  { label: "GDPR data processing terms", value: null, reason: "Contract terms are not published here" },
  { label: "Penetration test cadence", value: null, reason: "No test record exists in this repository" },
  { label: "Sub-processor list", value: null, reason: "Not published in this repository" },
];

// Legal pages. Structured as sections so all three share one renderer, and so the honest
// disclaimer at the top cannot be edited away independently of the body.
export const LEGAL_DISCLAIMER =
  "This page describes how the platform behaves, drawn from how it is actually built. It is not " +
  "the executed contract. The binding terms are the ones in your agreement with Zoiko Group — " +
  "ask your account contact for the current documents.";

export const LEGAL_PAGES = {
  privacy: {
    title: "Privacy",
    lead: "What the platform records about the people who use it, and what it deliberately does not.",
    sections: [
      {
        heading: "What is recorded about an attendee",
        body: "Registration for an event, presence while watching, and interactions the attendee chooses to make — a question, a poll response, a reaction. Presence is sampled to produce concurrency figures.",
      },
      {
        heading: "What is not collected",
        body: "The platform does not build per-viewer profiles. Audience geography, device fingerprints and per-person watch profiles are not collected, which is why the analytics surfaces report them as unmeasured rather than showing a number.",
      },
      {
        heading: "Organization data",
        body: "An organization's events, recordings, transcripts, members and invitations are held for that organization. Reads are scoped server-side to the caller's organization.",
      },
      {
        heading: "Retention",
        body: "Recordings and transcripts follow the retention configuration set by the organization, subject to legal holds. A purge is permanent and is refused while a hold is active.",
      },
      {
        heading: "Access by Zoiko Group staff",
        body: "Platform staff access to customer data requires scoped elevation with step-up authentication, and is written to an append-only audit log with actor, target and origin.",
      },
      {
        heading: "Your rights",
        body: "Requests to access, correct, export or delete personal data should go to your organization's administrator, who can act on them in the console, or to Zoiko Group directly through the contact page.",
      },
    ],
  },
  terms: {
    title: "Terms",
    lead: "How the service is provided, and the obligations that run in both directions.",
    sections: [
      {
        heading: "The service",
        body: "ZoikoStream provides video contribution, ingest, production, delivery and playback, plus optional managed Live Events. Every plan includes the full API; plans differ by capacity, retention and support.",
      },
      {
        heading: "Your account and credentials",
        body: "API keys and publish keys are credentials. You are responsible for keeping them server-side, scoping them per environment, and rotating them on staff change or suspected exposure.",
      },
      {
        heading: "Acceptable use",
        body: "You may not use the platform to distribute content you have no right to distribute, to broadcast material that is unlawful in the jurisdictions you reach, or to attempt to reach another tenant's data.",
      },
      {
        heading: "Capacity and overage",
        body: "Usage above an entitlement does not interrupt a live session. It is surfaced in your console and reconciled on the next invoice.",
      },
      {
        heading: "Availability",
        body: "Availability is measured per lifecycle stage and per region. Where a contract includes credits, they are calculated against the stages your traffic actually used.",
      },
      {
        heading: "Termination and export",
        body: "On termination you retain a window to export recordings, transcripts and analytics before deletion. Legal holds survive termination until released.",
      },
    ],
  },
  security: {
    title: "Security",
    lead: "The controls in the platform, and an honest account of what is not yet certified.",
    sections: [],
  },
};

// ── Support and status ───────────────────────────────────────────────────────

export const SUPPORT_CHANNELS = [
  {
    icon: FiBookOpen,
    accent: "violet",
    title: "Documentation",
    body: "The lifecycle guides and API reference cover most integration questions, including the ones about what the platform deliberately does not measure.",
    cta: "Read the docs",
    href: "/docs",
  },
  {
    icon: FiActivity,
    accent: "indigo",
    title: "Platform status",
    body: "Current service health, probed per request, with declared-but-unintegrated services listed separately from real outages.",
    cta: "View status",
    href: "/status",
  },
  {
    icon: FiUsers,
    accent: "blue",
    title: "Your organization's console",
    body: "Signed-in administrators can open a support request from Support & Status, where it arrives with workspace context attached.",
    cta: "Open the console",
    href: "/organization/support",
  },
  {
    icon: FiMic,
    accent: "emerald",
    title: "Talk to a person",
    body: "For a managed event, a procurement question, or an incident that needs a human now.",
    cta: "Contact us",
    href: "/contact",
  },
];

export const SUPPORT_EXPECTATIONS = [
  ["Sev 1 — a live broadcast is failing", "Immediate. Use the phone path in your agreement; a ticket alone is the wrong channel for an event on air."],
  ["Sev 2 — production impaired", "Same business day."],
  ["Sev 3 — a question or a defect with a workaround", "Next business day."],
  ["Sev 4 — a request or a suggestion", "Triaged weekly."],
];

// Response targets above are the operating intent; the platform does not currently measure
// achieved response times, so the status page reports that rather than a figure.
export const STATUS_UNMEASURED = [
  { label: "90-day uptime history", reason: "The availability probe retains 30 days at most" },
  { label: "Incident history", reason: "Resolved incidents are not retained as a public timeline" },
  { label: "Achieved support response time", reason: "Ticket response latency is not aggregated" },
  { label: "Scheduled maintenance windows", reason: "Maintenance windows are not modelled" },
];

// ── Resources ────────────────────────────────────────────────────────────────

// The changelog mirrors what the platform actually versions: a dated API version and a
// deprecation, both of which the console already displays. Nothing else is invented.
export const API_VERSION = "2026-06-01";

export const CHANGELOG = [
  {
    date: "2026-06-01",
    version: "2026-06-01",
    channel: "api",
    title: "Current API version",
    notes: [
      "Access links carry an optional expiry and can be rotated in place, re-secreting the same row.",
      "Event duplication copies configuration only — never audience, registrations or history.",
      "Playback credentials are minted when playback starts and re-minted on recovery, rather than issued on page load.",
    ],
  },
  {
    date: "2026-10-01",
    version: "Node SDK v2",
    channel: "deprecation",
    title: "Node SDK v2 reaches end of support",
    notes: [
      "v3 is the current major and is a drop-in for most callers.",
      "Webhook signature helpers moved to a named export in v3.",
    ],
  },
];

export const BLOG_TOPICS = [
  {
    icon: FiShield,
    accent: "violet",
    title: "Why registration is enforced on the media, not the page",
    body: "A landing page has to stay reachable to someone who has not registered yet — that is where the button is. Moving the gate to the media is what makes both true at once.",
    read: "/docs/guides",
  },
  {
    icon: FiActivity,
    accent: "indigo",
    title: "An em dash beats a zero",
    body: "Our consoles show a blank with a reason wherever nothing is being observed. A zero in the same slot reads as a measured clean result, and that is how operators learn to trust a dashboard that is lying.",
    read: "/status",
  },
  {
    icon: FiLock,
    accent: "blue",
    title: "Showing a secret exactly once",
    body: "Access-link tokens are hashed on arrival. There is no copy button on an existing row because the secret genuinely cannot be recovered — offering one would teach users to expect the impossible.",
    read: "/security",
  },
  {
    icon: FiRadio,
    accent: "emerald",
    title: "Designing backwards from the unrepeatable event",
    body: "A graduation does not get a second take. Readiness gates that block rather than warn are the difference between finding out beforehand and finding out on air.",
    read: "/live-events",
  },
];

// ── Live Events (public) ─────────────────────────────────────────────────────

export const LIVE_EVENT_PHASES = [
  {
    id: "plan",
    title: "Plan",
    body: "Scope the broadcast, classify its impact, and agree what would constitute failure. Impact class is what decides later whether a failing gate blocks or merely warns.",
    points: ["Impact classification", "Success and failure criteria", "Access model chosen up front", "Capacity agreed"],
  },
  {
    id: "readiness",
    title: "Readiness",
    body: "Gates are evaluated against the event's stored configuration: title, schedule, host, moderator, recording, account standing and redundancy. An unrepeatable event with a mandatory gate failing is blocked, not warned.",
    points: ["Gate verdict per event", "Operators rostered", "Contributor device checks", "Rehearsal on the real path"],
  },
  {
    id: "production",
    title: "Production",
    body: "A control room with a real stage: the host admits and arranges contributors, the moderator holds audience authority, and each speaker gets a publish grant scoped to their own sources.",
    points: ["Host control room", "Moderator console", "Backstage and waiting room", "Source-scoped grants"],
  },
  {
    id: "audience",
    title: "Audience",
    body: "Access decided by visibility, registration, passphrase and watch window — evaluated when media is requested, so the landing page stays reachable to someone still signing up.",
    points: ["Registration enforced on playback", "Access links, revocable", "Captions and preferences", "Q&A, polls, reactions"],
  },
  {
    id: "replay",
    title: "Replay",
    body: "The finished session becomes a governed asset with its transcript, published to the same audience under the same rules if replay is enabled.",
    points: ["Recording and transcript", "Same access model", "Retention and holds", "Analytics per session"],
  },
];

export const LIVE_EVENT_ASSURANCES = [
  ["Gates that block", "For an unrepeatable event, a mandatory gate failing stops the event going ahead. The console and the API never disagree about the verdict, because only one of them computes it."],
  ["An operator on duty", "Readiness surfaces events starting soon with nobody rostered to host them, as its own signal rather than a line in a list."],
  ["Escalation that names a human", "A Sev 1 during a live broadcast follows the phone path in your agreement. A ticket is the wrong channel for an event already on air."],
];
