export function qualifyHealthVerdict(health, verdict) {
  const notConfigured = Number(health?.not_configured || 0);
  const unmonitored = Number(health?.unmonitored || 0);
  if (!notConfigured && !unmonitored) return verdict;

  const details = [];
  if (notConfigured) details.push(`${notConfigured} not integrated`);
  if (unmonitored) details.push(`${unmonitored} not monitored`);

  if (verdict.status !== "ok") {
    return { ...verdict, label: `${verdict.label} · ${details.join(" · ")}` };
  }

  return {
    status: "unmonitored",
    label: notConfigured
      ? `Operational with limitations · ${details.join(" · ")}`
      : `Operational · ${details.join(" · ")}`,
  };
}
