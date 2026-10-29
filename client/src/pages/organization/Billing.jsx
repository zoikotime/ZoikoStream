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
import OrganizationPageHeader from "../../components/organization/OrganizationPageHeader";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import {
  FiClock, FiHardDrive, FiUsers, FiCalendar, FiCreditCard, FiCheck, FiMail, FiFileText,
} from "react-icons/fi";
import { cx, ACCENT } from "../../ui/tokens";
import api, { diagnoseLoadError, errMsg, transportMessage } from "../../api";
import { notify } from "../../ui/Toast";
import useApi from "../../hooks/useApi";
import Card from "../../ui/Card";
import Badge from "../../ui/Badge";
import Spinner from "../../ui/Spinner";
import { fmtDate } from "../../data/events";
import { SUPPORTS_IN_APP_CHECKOUT } from "../../platform";
import { onAppResume, openExternal } from "../../native/bridge";
import { visiblePlans, planCta, downgradePath, isRankedPlan, isTrialing } from "./planPresentation";

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

// What to tell someone whose Upgrade click did not reach Stripe.
//
// The backend authors real customer-facing prose for the cases it can explain — 404 "Plan not
// found", 409 "already has a paid subscription", 422 a bad cadence, 503 "Payments are not
// configured" — so those are shown as written. The exceptions are deliberate: a 502 from that
// route is `f"Payment provider error: {e}"`, which interpolates a provider exception and is the
// one place a Stripe-side detail could reach a browser; 401/403 are about the session, not the
// purchase; and no status at all means the request never left the machine.
const checkoutError = (e) => {
  const status = e?.response?.status;
  if (!status) return "Couldn't reach ZoikoStream to start checkout. Check your connection and try again.";
  if (status === 401) return "Your session has expired. Sign in again to continue.";
  if (status === 403) return "You need to be an organization owner or admin to change billing.";
  if (status === 502) return "Stripe couldn't start a checkout just now. Nothing has been charged — please try again in a moment.";
  if (status >= 500 && status !== 503) return "Something went wrong starting checkout. Nothing has been charged.";
  return errMsg(e, "Couldn't start checkout. Nothing has been charged.");
};

