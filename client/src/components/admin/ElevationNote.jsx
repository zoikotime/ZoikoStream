import { ConsoleButton as Button } from "../../ui/Button";
import { notify } from "../../ui/Toast";
import { errMsg } from "../../api";

// What a platform-elevation-gated control is waiting for, and the established way to provide
// it: the state from useElevation("platform"), and its Elevate button. A page says what needs
// the elevation (`need`) and why it is being asked for, for the audit (`reason`).
//
// Shared by the retention panel and the settings form above it on Platform Configuration, so
// the page has one elevation prompt rather than two that drift apart.
export default function ElevationNote({ elevation, need, reason }) {
  if (elevation.status === "active") return null;
  const msg = {
    unknown: "Your elevation state could not be read, so changes may be refused.",
    expired: "Your platform elevation has expired.",
    wrong_scope: "Your current elevation does not cover platform configuration.",
    none: need,
  }[elevation.status];
  return (
    <div className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800 dark:border-amber-500/25 dark:bg-amber-500/10 dark:text-amber-200">
      <span>{msg}</span>
      {elevation.status !== "unknown" && (
        <Button
          size="sm"
          variant="secondary"
          loading={elevation.elevating}
          onClick={async () => {
            try {
              const ok = await elevation.elevate(reason);
              if (!ok) notify.error("You hold a different elevation; end it from the rail, then elevate for platform.");
            } catch (e) {
              notify.error(errMsg(e));
            }
          }}
        >
          Elevate for platform
        </Button>
      )}
    </div>
  );
}
