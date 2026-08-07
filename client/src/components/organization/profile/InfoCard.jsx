import { useState } from "react";
import { motion } from "framer-motion";
import { Check, Copy } from "lucide-react";
import { cx, focusRing } from "../../../ui/tokens";
import Skeleton from "../../../ui/Skeleton";
import { notify } from "../../../ui/Toast";
import { CARD, CARD_INTERACTIVE, CHIP, TXT } from "./styles";
import { item } from "./motion";

// A single field in Personal Information.
//
// `copy` turns the card into a one-click copy target for the values people actually paste
// somewhere (email, username, organization). The button only materialises on hover or
// keyboard focus so the resting card stays quiet.
export default function InfoCard({
  icon: Icon,
  label,
  value,
  tone = "slate",
  copy = false,
  trailing,
  loading = false,
}) {
  const [copied, setCopied] = useState(false);
  const copyable = copy && typeof value === "string" && value.length > 0;

  const onCopy = async () => {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      notify.success(`${label} copied`);
      setTimeout(() => setCopied(false), 1600);
    } catch {
      notify.error("Clipboard is unavailable in this browser");
    }
  };

  return (
    <motion.div variants={item} className={cx(CARD, CARD_INTERACTIVE, "group p-5")}>
      <div className="flex items-center justify-between gap-3">
        <div className="flex min-w-0 items-center gap-2.5">
          <span className={cx("grid h-7 w-7 shrink-0 place-items-center rounded-lg", CHIP[tone] || CHIP.slate)}>
            <Icon className="h-3.5 w-3.5" aria-hidden="true" />
          </span>
          <span className={cx("truncate text-[12px] font-semibold uppercase tracking-wider", TXT.faint)}>
            {label}
          </span>
        </div>

        {copyable && (
          <button
            type="button"
            onClick={onCopy}
            aria-label={`Copy ${label.toLowerCase()}`}
            className={cx(
              "grid h-7 w-7 shrink-0 place-items-center rounded-lg opacity-0 transition duration-150",
              "text-slate-400 hover:bg-slate-100 hover:text-slate-700 focus-visible:opacity-100 group-hover:opacity-100",
              "dark:text-neutral-500 dark:hover:bg-white/[0.07] dark:hover:text-white",
              focusRing
            )}
          >
            {copied ? <Check className="h-3.5 w-3.5 text-emerald-500" /> : <Copy className="h-3.5 w-3.5" />}
          </button>
        )}
      </div>

      <div className="mt-3 flex items-center gap-2">
        {loading ? (
          <Skeleton className="h-5 w-32" />
        ) : (
          <>
            <p className={cx("min-w-0 truncate text-[15px] font-semibold", TXT.heading)} title={typeof value === "string" ? value : undefined}>
              {value || "—"}
            </p>
            {trailing}
          </>
        )}
      </div>
    </motion.div>
  );
}
