// client/src/hooks/useEventOverrun.js
// Hook for tracking live event scheduled duration, overrun time, and pre-end reminders.
import { useCallback, useEffect, useRef, useState } from "react";
import useInterval from "./useInterval";
import { notify } from "../ui/Toast";
import { playAlertChime } from "../utils/sound";

export const REMINDER_THRESHOLDS = [
  { seconds: 600, message: "10 minutes remaining" },
  { seconds: 300, message: "5 minutes remaining" },
  { seconds: 60, message: "1 minute remaining" },
  { seconds: 0, message: "Scheduled event time has ended" },
];

export const formatDuration = (totalSeconds) => {
  const s = Math.max(0, Math.floor(totalSeconds));
  const hrs = Math.floor(s / 3600);
  const mins = Math.floor((s % 3600) / 60);
  const secs = s % 60;
  const pad = (n) => String(n).padStart(2, "0");
  if (hrs > 0) {
    return `${hrs}:${pad(mins)}:${pad(secs)}`;
  }
  return `${pad(mins)}:${pad(secs)}`;
};

export const formatOverrunMinutes = (overrunSeconds) => {
  const s = Math.max(0, Math.floor(overrunSeconds));
  const mins = Math.floor(s / 60);
  const secs = s % 60;
  if (mins > 0 && secs > 0) {
    return `${mins}m ${secs}s`;
  }
  if (mins > 0) {
    return `${mins} minute${mins === 1 ? "" : "s"}`;
  }
  return `${secs} second${secs === 1 ? "" : "s"}`;
};

export function computeOverrunState(scheduledEnd, now = Date.now()) {
  if (!scheduledEnd) {
    return {
      scheduledEnd: null,
      state: "no_schedule",
      remainingSeconds: null,
      overrunSeconds: null,
      formattedRemaining: null,
      formattedOverrun: null,
      overrunMinutesText: null,
      isOvertime: false,
      isEndingSoon: false,
    };
  }

  const endMs = new Date(scheduledEnd).getTime();
  if (isNaN(endMs)) {
    return {
      scheduledEnd: null,
      state: "no_schedule",
      remainingSeconds: null,
      overrunSeconds: null,
      formattedRemaining: null,
      formattedOverrun: null,
      overrunMinutesText: null,
      isOvertime: false,
      isEndingSoon: false,
    };
  }

  const diffSeconds = Math.floor((endMs - now) / 1000);

  if (diffSeconds > 600) {
    return {
      scheduledEnd,
      state: "on_time",
      remainingSeconds: diffSeconds,
      overrunSeconds: 0,
      formattedRemaining: formatDuration(diffSeconds),
      formattedOverrun: "+00:00",
      overrunMinutesText: "0 minutes",
      isOvertime: false,
      isEndingSoon: false,
    };
  }

  if (diffSeconds > 0) {
    return {
      scheduledEnd,
      state: "ending_soon",
      remainingSeconds: diffSeconds,
      overrunSeconds: 0,
      formattedRemaining: formatDuration(diffSeconds),
      formattedOverrun: "+00:00",
      overrunMinutesText: "0 minutes",
      isOvertime: false,
      isEndingSoon: true,
    };
  }

  if (diffSeconds === 0) {
    return {
      scheduledEnd,
      state: "ended_schedule",
      remainingSeconds: 0,
      overrunSeconds: 0,
      formattedRemaining: "00:00",
      formattedOverrun: "+00:00",
      overrunMinutesText: "0 minutes",
      isOvertime: true,
      isEndingSoon: false,
    };
  }

  const overrun = Math.abs(diffSeconds);
  return {
    scheduledEnd,
    state: "overtime",
    remainingSeconds: 0,
    overrunSeconds: overrun,
    formattedRemaining: "00:00",
    formattedOverrun: `+${formatDuration(overrun)}`,
    overrunMinutesText: formatOverrunMinutes(overrun),
    isOvertime: true,
    isEndingSoon: false,
  };
}

export default function useEventOverrun({
  scheduledEnd,
  isLive = false,
  onThresholdReached,
} = {}) {
  const triggeredRef = useRef(new Set());
  const prevEndRef = useRef(scheduledEnd);
  const isInitialMount = useRef(true);

  const calculate = useCallback(
    () => computeOverrunState(scheduledEnd),
    [scheduledEnd]
  );

  const [overrunInfo, setOverrunInfo] = useState(calculate);

  // If scheduledEnd changes (reschedule / extend), reset thresholds that are back in the future
  useEffect(() => {
    if (scheduledEnd !== prevEndRef.current) {
      prevEndRef.current = scheduledEnd;
      if (scheduledEnd) {
        const endMs = new Date(scheduledEnd).getTime();
        const diff = Math.floor((endMs - Date.now()) / 1000);
        // Clear any triggered thresholds that are now in the future
        for (const t of REMINDER_THRESHOLDS) {
          if (diff > t.seconds) {
            triggeredRef.current.delete(t.seconds);
          }
        }
      } else {
        triggeredRef.current.clear();
      }
      setOverrunInfo(computeOverrunState(scheduledEnd));
    }
  }, [scheduledEnd]);

  // On first mount, suppress reminders for thresholds that passed long ago (more than 15s ago)
  useEffect(() => {
    if (isInitialMount.current) {
      isInitialMount.current = false;
      if (scheduledEnd) {
        const endMs = new Date(scheduledEnd).getTime();
        const diff = Math.floor((endMs - Date.now()) / 1000);
        for (const t of REMINDER_THRESHOLDS) {
          if (diff < t.seconds - 15) {
            // Already passed long before opening the console
            triggeredRef.current.add(t.seconds);
          }
        }
      }
    }
  }, [scheduledEnd]);

  const tick = useCallback(() => {
    const next = computeOverrunState(scheduledEnd);
    setOverrunInfo(next);

    // Only fire pre-end reminders if broadcast is live
    if (!isLive || !scheduledEnd) return;

    const endMs = new Date(scheduledEnd).getTime();
    const diff = Math.floor((endMs - Date.now()) / 1000);

    for (const t of REMINDER_THRESHOLDS) {
      if (diff <= t.seconds && !triggeredRef.current.has(t.seconds)) {
        triggeredRef.current.add(t.seconds);
        if (onThresholdReached) {
          onThresholdReached(t);
        } else {
          notify.warning(t.message);
          try {
            playAlertChime();
          } catch {
            // Audio autoplay policy fallback
          }
        }
      }
    }
  }, [scheduledEnd, isLive, onThresholdReached]);

  // Tick every 1 second while scheduledEnd is present and not ended
  useInterval(tick, 1000, !!scheduledEnd);

  return overrunInfo;
}
