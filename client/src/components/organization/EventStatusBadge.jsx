import Badge from "../../ui/Badge";
import { statusMeta } from "../../data/events";

// ONE status pill for every event surface (table, detail header, duplicate dialog, bulk
// bar). Wraps the design-system Badge with the event status vocabulary so the colour of
// "live" can never drift between two screens.
//
// Accessibility: colour is never the only signal — the label is always rendered, and the
// pulse dot is aria-hidden inside Badge. `size="sm"` matches the table's row density.
export default function EventStatusBadge({ status, size = "md", className = "" }) {
  const m = statusMeta(status);
  return (
    <Badge tone={m.tone} dot={m.pulse} size={size} className={className}>
      {m.label}
    </Badge>
  );
}
