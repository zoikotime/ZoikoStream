import { motion } from "framer-motion";
import { Link } from "react-router-dom";
import { Building2, CalendarDays, Layers, Lock, Mail, Pencil, Sparkles, Star } from "lucide-react";
import { cx, focusRing } from "../../../ui/tokens";
import Skeleton from "../../../ui/Skeleton";
import { CARD, TXT } from "./styles";
import { fadeUp, stagger } from "./motion";
import { fmtDate, roleLabel } from "./derive";

// Header for /organization/profile: cover, avatar, identity, primary actions and the
// four identity facts underneath.
//
// The whole block is one card so the avatar can overlap the cover without escaping a
// bounding box, and the avatar / name / buttons all sit on ONE bottom edge (items-end)
// instead of three different ones.

const initials = (name = "") =>
  name.trim().split(/\s+/).slice(0, 2).map((w) => w[0]).join("").toUpperCase() || "?";

// Health verdicts use the same vocabulary as the console topbar, so the two never
// disagree about what "Degraded" means.
const VERDICT = {
  ok: { label: "Healthy", dot: "bg-emerald-500" },
  warn: { label: "Degraded", dot: "bg-amber-500" },
  down: { label: "Disrupted", dot: "bg-rose-500" },
  not_configured: { label: "Not configured", dot: "bg-slate-400" },
};

// The cover runs two different gradients rather than one dimmed for dark mode: a pale
// sky→lilac→blush wash on white, and a saturated violet→fuchsia on black. A single
// gradient can't be both — the pale one disappears against black, and the saturated one
// overpowers a light page.
const COVER =
  "bg-[linear-gradient(105deg,#bfe3f5_0%,#d3d6f7_38%,#ddd0f4_62%,#f7d6e6_100%)] dark:bg-[linear-gradient(105deg,#4f46e5_0%,#7c3aed_38%,#a855f7_66%,#ec4899_100%)]";

// Pills sit on the cover in both themes, so they stay light-on-tint with dark text —
// white text would vanish against the pale light-mode wash.
const PILL =
  "inline-flex items-center gap-1.5 rounded-full border border-white/70 bg-white/80 px-3 py-1.5 text-[12px] font-semibold text-slate-700 shadow-sm backdrop-blur-sm dark:border-white/30 dark:bg-white/85 dark:text-slate-800";

function Fact({ icon: Icon, label, value, loading, href }) {
  return (
    <div className="flex items-center gap-3">
      <span className="grid h-9 w-9 shrink-0 place-items-center rounded-xl bg-violet-100 text-violet-600 dark:bg-violet-500/15 dark:text-violet-300">
        <Icon className="h-4 w-4" aria-hidden="true" />
      </span>
      <div className="min-w-0">
        <p className={cx("text-[11px] font-semibold uppercase tracking-wider", TXT.faint)}>{label}</p>
        {loading ? (
          <Skeleton className="mt-1.5 h-4 w-24" />
        ) : href ? (
          // An unset fact that has somewhere to go becomes the action instead of a dash.
          <Link
            to={href.to}
            className="text-[15px] font-semibold text-violet-600 hover:text-violet-700 dark:text-violet-400 dark:hover:text-violet-300"
          >
            {href.label} →
          </Link>
        ) : (
          <p className={cx("truncate text-[15px] font-semibold", TXT.heading)} title={value || undefined}>
            {value || "—"}
          </p>
        )}
      </div>
    </div>
  );
}

