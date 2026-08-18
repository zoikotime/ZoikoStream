import { Link } from "react-router-dom";
import { CONSOLE, cx, type } from "../../ui/tokens";
import Panel from "../admin/Panel";

// Developer-platform posture. Credential and webhook counts are real (they are stored on the
// organization); the error-rate, SDK-exposure, failure-streak and rate-limit figures the
// design shows have no producer in this stack, so they read "—" with the reason rather than
// a zero that would look like a clean bill of health.
export default function DeveloperOps({ ops }) {
  const d = ops || {};

  const rows = [
    { label: "Active credentials", value: d.credentials_active, tone: CONSOLE.heading },
    { label: "Webhook endpoints", value: d.webhooks_configured, tone: CONSOLE.heading },
    {
      label: "Applications with elevated error rate",
      value: d.apps_elevated_error_rate,
      tone: d.apps_elevated_error_rate ? "text-rose-600 dark:text-rose-400" : CONSOLE.heading,
    },
    {
      label: "Deprecated SDK exposure",
      value: d.deprecated_sdk_exposure,
      mono: true,
      tone: d.deprecated_sdk_exposure ? "text-amber-600 dark:text-amber-400" : CONSOLE.heading,
    },
    {
      label: "Webhook failure streaks",
      value: d.webhook_failure_streaks,
      tone: d.webhook_failure_streaks ? "text-rose-600 dark:text-rose-400" : CONSOLE.heading,
    },
    { label: "Rate-limit events (24h)", value: d.rate_limit_events_24h, tone: CONSOLE.heading },
  ];

  const unmeasured = rows.filter((r) => r.value == null).length;

  return (
    <Panel
      title="Developer operations"
      action={
        <Link to="/organization/developers" className={cx("text-[12px] font-semibold", CONSOLE.link)}>
          Developer Platform →
        </Link>
      }
      flush
    >
      <ul className={cx("divide-y", CONSOLE.divideY)}>
        {rows.map((r) => (
          <li
            key={r.label}
            className="flex items-center justify-between gap-4 px-4 py-2.5 transition-colors duration-150 hover:bg-slate-50 motion-reduce:transition-none sm:px-5 dark:hover:bg-white/[0.03]"
          >
            <p className={cx("min-w-0 text-[13px]", CONSOLE.body)}>{r.label}</p>
            <span
              className={cx(
                "shrink-0 text-[13px] font-semibold tabular-nums",
                r.mono && type.mono,
                r.value == null ? CONSOLE.faint : r.tone
              )}
              // The panel footnote says why some rows are dashed; this puts the reason on the
              // row itself, where the reader is actually looking.
              title={r.value == null ? "No source integrated for this figure yet" : undefined}
            >
              {r.value == null ? "—" : r.value}
            </span>
          </li>
        ))}
      </ul>
      {unmeasured > 0 && d.note && (
        <p className={cx("border-t px-4 py-2.5 text-[11px] leading-snug sm:px-5", CONSOLE.divider, CONSOLE.faint)}>
          {d.note}
        </p>
      )}
    </Panel>
  );
}
