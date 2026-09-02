import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";

import StudioStage from "./StudioStage";

vi.mock("../../hooks/useInterval", () => ({ default: () => {} }));

// THE DEFECT: going live flips broadcast.status to "live" the moment the backend accepts it,
// but LiveKit publishing completes a beat later. In that window the stage showed a saturated
// red "On air" chip while nothing was reaching a single viewer — contradicting the honesty
// banner directly beneath it, which correctly read "Connecting the publisher…". The same held
// for a host who was live with the preview off: no media going out, chip still claiming ON AIR.
const media = {
  videoRef: () => {}, actual: null, active: true, error: null,
  phase: "ready", videoTrack: {},
};
const stage = (props) =>
  render(<StudioStage media={media} analytics={null} recording={null} {...props} />);

describe("StudioStage on-air chip", () => {
  it("does not claim On air while the publisher is still connecting", () => {
    stage({ broadcast: { status: "live" }, isPublishing: false, camera: true, mic: true });
    expect(screen.queryByText("On air")).toBeNull();
    expect(screen.getByText("Going live…")).toBeInTheDocument();
  });

  it("claims On air once media is actually publishing", () => {
    stage({ broadcast: { status: "live" }, isPublishing: true, camera: true, mic: true });
    expect(screen.getByText("On air")).toBeInTheDocument();
  });

  it("still reports a reconnecting publisher rather than On air", () => {
    stage({ broadcast: { status: "live" }, isPublishing: false, isReconnecting: true,
            camera: true, mic: true });
    expect(screen.queryByText("On air")).toBeNull();
    expect(screen.getByText("Reconnecting")).toBeInTheDocument();
  });

  it("still reports a backend-confirmed degraded feed rather than On air", () => {
    stage({ broadcast: { status: "live" }, isPublishing: true, eventStatus: "degraded",
            camera: true, mic: true });
    expect(screen.queryByText("On air")).toBeNull();
    expect(screen.getByText("At risk")).toBeInTheDocument();
  });

  // Publishing is irrelevant before the event is live — this must stay plain "Preview".
  it("shows Preview before the broadcast is live", () => {
    stage({ broadcast: { status: "preview" }, isPublishing: false, camera: true, mic: true });
    expect(screen.getByText("Preview")).toBeInTheDocument();
    expect(screen.queryByText("Going live…")).toBeNull();
  });
});
