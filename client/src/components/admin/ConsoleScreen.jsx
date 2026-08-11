import { CONSOLE, cx, type } from "../../ui/tokens";
import Skeleton from "../../ui/Skeleton";
import { ConsoleButton } from "../../ui/Button";
import Badge from "../../ui/Badge";
import { errMsg } from "../../api";

// Shell for a Command Center section page: title block, freshness receipt, error diagnosis,
// and a skeleton while the first payload lands.
//
// Extracted from pages/admin/Dashboard, which had all four inline. The five section pages
// added alongside it would each have needed the same 60 lines — including the error
// DIAGNOSIS, which is the part worth sharing: "the API didn't answer" sends someone hunting
// the network when a 404 means the server predates the endpoint and a 401 means the session
// expired. Different fixes, so the page names which one it is.
const SLO_SECONDS = 600;

function diagnose(error, endpoint) {
  const status = error?.response?.status;
  if (status === 404)
    return `The API responded, but doesn’t have ${endpoint} — the server is running an older build. Restart it to pick up the current code.`;
  if (status === 401 || status === 403)
    return "Your session isn’t authorised for the platform console. Sign in again as a super admin.";
  if (status) return `The platform API returned ${status}: ${errMsg(error)}`;
  return "The platform API is unreachable — check that the API server is running and that VITE_API_URL points at it.";
}

export default function ConsoleScreen({
  title,
  subtitle,
  actions,
  // This section runs entirely on fixture data (see the page's own *Data.js import) —
  // nothing here is read from or written to the platform database yet.
  demoData = false,
  // Freshness: seconds since the payload arrived. Omit to hide the receipt.
  ageSeconds,
  loading = false,
  error,
  hasData = false,
  endpoint = "this endpoint",
  onRetry,
  skeleton,
  children,
}) {
  const withinSlo = ageSeconds == null || ageSeconds <= SLO_SECONDS;
  const ageLabel = ageSeconds < 1 ? "just now" : `${ageSeconds} sec ago`;
  const freshTone = withinSlo
    ? "text-green-600 dark:text-green-400"
    : "text-amber-600 dark:text-amber-400";

  return (
    <div className="mx-auto max-w-[1500px] space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2.5">
            <h1
              className={cx(
                "text-[24px] font-bold leading-tight tracking-tight sm:text-[28px]",
                CONSOLE.heading
              )}
            >
              {title}
            </h1>
            {demoData && (
              <Badge tone="warning" dot title="This section runs on fixture data — nothing here reads from or writes to the platform database yet">
                Preview · demo data
              </Badge>
            )}
          </div>
          {subtitle && <p className={cx("mt-1 max-w-3xl text-[13px]", CONSOLE.muted)}>{subtitle}</p>}
          {ageSeconds != null && hasData && (
            <p className={cx("mt-2 flex items-center gap-1.5 text-[12px]", type.mono)}>
              <span className={freshTone}>Refreshed {ageLabel}</span>
              <span className={CONSOLE.faint}>·</span>
              <span className={freshTone}>{withinSlo ? "within SLO" : "stale — refresh"}</span>
            </p>
          )}
        </div>
        {actions && <div className="flex shrink-0 flex-wrap items-center gap-2">{actions}</div>}
      </div>

      {error && !hasData ? (
        <div className="rounded-xl border border-rose-200 bg-rose-50 px-5 py-4 dark:border-rose-500/25 dark:bg-rose-500/10">
          <p className="text-[13px] font-semibold text-rose-700 dark:text-rose-300">
            Couldn’t load {title}
          </p>
          <p className="mt-1 text-[12px] text-rose-600 dark:text-rose-400">{diagnose(error, endpoint)}</p>
          <p className="mt-1 text-[12px] text-rose-600/80 dark:text-rose-400/80">
            Nothing on this page is safe to read until it loads.
          </p>
          {onRetry && (
            <ConsoleButton variant="secondary" size="sm" className="mt-3" onClick={onRetry}>
              Try again
            </ConsoleButton>
          )}
        </div>
      ) : loading && !hasData ? (
        skeleton || (
          <div className="space-y-4">
            <Skeleton variant="block" className="h-32" />
            <div className="grid gap-4 xl:grid-cols-2">
              <Skeleton variant="block" className="h-72" />
              <Skeleton variant="block" className="h-72" />
            </div>
          </div>
        )
      ) : (
        children
      )}
    </div>
  );
}
