// FactGrid: three states that must never be confused.
//
//   a measured 0        -> "0"      — we looked, and the answer was none
//   collected, N/A here -> "—" + why — the metric exists but not for this window/event
//   not collected       -> "—" + why — nothing is recorded, by design
//
// ── THE BUG ─────────────────────────────────────────────────────────────────────────────
// Every caller already supplied a precise `reason`, and this component put it ONLY in
// `title`. So the Audience page's "Not measured" panel — which knows that geography is blank
// because "Per-viewer location is not collected" — reached the reader as three bare dashes,
// and a blank show-rate ("No private-event registrations in this window") looked identical to
// a broken metric. A tooltip is unreachable on a touch device and undiscoverable on a
// desktop, so a page that was being precise looked like one that was failing.
//
// The dash itself is unchanged and is the whole point of the component: a `0` in its place
// would assert a measurement that was never taken.
import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import FactGrid from "./FactGrid";

const factFor = (label) => screen.getByText(label).closest("div");

const renderGrid = (facts) => render(<FactGrid facts={facts} />);

describe("a measured value", () => {
  it("is shown as given", () => {
    renderGrid([{ label: "Estimated attendees", value: 12 }]);
    expect(within(factFor("Estimated attendees")).getByText("12")).toBeInTheDocument();
  });

  it("renders a measured ZERO as 0, never as a dash", () => {
    // The distinction the component exists for: 0 is an answer.
    renderGrid([{ label: "Returning attendees", value: 0 }]);
    const fact = factFor("Returning attendees");
    expect(within(fact).getByText("0")).toBeInTheDocument();
    expect(within(fact).queryByText("—")).not.toBeInTheDocument();
  });

  it("renders a measured zero STRING too", () => {
    renderGrid([{ label: "Average watch time", value: "0m" }]);
    expect(within(factFor("Average watch time")).getByText("0m")).toBeInTheDocument();
  });

  it("shows no explanatory text when there is a value", () => {
    renderGrid([{ label: "Estimated attendees", value: 12, reason: "should not appear" }]);
    expect(screen.queryByText("should not appear")).not.toBeInTheDocument();
  });
});

describe("an absent value", () => {
  it("renders a dash, never a zero", () => {
    renderGrid([{ label: "Audience geography", value: null, reason: "Not collected" }]);
    const fact = factFor("Audience geography");
    expect(within(fact).getByText("—")).toBeInTheDocument();
    expect(within(fact).queryByText("0")).not.toBeInTheDocument();
  });

  it("PRINTS the reason rather than hiding it in a tooltip", () => {
    // The regression: this was `title`-only, so the page showed three unexplained dashes.
    renderGrid([
      { label: "Audience geography", value: null, reason: "Per-viewer location is not collected" },
    ]);
    expect(screen.getByText("Per-viewer location is not collected")).toBeInTheDocument();
  });

  it("keeps the hover explanation as well, for a clipped column", () => {
    renderGrid([{ label: "Blocked join attempts", value: null, reason: "Not recorded" }]);
    expect(within(factFor("Blocked join attempts")).getByTitle("Not recorded")).toBeInTheDocument();
  });

  it("treats undefined the same as null", () => {
    renderGrid([{ label: "Device and player mix", value: undefined, reason: "Not ingested" }]);
    expect(within(factFor("Device and player mix")).getByText("—")).toBeInTheDocument();
    expect(screen.getByText("Not ingested")).toBeInTheDocument();
  });

  it("shows a bare dash when no reason was supplied", () => {
    // A loading panel passes no reason: the figure is not missing, it is not here YET, and
    // printing "No data" under it would be a false statement that resolves a second later.
    renderGrid([{ label: "Estimated attendees", value: null }]);
    const fact = factFor("Estimated attendees");
    expect(within(fact).getByText("—")).toBeInTheDocument();
    expect(fact.querySelector("p")).toBeNull();
  });
});

describe("the three states stay distinguishable side by side", () => {
  it("separates measured-zero, not-applicable and not-collected", () => {
    renderGrid([
      { label: "Returning attendees", value: 0 },
      { label: "Registration → attendance", value: null,
        reason: "No private-event registrations in this window" },
      { label: "Audience geography", value: null,
        reason: "Per-viewer location is not collected" },
    ]);
    // Measured.
    expect(within(factFor("Returning attendees")).getByText("0")).toBeInTheDocument();
    // Collected, but does not apply to this window.
    expect(screen.getByText("No private-event registrations in this window")).toBeInTheDocument();
    // Never collected at all, by design.
    expect(screen.getByText("Per-viewer location is not collected")).toBeInTheDocument();
    // Both absences read as absences, not as zero.
    expect(screen.getAllByText("—")).toHaveLength(2);
  });
});
