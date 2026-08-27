# ZST-LE-COM-001 gap map — Phase 5 completion

Mapping of the Live Events Commercial Operating Standard against the implementation as it
stood after commit `792a612` ("Add Live Events commercial billing and Stripe hosted
Checkout"). This is the input to the Phase 5 work, not a re-audit: everything marked KEEP is
already correct and is deliberately left untouched.

Legend — **KEEP**: correct, do not modify. **FIX**: implemented but defective. **EXTEND**:
foundation exists, missing enforcement. **NEW**: absent.

---

## 1. Commercial lifecycle

| Requirement | State | Where |
|---|---|---|
| Event ↔ EventOrder relationship | KEEP | `EventOrder.event_id`, `get_current_order` (one effective version, invariant #1) |
| Commercial account ownership | KEEP | `CommercialAccount.org_id`, `get_or_create_commercial_account` |
| Purchaser vs organizer separation | KEEP | `purchaser_type`/`purchaser_id` distinct from `Event.created_by` and from seller |
| Seller legal entity binding | EXTEND | `resolve_seller_entity` fails closed at invoice time only — nothing binds it at order acceptance, so an order can be accepted and paid that can never be invoiced |
| Event risk classification | EXTEND | `risk_tier` + `elevated_risk_tier` floor exist; nothing requires the order's tier to match its service profile |
| Service profile binding | EXTEND | `service_profile_id` is nullable even for `billing_classification="commercial"` |
| Enforced lifecycle DRAFT→…→CANCELED | NEW | State is decomposed across `EventOrder.status`, `PaymentSchedule.status`, `CapacityReservation.state` and `Event.status`. No single lifecycle exists, so FINANCIAL_HOLD / CAPACITY_HELD / CONFIRMED / READY are not addressable states and transition history is unrecorded |

**Decision.** The lifecycle is added as a **derived** state plus an append-only transition
log, not as a writable column. Derivation from the existing facts is what makes it
non-bypassable — there is no setter to bypass. Replacing the decomposed dimensions would be
a rewrite of the engine and is explicitly out of scope.

## 2. Booking and capacity

| Requirement | State | Where |
|---|---|---|
| Pools, containment matching, `SELECT … FOR UPDATE` oversell guard | KEEP | `_claim_pool_capacity`, `find_capacity_pool` |
| Soft hold → hard reservation | KEEP | `soft_hold_capacity`, `hard_reserve_capacity` (promotion takes no second claim) |
| Release after cancellation | KEEP | `cancel_order` releases `soft_held` + `hard_reserved` |
| Soft hold expiry | EXTEND | `expire_stale_soft_holds` is only called from `soft_hold_capacity`; no scheduler and no operator-invokable sweep, so lapsed holds occupy pools until the next hold attempt |
| Capacity audit history | FIX | **No `audit()` call exists on any reservation transition** — hold, reserve, consume and release leave no audit trail at all. Only pool create/activate are audited |
| Payment ≠ confirmed | KEEP | `hard_reserve_capacity` consults no Payment; documented against doc B4 |
| Confirmation = payment + capacity + readiness | EXTEND | Enforced at go-live (`evaluate_readiness`) but not at `activate_order`, which checks only `status == "accepted"` |

## 3. Risk tier and service profile

| Requirement | State | Where |
|---|---|---|
| R0–R3 vocabulary + per-tier profile flags | KEEP | `RISK_TIERS`, `ServiceProfile.requires_*` |
| Profile versioning + publish | KEEP | `version_label`, `publish_service_profile` |
| Risk tier enforcement | EXTEND | Nothing requires a commercial order to carry a published profile, nor that the profile's tier matches the order's |
| Managed-only restrictions | NEW | No `managed_only` concept anywhere |
| Assured Event validation | NEW | `assured_event_eligible` is stored, exposed in schemas, and **read by no code path** |
| R2/R3 backup contribution / recording / checklist / command owner / rehearsal | EXTEND | All five flags drive `required_readiness_checks`, which is correct; but `capacity_confirmed` returns `True` when no profile is attached, so an unprofiled commercial order has no obligation at all |

## 4. Readiness and go-live

| Requirement | State | Where |
|---|---|---|
| Computed READY, never one boolean | KEEP | `evaluate_readiness` — profile checks + contributor + capacity + envelope + financial, with `NORMAL_PASS`/`EXCEPTION_APPROVED`/`BLOCKED` |
| Single gate on every production path | KEEP | `golive_block_reason` wired into `PATCH /events` and the socket `golive` handler; blocked attempts audited |
| Commercial acceptance check | EXTEND | Only checks that *an* order exists for a commercial event, not that it is accepted |
| Recording readiness | EXTEND | Covered as a manual attestation only; the profile's `requires_dual_recording` is not cross-checked against reserved capacity |
| Operational approvals | NEW | No operational-acceptance gate for R3 |

## 5. Cancellation, reschedule, change orders

| Requirement | State | Where |
|---|---|---|
| Policy-based cancellation calculation | KEEP | `find_cancellation_policy` returns None rather than guessing; `cancel_order` fails closed |
| Capacity release on cancel | KEEP | releases both holding states |
| Refund calculation | FIX | Three defects: refund is a % of `order.total_amount` rather than of money actually collected (so an unpaid order yields a positive refund); the `RefundCredit` is created with **no `source_payment_id`**, which makes `execute_refund_credit` skip the provider call entirely and mark it `executed` with no money moved; and `cancel_order` has no status guard so it can run twice and file two refunds |
| Cancellation audit history | KEEP | `commercial.order.cancel` audit entry |
| Reschedule | NEW | No model, no workflow, no capacity re-hold, no history |
| Change order approval + delta + acceptance | FIX | `accept_change_order` adds `price_delta` to `subtotal` but never inserts or removes `EventOrderLine` rows, so `sum(lines) != subtotal` afterwards and the delta has no price provenance. Also accepts straight from `draft` with no maker-checker and no exception backing a negative delta |

## 6. Finance controls

| Requirement | State | Where |
|---|---|---|
| Maker-checker (exceptions, refunds) | KEEP | `approve_commercial_exception`, `approve_refund_credit` — requester ≠ approver, enforced in CRUD |
| Price override / waiver / complimentary / exceptional cancellation / financial-hold override / risk-tier reduction | KEEP | `EXCEPTION_TYPES` + narrow scoping in `active_exception` (approved, unexpired, right order, right gate) |
| Discount approvals | NEW | No exception type; a negative change-order delta needs no approval |
| Write-off controls | NEW | `write_off` exists as an RBAC action with no endpoint and no code path |
| Refund / credit approvals | FIX | Flow is correct but `execute_refund_credit` caps against `payment.amount` without subtracting prior executed refunds, so the same payment can be refunded repeatedly |
| Requester + approver + reason + timestamp + audit on every exception | KEEP | All present on `CommercialException` |

## 7. Tax and localization

| Requirement | State | Where |
|---|---|---|
| Determination versioning + fail-closed | KEEP | NULL = undetermined; `issue_invoice` and `order_payable_amount` both refuse; zero requires an exemption reason; invoice snapshots the facts |
| No hard-coded tax | KEEP | verified — no rate or treatment literal anywhere |
| Seller entity tax mapping | EXTEND | A determination can be recorded before any seller entity is assigned, so tax is determined against an unknown seller |
| Currency validation | EXTEND | Checked against the seller entity at invoice time only — too late; the order was already accepted and paid in that currency |

## 8. Audience commerce separation

| Requirement | State | Where |
|---|---|---|
| Audience payments disabled | KEEP (implicit) | No model or route lets an audience member pay |
| Organizer commerce isolated | KEEP | Ledger 3 absent by design |
| No attendee payment settles a Zoiko invoice | KEEP (structural) | `Payment → EventOrder → Invoice(ledger="live_event")` |
| Separate routing if enabled later | EXTEND | Separation is by *absence*, with nothing asserting it stays that way. Needs an explicit guard + regression test so a future feature cannot quietly wire an audience payment into Ledger 2 |

## 9. Operations evidence

| Requirement | State | Where |
|---|---|---|
| Readiness evidence | KEEP | `ReadinessCheck` is append-only by construction (`record_readiness_check` always inserts; `evaluate_readiness` takes the latest per code) |
| Incident records | KEEP | `EventIncident` + `platform_incident_id` correlation |
| Service failures / remedies | KEEP | `propose_remedy` → pending `RefundCredit`, never auto-approved |
| Operational acceptance | NEW | No acceptance record — reuses `ReadinessCheck` with a defined code rather than a new table |
| Evidence immutability | EXTEND | `submit_dispute_evidence` merges dicts, silently overwriting prior evidence values |

## 10. Security and RBAC

| Requirement | State | Where |
|---|---|---|
| Section-25 matrix (5 actions) | KEEP | `security.commercial_can`, unscoped `super_admin` = full access (documented, preserves existing accounts) |
| Tenant isolation | KEEP | Every event/order/entitlement/refund lookup routes through `org_scoped`; webhook trusts no caller-supplied tenant |
| Finance vs Operations split | EXTEND | Most Zoiko-side routes are `require_super_admin`, so a scoped `support`/`security` staff row has the same access as `finance_ops` on capacity, invoices and reconciliation |
| Customer sees only own data | KEEP | org-scoped; draft catalogs hidden from customers |

## 11. Frontend

| Surface | State |
|---|---|
| Org event commercial view, quote acceptance, order status, payment status, checkout | KEEP — `EventCommercial.jsx` |
| Admin catalog / service profiles / cancellation policies | KEEP — `Commerce.jsx`, modals |
| Admin per-event commerce (quotes, orders, lines, capacity, readiness, incidents, invoices) | KEEP — `EventCommerceAdmin.jsx` |
| **Seller entities** | NEW — no client code. Blocks every invoice |
| **Capacity pools** | NEW — no client code. Blocks every reservation |
| **Tax determination** | NEW — no client code. Blocks checkout *and* invoicing |
| Refund approve/execute | NEW — remedies can be proposed, never approved |
| Commercial exceptions queue | NEW |
| Change orders | NEW |
| Unmatched settlements / period close | NEW |
| Lifecycle visibility | NEW |

## 12. Reconciliation

| Requirement | State | Where |
|---|---|---|
| Capacity-to-order, order-to-invoice, event-to-order controls | KEEP | `list_capacity_orphans`, `list_orders_missing_invoice`, `list_events_missing_classification` |
| Period close + exception queue | KEEP | `close_period` freezes a snapshot, files exceptions, no reopen path |
| Daily payment / unmatched settlement | FIX | `list_unmatched_settlements` queries `Payment.state == "unmatched"` — **a state no code path ever writes**. Real unmatched money lives in the `UnmatchedSettlement` table. So `GET /commercial/reconciliation` always reports zero and period close never files the exception |

## 13. Test coverage

Zero test references to: `accept_change_order`, `cancel_order`, `calculate_cancellation`,
`approve_refund_credit`, `execute_refund_credit`, `resolve_dispute`, `close_period`,
`activate_order`. Every defect above sits in one of those functions.

Well covered and not to be disturbed: order construction/acceptance, capacity oversell,
payment state machine, webhook duplication (including concurrency), tax fail-closed, tenant
isolation, go-live gating.

---

## Work order

1. Defects first (they corrupt money): refund netting → cancellation refunds → unmatched
   reconciliation → change-order lines → `activate_order` → capacity audit trail.
2. Lifecycle state machine + transition log.
3. Risk/profile/assured/managed-only enforcement, extended readiness.
4. Reschedule.
5. Finance controls (write-off, discount), tax/currency binding, audience guard.
6. RBAC action extension.
7. Frontend, blocking surfaces first.
8. Tests for every function listed in §13.

---

## Outcome

Steps 1–8 are done. What is deliberately **not** done, and why:

| Deferred | Reason |
|---|---|
| Tax determination gated on a resolved seller entity | Would refuse the determination on every order whose account has no entity assigned — including the entire existing mock-provider payment path (~90 tests) — to prevent something `issue_invoice` already blocks. Replaced with the `order_without_seller_entity` reconciliation control, so Finance sees it at period close instead of at invoice time. |
| Dispute/chargeback webhook wiring | Stripe `charge.dispute.*` remains evidence-only. The `PaymentDispute` case workflow exists and is staff-driven; wiring the webhook means deciding reserve handling and case ownership on real provider payloads, which needs a live merchant account to validate against. Still a production blocker. |
| Deposit / payment-terms registry | `PaymentSchedule` amounts are still hand-entered per order. Deriving them needs an approved deposit-percentage policy, and inventing one is the exact failure the standard prohibits. Blocked on Commercial, not on code. |
| Replay retention enforcement | `ReplayEntitlement.expires_at` is still unenforced; needs a scheduler. |
| Alembic | Schema is still `create_all` + idempotent ALTERs (`_PHASE5_STATEMENTS`). |
| Change-order / exceptions-queue / period-close UIs | Backend is complete and enforced; these three remain API-only. |
| `assert_ledger_isolation` | Written, then removed. With `Payment -> EventOrder <- Invoice` cross-ledger settlement is not expressible, so the check could never fire and would only have looked like enforcement. |
