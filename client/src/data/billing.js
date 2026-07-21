// client/src/data/billing.js
// Dummy data for the Billing page (/organization/billing).
// ponytail: mock data — swap for the billing/subscription endpoints when they land.

export const plans = [
  {
    id: "starter",
    name: "Starter",
    price: 149,
    blurb: "For small teams getting started",
    limits: { streamingHours: 50, storageGB: 100, users: 10, events: 20 },
    features: ["50 streaming hours", "100 GB storage", "10 team members", "720p streaming", "Standard support"],
  },
  {
    id: "business",
    name: "Business",
    price: 499,
    blurb: "For growing organizations",
    limits: { streamingHours: 500, storageGB: 1000, users: 50, events: 200 },
    features: ["500 streaming hours", "1 TB storage", "50 team members", "1080p streaming", "Custom branding", "Analytics dashboard", "Priority support"],
  },
  {
    id: "enterprise",
    name: "Enterprise",
    price: 1499,
    blurb: "For large-scale operations",
    limits: { streamingHours: 2500, storageGB: 5000, users: 250, events: 1000 },
    features: ["2,500 streaming hours", "5 TB storage", "250 team members", "4K streaming", "SSO & SAML", "Dedicated support", "99.9% SLA"],
  },
];

export const currentPlanId = "business";
export const renewalDate = "2026-08-14";

// Consumption this cycle — compared against the current plan's limits.
export const usage = { streamingHours: 342, storageGB: 612, users: 38, events: 147 };

export const paymentMethodsSeed = [
  { id: 1, brand: "Visa", last4: "4242", exp: "08/27", name: "Ava Chen", default: true },
  { id: 2, brand: "Mastercard", last4: "8210", exp: "11/26", name: "Zoiko Group", default: false },
];

export const invoices = [
  { id: "INV-2026-007", date: "2026-07-14", amount: 499, status: "Paid", description: "Business plan · Monthly" },
  { id: "INV-2026-006", date: "2026-06-14", amount: 499, status: "Paid", description: "Business plan · Monthly" },
  { id: "INV-2026-005", date: "2026-05-14", amount: 499, status: "Paid", description: "Business plan · Monthly" },
  { id: "INV-2026-004", date: "2026-04-14", amount: 499, status: "Paid", description: "Business plan · Monthly" },
  { id: "INV-2026-003", date: "2026-03-14", amount: 499, status: "Paid", description: "Business plan · Monthly" },
  { id: "INV-2026-002", date: "2026-02-14", amount: 149, status: "Paid", description: "Starter plan · Monthly" },
];

export const getPlan = (id) => plans.find((p) => p.id === id) || plans[0];

// 1000 GB → "1 TB", 612 → "612 GB", 1500 → "1.5 TB".
export const fmtGB = (gb) =>
  gb >= 1000 ? `${(gb / 1000) % 1 === 0 ? gb / 1000 : (gb / 1000).toFixed(1)} TB` : `${gb} GB`;
