import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import useEventOverrun, {
  computeOverrunState,
  formatDuration,
  formatOverrunMinutes,
  REMINDER_THRESHOLDS,
} from "./useEventOverrun";
import { notify } from "../ui/Toast";

vi.mock("../ui/Toast", () => ({
  notify: {
    warning: vi.fn(),
    info: vi.fn(),
    error: vi.fn(),
  },
}));

vi.mock("../utils/sound", () => ({
  playAlertChime: vi.fn(),
}));

describe("useEventOverrun & computeOverrunState", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("1. event before warning window -> on_time state, no alert", () => {
    const now = new Date("2026-09-29T11:30:00Z").getTime();
    const scheduledEnd = "2026-09-29T11:53:00Z"; // 23 mins left
    const state = computeOverrunState(scheduledEnd, now);

    expect(state.state).toBe("on_time");
    expect(state.remainingSeconds).toBe(23 * 60);
    expect(state.overrunSeconds).toBe(0);
    expect(state.isOvertime).toBe(false);
    expect(state.isEndingSoon).toBe(false);
    expect(state.formattedRemaining).toBe("23:00");
  });

  it("2. 10-minute warning threshold is defined as 600s", () => {
    const now = new Date("2026-09-29T11:43:00Z").getTime();
    const scheduledEnd = "2026-09-29T11:53:00Z"; // 10 mins = 600s
    const state = computeOverrunState(scheduledEnd, now);

    expect(state.state).toBe("ending_soon");
    expect(state.remainingSeconds).toBe(600);
    expect(state.isEndingSoon).toBe(true);
    expect(state.isOvertime).toBe(false);
  });

  it("3. 5-minute warning threshold is defined as 300s", () => {
    const now = new Date("2026-09-29T11:48:00Z").getTime();
    const scheduledEnd = "2026-09-29T11:53:00Z"; // 5 mins = 300s
    const state = computeOverrunState(scheduledEnd, now);

    expect(state.state).toBe("ending_soon");
    expect(state.remainingSeconds).toBe(300);
    expect(state.isEndingSoon).toBe(true);
  });

  it("4. 1-minute warning threshold is defined as 60s", () => {
    const now = new Date("2026-09-29T11:52:00Z").getTime();
    const scheduledEnd = "2026-09-29T11:53:00Z"; // 1 min = 60s
    const state = computeOverrunState(scheduledEnd, now);

    expect(state.state).toBe("ending_soon");
    expect(state.remainingSeconds).toBe(60);
    expect(state.isEndingSoon).toBe(true);
  });

  it("5. scheduled end reached -> state is ended_schedule with isOvertime true", () => {
    const now = new Date("2026-09-29T11:53:00Z").getTime();
    const scheduledEnd = "2026-09-29T11:53:00Z";
    const state = computeOverrunState(scheduledEnd, now);

    expect(state.state).toBe("ended_schedule");
    expect(state.remainingSeconds).toBe(0);
    expect(state.overrunSeconds).toBe(0);
    expect(state.isOvertime).toBe(true);
  });

  it("6. overtime +1 minute displayed as +01:00", () => {
    const now = new Date("2026-09-29T11:54:00Z").getTime();
    const scheduledEnd = "2026-09-29T11:53:00Z";
    const state = computeOverrunState(scheduledEnd, now);

    expect(state.state).toBe("overtime");
    expect(state.overrunSeconds).toBe(60);
    expect(state.formattedOverrun).toBe("+01:00");
    expect(state.overrunMinutesText).toBe("1 minute");
    expect(state.isOvertime).toBe(true);
  });

  it("7. overtime timer continues increasing (+03:42)", () => {
    const now = new Date("2026-09-29T11:56:42Z").getTime();
    const scheduledEnd = "2026-09-29T11:53:00Z";
    const state = computeOverrunState(scheduledEnd, now);

    expect(state.state).toBe("overtime");
    expect(state.overrunSeconds).toBe(222);
    expect(state.formattedOverrun).toBe("+03:42");
    expect(state.overrunMinutesText).toBe("3m 42s");
  });

  it("8. warning persists while live across multiple ticks", () => {
    const startTime = new Date("2026-09-29T11:53:30Z").getTime();
    vi.setSystemTime(startTime);

    const { result } = renderHook(() =>
      useEventOverrun({
        scheduledEnd: "2026-09-29T11:53:00Z",
        isLive: true,
      })
    );

    expect(result.current.isOvertime).toBe(true);
    expect(result.current.overrunSeconds).toBe(30);

    // Advance 30 seconds
    act(() => {
      vi.advanceTimersByTime(30000);
    });

    expect(result.current.isOvertime).toBe(true);
    expect(result.current.overrunSeconds).toBe(60);
    expect(result.current.formattedOverrun).toBe("+01:00");
  });

  it("9. ending broadcast removes live alerts when isLive is false", () => {
    const startTime = new Date("2026-09-29T11:52:50Z").getTime();
    vi.setSystemTime(startTime);

    const onThreshold = vi.fn();
    const { rerender } = renderHook(
      ({ isLive }) =>
        useEventOverrun({
          scheduledEnd: "2026-09-29T11:53:00Z",
          isLive,
          onThresholdReached: onThreshold,
        }),
      { initialProps: { isLive: true } }
    );

    // End broadcast
    rerender({ isLive: false });

    // Advance past end
    act(() => {
      vi.advanceTimersByTime(20000);
    });

    // onThresholdReached should NOT fire when isLive is false
    expect(onThreshold).not.toHaveBeenCalled();
  });

  it("10. rescheduling updates deadline immediately", () => {
    const startTime = new Date("2026-09-29T11:54:00Z").getTime();
    vi.setSystemTime(startTime);

    const { result, rerender } = renderHook(
      ({ scheduledEnd }) =>
        useEventOverrun({
          scheduledEnd,
          isLive: true,
        }),
      { initialProps: { scheduledEnd: "2026-09-29T11:53:00Z" } }
    );

    expect(result.current.isOvertime).toBe(true);
    expect(result.current.overrunSeconds).toBe(60);

    // Reschedule to 12:15
    rerender({ scheduledEnd: "2026-09-29T12:15:00Z" });

    expect(result.current.isOvertime).toBe(false);
    expect(result.current.remainingSeconds).toBe(21 * 60);
    expect(result.current.formattedRemaining).toBe("21:00");
  });

  it("11. refresh/reconnect preserves correct overtime", () => {
    const now = new Date("2026-09-29T11:57:30Z").getTime();
    vi.setSystemTime(now);

    const { result: first } = renderHook(() =>
      useEventOverrun({
        scheduledEnd: "2026-09-29T11:53:00Z",
        isLive: true,
      })
    );
    expect(first.current.overrunSeconds).toBe(270);
    expect(first.current.formattedOverrun).toBe("+04:30");

    // Simulating reconnect 10s later
    vi.setSystemTime(now + 10000);
    const { result: second } = renderHook(() =>
      useEventOverrun({
        scheduledEnd: "2026-09-29T11:53:00Z",
        isLive: true,
      })
    );
    expect(second.current.overrunSeconds).toBe(280);
    expect(second.current.formattedOverrun).toBe("+04:40");
  });

  it("12. no duplicate notifications: thresholds trigger exactly once", () => {
    // Start at 602 seconds remaining (just before 10 minute warning)
    const startTime = new Date("2026-09-29T11:42:58Z").getTime();
    vi.setSystemTime(startTime);

    const onThreshold = vi.fn();
    renderHook(() =>
      useEventOverrun({
        scheduledEnd: "2026-09-29T11:53:00Z",
        isLive: true,
        onThresholdReached: onThreshold,
      })
    );

    expect(onThreshold).not.toHaveBeenCalled();

    // Advance 5 seconds (crosses 600s boundary)
    act(() => {
      vi.advanceTimersByTime(5000);
    });

    expect(onThreshold).toHaveBeenCalledTimes(1);
    expect(onThreshold).toHaveBeenCalledWith(
      expect.objectContaining({ seconds: 600, message: "10 minutes remaining" })
    );

    // Advance another 10 seconds (still within 10 minute window)
    act(() => {
      vi.advanceTimersByTime(10000);
    });

    // Should NOT have triggered 600s again!
    expect(onThreshold).toHaveBeenCalledTimes(1);
  });

  it("13. helper format duration formats correctly", () => {
    expect(formatDuration(0)).toBe("00:00");
    expect(formatDuration(65)).toBe("01:05");
    expect(formatDuration(3665)).toBe("1:01:05");
  });

  it("14. helper format overrun minutes handles singular/plural and compound", () => {
    expect(formatOverrunMinutes(45)).toBe("45 seconds");
    expect(formatOverrunMinutes(60)).toBe("1 minute");
    expect(formatOverrunMinutes(120)).toBe("2 minutes");
    expect(formatOverrunMinutes(222)).toBe("3m 42s");
  });
});
