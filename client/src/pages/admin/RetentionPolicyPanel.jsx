import { useState } from "react";
import { Badge, Button, Panel } from "../../components/admin";
import useElevation from "../../components/admin/useElevation";
import { Input, Label, Textarea } from "../../ui/forms";
import { notify } from "../../ui/Toast";
import api, { errMsg } from "../../api";
import useApi from "../../hooks/useApi";
import { useAuth } from "../../auth/AuthContext";

// Media retention policy — the governed, maker-checker workflow (GET/POST
// /admin/retention-policy…, services/media_retention.propose/approve/reject_policy).
//
// The retention policy is a KEEP-GUARANTEE: the promise that a recording is not deleted before
// a date. It used to be writable through the generic settings PATCH, where one request could
// set it to zero. That path is closed; this is the only one:
//   propose  — one person, with a reason; the EFFECTIVE policy does not move
//   approve  — a DIFFERENT person; only then does it change
//   reject   — the proposal is discarded and nothing changes
// The server enforces every rule (bounds, distinct approver, one pending at a time). This panel
// mirrors them so a control is not offered that can only be refused — and still reports the
// server's refusal when, for example, an elevation lapses between render and click.

const fmt = (iso) => (iso ? new Date(iso).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" }) : "—");

// "" stays "" so an emptied field is a validation error, never silently 0 (Number("") is 0).
const asInt = (v) => (v === "" || v == null ? null : Number(v));

function ElevationNote({ elevation }) {
  if (elevation.status === "active") return null;
  const msg = {
    unknown: "Your elevation state could not be read, so changes may be refused.",
    expired: "Your platform elevation has expired.",
    wrong_scope: "Your current elevation does not cover platform configuration.",
    none: "Changing the retention policy needs platform elevation.",
  }[elevation.status];
  return (
    <div className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800 dark:border-amber-500/25 dark:bg-amber-500/10 dark:text-amber-200">
      <span>{msg}</span>
      {elevation.status !== "unknown" && (
        <Button
          size="sm"
          variant="secondary"
          loading={elevation.elevating}
          onClick={async () => {
            try {
              const ok = await elevation.elevate("Govern the media retention policy");
              if (!ok) notify.error("You hold a different elevation; end it from the rail, then elevate for platform.");
            } catch (e) {
              notify.error(errMsg(e));
            }
          }}
        >
          Elevate for platform
        </Button>
      )}
    </div>
  );
}

export default function RetentionPolicyPanel() {
  const { user } = useAuth();
  const elevation = useElevation("platform");
  const { data, loading, error, reload } = useApi(() => api.get("/admin/retention-policy").then((r) => r.data));
  const [form, setForm] = useState({ retention_days: "", warning_days: "", reason: "" });
  const [rejectReason, setRejectReason] = useState("");
  const [busy, setBusy] = useState(null);

  const run = async (key, fn, done) => {
    setBusy(key);
    try {
      await fn();
      notify.success(done);
      reload();
      return true;
    } catch (e) {
      notify.error(errMsg(e));
      return false;
    } finally {
      setBusy(null);
    }
  };

  if (error) {
    return (
      <Panel eyebrow="Governance" title="Media retention policy">
        <div className="flex items-center justify-between gap-3 text-sm text-rose-700 dark:text-rose-300" role="alert">
          <span>Couldn&apos;t load the retention policy.</span>
          <Button variant="secondary" size="sm" onClick={reload}>Retry</Button>
        </div>
      </Panel>
    );
  }
  if (loading || !data) {
    return (
      <Panel eyebrow="Governance" title="Media retention policy">
        <div className="zk-skeleton h-16 rounded bg-slate-200 dark:bg-slate-800" />
      </Panel>
    );
  }

  const { effective, proposal, bounds, impact } = data;
  const mineProposal = Boolean(proposal && user?.id && proposal.requested_by === String(user.id));
  const rd = asInt(form.retention_days);
  const wd = asInt(form.warning_days);
  // Mirrors services/media_retention.validate_policy_values. The server re-checks.
  const problems = [];
  if (rd == null || !Number.isInteger(rd)) problems.push("Retention days must be a whole number.");
  else if (rd < bounds.min_retention_days || rd > bounds.max_retention_days) {
    problems.push(`Retention must be between ${bounds.min_retention_days} and ${bounds.max_retention_days} days.`);
  }
  if (wd == null || !Number.isInteger(wd)) problems.push("Warning days must be a whole number.");
  else if (wd < bounds.min_warning_days) problems.push(`Warning must be at least ${bounds.min_warning_days} day.`);
  else if (rd != null && wd >= rd) problems.push("The warning must come before the retention period ends.");
  if (form.reason.trim().length < 10) problems.push("Give a reason of at least 10 characters.");
  if (rd === Number(effective.retention_days) && wd === Number(effective.warning_days)) {
    problems.push("That is the policy already in effect.");
  }
  const touched = form.retention_days !== "" || form.warning_days !== "" || form.reason !== "";

  return (
    <Panel
      eyebrow="Governance"
      title="Media retention policy"
      description="The minimum time a recording is kept before it may be deleted. Changes need a second Super Admin's approval."
    >
      <div className="space-y-4" data-testid="retention-panel">
        <ElevationNote elevation={elevation} />

        <dl className="grid gap-3 text-sm sm:grid-cols-4">
          <div><dt className="text-xs text-slate-500">In effect</dt><dd className="font-medium text-slate-800 dark:text-slate-100" data-testid="retention-effective">{effective.retention_days} days</dd></div>
          <div><dt className="text-xs text-slate-500">Deletion warning</dt><dd className="font-medium text-slate-800 dark:text-slate-100">{effective.warning_days} days before</dd></div>
          <div><dt className="text-xs text-slate-500">Version</dt><dd className="font-mono text-[12px] text-slate-700 dark:text-slate-200">{effective.version}</dd></div>
          <div>
            <dt className="text-xs text-slate-500">Effective since</dt>
            <dd className="text-slate-700 dark:text-slate-200">
              {effective.effective_at ? fmt(effective.effective_at) : "Built-in default"}
              {effective.approved_by_email && <span className="block text-[11px] text-slate-400">approved by {effective.approved_by_email}</span>}
            </dd>
          </div>
        </dl>

        {proposal ? (
          <div className="space-y-3 rounded-lg border border-violet-200 bg-violet-50/60 p-4 dark:border-violet-500/25 dark:bg-violet-500/10" data-testid="retention-proposal">
            <div className="flex flex-wrap items-center gap-2">
              <Badge tone="warning">Pending approval</Badge>
              <span className="text-sm text-slate-700 dark:text-slate-200">
                {proposal.previous?.retention_days ?? effective.retention_days} → <strong>{proposal.retention_days}</strong> days ·
                warning {proposal.previous?.warning_days ?? effective.warning_days} → <strong>{proposal.warning_days}</strong> days
              </span>
            </div>
            <p className="text-xs text-slate-600 dark:text-slate-300">
              Proposed by {proposal.requested_by_email || "an unrecorded requester"} · {fmt(proposal.requested_at)}
            </p>
            <p className="text-sm text-slate-700 dark:text-slate-200">&ldquo;{proposal.reason}&rdquo;</p>
            <p className="text-xs text-slate-500 dark:text-slate-400">{impact} The policy above stays in effect until this is approved.</p>
            {mineProposal && (
              <p className="text-xs font-medium text-slate-600 dark:text-slate-300" data-testid="retention-own-proposal">
                You proposed this change, so a different Super Admin must approve it. You can still reject (withdraw) it.
              </p>
            )}
            <div className="flex flex-wrap items-end gap-2">
              {!mineProposal && (
                <Button
                  size="sm"
                  loading={busy === "approve"}
                  disabled={elevation.status !== "active" || Boolean(busy)}
                  onClick={() => run("approve", () => api.post("/admin/retention-policy/proposals/approve"), "Retention policy change approved and in effect")}
                >
                  Approve change
                </Button>
              )}
              <div className="min-w-[220px] flex-1">
                <Label variant="console">Rejection note (optional)</Label>
                <Input variant="console" value={rejectReason} onChange={(e) => setRejectReason(e.target.value)} />
              </div>
              <Button
                size="sm"
                variant="secondary"
                loading={busy === "reject"}
                disabled={elevation.status !== "active" || Boolean(busy)}
                onClick={async () => {
                  const ok = await run("reject", () => api.post("/admin/retention-policy/proposals/reject", { reason: rejectReason || null }), "Proposal rejected — policy unchanged");
                  if (ok) setRejectReason("");
                }}
              >
                Reject
              </Button>
            </div>
          </div>
        ) : (
          <div className="space-y-3">
            <div className="grid gap-4 sm:grid-cols-2">
              <div>
                <Label variant="console">Proposed retention (days, {bounds.min_retention_days}–{bounds.max_retention_days})</Label>
                <Input variant="console" type="number" min={bounds.min_retention_days} max={bounds.max_retention_days} step={1}
                  value={form.retention_days} placeholder={String(effective.retention_days)}
                  onChange={(e) => setForm((f) => ({ ...f, retention_days: e.target.value }))} />
              </div>
              <div>
                <Label variant="console">Proposed deletion warning (days before)</Label>
                <Input variant="console" type="number" min={bounds.min_warning_days} step={1}
                  value={form.warning_days} placeholder={String(effective.warning_days)}
                  onChange={(e) => setForm((f) => ({ ...f, warning_days: e.target.value }))} />
              </div>
            </div>
            <div>
              <Label variant="console">Reason (recorded in the audit log)</Label>
              <Textarea variant="console" rows={2} value={form.reason} onChange={(e) => setForm((f) => ({ ...f, reason: e.target.value }))} />
            </div>
            <p className="text-xs text-slate-500 dark:text-slate-400">{impact}</p>
            {touched && problems.length > 0 && (
              <ul className="list-disc pl-5 text-xs text-rose-600 dark:text-rose-400" data-testid="retention-problems">
                {problems.map((p) => <li key={p}>{p}</li>)}
              </ul>
            )}
            <Button
              size="sm"
              loading={busy === "propose"}
              disabled={problems.length > 0 || elevation.status !== "active" || Boolean(busy)}
              onClick={async () => {
                const ok = await run("propose",
                  () => api.post("/admin/retention-policy/proposals", { retention_days: rd, warning_days: wd, reason: form.reason.trim() }),
                  "Change proposed — it takes effect only after another Super Admin approves it");
                if (ok) setForm({ retention_days: "", warning_days: "", reason: "" });
              }}
            >
              Propose change
            </Button>
          </div>
        )}
      </div>
    </Panel>
  );
}
