import { Link } from "react-router-dom";
import { CONSOLE, cx } from "../../ui/tokens";
import Panel from "../admin/Panel";

// Things this organization has to act on. Every row is derived from a real record — an
// expiring credential, an entitlement threshold crossed, a blocked event, an unresolved
// governance obligation, a pending invitation — and carries the action that resolves it.
const DOT = {
  critical: "bg-rose-500",
  warning: "bg-amber-500",
  info: "bg-blue-500",
};

export default function AttentionRequired({ items = [] }) {
  return (
    <Panel
      title="Attention required"
      count={items.filter((i) => i.severity === "critical").length}
      action={
        <Link to="/organization/support" className={cx("text-[12px] font-semibold", CONSOLE.link)}>
          View all →
        </Link>
      }
      flush
    >
      {items.length === 0 ? (
        <div className="px-5 py-10 text-center">
          <p className={cx("text-[13px] font-medium", CONSOLE.body)}>Nothing needs attention</p>
          <p className={cx("mt-1 text-[12px]", CONSOLE.faint)}>
            Credentials, entitlements, events and invitations are all in good standing.
          </p>
        </div>
      ) : (
        <ul className={cx("divide-y", CONSOLE.divideY)}>
          {items.map((it) => (
            <li key={it.id} className="flex items-start gap-3 px-4 py-3 sm:px-5">
              <span
                className={cx("mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full", DOT[it.severity] || DOT.info)}
                aria-hidden="true"
              />
              <div className="min-w-0 flex-1">
                <p className={cx("text-[13px] font-semibold leading-snug", CONSOLE.heading)}>{it.title}</p>
                <p className={cx("mt-0.5 truncate text-[11px]", CONSOLE.faint)}>{it.detail}</p>
              </div>
              <Link
                to={it.to}
                className={cx("shrink-0 whitespace-nowrap text-[12px] font-semibold", CONSOLE.link)}
              >
                {it.action} →
              </Link>
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}
