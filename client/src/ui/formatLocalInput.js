// Display formatting for a datetime-local value, kept out of DateTimeField.jsx because a
// component file may export only components (react-refresh/only-export-components) — Fast
// Refresh cannot tell a changed helper from a changed component and gives up on the module.
//
// Fixed en-US rather than the viewer's locale, because the point is a predictable
// "MMM DD, YYYY" on every machine — a locale-driven format is what produced the mismatch this
// replaces. dateStyle/timeStyle are the same pair Dashboard's fmtInstant uses, so the field
// and the window line below it read identically.
const DISPLAY = new Intl.DateTimeFormat("en-US", { dateStyle: "medium", timeStyle: "short" });

// The shape is checked before parsing, because Date() is lenient in a way that is actively
// misleading here: new Date("2026-10-") is Oct 1, not an error, so a half-typed value would
// be painted over with a confident, wrong date.
const LOCAL_INPUT = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2})?$/;

// "2026-10-18T10:05" -> "Oct 18, 2026, 10:05 AM". Empty for anything incomplete (half-typed,
// cleared), which is the signal to leave the native text showing rather than cover the field
// with nothing — or worse, with a guess.
export function formatLocalInput(value) {
  if (!value || !LOCAL_INPUT.test(value)) return "";
  // No Z and no offset, so this parses as the operator's own wall clock — the same reading
  // commandCenterParams relies on. Display never converts, so it cannot shift a day.
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? "" : DISPLAY.format(d);
}

