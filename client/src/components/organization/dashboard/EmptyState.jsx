import { CONSOLE, cx } from "../../../ui/tokens";

// The dashboard's "there is nothing here yet" block.
//
// Every empty region on this page says two things: what is absent, and what would make it
// appear. A panel that renders only a dash tells the reader the product is broken; one that
// says "Analytics appear once viewers attend an event" tells them it is waiting for them.
export default function EmptyState({ icon: Icon, title, description, action, className = "" }) {
  return (
    <div className={cx("px-5 py-10 text-center", className)}>
      {Icon && (
        <span
          aria-hidden="true"
          className="mx-auto mb-3 grid h-10 w-10 place-items-center rounded-full bg-slate-100 text-slate-400 dark:bg-white/[0.07] dark:text-neutral-400"
        >
          <Icon className="text-[17px]" />
        </span>
      )}
      <p className={cx("text-[13px] font-semibold", CONSOLE.body)}>{title}</p>
      {description && <p className={cx("mx-auto mt-1 max-w-xs text-[12px]", CONSOLE.faint)}>{description}</p>}
      {action && <div className="mt-4 flex justify-center">{action}</div>}
    </div>
  );
}
