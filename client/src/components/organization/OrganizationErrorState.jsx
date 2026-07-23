import { FiAlertTriangle, FiRefreshCw } from "react-icons/fi";
import Button from "../admin/Button";
import { errMsg } from "../../api";
import { cx, panelSurface } from "../../ui/tokens";

// Standard error surface for org pages: icon + message + retry. Replaces the
// silent console.error the pages used to swallow API failures with.
export default function OrganizationErrorState({
  error,
  onRetry,
  title = "Something went wrong",
  className = "",
}) {
  return (
    <div
      className={cx(
        panelSurface,
        "flex flex-col items-start gap-3 p-5 sm:flex-row sm:items-center sm:justify-between",
        className
      )}
    >
      <div className="flex items-center gap-3">
        <span className="grid h-9 w-9 shrink-0 place-items-center rounded-lg bg-rose-100 text-rose-600 dark:bg-rose-500/15 dark:text-rose-400">
          <FiAlertTriangle />
        </span>
        <div>
          <p className="text-sm font-semibold text-slate-900 dark:text-white">{title}</p>
          <p className="text-xs text-slate-500 dark:text-slate-400">{errMsg(error)}</p>
        </div>
      </div>
      {onRetry && (
        <Button variant="secondary" size="sm" leftIcon={FiRefreshCw} onClick={onRetry}>
          Retry
        </Button>
      )}
    </div>
  );
}
