import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import { StageStrip } from "./EventOverview";

const isHighlighted = (el) => el.className.split(" ").includes("border-violet-300");

describe("StageStrip lifecycle progress bar single-selection and highlight", () => {
  it("1: Initial active step is correctly derived from the lifecycle state", () => {
    // Draft -> Contribute (index 0)
    const { unmount: unmountDraft } = render(
      <MemoryRouter>
        <StageStrip status="draft" eventId="e1" />
      </MemoryRouter>
    );
    const contribute = screen.getByRole("link", { name: /^Contribute,/ });
    expect(contribute).toHaveAttribute("aria-current", "step");
    expect(isHighlighted(contribute)).toBe(true);
    expect(screen.getAllByRole("link").filter(isHighlighted)).toHaveLength(1);
    unmountDraft();

    // Cancelled -> Understand (index 5)
    const { unmount: unmountCancelled } = render(
      <MemoryRouter>
        <StageStrip status="cancelled" eventId="e1" />
      </MemoryRouter>
    );
    const understand = screen.getByRole("link", { name: /^Understand,/ });
    expect(understand).toHaveAttribute("aria-current", "step");
    expect(isHighlighted(understand)).toBe(true);
    expect(screen.getAllByRole("link").filter(isHighlighted)).toHaveLength(1);
    unmountCancelled();

    // Scheduled -> Produce (index 2)
    const { unmount: unmountScheduled } = render(
      <MemoryRouter>
        <StageStrip status="scheduled" eventId="e1" />
      </MemoryRouter>
    );
    const produce = screen.getByRole("link", { name: /^Produce,/ });
    expect(produce).toHaveAttribute("aria-current", "step");
    expect(isHighlighted(produce)).toBe(true);
    expect(screen.getAllByRole("link").filter(isHighlighted)).toHaveLength(1);
    unmountScheduled();
  });

  it("2 & 3: Clicking Ingest highlights only Ingest, and Understand loses its active highlight", async () => {
    render(
      <MemoryRouter>
        <StageStrip status="cancelled" eventId="e1" />
      </MemoryRouter>
    );

    const understand = screen.getByRole("link", { name: /^Understand,/ });
    const ingest = screen.getByRole("link", { name: /^Ingest,/ });
    const allLinks = screen.getAllByRole("link");

    // Initially Understand is highlighted
    expect(isHighlighted(understand)).toBe(true);
    expect(understand).toHaveAttribute("aria-current", "step");
    expect(isHighlighted(ingest)).toBe(false);
    expect(ingest).not.toHaveAttribute("aria-current");

    // Click Ingest
    await userEvent.click(ingest);

    // Ingest is now highlighted; Understand is no longer highlighted
    expect(isHighlighted(ingest)).toBe(true);
    expect(ingest).toHaveAttribute("aria-current", "step");
    expect(isHighlighted(understand)).toBe(false);
    expect(understand).not.toHaveAttribute("aria-current");
    expect(allLinks.filter(isHighlighted)).toHaveLength(1);
  });

  it("4: Clicking each of the seven steps updates the active highlight correctly and mutually exclusively", async () => {
    render(
      <MemoryRouter>
        <StageStrip status="draft" eventId="e1" />
      </MemoryRouter>
    );

    const labels = ["Contribute", "Ingest", "Produce", "Secure", "Deliver", "Understand", "Preserve"];
    const allLinks = screen.getAllByRole("link");

    for (const label of labels) {
      const link = screen.getByRole("link", { name: new RegExp(`^${label},`) });
      await userEvent.click(link);

      expect(isHighlighted(link)).toBe(true);
      expect(link).toHaveAttribute("aria-current", "step");

      // Verify strictly one active highlight across the entire strip
      const highlighted = allLinks.filter(isHighlighted);
      expect(highlighted).toHaveLength(1);
      expect(highlighted[0]).toBe(link);
    }
  });

  it("5: Repeatedly switching between steps never produces multiple selected highlights", async () => {
    render(
      <MemoryRouter>
        <StageStrip status="cancelled" eventId="e1" />
      </MemoryRouter>
    );

    const understand = screen.getByRole("link", { name: /^Understand,/ });
    const ingest = screen.getByRole("link", { name: /^Ingest,/ });
    const deliver = screen.getByRole("link", { name: /^Deliver,/ });
    const allLinks = screen.getAllByRole("link");

    // Switch: Understand -> Ingest -> Understand -> Deliver -> Ingest
    await userEvent.click(ingest);
    expect(allLinks.filter(isHighlighted)).toHaveLength(1);
    expect(isHighlighted(ingest)).toBe(true);

    await userEvent.click(understand);
    expect(allLinks.filter(isHighlighted)).toHaveLength(1);
    expect(isHighlighted(understand)).toBe(true);

    await userEvent.click(deliver);
    expect(allLinks.filter(isHighlighted)).toHaveLength(1);
    expect(isHighlighted(deliver)).toBe(true);

    await userEvent.click(ingest);
    expect(allLinks.filter(isHighlighted)).toHaveLength(1);
    expect(isHighlighted(ingest)).toBe(true);
  });

  it("6: Completed steps retain their completed styling without incorrectly appearing selected", async () => {
    render(
      <MemoryRouter>
        <StageStrip status="cancelled" eventId="e1" />
      </MemoryRouter>
    );

    const contribute = screen.getByRole("link", { name: /^Contribute,/ });
    const produce = screen.getByRole("link", { name: /^Produce,/ });
    const secure = screen.getByRole("link", { name: /^Secure,/ });
    const ingest = screen.getByRole("link", { name: /^Ingest,/ });

    // In a cancelled event, stages 0 to 4 are completed
    expect(contribute).toHaveAccessibleName(/Contribute, completed/);
    expect(produce).toHaveAccessibleName(/Produce, completed/);
    expect(secure).toHaveAccessibleName(/Secure, completed/);

    // None of these completed steps should have active selection highlight initially
    expect(isHighlighted(contribute)).toBe(false);
    expect(isHighlighted(produce)).toBe(false);
    expect(isHighlighted(secure)).toBe(false);

    // Select Ingest
    await userEvent.click(ingest);

    // Ingest gets selected
    expect(isHighlighted(ingest)).toBe(true);
    // Other completed steps still retain completed semantics and do not appear selected
    expect(contribute).toHaveAccessibleName(/Contribute, completed/);
    expect(isHighlighted(contribute)).toBe(false);
    expect(produce).toHaveAccessibleName(/Produce, completed/);
    expect(isHighlighted(produce)).toBe(false);
  });

  it("7: Updating the underlying lifecycle state updates the active step correctly", () => {
    const { rerender } = render(
      <MemoryRouter>
        <StageStrip status="draft" eventId="e1" />
      </MemoryRouter>
    );

    const contribute = screen.getByRole("link", { name: /^Contribute,/ });
    expect(isHighlighted(contribute)).toBe(true);

    // Status updates to scheduled (current becomes Produce)
    rerender(
      <MemoryRouter>
        <StageStrip status="scheduled" eventId="e1" />
      </MemoryRouter>
    );

    const produce = screen.getByRole("link", { name: /^Produce,/ });
    expect(isHighlighted(produce)).toBe(true);
    expect(isHighlighted(contribute)).toBe(false);

    // Status updates to cancelled (current becomes Understand)
    rerender(
      <MemoryRouter>
        <StageStrip status="cancelled" eventId="e1" />
      </MemoryRouter>
    );

    const understand = screen.getByRole("link", { name: /^Understand,/ });
    expect(isHighlighted(understand)).toBe(true);
    expect(isHighlighted(produce)).toBe(false);
  });

  it("8: Existing lifecycle navigation destinations remain intact", () => {
    render(
      <MemoryRouter>
        <StageStrip status="scheduled" eventId="e1" />
      </MemoryRouter>
    );

    expect(screen.getByRole("link", { name: /^Contribute,/ }))
      .toHaveAttribute("href", "/organization/events/e1/preparation#team");
    expect(screen.getByRole("link", { name: /^Ingest,/ }))
      .toHaveAttribute("href", "/organization/events/e1/preparation#sources");
    expect(screen.getByRole("link", { name: /^Produce,/ }))
      .toHaveAttribute("href", "/organization/events/e1/live");
    expect(screen.getByRole("link", { name: /^Secure,/ }))
      .toHaveAttribute("href", "/organization/events/e1/access#diagnostics");
    expect(screen.getByRole("link", { name: /^Deliver,/ }))
      .toHaveAttribute("href", "/organization/events/e1/insights#quality");
    expect(screen.getByRole("link", { name: /^Understand,/ }))
      .toHaveAttribute("href", "/organization/events/e1/insights");
    expect(screen.getByRole("link", { name: /^Preserve,/ }))
      .toHaveAttribute("href", "/organization/events/e1/replay#retention");
  });
});

