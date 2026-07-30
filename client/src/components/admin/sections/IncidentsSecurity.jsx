import { Link } from "react-router-dom";
import { FiAlertTriangle, FiCheck, FiShield } from "react-icons/fi";
import { CONSOLE, cx } from "../../../ui/tokens";
import Panel from "../Panel";
import { timeAgo } from "../format";

// Operational and security incidents in one feed, newest first — an operator does not
// think of them as separate lists during an event. The icon carries the kind, the tint
// carries how bad it is, and resolved entries stay visible for the selected window so the
// page shows what just happened, not only what is still burning.
const KIND_ICON = { security: FiShield, governance: FiShield, operational: FiAlertTriangle };

const TONE = {
  sev1: "border-rose-200 bg-rose-50 text-rose-600 dark:border-rose-500/25 dark:bg-rose-500/10 dark:text-rose-400",
  sev2: "border-rose-200 bg-rose-50 text-rose-600 dark:border-rose-500/25 dark:bg-rose-500/10 dark:text-rose-400",
  sev3: "border-amber-200 bg-amber-50 text-amber-600 dark:border-amber-500/25 dark:bg-amber-500/10 dark:text-amber-400",
  sev4: "border-slate-200 bg-slate-50 text-slate-500 dark:border-white/10 dark:bg-white/[0.04] dark:text-neutral-400",
};
const RESOLVED =
  "border-green-200 bg-green-50 text-green-600 dark:border-green-500/25 dark:bg-green-500/10 dark:text-green-400";

const SEV_WORD = { sev1: "Critical", sev2: "Major", sev3: "Minor", sev4: "Low" };

export default function IncidentsSecurity({ incidents = [] }) {
  return (
    <Panel
      title="Incidents & security"
      count={incidents.filter((i) => i.status !== "resolved").length}
      action={
        <Link to="/admin/status" className={cx("text-[12px] font-semibold", CONSOLE.link)}>
          System Status →
        </Link>
      }
      flush
    >
      {incidents.length === 0 ? (
        <div className="px-5 py-10 text-center">
          <p className={cx("text-[13px] font-medium", CONSOLE.body)}>No incidents in this window</p>
          <p className={cx("mt-1 text-[12px]", CONSOLE.faint)}>
            Nothing operational, security or governance related has been recorded.
          </p>
        </div>
      ) : (
        <ul className={cx("divide-y", CONSOLE.divideY)}>
          {incidents.map((i) => {
            const resolved = i.status === "resolved";
            const Icon = resolved ? FiCheck : KIND_ICON[i.kind] || FiAlertTriangle;
            return (
              <li key={i.id} className="flex items-start gap-3 px-4 py-3 sm:px-5">
                <span
                  className={cx(
                    "mt-0.5 grid h-8 w-8 shrink-0 place-items-center rounded-lg border",
                    resolved ? RESOLVED : TONE[i.severity] || TONE.sev4
                  )}
                >
                  <Icon className="text-[15px]" aria-hidden="true" />
                </span>
                <div className="min-w-0 flex-1">
                  <div className="flex items-start justify-between gap-3">
                    <p className={cx("text-[13px] font-semibold leading-snug", CONSOLE.heading)}>{i.title}</p>
                    <span className={cx("shrink-0 text-[11px]", CONSOLE.faint)}>{timeAgo(i.started_at)}</span>
                  </div>
                  <p className={cx("mt-0.5 truncate text-[11px]", CONSOLE.faint)}>
                    {[
                      SEV_WORD[i.severity] || i.severity,
                      i.stage ? `${cap(i.stage)} stage` : null,
                      i.region ? i.region.toUpperCase() : null,
                      i.ref,
                      i.commander ? `commander ${i.commander}` : null,
                      resolved ? "resolved" : i.status,
                    ]
                      .filter(Boolean)
                      .join(" · ")}
                  </p>
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </Panel>
  );
}

const cap = (s = "") => s.charAt(0).toUpperCase() + s.slice(1);
