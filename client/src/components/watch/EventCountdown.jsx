// client/src/components/watch/EventCountdown.jsx
// "Event ends in HH:MM:SS" stat card for the Viewer Portal. Renders nothing if the event
// carries no end_time (GET /events/{id}/watch's `end_time`, added alongside `category`
// and `raise_hand_enabled`) or if that time has already passed — same "don't render what
// has no data" convention EventInfo.jsx's Agenda/Resources tabs already follow.
import { useState } from "react";
import { FiClock } from "react-icons/fi";
import useInterval from "../../hooks/useInterval";
import Card from "../../ui/Card";

const pad = (n) => String(n).padStart(2, "0");

function splitRemaining(ms) {
  const total = Math.max(0, Math.floor(ms / 1000));
  return {
    hrs: pad(Math.floor(total / 3600)),
    mins: pad(Math.floor((total % 3600) / 60)),
    secs: pad(total % 60),
  };
}

function Segment({ value, label }) {
  return (
    <div className="text-center">
      <p className="zk-tnum text-2xl font-bold text-slate-900 dark:text-white">{value}</p>
      <p className="text-[10px] font-semibold uppercase tracking-wider text-slate-400">{label}</p>
    </div>
  );
}

export default function EventCountdown({ endISO, className = "" }) {
  const endMs = endISO ? new Date(endISO).getTime() : null;
  const [now, setNow] = useState(() => Date.now());
  useInterval(() => setNow(Date.now()), 1000, Boolean(endMs));

  if (!endMs || endMs <= now) return null;

  const { hrs, mins, secs } = splitRemaining(endMs - now);

  return (
    <Card className={className}>
      <div className="flex items-center gap-2 text-sm font-medium text-slate-500 dark:text-slate-400">
        <FiClock aria-hidden /> Event ends in
      </div>
      <div className="mt-3 flex items-center justify-center gap-4">
        <Segment value={hrs} label="Hrs" />
        <span className="text-xl font-bold text-slate-300 dark:text-slate-700">:</span>
        <Segment value={mins} label="Mins" />
        <span className="text-xl font-bold text-slate-300 dark:text-slate-700">:</span>
        <Segment value={secs} label="Secs" />
      </div>
    </Card>
  );
}
