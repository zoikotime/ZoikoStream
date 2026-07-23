import { cx, TONE } from "../../ui/tokens";

// Semantic pill: tone color + optional leading dot or icon. Never bare text for status.
// tone: success | warning | danger | info | brand | neutral
export default function Badge({ tone = "neutral", icon: Icon, dot = false, size = "md", className = "", children }) {
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
