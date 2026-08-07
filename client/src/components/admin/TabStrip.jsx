import { CONSOLE, cx, focusRing } from "../../ui/tokens";

// The console's segmented tablist — the same skin as the Action Queues strip on the Command
// Center, sized for the ten or eleven tabs a readiness record and an Asset 360 carry: it
// scrolls horizontally instead of wrapping, so the record header never grows a second row.
//
// Roving tabindex plus Left/Right/Home/End, per the WAI-ARIA tabs pattern, because a
// keyboard-only operator has to be able to complete every authorized workflow.
//
// tabs: [{ key, label, count? }]
export default function TabStrip({ tabs, active, onChange, label, idPrefix = "tab" }) {
  const go = (index) => {
    const next = tabs[(index + tabs.length) % tabs.length];
    onChange(next.key);
    document.getElementById(`${idPrefix}-${next.key}`)?.focus();
  };

  const onKeyDown = (e) => {
    const i = tabs.findIndex((t) => t.key === active);
    const key = { ArrowRight: i + 1, ArrowLeft: i - 1, Home: 0, End: tabs.length - 1 }[e.key];
    if (key === undefined) return;
    e.preventDefault();
    go(key);
  };

  return (
    <div
      role="tablist"
      aria-label={label}
      onKeyDown={onKeyDown}
      className={cx("flex gap-1 overflow-x-auto rounded-lg p-1", CONSOLE.segment)}
    >
      {tabs.map((t) => (
        <button
          key={t.key}
          type="button"
          role="tab"
          id={`${idPrefix}-${t.key}`}
          aria-controls={`${idPrefix}panel-${t.key}`}
          aria-selected={active === t.key}
          tabIndex={active === t.key ? 0 : -1}
          onClick={() => onChange(t.key)}
          className={cx(
            "flex min-h-[32px] shrink-0 items-center gap-1.5 whitespace-nowrap rounded-md px-3 py-1.5 text-[12px] font-semibold transition duration-150 motion-reduce:transition-none",
            focusRing,
            active === t.key ? CONSOLE.segmentOn : CONSOLE.segmentOff
          )}
        >
          {t.label}
          {t.count > 0 && <span className="tabular-nums opacity-70">· {t.count}</span>}
        </button>
      ))}
    </div>
  );
}
