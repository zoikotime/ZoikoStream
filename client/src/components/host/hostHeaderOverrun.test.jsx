import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import HostHeader from "./HostHeader";

vi.mock("../../hooks/useSystemStats", () => ({ default: () => ({}) }));
vi.mock("../../theme/ThemeContext", () => ({ useTheme: () => ({ theme: "light", toggle: () => {} }) }));

const renderHeader = (props) =>
  render(
    <MemoryRouter>
      <HostHeader {...props} />
    </MemoryRouter>
  );

describe("HostHeader scheduled overrun displays", () => {
  it("displays OVERTIME badge and cluster readout when broadcast is live and in overtime", () => {
    const overrun = {
      isOvertime: true,
      isEndingSoon: false,
      formattedOverrun: "+03:42",
      overrunMinutesText: "3m 42s",
      remainingSeconds: 0,
      overrunSeconds: 222,
    };

    renderHeader({
      event: { name: "Keynote", scheduled_end: "2026-09-29T11:53:00Z" },
      broadcast: { status: "live", started_at: "2026-09-29T11:42:00Z" },
      overrun,
      canHost: true,
      ready: true,
    });

    // Badge
    expect(screen.getByText(/OVERTIME \+03:42/)).toBeInTheDocument();
    // Cluster readout
    expect(screen.getByText("+03:42")).toBeInTheDocument();
  });

  it("displays ending soon badge and countdown when approaching scheduled end", () => {
    const overrun = {
      isOvertime: false,
      isEndingSoon: true,
      formattedRemaining: "04:30",
      remainingSeconds: 270,
      overrunSeconds: 0,
    };

    renderHeader({
      event: { name: "Keynote", scheduled_end: "2026-09-29T11:53:00Z" },
      broadcast: { status: "live", started_at: "2026-09-29T11:42:00Z" },
      overrun,
      canHost: true,
      ready: true,
    });

    expect(screen.getAllByText("04:30 left")).toHaveLength(2);
  });

  it("does not display overtime badge when broadcast is not live", () => {
    const overrun = {
      isOvertime: true,
      isEndingSoon: false,
      formattedOverrun: "+03:42",
      overrunMinutesText: "3m 42s",
    };

    renderHeader({
      event: { name: "Keynote", scheduled_end: "2026-09-29T11:53:00Z" },
      broadcast: { status: "ended", started_at: "2026-09-29T11:42:00Z" },
      overrun,
      canHost: true,
      ready: true,
    });

    expect(screen.queryByText(/OVERTIME/)).toBeNull();
  });
});
