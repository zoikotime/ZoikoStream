// client/src/data/orgSettings.js
// Dummy data + option lists for the Organization Settings page (/organization/settings).
// ponytail: mock data — swap for the org-settings endpoints when the backend lands.

export const orgProfile = {
  name: "Zoiko Group",
  slug: "zoiko-group",
  website: "https://zoikotech.com",
  supportEmail: "support@zoikotech.com",
  industry: "Technology",
  size: "51–200",
  description: "Enterprise live-streaming and virtual events for global teams.",
};
export const INDUSTRIES = ["Technology", "Media & Entertainment", "Education", "Finance", "Healthcare", "Retail", "Nonprofit", "Other"];
export const COMPANY_SIZES = ["1–10", "11–50", "51–200", "201–500", "500+"];

export const domain = { custom: "events.zoikotech.com", status: "Verified" };

// Keys into the app's ACCENT token map (Branding color picker).
// "emerald" is deliberately absent: index.css remaps the emerald scale onto the brand purple
// (--color-emerald-600: #7c3aed), which is byte-identical to violet-600. Offering both drew two
// indistinguishable swatches, and picking the second stored the string "emerald" for a purple.
// An org that already saved "emerald" falls back to "violet" — the same colour, so nothing moves.
export const ACCENTS = ["violet", "indigo", "blue", "amber", "rose"];

export const securityDefaults = {
  require2fa: true,
  enforceSSO: false,
  minPasswordLength: 12,
  sessionTimeout: "8 hours",
  allowedDomains: "zoikotech.com",
};
export const SESSION_TIMEOUTS = ["1 hour", "8 hours", "24 hours", "7 days", "Never"];
export const PASSWORD_LENGTHS = [8, 10, 12, 16];

// User Permissions — Owner always has full access, so it isn't editable here.
export const ROLES = ["Admin", "Moderator", "Host", "Member"];
export const PERMISSIONS = [
  { key: "events", label: "Create & manage events" },
  { key: "recordings", label: "Manage recordings" },
  { key: "analytics", label: "View analytics" },
  { key: "users", label: "Invite & manage users" },
  { key: "billing", label: "Manage billing" },
  { key: "settings", label: "Manage settings" },
];
export const permissionDefaults = {
  Admin: ["events", "recordings", "analytics", "users", "billing", "settings"],
  Moderator: ["events", "recordings", "analytics"],
  Host: ["events", "recordings", "analytics"],
  Member: ["analytics"],
};

export const notificationGroups = [
  {
    title: "Email",
    items: [
      { key: "eventScheduled", label: "New event scheduled", desc: "When a teammate schedules an event" },
      { key: "eventStarting", label: "Event starting soon", desc: "30 minutes before an event goes live" },
      { key: "recordingReady", label: "Recording ready", desc: "When a recording finishes processing" },
      { key: "weeklySummary", label: "Weekly analytics summary", desc: "A digest of last week's performance" },
      { key: "billing", label: "Billing & invoices", desc: "Receipts and payment reminders" },
    ],
  },
  {
    title: "Product",
    items: [
      { key: "mentions", label: "Mentions in chat & Q&A", desc: "When someone @mentions your team" },
      { key: "memberJoined", label: "New team member joined", desc: "When an invite is accepted" },
      { key: "securityAlerts", label: "Security alerts", desc: "New sign-ins and permission changes" },
    ],
  },
];
export const notificationDefaults = {
  eventScheduled: true, eventStarting: true, recordingReady: true, weeklySummary: false,
  billing: true, mentions: true, memberJoined: false, securityAlerts: true,
};

export const apiKeysSeed = [
  { id: 1, name: "Production", token: "zk_live_9f2a7c4d8e1b6a30", created: "2026-02-11", lastUsed: "2026-07-20" },
  { id: 2, name: "Staging", token: "zk_test_3b8e1d5f7a2c9048", created: "2026-04-02", lastUsed: "2026-07-18" },
];

export const integrationsSeed = [
  { key: "slack", name: "Slack", desc: "Post event alerts to channels", connected: true },
  { key: "zoom", name: "Zoom", desc: "Import meetings as events", connected: false },
  { key: "salesforce", name: "Salesforce", desc: "Sync attendee data to your CRM", connected: false },
  { key: "zapier", name: "Zapier", desc: "Automate workflows across apps", connected: true },
  { key: "ga", name: "Google Analytics", desc: "Track viewer traffic", connected: false },
  { key: "webhooks", name: "Webhooks", desc: "Send events to your endpoint", connected: true },
];
