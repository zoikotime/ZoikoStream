// ponytail: mock organizations — swap for GET /admin/organizations when the backend lands.
export const ORGS = [
  { name: "Zoiko Industries", plan: "Enterprise", users: 84, events: 132, status: "Active" },
  { name: "Acme Corp", plan: "Pro", users: 41, events: 58, status: "Active" },
  { name: "Globex Media", plan: "Pro", users: 27, events: 33, status: "Trial" },
  { name: "Initech", plan: "Starter", users: 12, events: 9, status: "Suspended" },
  { name: "Umbrella Co", plan: "Enterprise", users: 63, events: 91, status: "Active" },
  { name: "Hooli", plan: "Starter", users: 8, events: 4, status: "Trial" },
];

export const ORG_STATUS = {
  Active: "bg-emerald-100 text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-400",
  Trial: "bg-amber-100 text-amber-700 dark:bg-amber-500/15 dark:text-amber-400",
  Suspended: "bg-red-100 text-red-700 dark:bg-red-500/15 dark:text-red-400",
};
