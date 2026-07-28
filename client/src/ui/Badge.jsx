import { cx, STATUS, TONE } from "./tokens";

// ONE status pill, two appearances so both surfaces keep their exact look:
//   marketing (pass `status`) — STATUS palette (emerald→violet), gap-1.5, px-2.5 text-xs.
//   console   (pass `tone`)   — TONE palette (literal green), gap-1, size-based (was admin/Badge).
// `dot` adds a leading indicator dot in both; `live` (marketing) makes it pulse;
// `icon` + `size` (console) match the old admin API.
export default function Badge({
  status,
  tone,
  icon: Icon,
  dot = false,
  live = false,
  size = "md",
  className = "",
  children,
}) {
  if (tone !== undefined) {
    // console appearance (was components/admin/Badge)
    const sz = size === "sm" ? "px-1.5 py-0.5 text-[10px]" : "px-2 py-0.5 text-[11px]";
    return (
      <span
        className={cx(
          "inline-flex items-center gap-1 whitespace-nowrap rounded-full font-semibold",
          sz,
          TONE[tone] || TONE.neutral,
          className
        )}
      >
        {dot && <span className="h-1.5 w-1.5 rounded-full bg-current" aria-hidden="true" />}
        {Icon && <Icon className="text-[11px]" aria-hidden="true" />}
        {children}
      </span>
    );
  }

  // marketing appearance
  const tint = STATUS[status || "neutral"] || STATUS.neutral;
  return (
    <span className={cx("inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-semibold", tint, className)}>
      {(dot || live) && <span className={cx("h-1.5 w-1.5 rounded-full bg-current", live && "animate-pulse")} />}
      {children}
    </span>
  );
}
