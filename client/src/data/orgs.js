// ponytail: mock organizations — swap for GET /admin/organizations when the backend lands.
// Rows are derived deterministically from a seed (name/plan/status) so every field is
// stable across renders (no Math.random), matching the data house style.
// Status pills come from the design system: <Badge status={o.status.toLowerCase()} />.
// Health is operational (ok/warn/down); status is the account state (Active/Trial/Suspended).

const REGIONS = ["US East", "US West", "EU West", "AP South", "SA East"];

const SEED = [
  ["Zoiko Industries", "Enterprise", "Active"],
  ["Umbrella Co", "Enterprise", "Active"],
  ["Acme Corp", "Pro", "Active"],
  ["Globex Media", "Pro", "Trial"],
  ["Initech", "Starter", "Suspended"],
  ["Hooli", "Starter", "Trial"],
  ["Vertex Live", "Enterprise", "Active"],
  ["Northwind Media", "Pro", "Active"],
  ["Pulse Studios", "Starter", "Trial"],
  ["Orbit Broadcasting", "Pro", "Active"],
  ["Soylent Corp", "Pro", "Active"],
  ["Wonka Media", "Enterprise", "Active"],
  ["Stark Streaming", "Enterprise", "Active"],
  ["Wayne Broadcast", "Pro", "Active"],
  ["Cyberdyne Live", "Pro", "Trial"],
  ["Tyrell Media", "Starter", "Suspended"],
  ["Massive Dynamic", "Enterprise", "Active"],
  ["Aperture Stream", "Pro", "Active"],
  ["Black Mesa Media", "Starter", "Trial"],
  ["Oscorp Live", "Pro", "Active"],
  ["Nakatomi Broadcast", "Enterprise", "Active"],
  ["Gekko Media", "Starter", "Suspended"],
  ["Prestige Worldwide", "Pro", "Active"],
  ["Vandelay Streaming", "Starter", "Trial"],
];

export const ORGS = SEED.map(([name, plan, status], i) => {
  const usersBase = plan === "Enterprise" ? 60 : plan === "Pro" ? 25 : 6;
  const users = usersBase + ((i * 7) % 30);
  const events = Math.round(users * 1.4) + (i % 5);
  const storageGb = Math.round(users * 14 + 40);
  const health = status === "Suspended" ? "down" : status === "Trial" ? (i % 2 ? "warn" : "ok") : i % 7 === 0 ? "warn" : "ok";
  return {
    id: i + 1,
    name,
    domain: name.toLowerCase().replace(/[^a-z0-9]+/g, "") + ".com",
    plan,
    status,
    users,
    events,
    bandwidth: `${(users * 0.28).toFixed(1)} TB`,
    storage: storageGb >= 1000 ? `${(storageGb / 1000).toFixed(1)} TB` : `${storageGb} GB`,
    region: REGIONS[i % REGIONS.length],
    health,
  };
});
