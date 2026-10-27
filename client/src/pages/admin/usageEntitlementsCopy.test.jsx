// The note under each quota on Usage & Entitlements.
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { QuotaBar } from "./UsageEntitlements";

const NO_QUOTA_NOTE = "No quota configured — new activity is blocked";

// A quota as services/org.py's `bar()` reports it.
const quota = (over = {}) => ({
  key: "streaming_hours",
  label: "Streaming hours",
  used: 0,
  limit: null,
  unit: "hrs",
  percent: null,
  remaining: null,
  unlimited: false,
  quota_state: "not_applicable",
  usage_state: "measured",
  enforced: true,
  enforced_limit: null,
  effective_limit: null,
  quota_source: "unconfigured",
  ...over,
});

const show = (q) => render(<QuotaBar quota={q} />);
const note = (key) => screen.getByTestId(`quota-${key}`).textContent;

describe("an unconfigured quota", () => {
  it("clearly says new activity is blocked", () => {
    show(quota());
    expect(note("streaming_hours")).toContain(NO_QUOTA_NOTE);
  });

  it("does not imply an unlimited or monitoring-only allowance", () => {
    show(quota());
    expect(note("streaming_hours")).not.toContain("unlimited");
    expect(note("streaming_hours")).not.toContain("Not enforced");
  });

  it("still reports the measured usage", () => {
    show(quota({ used: 12 }));
    expect(note("streaming_hours")).toContain("12 hrs");
  });
});

describe("the other metrics keep their own notes", () => {
  it("storage without an applicable quota reports that new recordings are blocked", () => {
    show(quota({ key: "storage", label: "Storage", unit: "GB" }));
    const text = note("storage");
    expect(text).toContain(NO_QUOTA_NOTE);
  });

  it("storage reports a configured platform default as its effective ceiling", () => {
    show(quota({
      key: "storage", label: "Storage", unit: "GB", quota_state: "within",
      effective_limit: 50, limit: null, enforced_limit: 50, quota_source: "platform_default",
      percent: 0, remaining: 50,
    }));
    expect(note("storage")).toContain("0 / 50 GB");
    expect(note("storage")).toContain("Platform default");
  });

  it("members without a plan is unchanged", () => {
    show(quota({ key: "members", label: "Members", unit: "seats", enforced: true }));
    const text = note("members");
    expect(text).toContain(NO_QUOTA_NOTE);
  });
});

describe("streaming hours with an enforced plan quota", () => {
  it("shows the effective measurement and remaining allowance", () => {
    show(quota({
      quota_state: "within", limit: 100, effective_limit: 100, used: 10,
      remaining: 90, percent: 10, quota_source: "plan",
    }));
    const text = note("streaming_hours");
    expect(text).toContain("10 / 100 hrs");
    expect(note("streaming_hours")).toContain("90 hrs remaining");
  });

  it("still reports an exceeded plan", () => {
    show(quota({
      quota_state: "exceeded", limit: 10, effective_limit: 10,
      used: 12, remaining: -2, percent: 120, quota_source: "plan",
    }));
    expect(note("streaming_hours")).toContain("Over by 2 hrs");
  });
});
