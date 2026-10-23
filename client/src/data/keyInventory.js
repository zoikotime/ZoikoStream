// The API-credentials figures from GET /organization/developer (the Developers page's KPI tile
// and readiness step). The server's own counts (`key_summary`) when it sends them; otherwise
// derived from the key list by the same rule — a key is active unless it is revoked or past its
// `expires_at` (`usable` when the server says so per key). An API that does not send the summary
// therefore still yields finished figures, never a tile stuck on "Loading…". Null only when the
// key list itself is missing: unreadable, which is not the same as zero.
export function keyInventory(data, now = Date.now()) {
  if (data?.key_summary) return data.key_summary;
  if (!Array.isArray(data?.api_keys)) return null;
  const active = (k) =>
    typeof k.usable === "boolean" ? k.usable : !k.revoked && !(k.expires_at && Date.parse(k.expires_at) <= now);
  return { total: data.api_keys.length, active: data.api_keys.filter(active).length };
}
