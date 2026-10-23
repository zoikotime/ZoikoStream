// Event-local and UTC time for the Event Workspace. A live operation is read against two
// clocks at once — the event's own zone and UTC (ZST-UX-NAV-001 §9.2) — so every timestamp
// that matters is shown in both, never in the browser's zone alone.

// An IANA zone the browser can use, or undefined (the browser's own). A malformed stored zone
// must degrade the label, never throw a RangeError into a render.
export function safeZone(zone) {
  if (!zone) return undefined;
  try {
    new Intl.DateTimeFormat("en-US", { timeZone: zone });
    return zone;
  } catch {
    return undefined;
  }
}

const valid = (iso) => {
  if (!iso) return null;
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? null : d;
};

// "Sat, Oct 24, 2026 · 6:00 PM BST"
export function fmtZoned(iso, zone) {
  const d = valid(iso);
  if (!d) return "—";
  const timeZone = safeZone(zone);
  const date = d.toLocaleDateString("en-US", { weekday: "short", month: "short", day: "numeric", year: "numeric", timeZone });
  const time = d.toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit", timeZoneName: "short", timeZone });
  return `${date} · ${time}`;
}

// "Oct 24 · 17:00 UTC"
export function fmtUtc(iso) {
  const d = valid(iso);
  if (!d) return "—";
  const date = d.toLocaleDateString("en-US", { month: "short", day: "numeric", timeZone: "UTC" });
  const time = d.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit", timeZone: "UTC" });
  return `${date} · ${time} UTC`;
}

// "Oct 24 · 6:00 PM" in the event's zone, compact, for ledger rows.
export function fmtZonedShort(iso, zone) {
  const d = valid(iso);
  if (!d) return "—";
  const timeZone = safeZone(zone);
  const date = d.toLocaleDateString("en-US", { month: "short", day: "numeric", timeZone });
  const time = d.toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit", timeZone });
  return `${date} · ${time}`;
}

// Wall-clock date and time of an instant in a zone, as the form fields expect them.
export function zonedParts(iso, zone) {
  const d = valid(iso);
  if (!d) return { date: "", time: "" };
  const timeZone = safeZone(zone) || "UTC";
  const parts = Object.fromEntries(
    new Intl.DateTimeFormat("en-CA", {
      timeZone, year: "numeric", month: "2-digit", day: "2-digit",
      hour: "2-digit", minute: "2-digit", hourCycle: "h23",
    }).formatToParts(d).map((p) => [p.type, p.value])
  );
  return { date: `${parts.year}-${parts.month}-${parts.day}`, time: `${parts.hour}:${parts.minute}` };
}

// "just now", "40s ago", "3 min ago", "2 h ago" — how old a reading is.
export function ageOf(iso, now = Date.now()) {
  const d = valid(iso);
  if (!d) return null;
  const s = Math.max(0, Math.round((now - d.getTime()) / 1000));
  if (s < 5) return "just now";
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  return `${Math.round(s / 86400)} d ago`;
}

export function secondsSince(iso, now = Date.now()) {
  const d = valid(iso);
  return d ? Math.max(0, (now - d.getTime()) / 1000) : Infinity;
}

// "1h 05m", "12m", "45s"
export function fmtSeconds(total) {
  if (total == null) return "—";
  const s = Math.max(0, Math.round(total));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  if (h) return `${h}h ${String(m).padStart(2, "0")}m`;
  if (m) return `${m}m`;
  return `${s % 60}s`;
}

// The three-line date block of the workspace header: "Sat 24 Oct 2026" / "18:00
// Europe/London (BST)" / "17:00 UTC". 24-hour, as operations schedules are read.
export function dateBlock(iso, zone) {
  const d = valid(iso);
  if (!d) return null;
  const timeZone = safeZone(zone);
  const date = d.toLocaleDateString("en-GB", { weekday: "short", day: "numeric", month: "short", year: "numeric", timeZone });
  const time = d.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit", timeZone });
  const abbr = d.toLocaleTimeString("en-GB", { timeZoneName: "short", timeZone }).split(" ").pop();
  const utc = d.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit", timeZone: "UTC" });
  return {
    date: date.replace(",", ""),
    local: `${time} ${timeZone || "local time"}${abbr && abbr !== "UTC" ? ` (${abbr})` : ""}`,
    utc: `${utc} UTC`,
  };
}
