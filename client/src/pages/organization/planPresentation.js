// Which plans the organization Billing page offers, and what each card's call-to-action is.
//
// Pure functions, deliberately separated from Billing.jsx: the CTA matrix is a commercial
// rule with a dozen cases, and pinning it needs a table test rather than a dozen renders.
// Nothing here talks to the network or reads a price — it maps backend-reported state onto
// a label and an intent, and Billing.jsx renders that intent.
//
// THE CURRENT PLAN IS ALWAYS THE BACKEND'S `entitlements.plan_slug` (services/org.py), never
// inferred from price text, card order or what is published. This module only ever compares
// slugs.

// Retired tiers, hidden from plan SELECTION. They are still real rows in the plans table and
// still real subscriptions — migrate_plan_names.py renames starter -> developer and
// pro -> business, and until that has run against a deployment both spellings are served by
// GET /organization/plans. Hiding them here stops the console offering a tier nobody should
// buy; it deletes nothing and migrates nobody. See visiblePlans() for the one case where a
// retired plan is still shown.
export const RETIRED_PLAN_SLUGS = ["starter", "pro"];

// Display hierarchy: Developer -> Business -> Enterprise. The retired slugs carry the rank of
// the tier they become, which is the same equivalence migrate_plan_names.py encodes
// (starter IS developer, pro IS business) rather than a second, conflicting judgement.
export const PLAN_RANK = {
  developer: 1,
  business: 2,
  enterprise: 3,
  starter: 1,
  pro: 2,
};

/** Plans to render in Available Plans, in hierarchy order.
 *
 *  A retired plan is dropped UNLESS it is the organization's own current plan. Hiding the tier
 *  a customer is actually paying for would leave them looking at three cards, none of them
 *  theirs, with no indication which one they are on — so their card stays, marked Current, and
 *  they keep an honest view of what they have. Nobody else is ever offered it.
 */
export function visiblePlans(plans, currentSlug) {
  return (plans || [])
    .filter((p) => !RETIRED_PLAN_SLUGS.includes(p.slug) || p.slug === currentSlug)
    .slice()
    .sort((a, b) => (PLAN_RANK[a.slug] ?? 99) - (PLAN_RANK[b.slug] ?? 99));
}

/** What this plan's card should offer, given the organization's actual subscription.
 *
 *  Returns one of:
 *    { kind: "current"   } — this is the live plan; the button is disabled
 *    { kind: "upgrade"   } — a higher, self-service tier; keeps the existing Stripe path
 *    { kind: "downgrade" } — a lower tier; a sales conversation, never a browser-side change
 *    { kind: "contact"   } — quote-led (Enterprise), or a tier this page cannot rank
 *
 *  `checkoutLocked` is the SERVER's answer to "would a new checkout create a SECOND paid
 *  subscription" — the same predicate the checkout endpoint's 409 guard uses. It is what keeps
 *  this page from offering a button whose only possible outcome is that refusal.
 *
 *  `self_service` is the SERVER's answer to "is an approved Stripe price configured"
 *  (routers/organization.py::list_plans). It is never overridden here, so a plan Finance has
 *  not priced can never render a purchase button no matter where it sits in the hierarchy.
 */
export function planCta(plan, currentSlug, { checkoutLocked = false } = {}) {
  if (plan.slug === currentSlug) return { kind: "current" };

  const currentRank = PLAN_RANK[currentSlug];
  const targetRank = PLAN_RANK[plan.slug];

  // No entitled plan, or a slug this page does not know.
  if (currentRank === undefined || targetRank === undefined) {
    // `checkoutLocked` is the BACKEND's answer to "is Stripe already billing this tenant"
    // (entitlements.checkout_locked ← has_live_provider_subscription). When it is true there
    // is a live subscription that this page cannot place in the hierarchy — a completed but
    // not-yet-activated conversion, or a plan outside Developer/Business/Enterprise. Offering
    // "Upgrade" there is exactly the bug: checkout answers 409 every time, because succeeding
    // would mean a second subscription and a second charge. So nothing is purchasable and the
    // tier is not guessed at; the conversation goes to a human.
    if (checkoutLocked) return { kind: "contact" };
    // Genuinely no live subscription: the ordinary purchase path.
    return plan.self_service ? { kind: "upgrade" } : { kind: "contact" };
  }

  if (targetRank < currentRank) return { kind: "downgrade" };
  // Equal rank across different slugs means a retired tier and its replacement (pro/business).
  // That is neither an upgrade nor a downgrade, so it goes to sales rather than to checkout.
  if (targetRank === currentRank) return { kind: "contact" };

  // Higher tier. Enterprise lands here too and is contract-priced, so self_service — not the
  // rank — decides whether there is anything to buy.
  return plan.self_service ? { kind: "upgrade" } : { kind: "contact" };
}

/** Where a downgrade goes: the existing public contact form, with the plan already named.
 *  Downgrades are never executed from the browser — there is no endpoint for it and Section 12
 *  requires an approved path — so this is a route, not a mutation.
 */
export function downgradePath(plan) {
  return `/contact?plan=${encodeURIComponent(plan.name)}&action=downgrade`;
}
