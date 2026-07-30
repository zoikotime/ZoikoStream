import { Link } from "react-router-dom";
import { CONSOLE, cx, type } from "../../../ui/tokens";
import Panel from "../Panel";
import { initials } from "../format";

// Recent privileged actions, straight from the append-only audit log. The actor's operating
// team is joined from their user row where the account still exists; the log itself keeps
// only the email, by design, so an entry outlives the account that made it.
//
// Times are absolute (HH:mm), not relative — "14:31" is what gets compared against an
// incident timeline, "18 minutes ago" is not.
const clock = (iso) => {
  if (!iso) return "—";
  const d = new Date(iso);
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });
};

// "organization.suspend" -> "Suspended organization". The audit log stores machine actions;
// the console reads better in human ones, and the raw action stays in the title attribute.
const VERBS = {
  create: "Created", update: "Updated", delete: "Deleted", suspend: "Suspended",
  revoke: "Revoked", start: "Started", end: "Ended",
};

function phrase(action, target) {
  const [subject, verb] = String(action).split(".");
  const noun = subject?.replace(/_/g, " ");
  const label = VERBS[verb] || (verb ? verb.charAt(0).toUpperCase() + verb.slice(1) : "Acted on");
  return `${label} ${noun}${target ? ` — ${target}` : ""}`;
}

export default function PrivilegedActivity({ activity = [] }) {
  return (
    <Panel
      title="Recent privileged activity"
      action={
        <Link to="/admin/audit" className={cx("text-[12px] font-semibold", CONSOLE.link)}>
          Audit →
        </Link>
      }
      flush
    >
      {activity.length === 0 ? (
        <div className="px-5 py-10 text-center">
          <p className={cx("text-[13px] font-medium", CONSOLE.body)}>No privileged actions recorded</p>
          <p className={cx("mt-1 text-[12px]", CONSOLE.faint)}>
            Every platform mutation is written to the audit log as it happens.
          </p>
        </div>
      ) : (
        <ul className={cx("divide-y", CONSOLE.divideY)}>
          {activity.map((a) => (
            <li key={a.id} className="flex items-start gap-3 px-4 py-3 sm:px-5">
              <span
                className={cx(
                  "mt-0.5 grid h-7 w-7 shrink-0 place-items-center rounded-full text-[10px] font-bold",
                  "bg-violet-100 text-violet-700 dark:bg-violet-500/20 dark:text-violet-300"
                )}
              >
                {initials(a.actor) || "SY"}
              </span>
              <div className="min-w-0 flex-1">
                <div className="flex items-baseline justify-between gap-3">
                  <p className="min-w-0 truncate">
                    <span className={cx("text-[13px] font-semibold", CONSOLE.heading)}>{a.actor}</span>
                    {a.department && (
                      <span className={cx("ml-1.5 text-[11px]", CONSOLE.faint)}>· {a.department}</span>
                    )}
                  </p>
                  <span className={cx("shrink-0 text-[11px]", type.mono, CONSOLE.faint)}>{clock(a.at)}</span>
                </div>
                <p className={cx("mt-0.5 truncate text-[12px]", CONSOLE.body)} title={a.action}>
                  {phrase(a.action, a.target)}
                </p>
              </div>
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}
