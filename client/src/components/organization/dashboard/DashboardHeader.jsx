import { FiRefreshCw } from "react-icons/fi";
import { CONSOLE, cx } from "../../../ui/tokens";
import { ConsoleButton } from "../../../ui/Button";
import { greetingName } from "./dashboardConfig";

export default function DashboardHeader({ user, onRefresh, refreshing = false }) {
  const name = greetingName(user);
  return (
    <div className="flex flex-wrap items-start justify-between gap-x-6 gap-y-3">
      <div className="min-w-0">
        <h1 className={cx("text-[26px] font-bold leading-tight tracking-tight sm:text-[30px]", CONSOLE.heading)}>
          {name ? `Welcome back, ${name}` : "Welcome back"}{" "}
          {/* Decorative: the greeting already reads correctly without it, so it is hidden
              from assistive tech rather than announced as "waving hand sign". */}
          <span aria-hidden="true">👋</span>
        </h1>
        <p className={cx("mt-1.5 text-[14px]", CONSOLE.muted)}>
          Here&rsquo;s what&rsquo;s happening with your events today.
        </p>
      </div>

      <ConsoleButton
        variant="secondary"
        size="md"
        onClick={onRefresh}
        loading={refreshing}
        leftIcon={FiRefreshCw}
      >
        Refresh
      </ConsoleButton>
    </div>
  );
}
