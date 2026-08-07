import { motion } from "framer-motion";
import { Link } from "react-router-dom";
import { Check, KeyRound, Minus, ShieldCheck } from "lucide-react";
import { cx, focusRing } from "../../../ui/tokens";
import Skeleton from "../../../ui/Skeleton";
import { CARD, INSET, TXT } from "./styles";
import { item, stagger } from "./motion";
import { scoreTone } from "./derive";

// Security posture, scored from the controls this platform actually stores
// (organization.security + domain verification). The checklist is rendered in full so
// the number is auditable — an admin can see exactly which control cost them points.

const RING = {
  emerald: "stroke-emerald-500",
  amber: "stroke-amber-500",
  rose: "stroke-rose-500",
  violet: "stroke-violet-500",
};
const SCORE_TEXT = {
  emerald: "text-emerald-600 dark:text-emerald-400",
  amber: "text-amber-600 dark:text-amber-400",
  rose: "text-rose-600 dark:text-rose-400",
  violet: "text-violet-600 dark:text-violet-400",
};

const R = 34;
const CIRC = 2 * Math.PI * R;

function ScoreRing({ score, tone }) {
  const pct = Math.min(100, Math.max(0, score ?? 0));
  return (
    <div className="relative grid h-24 w-24 shrink-0 place-items-center">
      <svg viewBox="0 0 80 80" className="h-24 w-24 -rotate-90">
        <circle cx="40" cy="40" r={R} className="fill-none stroke-slate-100 dark:stroke-white/[0.08]" strokeWidth="7" />
        <motion.circle
          cx="40"
          cy="40"
          r={R}
          className={cx("fill-none", RING[tone] || RING.violet)}
          strokeWidth="7"
          strokeLinecap="round"
          strokeDasharray={CIRC}
          initial={{ strokeDashoffset: CIRC }}
          animate={{ strokeDashoffset: CIRC - (CIRC * pct) / 100 }}
          transition={{ duration: 0.9, ease: [0.22, 0.61, 0.36, 1], delay: 0.2 }}
        />
      </svg>
      <div className="absolute inset-0 grid place-items-center">
        <span className={cx("text-xl font-bold tabular-nums", SCORE_TEXT[tone] || SCORE_TEXT.violet)}>
          {score == null ? "—" : score}
        </span>
      </div>
    </div>
  );
}

