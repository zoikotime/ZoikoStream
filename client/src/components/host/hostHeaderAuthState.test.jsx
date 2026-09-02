import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

import HostHeader from "./HostHeader";

vi.mock("../../hooks/useSystemStats", () => ({ default: () => ({}) }));
vi.mock("../../theme/ThemeContext", () => ({ useTheme: () => ({ theme: "light", toggle: () => {} }) }));

// THE PRODUCTION BUG: with the control socket failing (header showed "Offline · 11"), the
// opening snapshot never arrived, so state.event stayed null and state.canHost stayed at its
// reducer default of false. The console then told a host who WAS correctly assigned in the
// database "No host assigned" and "View only". Those are claims about authorisation; the
// console had simply not been told anything yet. UNKNOWN is not UNAUTHORISED.
// HostHeader renders a router <Link> ("Exit studio"), so it needs a Router in scope.
const renderHeader = (props) =>
  render(<MemoryRouter><HostHeader broadcast={null} analytics={null} {...props} /></MemoryRouter>);

describe("HostHeader authorisation claims", () => {
  it("does not claim the event has no host before the snapshot arrives", () => {
    renderHeader({ ready: false, canHost: false, event: null });
    expect(screen.queryByText("No host assigned")).toBeNull();
    expect(screen.getByText(/connecting to the studio/i)).toBeInTheDocument();
  });

  it("does not brand a not-yet-known user View only", () => {
    renderHeader({ ready: false, canHost: false, event: null });
    expect(screen.queryByText("View only")).toBeNull();
    expect(screen.getByText("Connecting…")).toBeInTheDocument();
  });

  // The other half: once the console HAS been told, it must report the truth plainly.
  // This is what stops the fix from becoming a way to hide a real authorisation failure.
  it("still says View only for a genuine non-host once the snapshot has arrived", () => {
    renderHeader({ ready: true, canHost: false, event: { name: "E", host: "Someone Else" } });
    expect(screen.getByText("View only")).toBeInTheDocument();
  });

  it("still says No host assigned when the event genuinely has none", () => {
    renderHeader({ ready: true, canHost: false, event: { name: "E", host: null } });
    expect(screen.getByText("No host assigned")).toBeInTheDocument();
  });

  it("shows neither warning for an authorised host", () => {
    renderHeader({ ready: true, canHost: true, event: { name: "E", host: "Harish Reddy Satti" } });
    expect(screen.queryByText("View only")).toBeNull();
    expect(screen.queryByText("No host assigned")).toBeNull();
    expect(screen.getByText("Hosted by Harish Reddy Satti")).toBeInTheDocument();
  });
});
