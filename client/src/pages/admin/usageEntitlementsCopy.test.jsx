// The note under each quota on Usage & Entitlements.
//
// Streaming hours for an organization with no entitled plan used to read as two clauses
// joined by a separator — "No entitled plan · Not enforced — shown against the plan only" —
// and now reads as one sentence. The wording is scoped to that metric: `enforced: false` is
// a general flag that today only streaming hours carries, so storage and members must keep
// their own notes, and a future non-enforced metric must not inherit this sentence.
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { QuotaBar } from "./UsageEntitlements";

const NEW_NOTE = "No entitled plan — Standard usage limits apply";
const OLD_CLAUSE = "Not enforced — shown against the plan only";

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
  enforced: false,
  enforced_limit: null,
  ...over,
});

const show = (q) => render(<QuotaBar quota={q} />);
const note = (key) => screen.getByTestId(`quota-${key}`).textContent;

describe("streaming hours without an entitled plan", () => {
  it("reads exactly as specified", () => {
    show(quota());
    expect(note("streaming_hours")).toContain(NEW_NOTE);
  });

  it("no longer shows the clause it replaced", () => {
    show(quota());
    expect(note("streaming_hours")).not.toContain(OLD_CLAUSE);
  });

  it("does not leave the bare phrase dangling beside the new sentence", () => {
    // "No entitled plan" must appear once, as part of the sentence — not also on its own.
    const text = (show(quota()), note("streaming_hours"));
    expect(text.match(/No entitled plan/g)).toHaveLength(1);
  });

  it("still reports the measured usage", () => {
    show(quota({ used: 12 }));
    expect(note("streaming_hours")).toContain("12 hrs");
  });
});

describe("the other metrics keep their own notes", () => {
  it("storage without a plan still says only 'No entitled plan'", () => {
    show(quota({ key: "storage", label: "Storage", unit: "GB", enforced: true, enforced_limit: 5000 }));
    const text = note("storage");
    expect(text).toContain("No entitled plan");
    expect(text).not.toContain("Standard usage limits apply");
  });

  it("storage still reports the ceiling that actually applies", () => {
    show(quota({ key: "storage", label: "Storage", unit: "GB", enforced: true, enforced_limit: 5000 }));
    expect(note("storage")).toContain("Enforced ceiling 5000 GB");
  });

  it("members without a plan is unchanged", () => {
    show(quota({ key: "members", label: "Members", unit: "seats", enforced: true }));
    const text = note("members");
    expect(text).toContain("No entitled plan");
    expect(text).not.toContain("Standard usage limits apply");
  });

  it("a non-enforced metric that is not streaming hours keeps the old clause", () => {
    // The wording is scoped by key on purpose, so the general flag does not carry it.
    show(quota({ key: "something_else", label: "Something else", enforced: false }));
    const text = note("something_else");
    expect(text).toContain(OLD_CLAUSE);
    expect(text).not.toContain("Standard usage limits apply");
  });
});

describe("streaming hours WITH a plan is untouched", () => {
  it("still states that nothing enforces it", () => {
    show(quota({ quota_state: "within", limit: 100, used: 10, remaining: 90, percent: 10 }));
    const text = note("streaming_hours");
    expect(text).toContain(OLD_CLAUSE);
    expect(text).not.toContain("Standard usage limits apply");
  });

  it("still reports remaining and percent", () => {
    show(quota({ quota_state: "within", limit: 100, used: 10, remaining: 90, percent: 10 }));
    expect(note("streaming_hours")).toContain("90 hrs remaining");
  });

  it("still reports an exceeded plan", () => {
    show(quota({ quota_state: "exceeded", limit: 10, used: 12, remaining: -2, percent: 120 }));
    expect(note("streaming_hours")).toContain("Over by 2 hrs");
  });
});
