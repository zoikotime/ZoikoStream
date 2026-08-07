import { CONSOLE, cx } from "../../ui/tokens";

// Label/value pair for the detail drawers. StatRow is the same idea but pins the value to the
// right edge and never wraps it, which is correct for a short metric and wrong for
// "SRT · enc-northstar-a1 · 12 Mbps CBR" in a drawer that narrows to 96vw on a phone.
//
// So this is the LONG-value sibling: a real <dl> row that sits side-by-side with room to
// spare and stacks below `sm`. Keeps StatRow's em-dash convention — a null value renders "—"
// in the faint tone rather than an empty cell that reads as a measured blank.
//
// Wrap groups of these in <dl>, which is what the drawers do per section.
export default function DetailField({ label, value, children, className = "" }) {
  const content = children ?? value;
  const missing = content == null || content === "" || content === "—";
  return (
    <div className={cx("grid gap-0.5 py-1.5 sm:grid-cols-[minmax(0,190px)_1fr] sm:gap-4", className)}>
      <dt className={cx("text-[12px] leading-5", CONSOLE.muted)}>{label}</dt>
      <dd className={cx("min-w-0 break-words text-[13px] font-medium leading-5", missing ? CONSOLE.faint : CONSOLE.heading)}>
        {missing ? "—" : content}
      </dd>
    </div>
  );
}
