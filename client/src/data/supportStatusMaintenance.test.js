// The Support page's maintenance rules (data/supportStatus.js): which of the three empty states
// is true, how a window's time reads, and that nothing claims "no maintenance" without an
// authoritative schedule behind it.
import { describe, expect, it } from "vitest";
import {
  MAINTENANCE_COPY, formatMaintenanceWindow, maintenanceLabel, maintenanceState,
} from "./supportStatus";

const window_ = (over = {}) => ({
  reference: "MNT-1", title: "Streaming node replacement", kind: "scheduled", status: "scheduled",
  starts_at: "2026-10-18T02:00:00+00:00", ends_at: "2026-10-18T04:00:00+00:00",
  impact: "Live sessions may briefly reconnect.", components: ["Live streaming"], regions: ["Europe"],
  ...over,
});

describe("maintenanceState", () => {
  it("lists published windows whatever the schedule's authority", () => {
    expect(maintenanceState({ authoritative: false, upcoming: [window_()] }))
      .toEqual({ kind: "upcoming", items: [window_()] });
  });

  it("says there is no scheduled maintenance only when an empty schedule is authoritative", () => {
    expect(maintenanceState({ authoritative: true, upcoming: [] }))
      .toEqual({ kind: "none", text: "No scheduled maintenance at this time." });
  });

  it("otherwise promises nothing about absence", () => {
    const state = maintenanceState({ authoritative: false, upcoming: [] });
    expect(state).toEqual({ kind: "pending", text: "Maintenance updates will appear here when available." });
    expect(state.text).not.toMatch(/no scheduled maintenance/i);
  });

  it.each([[undefined], [null], [{}], [{ upcoming: "x" }]])(
    "treats a missing or malformed feed (%j) as unavailable, never as none", (payload) => {
      expect(maintenanceState(payload))
        .toEqual({ kind: "unavailable", text: "Scheduled maintenance information is temporarily unavailable." });
    });

  it("never uses implementation wording in any state", () => {
    for (const text of Object.values(MAINTENANCE_COPY)) {
      expect(text).not.toMatch(/calendar|scheduler|deployment|connected|configured|integrat|provider|backend/i);
    }
  });
});

describe("formatMaintenanceWindow", () => {
  const utc = { timeZone: "UTC", locale: "en-US" };

  it("shows a same-day window once, with the zone named, and its length", () => {
    expect(formatMaintenanceWindow("2026-10-18T02:00:00Z", "2026-10-18T04:00:00Z", utc))
      .toEqual({ when: "Sun, Oct 18, 2:00 AM – 4:00 AM UTC", duration: "2 h" });
  });

  it("shows both dates when a window crosses midnight", () => {
    expect(formatMaintenanceWindow("2026-10-18T23:30:00Z", "2026-10-19T01:00:00Z", utc))
      .toEqual({ when: "Sun, Oct 18, 11:30 PM – Mon, Oct 19, 1:00 AM UTC", duration: "1 h 30 min" });
  });

  it("reads in the viewer's own zone", () => {
    const kolkata = formatMaintenanceWindow("2026-10-18T02:00:00Z", "2026-10-18T02:45:00Z",
                                            { timeZone: "Asia/Kolkata", locale: "en-US" });
    expect(kolkata).toEqual({ when: "Sun, Oct 18, 7:30 AM – 8:15 AM GMT+5:30", duration: "45 min" });
  });

  it("copes with a missing end, and refuses an unreadable start", () => {
    expect(formatMaintenanceWindow("2026-10-18T02:00:00Z", null, utc))
      .toEqual({ when: "Sun, Oct 18, 2:00 AM UTC", duration: null });
    expect(formatMaintenanceWindow("not a date", null, utc)).toBeNull();
  });
});

describe("maintenanceLabel", () => {
  it("names the window's real state, and never calls emergency work scheduled", () => {
    expect(maintenanceLabel(window_())).toBe("Scheduled");
    expect(maintenanceLabel(window_({ status: "in_progress" }))).toBe("In progress");
    expect(maintenanceLabel(window_({ status: "extended" }))).toBe("Extended");
    expect(maintenanceLabel(window_({ kind: "emergency" }))).toBe("Emergency");
  });
});