// A Checkout URL is a full-page navigation, so it is validated before the browser follows it.
// `assign(undefined)` walks to "<origin>/undefined" — a 404 dressed up as a redirect, with the
// purchase silently not started — and an arbitrary origin would be an open redirect out of a
// billing page.
const isStripeCheckoutUrl = (url) => {
  if (typeof url !== "string" || !url) return false;
  try {
    const u = new URL(url);
    return u.protocol === "https:"
      && (u.hostname === "checkout.stripe.com" || u.hostname.endsWith(".checkout.stripe.com"));
  } catch {
    return false;
  }
};

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
  // Guards startCheckout against a double-click; see there for why state alone is not enough.
  const checkoutInFlight = useRef(false);
  // Native only: a checkout was handed to the device browser and this page is now waiting for
  // the user to come back so it can re-read entitlements. Never set on the web, where the
  // browser navigates away and the ?session_id flow below answers instead.
  const [awaitingExternal, setAwaitingExternal] = useState(false);
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
  const [selectedPlanSlug, setSelectedPlanSlug] = useState(null);
  const intervalChoices = useMemo(() => {
    const seen = new Set();
    (plansList || []).forEach((p) => (p.billing_intervals || []).forEach((i) => seen.add(i)));
    return ["monthly", "annual"].filter((i) => seen.has(i));
  }, [plansList]);

  const startCheckout = async (planSlug, billingInterval) => {
    // Synchronous re-entry guard. `checkoutFor` also disables the button, but it is state: it
    // takes a render to apply, so two clicks dispatched in the same tick both got past it and
    // opened two Checkout Sessions. A ref is already true for the second call. The backend's
    // idempotency key makes Stripe collapse duplicates anyway; this stops them being sent.
    if (checkoutInFlight.current) return;
    checkoutInFlight.current = true;
    setCheckoutFor(planSlug);
    try {
      // The body carries a plan slug and a canonical cadence ONLY. The server resolves the
      // approved Stripe Price ID for that pair; there is deliberately no amount, currency or
      // price field a browser could tamper with.
      const { data } = await api.post("/organization/billing/checkout-session", {
        plan_slug: planSlug,
        billing_interval: billingInterval,
      });

      // Never navigate to a URL the server did not send, and never to one that is not
      // Stripe's. See isStripeCheckoutUrl.
      if (!isStripeCheckoutUrl(data?.checkout_url)) {
        setCheckoutFor(null);
        checkoutInFlight.current = false;
        notify.error("Checkout didn't start — no valid payment link was returned. "
          + "Nothing has been charged.");
        return;
      }

      if (SUPPORTS_IN_APP_CHECKOUT) {
        // Web: full-page navigation to Stripe-hosted Checkout — card details are entered on
        // Stripe's domain and never touch this origin. Stripe returns the browser here with
        // ?checkout=success&session_id=…, which the effect below reads back.
        window.location.assign(data.checkout_url);
        return;
      }

      // ── NATIVE: THE SAME PURCHASE, IN THE USER'S OWN BROWSER ──────────────────────────
      // A Custom Tab rather than this WebView, because an Android app that takes a
      // subscription payment through a third-party checkout is the shape Google Play's
      // payments policy rejects — and the cost of getting that wrong is the whole app.
      //
      // The consequence worth understanding: THIS APP NEVER SEES THE RESULT. Stripe's return
      // URL points at the web origin, so the success redirect lands in the browser, not here.
      // There is no session_id to read back and the "returned" flow above cannot run.
      //
      // That is survivable because the redirect was never what granted the subscription. A
      // signature-verified webhook binds it server-side (Section 18 forbids unlocking on the
      // redirect precisely because it is forgeable), so the app's job is only to re-read its
      // own state once the user comes back — which onAppResume does below. Until the webhook
      // lands the plan simply reads as it did before, which is the safe direction.
      await openExternal(data.checkout_url);
      setCheckoutFor(null);
      checkoutInFlight.current = false;
      setAwaitingExternal(true);
    } catch (e) {
      // Restore the button so the customer can retry — the point of failing here rather than
      // leaving them on a permanent "Redirecting…".
      setCheckoutFor(null);
      checkoutInFlight.current = false;
      notify.error(checkoutError(e));
    }
  };

  // Native only, and a no-op everywhere else: onAppResume returns an unsubscribe that does
  // nothing when there is no bridge. Refetches entitlements when the app comes back to the
  // foreground after a checkout was sent out to the browser.
  //
  // Gated on `awaitingExternal` rather than firing on every resume: a user who switches apps
  // while reading their invoices should not trigger a request each time they return.
  useEffect(() => {
    if (SUPPORTS_IN_APP_CHECKOUT || !awaitingExternal) return undefined;
    return onAppResume(() => {
      reloadOverview();
      setAwaitingExternal(false);
    });
  }, [awaitingExternal, reloadOverview]);

  // Returning from Stripe. The redirect proves only that the browser came back: it is
  // forgeable and may arrive before the webhook. Section 18 forbids unlocking on it, so the
  // real state is READ BACK from the backend and "pending" is shown until a signature-verified
  // webhook has bound the subscription.
  useEffect(() => {
    const sessionId = new URLSearchParams(window.location.search).get("session_id");
    if (returned?.state !== "checking" || !sessionId) return;
    let cancelled = false;
    api.get("/organization/billing/checkout-status", { params: { session_id: sessionId } })
      .then((r) => {
        if (cancelled) return;
        setReturned(r.data);
        // "confirmed" now means ENTITLED, not merely "an id was written", so this reload
        // genuinely has a new plan to show. Re-reading on "pending" would refetch the same
        // unchanged state.
        if (r.data?.state === "confirmed") reloadOverview?.();
      })
      // A failed read means "not confirmed yet", never "confirmed" — the safe direction.
      .catch(() => { if (!cancelled) setReturned({ state: "pending" }); });
    return () => { cancelled = true; };
    // Runs once for the initial "checking" state; setReturned then moves it out of that state.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const ent = overview?.entitlements;
  // Tiers the catalog no longer offers. The backend already excludes them from
  // GET /organization/plans; filtered again here so the RENDERED data is right even against a
  // server that predates that change — and so "only three plans are offered" is a property of
  // this component, testable without a backend.
  //
  // A filter, not CSS: a hidden card is still in the DOM, still in the grid's flow, and still
  // reachable by anything that reads the page.
  // Plans this page OFFERS, in tier order. `visiblePlans` drops the retired tiers
  // (starter/pro) EXCEPT when one is the organization's own current plan — hiding the tier a
  // customer is actually paying for would leave them looking at three cards, none of them
  // theirs. Distinct from `plansList`, which stays the whole served catalog and still backs
  // `currentPlan` below.
  const offeredPlans = useMemo(
    () => visiblePlans(plansList, ent?.plan_slug),
    [plansList, ent]
  );

  const currentPlan = useMemo(
    () => plansList?.find((p) => p.slug === ent?.plan_slug),
    [plansList, ent]
  );

  // Authoritative selected plan for comparison/upgrade:
  // Defaults to the user's current subscribed plan if known and offered, or the first selectable tier.
  const activeSelection = useMemo(() => {
    if (selectedPlanSlug) return selectedPlanSlug;
    const currentSlug = ent?.plan_slug;
    if (currentSlug && offeredPlans.some((p) => p.slug === currentSlug)) {
      return currentSlug;
    }
    const firstSelectable = offeredPlans.find((p) => p.pricing_state === "PUBLISHED" && p.self_service !== false);
    return firstSelectable?.slug || null;
  }, [selectedPlanSlug, ent?.plan_slug, offeredPlans]);
  // Does this organization already have a LIVE PAID subscription? A trial is not paid — that
  // is the self-service purchase path and must keep its Upgrade button. `active`/`past_due`
  // mean money is already moving, and the backend refuses a second checkout for exactly those
  // (it would create a second Stripe subscription and bill twice), so the CTA must not offer
  // one. The distinction comes from the backend's Section 12 status, not from anything the
  // page decides for itself. `trial` is the pre-Section-12 spelling still present on old rows.
  // Would a new checkout create a SECOND paid subscription? The backend's own answer
  // (`has_live_provider_subscription`, the predicate behind the checkout endpoint's 409).
  //
  // It used to BE the status list below, and that is what produced the bug: a subscription that
  // has completed Stripe checkout but has not been activated sits in `conversion_pending`, which
  // is in none of those three — so the page believed the tenant had no subscription, rendered
  // "Upgrade" everywhere, and every click came back 409. The `??` keeps an older backend on the
  // previous behaviour rather than treating a paying tenant as unsubscribed, which is the
  // dangerous direction; `conversion_pending` is added to that fallback for the same reason.
  const checkoutLocked = ent?.checkout_locked
    ?? ["active", "past_due", "plan_change_scheduled", "conversion_pending"].includes(ent?.status);
  // Retained name for the plan-change branches below: an org whose provider subscription is
  // live is exactly the one that must be MOVED rather than re-bought.
  const paidSubscription = checkoutLocked;
  // A live subscription the hierarchy cannot place: locked, with no entitled `plan_slug`.
  // Either a conversion Stripe has not confirmed, or a plan outside the known tiers.
  // Asked through planPresentation's OWN predicate, so the explanation cannot disappear while
  // the lock stays on. It previously tested `!ent.plan_slug`, which is narrower than the
  // condition planCta actually locks on: a live subscription on a plan slug outside the five
  // ranked tiers locked every card to "Talk to an expert" with nothing on the page saying so.
  const unmappedSubscription = checkoutLocked && !isRankedPlan(ent?.plan_slug);
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

  // ── Resolve an unidentified subscription against Stripe, once ──────────────────────────
  // `unmappedSubscription` is what a missed `customer.subscription.created` looks like from
  // here. Nothing local can resolve it — the missing fact is at Stripe — so the console asks
  // for it instead of showing "confirming…" forever. The endpoint only READS from Stripe and
  // applies the answer through the Section 12 state machine; it cannot create a subscription.
  //
  // Attempted once automatically, tracked by a ref so a re-render from the reload cannot start
  // a second attempt. `syncError` holds the real reason when it fails.
  //
  // THE ATTEMPT IS RETRYABLE, which it was not. The ref was set before the request and never
  // cleared, so ONE failure ended the repair for the life of the mount — and because the lock
  // that this repair exists to lift is what makes every plan card read "Talk to an expert"
  // instead of "Upgrade", a single dropped request left the whole page inert behind a banner
  // promising it would "clear shortly". It could not clear. There was no control to retry it
  // and no path back except reloading the browser.
  //
  // A transport failure is told apart from Stripe's own answer. "We could not reach the
  // server" is a statement about one request; "Stripe has no record of this subscription" is a
  // statement about the account, and only the second deserves the standing banner. The first
  // gets a Retry, because trying again is the entire remedy.
  const syncAttempted = useRef(false);
  const [syncError, setSyncError] = useState(null);
  const [syncUnreachable, setSyncUnreachable] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [syncAttempt, setSyncAttempt] = useState(0);
  const retrySync = useCallback(() => {
    syncAttempted.current = false;
    setSyncAttempt((n) => n + 1);
  }, []);
  useEffect(() => {
    if (!unmappedSubscription || syncAttempted.current) return undefined;
    syncAttempted.current = true;
    let cancelled = false;
    setSyncing(true);
    setSyncError(null);
    setSyncUnreachable(false);
    api.post("/organization/billing/sync")
      .then(async ({ data }) => {
        if (cancelled) return;
        if (data?.error) setSyncError(data.error);
        if (data?.synced) await reloadOverview?.();
      })
      .catch((e) => {
        if (cancelled) return;
        // No response means the repair never ran, so the ref is released and the banner
        // offers a retry rather than presenting a transport fault as an account problem.
        if (transportMessage(e)) setSyncUnreachable(true);
        setSyncError(errMsg(e));
      })
      .finally(() => { if (!cancelled) setSyncing(false); });
    return () => { cancelled = true; };
  }, [unmappedSubscription, reloadOverview, syncAttempt]);

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
      <OrganizationPageHeader
        breadcrumbs={[["Organization", "/organization/manage"], ["Plan, usage & billing"]]}
        eyebrow="Organization console / Plan, usage & billing"
        title="Billing"
        subtitle="Your subscription and usage"
      />

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
        /* Says WHY, and offers the one action that can help.
           "Try refreshing the page" was advice that could not work for any of the real
           causes: an expired session, a restricted organization, or — the one actually seen —
           a dev client pointed at a port with no API behind it. Refreshing repeats the failure
           and names nothing. diagnoseLoadError already distinguishes those cases for the admin
           console; billing deserves the same, because the fix differs completely between them. */
        <div
          role="alert"
          className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-rose-200 bg-rose-50 px-5 py-4 text-sm text-rose-700 dark:border-rose-500/20 dark:bg-rose-500/10 dark:text-rose-300"
        >
          <span>{diagnoseLoadError(overviewError, "/organization/overview")}</span>
          <button
            type="button"
            onClick={reloadOverview}
            className="shrink-0 rounded-lg border border-rose-300 px-3 py-1.5 text-xs font-semibold transition hover:bg-rose-100 dark:border-rose-500/40 dark:hover:bg-rose-500/10"
          >
            Retry
          </button>
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
                {/* The tier's own name, exactly as the catalog below and the server spell it
                    ("Developer", never "Developer Plan" on one card and "Developer" on the
                    next). Where the word "plan" is needed it is the label, not the name. */}
                <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
                  Current plan
                </p>
                <div className="flex flex-wrap items-center gap-3">
                  <h2 className="text-xl font-bold text-slate-900 dark:text-white">
                    {ent?.plan || "No plan"}
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
                  {/* A trial ENDS; a paid subscription RENEWS. Each state reads its own date,
                      with no fallback between them.

                      `trial_ends_at || current_period_end` was the other half of the defect:
                      a trialing subscription with no recorded trial end would silently borrow
                      the period end and present it as a renewal — a date the customer never
                      agreed to, under a word that implies they had. When a trial has no end
                      date on record the honest answer is to say it is a trial and name no
                      date, which is what the absent branch below does. */}
                  {(() => {
                    const trialing = isTrialing(ent?.status);
                    const when = trialing ? ent?.trial_ends_at : ent?.current_period_end;
                    if (!when) {
                      // Only worth saying for a trial: "no renewal date recorded" on a paid
                      // plan is a backend gap, and inventing a line about it here would hide
                      // it rather than report it.
                      return trialing ? (
                        <span className="inline-flex items-center gap-2 text-sm text-slate-500 dark:text-slate-400">
                          <FiCalendar /> In trial
                        </span>
                      ) : null;
                    }
                    return (
                      <span className="inline-flex items-center gap-2 text-sm text-slate-500 dark:text-slate-400">
                        <FiCalendar />
                        {trialing ? "Trial ends" : "Renews"} on {fmtDate(when)}
                      </span>
                    );
                  })()}
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
            {/* A live subscription this page cannot place. Shown instead of silently rendering
                three purchasable cards, which is what previously sent people into a 409 they
                could not act on. Names no Stripe identifier: the customer-facing fact is "you
                have one and we are confirming it"; the id is for the audit log. */}
            {unmappedSubscription && (
              <div
                role="status"
                className="mb-3 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-300"
              >
                <strong className="font-semibold">Current subscription detected.</strong>{" "}
                {syncUnreachable
                  // A request that got no answer. It says so, and offers the only thing that
                  // can help, rather than implying the subscription itself is in trouble.
                  ? <>{syncError}{" "}Plan changes are paused until we can confirm it, and
                      nothing further will be charged.{" "}
                      <button
                        type="button"
                        onClick={retrySync}
                        disabled={syncing}
                        className="font-semibold underline underline-offset-2 disabled:opacity-60"
                      >
                        {syncing ? "Retrying…" : "Try again"}
                      </button>{" "}or{" "}</>
                  : syncError
                    ? <>{syncError}{" "}Plan changes are paused and nothing further will be
                        charged.{" "}</>
                    : ent?.plan_slug
                      // A real, entitled plan that is simply not one of the tiers this page
                      // can move between. Nothing is wrong and nothing is pending, so it must
                      // not claim to be "synchronizing" — it says what is true: the plan is
                      // known, and changing it is a conversation rather than a button.
                      ? <>You&apos;re on <strong>{ent.plan || ent.plan_slug}</strong>, which
                          isn&apos;t one of the plans that can be changed from this page.
                          Nothing further will be charged.{" "}</>
                      : <>We&apos;re synchronizing it with Stripe, so plan changes are paused for
                          the moment. Nothing further will be charged.{" "}</>}
                <Link to="/contact" className="underline underline-offset-2">Contact us</Link>{" "}
                if this doesn&apos;t clear shortly.
              </div>
            )}
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
              {offeredPlans.map((p) => {
                // Decided from the BACKEND's current plan slug and the plan's server-set
                // `self_service`, never from the price shown on the card.
                const cta = planCta(p, ent?.plan_slug, { checkoutLocked });
                const current = cta.kind === "current";
                const isSelectable = p.pricing_state === "PUBLISHED" && p.self_service !== false;
                const isSelected = activeSelection === p.slug;
                return (
                  <div
                    key={p.id}
                    data-plan-slug={p.slug}
                    data-selected={isSelected ? "true" : "false"}
                    data-current={current ? "true" : "false"}
                    tabIndex={isSelectable ? 0 : undefined}
                    onClick={() => {
                      if (isSelectable) setSelectedPlanSlug(p.slug);
                    }}
                    onKeyDown={(e) => {
                      if (isSelectable && (e.key === "Enter" || e.key === " ")) {
                        e.preventDefault();
                        setSelectedPlanSlug(p.slug);
                      }
                    }}
                    className={cx(
                      "flex flex-col rounded-2xl border p-4 transition-colors duration-150 motion-reduce:transition-none",
                      isSelectable && "cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-400 focus-visible:ring-offset-2 dark:focus-visible:ring-offset-slate-900",
                      isSelected
                        ? "border-violet-500 ring-1 ring-violet-500/30"
                        : "border-slate-200 hover:border-slate-300 dark:border-slate-700 dark:hover:border-slate-600"
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
                    {/* CTA, one branch per planCta() outcome.
                        Current  : disabled, nothing to buy.
                        Buy      : a FIRST purchase. Always checkout, never the plan-change
                                   endpoint — there is no subscription to change. This used to
                                   render "Upgrade", and the click was answered with "This
                                   organization has no subscription record to upgrade".
                        Upgrade  : the EXISTING Stripe paths, still split by whether money is
                                   already moving — an org with a live paid subscription must
                                   not be sent to checkout, which would create a SECOND Stripe
                                   subscription; it schedules the move on the existing one.
                        Downgrade: a link to the contact form. No endpoint is called.
                        Contact  : quote-led (Enterprise) or unrankable. */}
                    {cta.kind === "current" ? (
                      /* Deliberately NON-ACTIONABLE, and that is the product rule, not an
                         omission: the organization is already on this plan, so there is
                         nothing to buy, schedule or cancel from here. It carries no onClick
                         at all, which is what makes an accidental second subscription
                         structurally impossible rather than merely guarded against.

                         `disabled` is the real HTML attribute, not a CSS imitation: it takes
                         the control out of the tab order and makes assistive technology
                         announce it as unavailable. pointer-events:none would hide the
                         button from a mouse while leaving a keyboard user able to activate
                         it, which is the wrong half of the problem to solve.

                         What WAS wrong is the signal. `disabled:opacity-100` cancels the
                         usual dimming — correct, because fading the plan somebody is paying
                         for would read as "deactivated" — but combined with a neutral
                         cursor the control looked like a live CTA, so people clicked it and
                         nothing happened. It now says why on hover, and the cursor says it
                         before the tooltip arrives. The tinted, outlined treatment is also
                         what separates it from the solid-violet Buy/Upgrade CTAs. */
                      <button
                        type="button"
                        disabled
                        aria-current="true"
                        aria-label={`${p.name} is your current plan`}
                        title={`You're on ${p.name}. There's nothing to do here — use another card to change plan.`}
                        className="mt-4 inline-flex w-full cursor-not-allowed items-center justify-center gap-1.5 rounded-lg border border-violet-300 bg-violet-50 px-3 py-1.5 text-xs font-semibold text-violet-700 disabled:opacity-100 dark:border-violet-800 dark:bg-violet-950/40 dark:text-violet-300"
                      >
                        Current Plan
                      </button>
                    ) : cta.kind === "buy" ? (
                      <button
                        type="button"
                        onClick={() => startCheckout(
                          p.slug,
                          (p.billing_intervals || []).includes(interval)
                            ? interval
                            : (p.billing_intervals || ["monthly"])[0],
                        )}
                        disabled={checkoutFor !== null}
                        aria-label={`Buy the ${p.name} plan`}
                        className="mt-4 inline-flex w-full items-center justify-center gap-1.5 rounded-lg bg-violet-600 px-3 py-1.5 text-xs font-medium text-white transition hover:bg-violet-700 disabled:opacity-60"
                      >
                        {checkoutFor === p.slug ? "Redirecting to Stripe…" : "Buy Plan"}
                      </button>
                    ) : cta.kind === "upgrade" ? (
                      <button
                        type="button"
                        onClick={() => {
                          const billingInterval = (p.billing_intervals || []).includes(interval)
                            ? interval
                            : (p.billing_intervals || ["monthly"])[0];
                          return paidSubscription
                            ? schedulePlanChange(p.slug, billingInterval)
                            : startCheckout(p.slug, billingInterval);
                        }}
                        disabled={paidSubscription
                          ? (changing !== null || Boolean(pending))
                          : checkoutFor !== null}
                        aria-label={`Upgrade to the ${p.name} plan`}
                        title={paidSubscription
                          ? (pending
                              ? "Cancel the scheduled change first"
                              : "Takes effect at the end of your billing period")
                          : undefined}
                        className="mt-4 inline-flex w-full items-center justify-center gap-1.5 rounded-lg bg-violet-600 px-3 py-1.5 text-xs font-medium text-white transition hover:bg-violet-700 disabled:opacity-60"
                      >
                        {checkoutFor === p.slug
                          ? "Redirecting to Stripe…"
                          : changing === p.slug ? "Scheduling…" : "Upgrade"}
                      </button>
                    ) : cta.kind === "downgrade" ? (
                      /* A move DOWN is the same scheduled plan change as a move up, and the
                         backend has always treated it that way: crud.admin.request_plan_change
                         validates the cadence, the plan's availability and its approved price,
                         and schedules it at `current_period_end` — it has no notion of
                         direction at all. Only this card insisted a downgrade was a sales
                         conversation, so a customer on Business could schedule their way UP
                         self-service but had to email somebody to come back down.

                         Still routed to a human when there is nothing to schedule: a plan with
                         no approved price (Enterprise) cannot be moved to by this endpoint,
                         and an organization with no paid subscription has no period end to
                         schedule against. Both keep the contact form. */
                      paidSubscription && p.self_service ? (
                        <button
                          type="button"
                          onClick={() => {
                            const billingInterval = (p.billing_intervals || []).includes(interval)
                              ? interval
                              : (p.billing_intervals || ["monthly"])[0];
                            // The confirmation carries the one fact that makes this safe to
                            // click: nothing changes today. Same convention the rest of the
                            // console uses for a consequential action.
                            const when = ent?.current_period_end
                              ? ` on ${fmtDate(ent.current_period_end)}`
                              : " at the end of your current billing period";
                            if (!window.confirm(
                              `Move to ${p.name}${when}?\n\n`
                              + `Your current plan stays active until then, and nothing is `
                              + `charged or credited now. You can cancel the change at any `
                              + `time before it takes effect.`
                            )) return;
                            return schedulePlanChange(p.slug, billingInterval);
                          }}
                          disabled={changing !== null || Boolean(pending)}
                          aria-label={`Move to the ${p.name} plan at the end of your billing period`}
                          title={pending
                            ? "Cancel the scheduled change first"
                            : "Takes effect at the end of your billing period"}
                          className="mt-4 inline-flex w-full items-center justify-center gap-1.5 rounded-lg border border-slate-200 px-3 py-1.5 text-xs font-medium text-slate-600 transition hover:bg-slate-50 disabled:opacity-60 dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-800"
                        >
                          {changing === p.slug ? "Scheduling…" : "Downgrade"}
                        </button>
                      ) : (
                        <Link
                          to={downgradePath(p)}
                          aria-label={`Contact us to downgrade to the ${p.name} plan`}
                          className="mt-4 inline-flex w-full items-center justify-center gap-1.5 rounded-lg border border-slate-200 px-3 py-1.5 text-xs font-medium text-slate-600 transition hover:bg-slate-50 dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-800"
                        >
                          Contact us to downgrade
                        </Link>
                      )
                    ) : (
                      <Link
                        to={`/contact?plan=${encodeURIComponent(p.name)}`}
                        aria-label={`Talk to an expert about the ${p.name} plan`}
                        className="mt-4 inline-flex w-full items-center justify-center gap-1.5 rounded-lg border border-slate-200 px-3 py-1.5 text-xs font-medium text-slate-600 transition hover:bg-slate-50 dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-800"
                      >
                        Talk to an expert
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
