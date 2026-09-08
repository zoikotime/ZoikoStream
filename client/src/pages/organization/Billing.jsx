// client/src/pages/organization/Billing.jsx
// Billing — subscription + usage. Route: /organization/billing, rendered inside
// OrganizationLayout. Backed by GET /organization/overview (entitlements: real plan/status/
// usage, from services/org.py entitlements()) and GET /organization/plans (pricing tiers).
//
// Stripe IS integrated for Ledger 1 (platform subscription): a plan with an approved Stripe
// price renders a self-service Upgrade that opens Stripe-hosted Checkout, and the confirmed
// subscription state comes back from our own backend via the verified webhook.
//
// What this page still does NOT have, and must therefore not imply it has:
//   * saved-card management — there is no payment-method endpoint. Card details are entered on
//     Stripe's page and never reach this origin, so there is nothing here to list or edit.
//   * an invoice list — Stripe issues invoices per billing period, but no endpoint surfaces
//     them yet, so this page states where they live rather than rendering an empty table.
// No fake card CRUD, no invented invoices, no fake plan-switch.
//
// PLAN CHANGES for an already-paid subscription go through POST/DELETE
// /organization/billing/plan-change, NOT through checkout (checkout creates a subscription;
// an existing customer needs the one they have MOVED). Per the approved commercial decisions
// the change is SCHEDULED, never immediate: no proration, effective at current_period_end, and
// the customer keeps their current plan's entitlements until then. This page therefore shows a
// change as PENDING with its effective date and never implies it has already happened.
import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import {
  FiClock, FiHardDrive, FiUsers, FiCalendar, FiCreditCard, FiCheck, FiMail, FiFileText,
} from "react-icons/fi";
import { cx, ACCENT } from "../../ui/tokens";
import api, { errMsg } from "../../api";
import { notify } from "../../ui/Toast";
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
  const { data: overview, loading: loadingOverview, error: overviewError,
          reload: reloadOverview } = useApi(() =>
    api.get("/organization/overview", { params: { range: "24h" } }).then((r) => r.data)
  );
  const { data: plansList, loading: loadingPlans } = useApi(() =>
    api.get("/organization/plans").then((r) => r.data)
  );

  // ── Stripe-hosted subscription checkout (ZST-COM-PLAN-001 Section 13/18) ──────────────
  const [checkoutFor, setCheckoutFor] = useState(null);
  // The URL is readable at first render, so the initial outcome is derived synchronously here
  // rather than in an effect (which would cause a cascading render). The effect below only
  // performs the async backend read.
  const [returned, setReturned] = useState(() => {
    const params = new URLSearchParams(window.location.search);
    const outcome = params.get("checkout");
    if (outcome === "cancelled") return { state: "cancelled" };
    if (outcome === "success" && params.get("session_id")) return { state: "checking" };
    return null;
  });

  // Billing cadence. The server publishes which cadences each plan can actually be bought on
  // (`billing_intervals`, from the approved price configuration), so this only ever selects
  // BETWEEN approved prices — it can never introduce one. Defaults to monthly, and the toggle
  // is not rendered at all unless some plan genuinely offers an alternative.
  const [interval, setInterval] = useState("monthly");
  const intervalChoices = useMemo(() => {
    const seen = new Set();
    (plansList || []).forEach((p) => (p.billing_intervals || []).forEach((i) => seen.add(i)));
    return ["monthly", "annual"].filter((i) => seen.has(i));
  }, [plansList]);

  const startCheckout = async (planSlug, billingInterval) => {
    setCheckoutFor(planSlug);
    try {
      // The body carries a plan slug and a canonical cadence ONLY. The server resolves the
      // approved Stripe Price ID for that pair; there is deliberately no amount, currency or
      // price field a browser could tamper with.
      const { data } = await api.post("/organization/billing/checkout-session", {
        plan_slug: planSlug,
        billing_interval: billingInterval,
      });
      // Full-page navigation to Stripe-hosted Checkout — card details are entered on Stripe's
      // domain and never touch this origin.
      window.location.assign(data.checkout_url);
    } catch (e) {
      setCheckoutFor(null);
      notify.error(errMsg(e));
    }
  };

  // Returning from Stripe. The redirect proves only that the browser came back: it is
  // forgeable and may arrive before the webhook. Section 18 forbids unlocking on it, so the
  // real state is READ BACK from the backend and "pending" is shown until a signature-verified
  // webhook has bound the subscription.
  useEffect(() => {
    const sessionId = new URLSearchParams(window.location.search).get("session_id");
    if (returned?.state !== "checking" || !sessionId) return;
    let cancelled = false;
    api.get("/organization/billing/checkout-status", { params: { session_id: sessionId } })
      .then((r) => { if (!cancelled) setReturned(r.data); })
      // A failed read means "not confirmed yet", never "confirmed" — the safe direction.
      .catch(() => { if (!cancelled) setReturned({ state: "pending" }); });
    return () => { cancelled = true; };
    // Runs once for the initial "checking" state; setReturned then moves it out of that state.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const ent = overview?.entitlements;
  const currentPlan = useMemo(
    () => plansList?.find((p) => p.slug === ent?.plan_slug),
    [plansList, ent]
  );
  // Does this organization already have a LIVE PAID subscription? A trial is not paid — that
  // is the self-service purchase path and must keep its Upgrade button. `active`/`past_due`
  // mean money is already moving, and the backend refuses a second checkout for exactly those
  // (it would create a second Stripe subscription and bill twice), so the CTA must not offer
  // one. The distinction comes from the backend's Section 12 status, not from anything the
  // page decides for itself. `trial` is the pre-Section-12 spelling still present on old rows.
  const paidSubscription = ["active", "past_due", "plan_change_scheduled"].includes(ent?.status);
  // A change already scheduled. All three fields are written together by the backend, so
  // `pending_plan_slug` is a sufficient test for "is one pending".
  const pending = ent?.pending_plan_slug
    ? {
        planSlug: ent.pending_plan_slug,
        planName: ent.pending_plan_name || ent.pending_plan_slug,
        interval: ent.pending_billing_interval,
        effectiveAt: ent.plan_change_effective_at,
      }
    : null;

  const [changing, setChanging] = useState(null);

  // Schedule a plan/cadence change for the end of the current period. Sends the SAME canonical
  // identifiers as checkout — plan slug and cadence, never a price — and the effective date is
  // decided by the backend from its own `current_period_end`, never computed here.
  const schedulePlanChange = async (planSlug, billingInterval) => {
    setChanging(planSlug);
    try {
      await api.post("/organization/billing/plan-change", {
        plan_slug: planSlug,
        billing_interval: billingInterval,
      });
      notify.success("Plan change scheduled for the end of your billing period.");
      // Re-read from the backend rather than assuming: the request may have been refused for a
      // reason only the server knows, and Section 18 forbids the UI asserting an outcome.
      await reloadOverview?.();
    } catch (e) {
      notify.error(errMsg(e));
    } finally {
      setChanging(null);
    }
  };

  const cancelPlanChange = async () => {
    setChanging("cancel");
    try {
      await api.delete("/organization/billing/plan-change");
      notify.success("Scheduled plan change cancelled. Your current plan continues.");
      await reloadOverview?.();
    } catch (e) {
      notify.error(errMsg(e));
    } finally {
      setChanging(null);
    }
  };
  const loading = loadingOverview || loadingPlans;

  return (
    <div className="space-y-6">
      {/* Header */}
      <div>
        <h1 className="text-2xl font-bold tracking-tight text-slate-900 dark:text-white">Billing</h1>
        <p className="text-sm text-slate-500 dark:text-slate-400">Your subscription and usage</p>
      </div>

      {/* Return from Stripe. Deliberately never says "payment successful" on the strength of
          the redirect: ZST-COM-PLAN-001 Section 18 requires the verified webhook to be the
          confirmation, so until the backend reports the subscription bound we say the payment
          is being confirmed. `returned` is what the BACKEND said, not what the URL said. */}
      {returned && (
        <div
          role="status"
          className={cx(
            "rounded-xl border px-4 py-3 text-sm",
            returned.state === "confirmed"
              ? "border-emerald-200 bg-emerald-50 text-emerald-800 dark:border-emerald-500/30 dark:bg-emerald-500/10 dark:text-emerald-300"
              : returned.state === "cancelled"
                ? "border-slate-200 bg-slate-50 text-slate-600 dark:border-slate-700 dark:bg-slate-800/50 dark:text-slate-300"
                : "border-amber-200 bg-amber-50 text-amber-800 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-300"
          )}
        >
          {returned.state === "confirmed" && (
            <>Payment confirmed. Your plan is now <strong>{returned.plan || "updated"}</strong>.</>
          )}
          {returned.state === "cancelled" && (
            <>Checkout was cancelled — nothing has been charged and your plan is unchanged. You can start again whenever you are ready.</>
          )}
          {(returned.state === "pending" || returned.state === "checking") && (
            <>Thanks — we&apos;re confirming your payment with Stripe. This page will show your new plan as soon as it&apos;s confirmed; you can safely leave and come back.</>
          )}
        </div>
      )}

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
                  {/* The cadence the tenant is actually billed on, from the backend. Rendered
                      only when recorded: rows predating the column have no cadence, and
                      showing "monthly" for them would assert something unverified. */}
                  {ent?.billing_interval && (
                    <span className="inline-flex items-center gap-2 text-sm capitalize text-slate-500 dark:text-slate-400">
                      <FiCreditCard /> Billed {ent.billing_interval}
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
                  had redirected them somewhere unexpected. The conversation now starts and
                  stays on a ZoikoStream page.
                  Still correct for CHANGING an existing paid plan: buying a plan is
                  self-service (see the Upgrade CTA below), but moving an already-active
                  subscription between paid tiers needs Section 12's PLAN_CHANGE_SCHEDULED
                  path, which is not implemented — so that remains a sales conversation. */}
              <Link
                to="/contact"
                className="inline-flex items-center gap-2 rounded-xl bg-slate-900 px-4 py-2.5 text-sm font-semibold text-white transition hover:bg-slate-800 dark:bg-white dark:text-slate-900 dark:hover:bg-slate-100"
              >
                <FiMail className="text-base" /> Contact sales
              </Link>
            </div>
          </Card>

          {/* A scheduled plan change. Shown as PENDING with its effective date — never as
              though it had already taken effect, because it has not: the customer is still on
              (and still billed for) their current plan until this date. */}
          {pending && (
            <Card padding="lg" className="border-violet-200 dark:border-violet-900/60">
              <div className="flex flex-wrap items-start justify-between gap-4">
                <div className="min-w-0">
                  <p className="flex items-center gap-2 text-sm font-semibold text-violet-700 dark:text-violet-300">
                    <FiCalendar className="text-base" /> Plan change scheduled
                  </p>
                  <p className="mt-2 text-sm text-slate-700 dark:text-slate-200">
                    <span className="font-medium">{currentPlan?.name || ent?.plan_slug}</span>
                    {ent?.billing_interval && (
                      <span className="text-slate-400"> ({ent.billing_interval})</span>
                    )}
                    <span className="mx-2 text-slate-400">&rarr;</span>
                    <span className="font-medium">{pending.planName}</span>
                    {pending.interval && (
                      <span className="text-slate-400"> ({pending.interval})</span>
                    )}
                  </p>
                  {pending.effectiveAt && (
                    <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
                      Effective {fmtDate(pending.effectiveAt)} — at the end of your current
                      billing period. No charge or credit is raised before then, and your
                      current plan stays active until that date.
                    </p>
                  )}
                </div>
                <button
                  type="button"
                  onClick={cancelPlanChange}
                  disabled={changing !== null}
                  className="shrink-0 rounded-xl border border-slate-200 px-4 py-2.5 text-sm font-medium text-slate-600 transition hover:bg-slate-50 disabled:opacity-60 dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-800"
                >
                  {changing === "cancel" ? "Cancelling…" : "Cancel change"}
                </button>
              </div>
            </Card>
          )}

          {/* Usage */}
          <div>
            <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-slate-400">Usage</h2>
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
              {(ent?.items || []).map((m) => (
                <UsageMeter key={m.label} {...m} />
              ))}
            </div>
          </div>

          {/* Payment methods. Cards ARE collected — on Stripe's hosted page, never here — so
              the old "no payment provider connected" copy was telling a paying customer the
              opposite of the truth. What is still absent is saved-card MANAGEMENT: there is no
              payment-method endpoint, so this states where card details live instead of
              rendering a list it cannot populate. */}
          <div>
            <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-slate-400">Payment Methods</h2>
            <Card padding="lg" className="flex flex-col items-center gap-2 py-10 text-center">
              <FiCreditCard className="text-2xl text-slate-300 dark:text-slate-600" />
              <p className="font-medium text-slate-700 dark:text-slate-200">Card details are held securely by Stripe</p>
              <p className="max-w-sm text-sm text-slate-500 dark:text-slate-400">
                Payment details are entered on Stripe's secure checkout page and are never stored by
                ZoikoStream. To update the card on file, contact us and we'll send a secure link.
              </p>
            </Card>
          </div>

          {/* Billing history. Stripe issues an invoice for each billing period; no endpoint
              surfaces them in the console yet, so this says where to find them rather than
              claiming none exist. */}
          <Card padding="lg" className="flex flex-col items-center gap-2 py-10 text-center">
            <FiFileText className="text-2xl text-slate-300 dark:text-slate-600" />
            <p className="font-medium text-slate-700 dark:text-slate-200">Invoices are issued each billing period</p>
            <p className="max-w-sm text-sm text-slate-500 dark:text-slate-400">
              Receipts and invoices for your subscription are emailed to your billing contact. A
              downloadable history isn't in the console yet — contact us if you need a copy.
            </p>
          </Card>

          {/* Plan comparison */}
          <div>
            <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
              <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-400">Available Plans</h2>
              {/* Rendered only when the server reports more than one purchasable cadence, so a
                  deployment whose annual prices are not yet configured shows no dead control. */}
              {intervalChoices.length > 1 && (
                <div role="group" aria-label="Billing interval"
                     className="inline-flex rounded-lg border border-slate-200 p-0.5 dark:border-slate-700">
                  {intervalChoices.map((choice) => (
                    <button
                      key={choice}
                      type="button"
                      onClick={() => setInterval(choice)}
                      aria-pressed={interval === choice}
                      className={cx(
                        "rounded-md px-3 py-1 text-xs font-medium capitalize transition",
                        interval === choice
                          ? "bg-violet-600 text-white"
                          : "text-slate-600 hover:bg-slate-50 dark:text-slate-300 dark:hover:bg-slate-800",
                      )}
                    >
                      {choice}
                    </button>
                  ))}
                </div>
              )}
            </div>
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
                    {!current && (paidSubscription && p.self_service ? (
                      // Already paying, and this plan is self-service: SCHEDULE the change
                      // rather than opening checkout. Checkout would create a second Stripe
                      // subscription and bill for both; this moves the existing one at the
                      // period boundary. The label says "Switch to" rather than "Upgrade"
                      // because nothing changes on click — a date is set.
                      <button
                        type="button"
                        onClick={() => schedulePlanChange(
                          p.slug,
                          (p.billing_intervals || []).includes(interval)
                            ? interval
                            : (p.billing_intervals || ["monthly"])[0],
                        )}
                        disabled={changing !== null || Boolean(pending)}
                        aria-label={`Schedule a change to the ${p.name} plan`}
                        title={pending
                          ? "Cancel the scheduled change first"
                          : "Takes effect at the end of your billing period"}
                        className="mt-4 inline-flex w-full items-center justify-center gap-1.5 rounded-lg border border-violet-300 px-3 py-1.5 text-xs font-medium text-violet-700 transition hover:bg-violet-50 disabled:opacity-50 dark:border-violet-800 dark:text-violet-300 dark:hover:bg-violet-950/40"
                      >
                        {changing === p.slug ? "Scheduling…" : `Switch to ${p.name}`}
                      </button>
                    ) : paidSubscription ? (
                      // Paying, but this plan is contract-priced — still a sales conversation.
                      <Link
                        to={`/contact?plan=${encodeURIComponent(p.name)}`}
                        aria-label={`Talk to an expert about the ${p.name} plan`}
                        className="mt-4 inline-flex w-full items-center justify-center gap-1.5 rounded-lg border border-slate-200 px-3 py-1.5 text-xs font-medium text-slate-600 transition hover:bg-slate-50 dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-800"
                      >
                        Talk to an expert
                      </Link>
                    ) : (
                      // ZST-COM-PLAN-001 Section 03 splits the CTA by plan type: a
                      // self-service plan gets "Start building / Upgrade", a contracted plan
                      // gets "Talk to an expert". Which is which is NOT decided here — the
                      // server sets `self_service` from whether an approved Stripe price is
                      // configured for the plan, so this only renders the decision.
                      p.self_service ? (
                        <button
                          type="button"
                          // Buy this plan on the selected cadence, falling back to whichever
                          // single cadence it offers — a plan priced monthly-only must not send
                          // an annual request just because the toggle happens to say annual.
                          onClick={() => startCheckout(
                            p.slug,
                            (p.billing_intervals || []).includes(interval)
                              ? interval
                              : (p.billing_intervals || ["monthly"])[0],
                          )}
                          disabled={checkoutFor !== null}
                          aria-label={`Upgrade to the ${p.name} plan`}
                          className="mt-4 inline-flex w-full items-center justify-center gap-1.5 rounded-lg bg-violet-600 px-3 py-1.5 text-xs font-medium text-white transition hover:bg-violet-700 disabled:opacity-60"
                        >
                          {checkoutFor === p.slug ? "Redirecting to Stripe…" : "Upgrade"}
                        </button>
                      ) : (
                        <Link
                          // Contracted / unpriced plans keep the inquiry path (Section 03
                          // "Talk to an expert"). Carries the plan so the form opens with the
                          // commercial topic chosen and the plan already named.
                          to={`/contact?plan=${encodeURIComponent(p.name)}`}
                          aria-label={`Talk to an expert about the ${p.name} plan`}
                          className="mt-4 inline-flex w-full items-center justify-center gap-1.5 rounded-lg border border-slate-200 px-3 py-1.5 text-xs font-medium text-slate-600 transition hover:bg-slate-50 dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-800"
                        >
                          Talk to an expert
                        </Link>
                      )
                    ))}
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