export default function SecurityPanel({ posture, loading = false, onChangePassword }) {
  const tone = scoreTone(posture?.score);

  return (
    <div className={cx(CARD, "overflow-hidden")}>
      <header className="flex items-center gap-3 border-b border-slate-100 px-6 py-4 dark:border-white/10">
        <span className="grid h-9 w-9 place-items-center rounded-xl bg-violet-100 text-violet-600 dark:bg-violet-500/15 dark:text-violet-300">
          <ShieldCheck className="h-4.5 w-4.5" aria-hidden="true" />
        </span>
        <div className="min-w-0">
          <h3 className={cx("text-[15px] font-semibold", TXT.heading)}>Security</h3>
          <p className={cx("text-[12px]", TXT.muted)}>Account credentials and organization posture.</p>
        </div>
      </header>

      {/* Password. This stack has no "change password while signed in" endpoint — the
          real, working path is the emailed one-time code, so the button goes there
          rather than opening a dialog that nothing can submit. */}
      <div className="flex flex-col gap-4 border-b border-slate-100 px-6 py-5 sm:flex-row sm:items-center sm:justify-between dark:border-white/10">
        <div className="flex items-center gap-3">
          <span className="grid h-10 w-10 shrink-0 place-items-center rounded-xl bg-slate-100 text-slate-500 dark:bg-white/[0.06] dark:text-neutral-400">
            <KeyRound className="h-4.5 w-4.5" aria-hidden="true" />
          </span>
          <div>
            <p className={cx("text-[13px] font-semibold", TXT.heading)}>Password</p>
            <p className={cx("mt-0.5 text-lg leading-5 tracking-[0.2em]", TXT.muted)}>••••••••</p>
          </div>
        </div>
        <button
          type="button"
          onClick={onChangePassword}
          className={cx(
            "inline-flex h-9 shrink-0 items-center justify-center gap-2 rounded-lg border border-slate-200 bg-white px-3.5 text-[13px] font-semibold text-slate-700 transition-colors duration-150 hover:border-violet-300 hover:bg-violet-50 hover:text-violet-700 dark:border-white/10 dark:bg-white/[0.03] dark:text-neutral-200 dark:hover:border-violet-400/30 dark:hover:bg-violet-500/10 dark:hover:text-white",
            focusRing
          )}
        >
          Change Password
        </button>
      </div>

      <div className="px-6 py-5">
        {loading ? (
          <div className="flex items-center gap-5">
            <Skeleton variant="circle" className="h-24 w-24 shrink-0" />
            <div className="flex-1 space-y-2">
              <Skeleton className="h-4 w-40" />
              <Skeleton className="h-3 w-56" />
            </div>
          </div>
        ) : posture?.score == null ? (
          // No security payload (request failed, or the caller isn't an org admin) —
          // say so rather than scoring an empty object as zero.
          <div className={cx(INSET, "px-4 py-5 text-center")}>
            <p className={cx("text-[13px] font-semibold", TXT.heading)}>Posture unavailable</p>
            <p className={cx("mx-auto mt-1 max-w-sm text-[12px] leading-5", TXT.muted)}>
              Security settings couldn't be read for this organization, so no score is shown
              rather than an assumed one.
            </p>
          </div>
        ) : (
          <>
            <div className="flex items-center gap-5">
              <ScoreRing score={posture.score} tone={tone} />
              <div className="min-w-0">
                <p className={cx("text-[15px] font-semibold", TXT.heading)}>
                  {posture.level}
                  <span className={cx("ml-2 text-[12px] font-medium tabular-nums", TXT.muted)}>
                    {posture.score}/100
                  </span>
                </p>
                <p className={cx("mt-1 text-[12px] leading-5", TXT.muted)}>
                  {posture.passed} of {posture.total} controls enabled. Scored from the security
                  settings this organization has saved.
                </p>
              </div>
            </div>

            <motion.ul
              variants={stagger(0.04)}
              initial="hidden"
              animate="show"
              className="mt-5 space-y-2"
            >
              {posture.checks.map((c) => (
                <motion.li
                  key={c.key}
                  variants={item}
                  className="flex items-start gap-2.5"
                  title={c.ok ? undefined : c.fix}
                >
                  <span
                    className={cx(
                      "mt-0.5 grid h-4 w-4 shrink-0 place-items-center rounded-full",
                      c.ok
                        ? "bg-emerald-100 text-emerald-600 dark:bg-emerald-500/15 dark:text-emerald-400"
                        : "bg-slate-100 text-slate-400 dark:bg-white/[0.07] dark:text-neutral-500"
                    )}
                  >
                    {c.ok ? <Check className="h-2.5 w-2.5" /> : <Minus className="h-2.5 w-2.5" />}
                  </span>
                  <span className={cx("text-[12px] leading-4", c.ok ? TXT.body : TXT.faint)}>
                    {c.label}
                  </span>
                  <span className={cx("ml-auto shrink-0 text-[11px] font-semibold tabular-nums", TXT.faint)}>
                    {c.ok ? `+${c.weight}` : `0/${c.weight}`}
                  </span>
                </motion.li>
              ))}
            </motion.ul>

            <Link
              to="/organization/settings?tab=security"
              className="mt-4 inline-flex text-[12px] font-semibold text-violet-600 hover:text-violet-700 dark:text-violet-400 dark:hover:text-violet-300"
            >
              Adjust security settings →
            </Link>
          </>
        )}
      </div>
    </div>
  );
}