export default function ProfileHeader({
  user,
  organization,
  workspace,
  workspaceCount = 0,
  health,
  plan,
  memberSince,
  loading = false,
  onChangePassword,
}) {
  const verdict = VERDICT[health?.status] || VERDICT.not_configured;
  const name = user?.full_name || "—";

  return (
    <motion.section
      variants={fadeUp}
      initial="hidden"
      animate="show"
      className={cx(CARD, "overflow-hidden")}
    >
      {/* Cover. Kept short — it is a brand band, not a hero, and every pixel of it pushes
          the actual content further down the page. */}
      <div className={cx("relative h-28 sm:h-32", COVER)}>
        <div
          aria-hidden="true"
          className="absolute inset-0 opacity-[0.22] [background-image:radial-gradient(circle_at_center,#fff_1px,transparent_1px)] [background-size:22px_22px]"
        />

        {/* Workspace + service health, pinned top-right so they never collide with the
            avatar that overlaps the bottom-left. */}
        <div className="absolute right-4 top-4 flex flex-wrap items-center justify-end gap-2 sm:right-6">
          <span className={PILL}>
            <Layers className="h-3.5 w-3.5" aria-hidden="true" />
            {workspace?.label || "production"}
            {workspaceCount > 1 && <span className="text-slate-400">+{workspaceCount - 1}</span>}
          </span>
          <span className={PILL} title={health?.cause || undefined}>
            <span className={cx("h-2 w-2 rounded-full", verdict.dot)} aria-hidden="true" />
            {health?.label || verdict.label}
          </span>
        </div>
      </div>

      {/* Identity row. items-end puts the avatar, the name block and the buttons on a
          single baseline at every breakpoint above sm. */}
      <div className="px-6 pb-6 sm:px-8">
        <div className="flex flex-col gap-5 lg:flex-row lg:items-end lg:justify-between">
          <div className="flex flex-col items-center gap-5 text-center sm:flex-row sm:items-end sm:text-left">
            <div className="relative -mt-12 shrink-0 sm:-mt-14">
              {/* Name, email, username and role come from the stored session, which is
                  hydrated synchronously — they are never in a loading state, so they are
                  rendered directly. Only the API-sourced facts below skeleton. */}
              <div className="grid h-24 w-24 place-items-center rounded-full bg-[linear-gradient(140deg,#bfe3f5_0%,#cdd2f6_45%,#d8c8f2_100%)] text-[34px] font-bold text-violet-900 shadow-lg shadow-violet-900/10 ring-4 ring-white dark:ring-black sm:h-28 sm:w-28 sm:text-[38px]">
                {initials(name)}
              </div>
              {/* Account-status dot, anchored to the avatar so status reads at a glance. */}
              <span
                className="absolute bottom-1 right-1 grid h-6 w-6 place-items-center rounded-full bg-white ring-1 ring-slate-200 dark:bg-black dark:ring-white/15"
                title="Account active"
              >
                <span className="h-3 w-3 rounded-full bg-emerald-500" aria-hidden="true" />
                <span className="sr-only">Account active</span>
              </span>
            </div>

            <div className="min-w-0 space-y-1.5 pb-1">
              <div className="flex flex-wrap items-center justify-center gap-x-3 gap-y-2 sm:justify-start">
                <h1 className={cx("truncate text-[26px] font-bold tracking-tight sm:text-[30px]", TXT.heading)}>
                  {name}
                </h1>
                <span className="inline-flex items-center gap-1.5 rounded-full bg-violet-100 px-2.5 py-1 text-[11px] font-bold text-violet-700 dark:bg-violet-500/15 dark:text-violet-300">
                  <Sparkles className="h-3 w-3" aria-hidden="true" />
                  {roleLabel(user?.role)}
                </span>
              </div>
              <div className={cx("flex flex-wrap items-center justify-center gap-x-2 text-sm sm:justify-start", TXT.muted)}>
                <Mail className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
                <span className="truncate">{user?.email}</span>
                {user?.username && (
                  <>
                    <span className="text-slate-300 dark:text-neutral-700" aria-hidden="true">·</span>
                    <span className="truncate font-medium">@{user.username}</span>
                  </>
                )}
              </div>
            </div>
          </div>

          {/* Centred on mobile (where the identity block is centred too), left-aligned once
              the identity block goes left-aligned, and pushed right only when the row
              becomes horizontal at lg. */}
          <div className="flex flex-wrap items-center justify-center gap-3 sm:justify-start lg:justify-end lg:pb-1">
            <Link
              to="/organization/settings?tab=general"
              className={cx(
                "inline-flex h-11 items-center gap-2 rounded-xl border border-slate-200 bg-white px-5 text-sm font-semibold text-slate-700 transition-colors duration-150 hover:border-slate-300 hover:bg-slate-50 dark:border-white/10 dark:bg-white/[0.04] dark:text-neutral-100 dark:hover:bg-white/[0.08]",
                focusRing
              )}
            >
              <Pencil className="h-4 w-4" aria-hidden="true" />
              Edit Profile
            </Link>
            <button
              type="button"
              onClick={onChangePassword}
              title="Sends a one-time code to your email address"
              className={cx(
                "inline-flex h-11 items-center gap-2 rounded-xl bg-violet-600 px-5 text-sm font-semibold text-white shadow-sm shadow-violet-600/25 transition-colors duration-150 hover:bg-violet-700 active:bg-violet-800",
                focusRing
              )}
            >
              <Lock className="h-4 w-4" aria-hidden="true" />
              Change Password
            </button>
          </div>
        </div>

        {/* Identity facts — one 8px-grid row, four columns on desktop, two on tablet. */}
        <motion.div
          variants={stagger(0.05, 0.1)}
          initial="hidden"
          animate="show"
          className="mt-6 grid grid-cols-1 gap-x-6 gap-y-5 border-t border-slate-100 pt-6 sm:grid-cols-2 lg:grid-cols-4 dark:border-white/10"
        >
          {[
            { icon: Building2, label: "Organization", value: organization?.name },
            {
              icon: Layers,
              label: "Workspace",
              value: workspace?.name ? `${workspace.label} · ${workspace.name}` : workspace?.label,
            },
            {
              icon: Star,
              label: "Plan",
              value: plan,
              // No subscribed plan is a state with an obvious next step, so the fact
              // becomes the link rather than an em dash.
              href: plan ? null : { to: "/organization/billing", label: "Upgrade" },
            },
            { icon: CalendarDays, label: "Member since", value: memberSince ? fmtDate(memberSince) : null },
          ].map((f) => (
            <motion.div key={f.label} variants={fadeUp}>
              <Fact {...f} loading={loading} />
            </motion.div>
          ))}
        </motion.div>
      </div>
    </motion.section>
  );
}
