// client/src/pages/organization/Billing.jsx
// Billing — subscription + usage. Route: /organization/billing, rendered inside
// OrganizationLayout. Backed by GET /organization/overview (entitlements: real plan/status/
// usage, from services/org.py entitlements()) and GET /organization/plans (pricing tiers).
//
// No payment provider is integrated yet (deliberate — see docs), so this page does not
// pretend to run a checkout: no fake card CRUD, no fake plan-switch, no invented invoices.
// Changing plans is still a real action, just not a self-serve one yet — contact support.
import { useMemo } from "react";
import { Link } from "react-router-dom";
import {
  FiClock, FiHardDrive, FiUsers, FiCalendar, FiCreditCard, FiCheck, FiMail, FiFileText,
} from "react-icons/fi";
import { cx, ACCENT } from "../../ui/tokens";
import api from "../../api";
import useApi from "../../hooks/useApi";
import Card from "../../ui/Card";
import Badge from "../../ui/Badge";
import Spinner from "../../ui/Spinner";
import { fmtDate } from "../../data/events";

const METER_ICON = { Storage: FiHardDrive, Members: FiUsers, "Streaming hours": FiClock };
const METER_ACCENT = { Storage: "blue", Members: "emerald", "Streaming hours": "violet" };

function UsageMeter({ label, used, limit, unit }) {
  const Icon = METER_ICON[label] || FiHardDrive;
  const accent = METER_ACCENT[label] || "violet";
  const pct = limit ? Math.min(100, Math.round((used / limit) * 100)) : null;
  const near = pct !== null && pct >= 90;
  return (
    <Card padding="md">
      <div className="flex items-center gap-3">
        <span className={cx("grid h-10 w-10 shrink-0 place-items-center rounded-xl", ACCENT[accent].chip)}>
          <Icon className="text-lg" />
        </span>
        <div className="min-w-0">
          <p className="truncate text-sm font-medium text-slate-500 dark:text-slate-400">{label}</p>
          <p className="text-lg font-bold text-slate-900 dark:text-white">
            {used.toLocaleString()} {unit}
            <span className="text-sm font-medium text-slate-400"> / {limit != null ? `${limit.toLocaleString()} ${unit}` : "Unlimited"}</span>
          </p>
        </div>
      </div>
      {pct !== null && (
        <>
          <div className="mt-3 h-2 w-full overflow-hidden rounded-full bg-slate-100 dark:bg-slate-800">
            <div className={cx("h-full rounded-full transition-all", near ? "bg-rose-500" : ACCENT[accent].solid)} style={{ width: `${pct}%` }} />
          </div>
          <p className={cx("mt-1.5 text-xs", near ? "text-rose-500" : "text-slate-400")}>{pct}% used</p>
        </>
      )}
    </Card>
  );
}

const STATUS_TONE = { active: "active", trial: "info", past_due: "warning", cancelled: "error" };

