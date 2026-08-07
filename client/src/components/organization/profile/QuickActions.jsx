import { motion } from "framer-motion";
import { Link } from "react-router-dom";
import { ArrowUpRight, CreditCard, Download, KeyRound, Layers, ShieldCheck, UserPlus, UserPen } from "lucide-react";
import { cx, focusRing } from "../../../ui/tokens";
import { CARD, CARD_INTERACTIVE, CHIP, TXT } from "./styles";
import { inView, item, liftHover, liftTap, stagger } from "./motion";

// Quick actions. Every card routes to a page that exists and that an org admin is
// authorised to use — nothing here opens a dead end.
//
// "Export Data" is the one action with no self-service endpoint in this stack, so it
// routes to Support (where the request is actually actioned) and says so, rather than
// rendering a button that would fail on click.
const ACTIONS = [
  {
    icon: UserPen,
    title: "Edit Profile",
    desc: "Organization name, industry, contact and timezone.",
    to: "/organization/settings?tab=general",
    tone: "violet",
  },
  {
    icon: UserPlus,
    title: "Invite Members",
    desc: "Send invitations and assign roles to your team.",
    to: "/organization/users",
    tone: "indigo",
  },
  {
    icon: Layers,
    title: "Manage Workspace",
    desc: "Workspace health, live sessions and entitlements.",
    to: "/organization/dashboard",
    tone: "blue",
  },
  {
    icon: KeyRound,
    title: "API Keys",
    desc: "Credential inventory, expiry and rotation posture.",
    to: "/organization/credentials",
    tone: "emerald",
  },
  {
    icon: ShieldCheck,
    title: "Security Settings",
    desc: "Two-factor, SSO, session limits and allowed domains.",
    to: "/organization/settings?tab=security",
    tone: "rose",
  },
  {
    icon: CreditCard,
    title: "Billing",
    desc: "Plan, usage against limits, invoices and payment.",
    to: "/organization/billing",
    tone: "amber",
  },
  {
    icon: Download,
    title: "Export Data",
    desc: "Request an org data export — handled by support.",
    to: "/organization/support",
    tone: "slate",
  },
];

export default function QuickActions() {
  return (
    <motion.div
      variants={stagger(0.05)}
      initial="hidden"
      whileInView="show"
      viewport={inView}
      className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4"
    >
      {ACTIONS.map((a) => (
        <motion.div key={a.title} variants={item} whileHover={liftHover} whileTap={liftTap}>
          <Link
            to={a.to}
            className={cx(CARD, CARD_INTERACTIVE, "group flex h-full flex-col p-5", focusRing)}
          >
            <div className="flex items-start justify-between gap-3">
              <span className={cx("grid h-10 w-10 shrink-0 place-items-center rounded-xl", CHIP[a.tone])}>
                <a.icon className="h-5 w-5" aria-hidden="true" />
              </span>
              <ArrowUpRight
                className={cx(
                  "h-4 w-4 shrink-0 transition-transform duration-200 group-hover:-translate-y-0.5 group-hover:translate-x-0.5",
                  TXT.faint
                )}
                aria-hidden="true"
              />
            </div>
            <p className={cx("mt-4 text-[15px] font-semibold", TXT.heading)}>{a.title}</p>
            <p className={cx("mt-1 text-[12px] leading-5", TXT.muted)}>{a.desc}</p>
          </Link>
        </motion.div>
      ))}
    </motion.div>
  );
}
