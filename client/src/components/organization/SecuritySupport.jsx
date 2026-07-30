import { Link } from "react-router-dom";
import { CONSOLE, cx } from "../../ui/tokens";
import Panel from "../admin/Panel";

// Security, access, support and maintenance posture in four columns.
//
// Each value is either a real record (SSO/2FA flags on the organization, pending invitations,
// open support tickets) or an explicit "—" for something this stack has no source for
// (findings scanner, access-review schedule, maintenance calendar). A zero would read as
// "we checked and found nothing", which is a different and untrue claim.
function Row({ label, value, tone, title }) {
  return (
    <div className="flex items-baseline justify-between gap-3 py-1">
      <p className={cx("min-w-0 text-[12px]", CONSOLE.body)}>{label}</p>
      <span
        className={cx("shrink-0 text-[12px] font-semibold tabular-nums", tone || CONSOLE.heading)}
        title={title}
      >
        {value}
      </span>
    </div>
  );
}

const GOOD = "text-green-600 dark:text-green-400";
const WARN = "text-amber-600 dark:text-amber-400";
const BAD = "text-rose-600 dark:text-rose-400";

export default function SecuritySupport({ posture }) {
  const s = posture || {};
  const dash = (v) => (v == null ? "—" : v);

  return (
    <Panel
      title="Security & support"
      action={
        <Link to="/organization/settings" className={cx("text-[12px] font-semibold", CONSOLE.link)}>
          Security & Governance →
        </Link>
      }
    >
      <div className="grid gap-x-8 gap-y-5 sm:grid-cols-2 lg:grid-cols-4">
        <div>
          <p className={cx("mb-1.5 text-[10px] font-semibold uppercase tracking-[0.1em]", CONSOLE.faint)}>
            Security posture
          </p>
          <Row
            label="Open findings"
            value={dash(s.open_findings)}
            tone={s.open_findings ? BAD : CONSOLE.faint}
            title={s.open_findings == null ? "Needs a security findings scanner (not integrated)" : undefined}
          />
          <Row
            label="SSO enforced"
            value={s.sso_enforced ? "Enabled" : "Off"}
            tone={s.sso_enforced ? GOOD : WARN}
          />
          <Row
            label="2FA required"
            value={s.two_factor_required ? "Enabled" : "Off"}
            tone={s.two_factor_required ? GOOD : WARN}
          />
        </div>

        <div>
          <p className={cx("mb-1.5 text-[10px] font-semibold uppercase tracking-[0.1em]", CONSOLE.faint)}>
            Access
          </p>
          <Row
            label="Next review due"
            value={s.next_review_days == null ? "—" : `${s.next_review_days} days`}
            tone={CONSOLE.faint}
            title={s.next_review_days == null ? "Needs an access-review schedule (not integrated)" : undefined}
          />
          <Row
            label="Members pending"
            value={dash(s.pending_members)}
            tone={s.pending_members ? WARN : CONSOLE.heading}
          />
          <Row
            label="Domain verified"
            value={s.domain_verified ? "Verified" : "Unverified"}
            tone={s.domain_verified ? GOOD : WARN}
          />
        </div>

        <div>
          <p className={cx("mb-1.5 text-[10px] font-semibold uppercase tracking-[0.1em]", CONSOLE.faint)}>
            Support
          </p>
          <Row
            label="Open cases"
            value={dash(s.open_cases)}
            tone={s.urgent_cases ? BAD : s.open_cases ? WARN : CONSOLE.heading}
          />
          <Row label="Urgent cases" value={dash(s.urgent_cases)} tone={s.urgent_cases ? BAD : CONSOLE.heading} />
          <Row
            label="Active session"
            value={s.active_support_session || "None"}
            tone={CONSOLE.faint}
          />
        </div>

        <div>
          <p className={cx("mb-1.5 text-[10px] font-semibold uppercase tracking-[0.1em]", CONSOLE.faint)}>
            Maintenance
          </p>
          <Row
            label="Upcoming window"
            value={s.maintenance_window || "—"}
            tone={CONSOLE.faint}
            title={s.maintenance_window == null ? "Needs a maintenance calendar (not integrated)" : undefined}
          />
          <Row
            label="Allowed domains"
            value={s.allowed_domains || "Any"}
            tone={CONSOLE.faint}
          />
        </div>
      </div>
    </Panel>
  );
}
