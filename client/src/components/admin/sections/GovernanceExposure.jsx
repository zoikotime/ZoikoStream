import { Link } from "react-router-dom";
import { CONSOLE, cx, type } from "../../../ui/tokens";
import Panel from "../Panel";

// Standing governance and commercial obligations. Each row is a real aggregate over
// governance_records — nothing here is a status light, they are counts of things a human
// has to close. A non-zero count is tinted by how much it matters, so the panel is readable
// at a glance without reading the numbers.
export default function GovernanceExposure({ governance }) {
  const g = governance || {};

  const rows = [
    {
      label: "Usage export delivery",
      value: g.usage_export_on_time_pct == null ? "—" : `${g.usage_export_on_time_pct}% on-time`,
      tone: g.usage_export_on_time_pct == null
        ? CONSOLE.faint
        : g.usage_export_on_time_pct >= 99
        ? "text-green-600 dark:text-green-400"
        : "text-amber-600 dark:text-amber-400",
      hint: g.usage_export_total ? `${g.usage_export_total} exports` : "No exports recorded",
    },
    { label: "Active legal holds", value: g.legal_holds ?? 0, warnAt: 1 },
    { label: "Entitlement overrides pending approval", value: g.entitlement_overrides_pending ?? 0, warnAt: 1 },
    { label: "Break-glass grants under 72h review", value: g.break_glass_under_review ?? 0, warnAt: 1 },
    { label: "Single-path overrides this quarter", value: g.single_path_overrides_quarter ?? 0, dangerAt: 1 },
  ];

  const toneFor = (row) => {
    if (row.tone) return row.tone;
    if (row.dangerAt != null && row.value >= row.dangerAt) return "text-rose-600 dark:text-rose-400";
    if (row.warnAt != null && row.value >= row.warnAt) return "text-amber-600 dark:text-amber-400";
    return CONSOLE.heading;
  };

  return (
    <Panel
      title="Governance & commercial exposure"
      action={
        <Link to="/admin/governance" className={cx("text-[12px] font-semibold", CONSOLE.link)}>
          Governance →
        </Link>
      }
      flush
    >
      <ul className={cx("divide-y", CONSOLE.divideY)}>
        {rows.map((row) => (
          <li key={row.label} className="flex items-center justify-between gap-4 px-4 py-3 sm:px-5">
            <div className="min-w-0">
              <p className={cx("text-[13px]", CONSOLE.body)}>{row.label}</p>
              {row.hint && <p className={cx("mt-0.5 text-[11px]", CONSOLE.faint)}>{row.hint}</p>}
            </div>
            <span className={cx("shrink-0 text-[14px] font-semibold tabular-nums", type.num, toneFor(row))}>
              {row.value}
            </span>
          </li>
        ))}
      </ul>
    </Panel>
  );
}
