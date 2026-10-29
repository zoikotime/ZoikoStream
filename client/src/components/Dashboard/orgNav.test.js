// The organization rail's contract as data (ZST-UX-NAV-001 §4): order, role and feature
// gating, and which child a location means. Rendering is OrganizationShell.test's job.
import { describe, expect, it } from "vitest";
import { ORG_NAV, activeChildId, groupIsActive, resolveOrgNav } from "./orgNav";

const labels = (items) => items.map((i) => i.label);
const byId = (id) => ORG_NAV.find((i) => i.id === id);

describe("resolveOrgNav", () => {
  it("gives an organization admin the seven destinations in the spec's order", () => {
    expect(labels(resolveOrgNav({ role: "org_admin" }))).toEqual([
      "Overview", "Events", "Audience & Access", "Media & Recordings", "Analytics", "Organization", "Support",
    ]);
  });

  it("adds Developers only when the organization has developer access in use", () => {
    expect(labels(resolveOrgNav({ role: "org_admin", features: { developers: true } })).at(-1)).toBe("Developers");
    expect(labels(resolveOrgNav({ role: "org_admin", features: {} }))).not.toContain("Developers");
  });

  it("keeps Developers while you are on one of its pages", () => {
    expect(labels(resolveOrgNav({ role: "org_admin", pathname: "/organization/credentials" }))).toContain("Developers");
  });

  it.each(["host", "speaker", "viewer", "billing_admin", undefined])(
    "shows %s only the Overview — the rest are admin routes",
    (role) => {
      expect(labels(resolveOrgNav({ role }))).toEqual(["Overview"]);
    }
  );

  it("treats a super admin as an admin", () => {
    expect(resolveOrgNav({ role: "super_admin" })).toHaveLength(7);
  });

  it("has no Profile, Streaming Sessions or webhooks entry anywhere", () => {
    const all = ORG_NAV.flatMap((i) => [i, ...(i.children || [])]);
    for (const item of all) {
      expect(item.label).not.toMatch(/profile|streaming sessions|webhook/i);
      expect(item.to).not.toMatch(/\/organization\/(profile|sessions|webhooks)/);
    }
  });
});

describe("which group and child a location means", () => {
  it("files pages reached from inside a group under that group", () => {
    expect(groupIsActive(byId("events.list"), "/organization/sessions")).toBe(true);
    expect(groupIsActive(byId("events.list"), "/organization/live-inputs")).toBe(true);
    expect(groupIsActive(byId("org.manage"), "/organization/billing")).toBe(true);
    expect(groupIsActive(byId("support.home"), "/organization/support/security")).toBe(true);
    expect(groupIsActive(byId("audience.home"), "/organization/audience")).toBe(true);
    expect(groupIsActive(byId("audience.home"), "/organization/audience/access")).toBe(true);
    expect(groupIsActive(byId("audience.home"), "/organization/playback")).toBe(true);
    expect(groupIsActive(byId("events.list"), "/organization/eventsx")).toBe(false);
  });

  it("picks the most specific child that matches path AND query", () => {
    const events = byId("events.list");
    expect(activeChildId(events, "/organization/events", "")).toBe("events.all");
    expect(activeChildId(events, "/organization/events", "?view=calendar")).toBe("events.calendar");
    expect(activeChildId(events, "/organization/events", "?status=on_air&page=2")).toBe("events.live");
    const org = byId("org.manage");
    expect(activeChildId(org, "/organization/settings", "?tab=branding")).toBe("org.branding");
    expect(activeChildId(org, "/organization/settings", "?tab=security")).toBe("org.settings");
    expect(activeChildId(org, "/organization/manage", "")).toBe("org.overview");
    expect(activeChildId(org, "/organization/billing/invoices", "")).toBeNull();
    const media = byId("media.list");
    expect(activeChildId(media, "/organization/recordings", "")).toBe("media.all");
    expect(activeChildId(media, "/organization/recordings", "?state=ready&q=gala")).toBe("media.ready");
    const analytics = byId("analytics.home");
    expect(activeChildId(analytics, "/organization/analytics", "")).toBe("analytics.overview");
    expect(activeChildId(analytics, "/organization/analytics", "", "#quality")).toBe("analytics.quality");
    const audience = byId("audience.home");
    expect(activeChildId(audience, "/organization/audience", "")).toBe("audience.overview");
    expect(activeChildId(audience, "/organization/audience/access", "")).toBe("audience.access");
    expect(activeChildId(audience, "/organization/playback", "")).toBe("audience.access");
  });
});
