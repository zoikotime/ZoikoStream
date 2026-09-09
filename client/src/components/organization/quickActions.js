import {
  CreditCard, Download, KeyRound, Layers, ShieldCheck, UserPen, UserPlus,
} from "lucide-react";

// The organization owner's shortcut set — one definition, shared by the topbar's Quick
// Actions menu and anything else that offers the same jumps.
//
// Extracted from profile/QuickActions.jsx so the two cannot drift: a route that moves has to
// move once. Every entry points at a page that EXISTS and that an org admin is authorised to
// open — nothing here is a dead end.
//
// "Export Data" is the one action with no self-service endpoint in this stack, so it routes
// to Support (where the request is actually actioned) and the description says so, rather
// than offering a button that would fail on click.
export const QUICK_ACTIONS = [
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
