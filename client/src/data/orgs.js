// ponytail: mock organizations — swap for GET /admin/organizations when the backend lands.
export const ORGS = [
  { name: "Zoiko Industries", plan: "Enterprise", users: 84, events: 132, status: "Active" },
  { name: "Acme Corp", plan: "Pro", users: 41, events: 58, status: "Active" },
  { name: "Globex Media", plan: "Pro", users: 27, events: 33, status: "Trial" },
  { name: "Initech", plan: "Starter", users: 12, events: 9, status: "Suspended" },
  { name: "Umbrella Co", plan: "Enterprise", users: 63, events: 91, status: "Active" },
  { name: "Hooli", plan: "Starter", users: 8, events: 4, status: "Trial" },
];

// Status pills now come from the design system: <Badge status={o.status.toLowerCase()} />.