export default function OrganizationBilling() {
  const { data: overview, loading: loadingOverview, error: overviewError } = useApi(() =>
    api.get("/organization/overview", { params: { range: "24h" } }).then((r) => r.data)
  );
  const { data: plansList, loading: loadingPlans } = useApi(() =>
    api.get("/organization/plans").then((r) => r.data)
  );

  const ent = overview?.entitlements;
  const currentPlan = useMemo(
    () => plansList?.find((p) => p.slug === ent?.plan_slug),
    [plansList, ent]
  );
  const loading = loadingOverview || loadingPlans;

  return (
    <div className="space-y-6">
      {/* Header */}
      <div>
        <h1 className="text-2xl font-bold tracking-tight text-slate-900 dark:text-white">Billing</h1>
        <p className="text-sm text-slate-500 dark:text-slate-400">Your subscription and usage</p>
      </div>

      {overviewError ? (
        <div className="rounded-xl border border-rose-200 bg-rose-50 px-5 py-4 text-sm text-rose-700 dark:border-rose-500/20 dark:bg-rose-500/10 dark:text-rose-300">
          Couldn't load billing information. Try refreshing the page.
        </div>
      ) : loading ? (
        <div className="grid place-items-center py-20"><Spinner /></div>
      ) : (
        <>
          {/* Current subscription. The plan is the one card on this page that should read as
              "yours", so the whole frame carries the brand instead of a magenta bar down the
              left edge: a violet border all the way round, over a lavender wash that fades out
              to the right. overflow-hidden keeps that wash inside the corner radius. */}
          <Card
            padding="lg"
            className="relative overflow-hidden border-violet-400/70 bg-gradient-to-br from-violet-50 via-white to-white shadow-[0_1px_3px_rgba(124,58,237,0.08)] dark:border-violet-500/40 dark:from-violet-500/[0.10] dark:via-slate-900 dark:to-slate-900"
          >
            <div className="flex flex-col gap-5 lg:flex-row lg:items-center lg:justify-between">
              <div>
                <div className="flex flex-wrap items-center gap-3">
                  <h2 className="text-xl font-bold text-slate-900 dark:text-white">
                    {ent?.plan || "No plan"}{ent?.plan ? " Plan" : ""}
                  </h2>
                  {ent?.status && <Badge status={STATUS_TONE[ent.status] || "info"} dot>{ent.status}</Badge>}
                </div>
                <div className="mt-4 flex flex-wrap items-center gap-x-6 gap-y-2">
                  {currentPlan && (
                    <span>
                      {currentPlan.pricing_state === "PUBLISHED" ? (
                        <>
                          <span className="text-3xl font-bold text-violet-600 dark:text-violet-400">${currentPlan.price_monthly}</span>
                          <span className="text-sm text-slate-400"> /month</span>
                        </>
                      ) : (
                        /* pricing_state is derived server-side (schemas/admin.PlanOut) so the
                           three commercial states stay distinct: CUSTOM means "quote
                           required", NOT_PUBLISHED means no approved price exists yet.
                           Neither is $0 — showing a number here would invent a price. */
                        <span className="text-sm font-medium text-slate-500 dark:text-slate-400">
                          {currentPlan.pricing_state === "CUSTOM"
                            ? "Custom pricing — contact sales for a quote"
                            : "Pricing not currently published — contact sales"}
                        </span>
                      )}
                    </span>
                  )}
                  {(ent?.trial_ends_at || ent?.current_period_end) && (
                    <span className="inline-flex items-center gap-2 text-sm text-slate-500 dark:text-slate-400">
                      <FiCalendar />
                      {ent.status === "trial" ? "Trial ends" : "Renews"} on{" "}
                      {fmtDate(ent.trial_ends_at || ent.current_period_end)}
                    </span>
                  )}
                </div>
              </div>
              {/* In-app contact form. This used to be a mail-protocol link, which handed the
                  click to whatever mail handler the operating system had registered — on a
                  domain whose mail is hosted externally that landed the operator on the mail
                  host's sign-in page, which from inside the product looked like ZoikoStream
                  had redirected them somewhere unexpected. Plan changes are still a sales
                  conversation (there is no self-service subscription billing behind this
                  page), but the conversation now starts and stays on a ZoikoStream page. */}
              <Link
                to="/contact"
                className="inline-flex items-center gap-2 rounded-xl bg-slate-900 px-4 py-2.5 text-sm font-semibold text-white transition hover:bg-slate-800 dark:bg-white dark:text-slate-900 dark:hover:bg-slate-100"
              >
                <FiMail className="text-base" /> Contact sales to change plan
              </Link>
            </div>
          </Card>

          {/* Usage */}
          <div>
            <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-slate-400">Usage</h2>
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
              {(ent?.items || []).map((m) => (
                <UsageMeter key={m.label} {...m} />
              ))}
            </div>
          </div>

          {/* Payment methods — no payment provider integrated yet */}
          <div>
            <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-slate-400">Payment Methods</h2>
            <Card padding="lg" className="flex flex-col items-center gap-2 py-10 text-center">
              <FiCreditCard className="text-2xl text-slate-300 dark:text-slate-600" />
              <p className="font-medium text-slate-700 dark:text-slate-200">No payment provider connected</p>
              <p className="max-w-sm text-sm text-slate-500 dark:text-slate-400">
                Cards and billing details aren't collected yet — plan changes and invoicing are handled directly by our team for now.
              </p>
            </Card>
          </div>

          {/* Billing history — no invoicing pipeline yet */}
          <Card padding="lg" className="flex flex-col items-center gap-2 py-10 text-center">
            <FiFileText className="text-2xl text-slate-300 dark:text-slate-600" />
            <p className="font-medium text-slate-700 dark:text-slate-200">No invoices yet</p>
            <p className="max-w-sm text-sm text-slate-500 dark:text-slate-400">
              Invoice generation needs a connected payment provider, which isn't set up yet.
            </p>
          </Card>

          {/* Plan comparison */}
          <div>
            <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-slate-400">Available Plans</h2>
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
              {(plansList || []).map((p) => {
                const current = p.slug === ent?.plan_slug;
                return (
                  <div
                    key={p.id}
                    className={cx(
                      "flex flex-col rounded-2xl border p-4",
                      current ? "border-violet-500 ring-1 ring-violet-500/30" : "border-slate-200 dark:border-slate-700"
                    )}
                  >
                    <div className="flex items-center justify-between">
                      <p className="font-semibold text-slate-900 dark:text-white">{p.name}</p>
                      {current && <Badge status="info">Current</Badge>}
                    </div>
                    <p className="mt-3">
                      {p.pricing_state === "PUBLISHED" ? (
                        <>
                          <span className="text-2xl font-bold text-slate-900 dark:text-white">${p.price_monthly}</span>
                          <span className="text-sm text-slate-400"> /mo</span>
                        </>
                      ) : (
                        <span className="text-sm font-medium text-slate-400">
                          {p.pricing_state === "CUSTOM" ? "Contact Sales" : "Not currently published"}
                        </span>
                      )}
                    </p>
                    <ul className="mt-3 flex-1 space-y-1.5">
                      {(p.features || []).map((f) => (
                        <li key={f} className="flex items-start gap-1.5 text-xs text-slate-600 dark:text-slate-300">
                          <FiCheck className="mt-0.5 shrink-0 text-emerald-500" /> {f}
                        </li>
                      ))}
                    </ul>
                    {!current && (
                      <Link
                        // Carries the plan through so the contact form opens with the
                        // commercial topic chosen and the plan already named in the message.
                        to={`/contact?plan=${encodeURIComponent(p.name)}`}
                        aria-label={`Contact us to switch to the ${p.name} plan`}
                        className="mt-4 inline-flex w-full items-center justify-center gap-1.5 rounded-lg border border-slate-200 px-3 py-1.5 text-xs font-medium text-slate-600 transition hover:bg-slate-50 dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-800"
                      >
                        Contact us to switch
                      </Link>
                    )}
                  </div>
                );
              })}
            </div>
          </div>
        </>
      )}
    </div>
  );
}
