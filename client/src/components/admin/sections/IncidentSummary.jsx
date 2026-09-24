import { Link } from "react-router-dom";
import { FiAlertTriangle, FiCheck } from "react-icons/fi";
import { CONSOLE, cx, type } from "../../../ui/tokens";
import Panel from "../Panel";
import { timeAgo } from "../format";

// "Is there a current incident?" — one of the five questions the Command Center exists to
// answer, and the whole of what it should say about incidents.
//
// This REPLACES the full incidents feed that used to live here. That feed listed every
// incident in the window, resolved ones included, with severity, stage, region, ref and
// commander — a second copy of what System Status and Trust & Safety already own, and a
// large empty panel reading "No incidents in this window" on the overwhelmingly common day
// when there are none. A dashboard should tell you there is nothing to do in one line, not
// in a card the size of the thing it is reporting the absence of.
//
// So: the count, the worst severity, the most recent one by name, and a link. Anyone who
// needs the list is one click from it.
const SEV_WORD = { sev1: "Critical", sev2: "Major", sev3: "Minor", sev4: "Low" };
const SEV_RANK = { sev1: 4, sev2: 3, sev3: 2, sev4: 1 };
const SEV_TONE = {
  sev1: "text-rose-600 dark:text-rose-400",
  sev2: "text-rose-600 dark:text-rose-400",
  sev3: "text-amber-600 dark:text-amber-400",
  sev4: "text-slate-500 dark:text-neutral-400",
};

export default function IncidentSummary({ incidents = [] }) {
  const active = incidents.filter((i) => i.status !== "resolved");
  const worst = active.reduce(
    (acc, i) => ((SEV_RANK[i.severity] || 0) > (SEV_RANK[acc?.severity] || 0) ? i : acc),
    null
  );

  return (
    <Panel
      title="Active incidents"
      count={active.length}
      action={
        <Link to="/admin/status" className={cx("text-[12px] font-semibold", CONSOLE.link)}>
          System Status →
        </Link>
      }
    >
      {active.length === 0 ? (
        // Compact, single line. The healthy state is the common one and must not cost a
        // screenful — but it still says what it actually checked, and says "recorded"
        // rather than implying the platform was probed and found perfect.
        <p className={cx("flex items-center gap-2 text-[13px]", CONSOLE.body)}>
          <FiCheck className="shrink-0 text-green-600 dark:text-green-400" aria-hidden="true" />
          No active incidents recorded.
          {incidents.length > 0 && (
            <span className={CONSOLE.faint}>
              {incidents.length} resolved in this window.
            </span>
          )}
        </p>
      ) : (
        <div className="flex items-start gap-3">
          <span className="mt-0.5 grid h-9 w-9 shrink-0 place-items-center rounded-lg bg-rose-100 text-rose-600 dark:bg-rose-500/15 dark:text-rose-400">
            <FiAlertTriangle aria-hidden="true" />
          </span>
          <div className="min-w-0">
            <p className={cx("text-[15px] font-semibold", SEV_TONE[worst?.severity] || CONSOLE.heading)}>
              {active.length} active ·{" "}
              <span className={type.mono}>{SEV_WORD[worst?.severity] || worst?.severity}</span>
            </p>
            <p className={cx("mt-0.5 truncate text-[12px]", CONSOLE.body)} title={worst?.title}>
              {worst?.title}
            </p>
            <p className={cx("mt-0.5 text-[11px]", CONSOLE.faint)}>
              started {timeAgo(worst?.started_at)}
              {active.length > 1 ? ` · ${active.length - 1} more` : ""}
            </p>
          </div>
        </div>
      )}
    </Panel>
  );
}
