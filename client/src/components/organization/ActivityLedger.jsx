import { useCallback, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import {
  FiActivity, FiAlertTriangle, FiCalendar, FiCopy, FiFilter, FiLock, FiSearch, FiShield, FiUser, FiX,
} from "react-icons/fi";
import api, { errMsg } from "../../api";
import { notify } from "../../ui/Toast";
import Badge from "../../ui/Badge";
import { ConsoleButton as Button } from "../../ui/Button";
import { CONSOLE, cx, focusRing } from "../../ui/tokens";
import OrganizationErrorState from "./OrganizationErrorState";
import Freshness from "./Freshness";
import { Banner, FactList, Footnote, KpiRow, KpiTile, Panel, Tag } from "./console/ConsoleUI";
import { CARD } from "./console/consoleStyles";
import { fmtUtc, fmtZoned, fmtZonedShort } from "../../data/eventTime";
import { DATE_FILTERS, OUTCOME_FILTERS, OUTCOME_TONE, sinceFor } from "../../data/activity";

// The selected row is tinted violet-50/70, where faint (slate-500) measured 4.49:1; its small
// print steps up to slate-600 there.
const ON_ROW_FAINT = "text-slate-600 dark:text-neutral-400";

// The Activity & Audit ledger (ZST-ENG-DASH-001 EVT-07 frames; engineering notes 1-10), shared
// by the Event Workspace and the organization's Security & audit page.
//
//  * The server decides scope, filters, pagination and redaction (services/activity_log). The
//    filters live in the URL so Back keeps them, and nothing else is kept between scopes.
//  * Requests are keyed by the filters they were made for: a slow answer to an older filter can
//    never overwrite a newer one, so what is on screen always matches the controls above it.
//  * A row's receipt is fetched on its own request, which the server re-authorizes — a
//    permission revoked mid-session denies the detail even though the list is on screen.
//  * Read-only. Nothing here starts, ends or changes anything; the evidence-export pathway is
//    shown as the gated, not-yet-offered thing it is, never as a download.

const PAGE_SIZE = 25;
const SEARCH_DEBOUNCE_MS = 300;
const PRIMARY_TABS = ["access", "production", "evidence"];

function Receipt({ entryId, detailUrl, timezone, showEvent, onClose }) {
  // Keyed by URL: an answer for a previously selected record is never shown for this one.
  const [state, setState] = useState({ url: null, data: null, error: null });
  const url = detailUrl(entryId);
  useEffect(() => {
    let alive = true;
    api.get(url)
      .then((r) => alive && setState({ url, data: r.data, error: null }))
      .catch((e) => alive && setState({ url, data: null, error: e }));
    return () => {
      alive = false;
    };
  }, [url]);

  const data = state.url === url ? state.data : null;
  const error = state.url === url ? state.error : null;
  const copyRef = async () => {
    try {
      await navigator.clipboard.writeText(data.reference);
      notify.success("Reference copied");
    } catch {
      notify.error("Couldn't copy — select the reference and copy it manually");
    }
  };

  return (
    <Panel
      title="Selected action receipt"
      subtitle="Read-only review of one recorded decision"
      tag="Redacted"
      tagTone="violet"
      action={
        <button type="button" onClick={onClose} aria-label="Close receipt" className={cx("rounded p-1", CONSOLE.faint, focusRing)}>
          <FiX aria-hidden="true" />
        </button>
      }
    >
      {error ? (
        <p className={cx("text-sm", CONSOLE.muted)} role="alert">
          {error?.response?.status === 403
            ? "Access required: you no longer have permission to read this record."
            : error?.response?.status === 404
              ? "This record is not available in this scope."
              : errMsg(error)}
        </p>
      ) : !data ? (
        <p className={cx("text-sm", CONSOLE.faint)}>Loading receipt…</p>
      ) : (
        <div className="space-y-3" aria-live="polite">
          <div className="flex items-start gap-3">
            <span aria-hidden="true" className={cx(
              "grid h-10 w-10 shrink-0 place-items-center rounded-xl",
              data.changed_state ? "bg-emerald-50 text-emerald-600 dark:bg-emerald-500/15 dark:text-emerald-300"
                : "bg-amber-50 text-amber-600 dark:bg-amber-500/15 dark:text-amber-300"
            )}>
              {data.changed_state ? <FiShield /> : <FiAlertTriangle />}
            </span>
            <div className="min-w-0 flex-1">
              <p className={cx("font-semibold", CONSOLE.heading)}>{data.title}</p>
              <p className={cx("text-[12px]", CONSOLE.faint)}>
                {data.category_label}{data.high_impact ? " · High impact" : ""}{data.changed_state ? "" : " · no state mutation"}
              </p>
            </div>
            <Badge tone={OUTCOME_TONE[data.outcome] || "neutral"}>{data.outcome_label}</Badge>
          </div>
          <dl className={cx("divide-y text-[13px]", CONSOLE.divideY)}>
            {[
              ["Action", <code key="a" className="break-all font-mono text-[12px]">{data.action}</code>],
              ["Actor / role", `${data.actor?.name}${data.actor?.role_label ? ` · ${data.actor.role_label}` : ""}`],
              ["Object / scope", showEvent ? (data.event?.title || "Organization") : "This event"],
              ["Decision", data.changed_state ? "Applied — the change was committed" : `${data.outcome_label} — no change was applied`],
              ["Timestamp", `${fmtZoned(data.at, timezone)} / ${fmtUtc(data.at).split(" · ")[1]}`],
              ...(data.details || []).map((d) => [d.label, d.value]),
            ].map(([k, v]) => (
              <div key={k} className="grid grid-cols-[8.5rem_1fr] gap-3 py-2">
                <dt className={CONSOLE.muted}>{k}</dt>
                <dd className={cx("font-medium", CONSOLE.heading)}>{v}</dd>
              </div>
            ))}
            <div className="grid grid-cols-[8.5rem_1fr] items-center gap-3 py-2">
              <dt className={CONSOLE.muted}>Reference</dt>
              <dd className="flex items-center gap-2">
                {data.reference ? (
                  <>
                    <code className={cx("font-mono text-[12px]", CONSOLE.heading)}>{data.reference}</code>
                    <button type="button" onClick={copyRef} aria-label="Copy reference" className={cx("rounded p-1", CONSOLE.faint, focusRing)}>
                      <FiCopy aria-hidden="true" />
                    </button>
                  </>
                ) : (
                  <span className={CONSOLE.faint}>Not recorded</span>
                )}
              </dd>
            </div>
          </dl>
          {(data.reason || !data.changed_state) && (
            <p className="rounded-lg border border-amber-200 bg-amber-50/70 px-3 py-2 text-[12.5px] text-amber-800 dark:border-amber-400/30 dark:bg-amber-500/10 dark:text-amber-200">
              {data.reason || "The request was not applied; nothing changed."}
            </p>
          )}
        </div>
      )}
    </Panel>
  );
}

// `exportInHeader`: the page header already carries the (gated) export request, so the export
// panel states the gate without repeating the same disabled control.
export default function ActivityLedger({ listUrl, detailUrl, timezone, showEvent = false, emptyText, searchId = "activity-search", exportInHeader = false }) {
  const [params, setParams] = useSearchParams();
  const category = params.get("category") || "";
  const outcome = params.get("outcome") || "";
  const actor = params.get("actor") || "";
  const range = params.get("range") || "";
  const q = params.get("q") || "";
  const page = Math.max(1, Number(params.get("page")) || 1);
  // null: show the first record's receipt; false: the reader closed it; an id: that record.
  const [selected, setSelected] = useState(null);
  const [search, setSearch] = useState(q);
  // A search arriving from outside (the topbar) replaces what is in the box.
  const [seenQ, setSeenQ] = useState(q);
  if (q !== seenQ) {
    setSeenQ(q);
    setSearch(q);
  }

  const setParam = useCallback((patch) => {
    setParams((prev) => {
      const next = new URLSearchParams(prev);
      for (const [k, v] of Object.entries(patch)) {
        if (v === "" || v == null) next.delete(k);
        else next.set(k, String(v));
      }
      if (!("page" in patch)) next.delete("page");
      return next;
    }, { replace: true });
    setSelected(null);
  }, [setParams]);

  // Typing filters after a pause, not per keystroke.
  useEffect(() => {
    if (search === q) return undefined;
    const t = setTimeout(() => setParam({ q: search.trim() }), SEARCH_DEBOUNCE_MS);
    return () => clearTimeout(t);
  }, [search, q, setParam]);

  const query = useMemo(() => {
    const p = { page, page_size: PAGE_SIZE };
    if (category) p.category = category;
    if (outcome) p.outcome = outcome;
    if (actor) p.actor = actor;
    if (q) p.q = q;
    const since = sinceFor(range);
    if (since) p.since = since;
    return p;
  }, [category, outcome, actor, q, range, page]);

  // Every request is keyed by the filters it was made for (and a refresh counter). An answer
  // is applied only while its request is still the current one — the effect's cleanup
  // retires the previous request — so a slow reply to an older filter can never overwrite a
  // newer one, and `loading` is simply "the current request has not answered yet".
  const [tick, setTick] = useState(0);
  const filterKey = JSON.stringify([listUrl, query]);
  const reqKey = `${filterKey}#${tick}`;
  const [state, setState] = useState({ reqKey: null, filterKey: null, data: null, error: null, errorKey: null });
  useEffect(() => {
    let alive = true;
    api.get(listUrl, { params: query })
      .then((r) => alive && setState({ reqKey, filterKey, data: r.data, error: null, errorKey: null }))
      .catch((e) => alive && setState((prev) => ({ ...prev, reqKey, error: e, errorKey: filterKey })));
    return () => {
      alive = false;
    };
  }, [reqKey, filterKey, listUrl, query]);
  const load = useCallback(() => setTick((t) => t + 1), []);

  const { data } = state;
  const loading = state.reqKey !== reqKey;
  // Rows on screen belong to these filters, or the table says it is updating. A refresh that
  // failed keeps them (and the freshness badge says so); a failed new filter does not.
  const current = state.filterKey === filterKey;
  const error = state.errorKey === filterKey ? state.error : null;
  const status = error?.response?.status;

  if (!data && error) {
    if (status === 403) {
      return (
        <div className={cx(CARD, "p-6")} role="alert">
          <p className={cx("font-semibold", CONSOLE.heading)}>Access required</p>
          <p className={cx("mt-1 text-sm", CONSOLE.muted)}>Activity &amp; Audit is available to organization admins.</p>
        </div>
      );
    }
    return <OrganizationErrorState error={error} onRetry={load} title="Couldn't load activity" />;
  }
  if (!data) return <p className={cx("text-sm", CONSOLE.faint)}>Loading activity…</p>;

  const s = data.summary;
  const filtered = Boolean(category || outcome || actor || q || range);
  const tabs = [{ value: "", label: "All activity", count: s.total }].concat(
    data.categories
      .filter((c) => PRIMARY_TABS.includes(c.value) || s.by_category[c.value] > 0)
      .map((c) => ({ ...c, count: s.by_category[c.value] || 0 }))
  );
  const pages = Math.max(1, Math.ceil(data.total / data.page_size));
  const receiptId = selected === false ? null : selected ?? (current ? data.items[0]?.id : null);
  const select = (
    "h-9 cursor-pointer rounded-lg border border-slate-200 bg-white pl-8 pr-3 text-[12.5px] font-medium text-slate-700 " +
    "hover:border-slate-300 focus-visible:border-violet-500 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-500/30 " +
    "dark:border-white/[0.14] dark:bg-white/[0.04] dark:text-neutral-200"
  );

  return (
    <div className="space-y-6">
      <Banner
        tone="info"
        icon={FiShield}
        title="Server-recorded audit records — integrity boundaries apply"
        aside={<Freshness asOf={data.as_of} staleAfter={120} failed={Boolean(error)} onRefresh={load} refreshing={loading} />}
      >
        Scope, actor roles, request references and decisions come from the server. {data.integrity_note}
      </Banner>

      <KpiRow>
        <KpiTile icon={FiActivity} tone="violet" label="Recorded events" value={s.total} caption="Audit entries in this scope" />
        <KpiTile icon={FiShield} tone="sky" label="High-impact actions" value={s.high_impact} caption="Go-live, end, revoke, export…" />
        {/* Refused = denied by permission OR rejected by the lifecycle — both, so named as both. */}
        <KpiTile icon={FiAlertTriangle} tone={s.refused ? "amber" : "slate"} label="Denied or rejected" value={s.refused} caption="No state changes applied" />
        <KpiTile icon={FiLock} tone="green" label="Export status" emphasis="text" value={data.export.label} caption="No package released" />
      </KpiRow>

      <div className="flex flex-wrap items-end justify-between gap-3">
        <div role="tablist" aria-label="Activity category" className={cx(CARD, "inline-flex flex-wrap rounded-xl p-1")}>
          {tabs.map((t) => {
            const on = t.value === category;
            return (
              <button
                key={t.value || "all"}
                type="button"
                role="tab"
                aria-selected={on}
                onClick={() => setParam({ category: t.value })}
                className={cx(
                  "relative rounded-lg px-3.5 py-2 text-[13px] font-medium",
                  focusRing,
                  on ? "bg-violet-50 text-violet-700 dark:bg-violet-500/15 dark:text-violet-200" : cx(CONSOLE.muted, "hover:text-slate-900 dark:hover:text-white")
                )}
              >
                {t.label}
                <span className="ml-1.5 tabular-nums">{t.count}</span>
                {on && <span aria-hidden="true" className="absolute inset-x-3 -bottom-0.5 h-0.5 rounded-full bg-violet-500" />}
              </button>
            );
          })}
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <label className="relative">
            <span className="sr-only">Search activity, actors and references</span>
            <FiSearch aria-hidden="true" className={cx("pointer-events-none absolute left-3 top-1/2 -translate-y-1/2", CONSOLE.faint)} />
            <input
              id={searchId}
              type="search"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search actions, actors, references…"
              className={cx(select, "w-60 cursor-text pl-9")}
              maxLength={100}
            />
          </label>
          <label className="relative">
            <span className="sr-only">Date range</span>
            <FiCalendar aria-hidden="true" className={cx("pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2", CONSOLE.faint)} />
            <select value={range} onChange={(e) => setParam({ range: e.target.value })} className={select}>
              {DATE_FILTERS.map((d) => <option key={d.value} value={d.value}>{d.label}</option>)}
            </select>
          </label>
          <label className="relative">
            <span className="sr-only">Result</span>
            <FiFilter aria-hidden="true" className={cx("pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2", CONSOLE.faint)} />
            <select value={outcome} onChange={(e) => setParam({ outcome: e.target.value })} className={select}>
              {OUTCOME_FILTERS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
            </select>
          </label>
          <label className="relative">
            <span className="sr-only">Actor</span>
            <FiUser aria-hidden="true" className={cx("pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2", CONSOLE.faint)} />
            <select value={actor} onChange={(e) => setParam({ actor: e.target.value })} className={select}>
              <option value="">All actors</option>
              {data.actors.map((a) => <option key={a.value} value={a.value}>{a.label} ({a.count})</option>)}
            </select>
          </label>
        </div>
      </div>

      <div className="grid gap-6 xl:grid-cols-[minmax(0,1.5fr)_minmax(0,1fr)]">
        <Panel
          title={showEvent ? "Organization activity ledger" : "Event activity ledger"}
          subtitle={showEvent ? "Organization-scoped actions, permissions and state-change outcomes" : "Event-scoped actions, permissions and state-change outcomes"}
          tag="Server-resolved scope"
          tagTone="violet"
          flush
        >
          {!current && error ? (
            <div className="p-5">
              <OrganizationErrorState error={error} onRetry={load} title="Couldn't apply these filters" />
            </div>
          ) : data.items.length === 0 ? (
            <div className="border-t border-slate-100 p-6 text-center dark:border-white/[0.08]">
              <p className={cx("text-sm font-semibold", CONSOLE.heading)}>
                {filtered ? "No records match these filters" : "No activity recorded yet"}
              </p>
              <p className={cx("mt-1 text-sm", CONSOLE.muted)}>
                {filtered ? "Change or clear the filters to see more." : emptyText}
              </p>
              {filtered && (
                <Button variant="secondary" size="sm" className="mt-3"
                  onClick={() => { setSearch(""); setParam({ category: "", outcome: "", actor: "", q: "", range: "" }); }}>
                  Clear filters
                </Button>
              )}
            </div>
          ) : (
            <div className={cx("overflow-x-auto border-t border-slate-100 dark:border-white/[0.08]", !current && "opacity-60")} aria-busy={!current}>
              <table className="w-full min-w-[640px] text-left text-sm">
                <caption className="sr-only">Activity records; select a row to read its receipt</caption>
                <thead className={cx("bg-slate-50/70 text-[11px] uppercase tracking-wider dark:bg-white/[0.03]", CONSOLE.muted)}>
                  <tr>
                    <th scope="col" className="px-5 py-2.5 font-semibold">{timezone ? "Local time" : "Time"}</th>
                    <th scope="col" className="px-3 py-2.5 font-semibold">Action / resource</th>
                    {showEvent && <th scope="col" className="px-3 py-2.5 font-semibold">Event</th>}
                    <th scope="col" className="px-3 py-2.5 font-semibold">Actor / role</th>
                    <th scope="col" className="px-5 py-2.5 text-right font-semibold">Result</th>
                  </tr>
                </thead>
                <tbody className={cx("divide-y", CONSOLE.divideY)}>
                  {data.items.map((item) => {
                    const on = item.id === receiptId;
                    return (
                      <tr
                        key={item.id}
                        className={cx("align-top", on ? "bg-violet-50/70 shadow-[inset_3px_0_0_#8b5cf6] dark:bg-violet-500/10" : "hover:bg-slate-50 dark:hover:bg-white/[0.04]")}
                      >
                        <td className="whitespace-nowrap px-5 py-3">
                          <p className={cx("text-[12.5px] font-medium", CONSOLE.body)}>{fmtZonedShort(item.at, timezone)}</p>
                          <p className={cx("text-[11px]", on ? ON_ROW_FAINT : CONSOLE.faint)}>{fmtUtc(item.at).split(" · ")[1]}</p>
                        </td>
                        <td className="px-3 py-3">
                          <button
                            type="button"
                            onClick={() => setSelected(item.id)}
                            aria-pressed={on}
                            className={cx("rounded text-left text-[13.5px] font-semibold hover:underline", CONSOLE.heading, focusRing)}
                          >
                            {item.title}
                          </button>
                          <p className={cx("font-mono text-[11px]", on ? ON_ROW_FAINT : CONSOLE.faint)}>
                            {item.action}
                            {item.high_impact && <span className="ml-1.5 font-sans font-semibold text-violet-600 dark:text-violet-300">· High impact</span>}
                          </p>
                        </td>
                        {showEvent && (
                          <td className={cx("px-3 py-3 text-[13px]", CONSOLE.body)}>{item.event?.title || "—"}</td>
                        )}
                        <td className="px-3 py-3">
                          <p className={cx("text-[13px] font-medium", CONSOLE.body)}>{item.actor?.name}</p>
                          <p className={cx("text-[11px]", on ? ON_ROW_FAINT : CONSOLE.faint)}>{item.actor?.role_label || ""}</p>
                        </td>
                        <td className="px-5 py-3 text-right">
                          <Badge tone={OUTCOME_TONE[item.outcome] || "neutral"}>{item.outcome_label}</Badge>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
          <div className={cx("flex flex-wrap items-center justify-between gap-3 border-t px-5 py-3 text-[12px]", CONSOLE.divider, CONSOLE.muted)}>
            <span>
              {!current ? "Updating… " : ""}Showing {data.items.length} of {data.total} record{data.total === 1 ? "" : "s"}
              {timezone ? " · timestamps have event-local and UTC meaning" : " · times in your zone and UTC"}
            </span>
            {pages > 1 && (
              <span className="flex items-center gap-2">
                <Button variant="secondary" size="sm" disabled={page <= 1 || loading} onClick={() => setParam({ page: page - 1 })}>Previous</Button>
                <span>Page {page} of {pages}</span>
                <Button variant="secondary" size="sm" disabled={page >= pages || loading} onClick={() => setParam({ page: page + 1 })}>Next</Button>
              </span>
            )}
          </div>
        </Panel>

        {receiptId ? (
          <Receipt entryId={receiptId} detailUrl={detailUrl} timezone={timezone} showEvent={showEvent} onClose={() => setSelected(false)} />
        ) : (
          <div className={cx(CARD, "p-5 text-sm", CONSOLE.muted)}>
            Select a record to read its receipt: who acted, under what role, what the server decided and the request reference.
          </div>
        )}
      </div>

      <div className="grid gap-6 xl:grid-cols-3">
        <Panel title="Evidence confidence & boundaries" subtitle="Do not overstate audit assurance" tag="Review required" tagTone="amber">
          <FactList rows={[
            ["Event/tenant scope", data.integrity.tenant_scope?.label || "—"],
            ["Record hash verification", data.integrity.hash_verification?.label || "Unknown", "warn"],
            ["Storage immutability", data.integrity.immutability?.label || "Unknown", "warn"],
            ["External time anchoring", data.integrity.time_anchoring?.label || "Unknown", "warn"],
          ]} />
          <Footnote>{data.integrity_note}</Footnote>
        </Panel>

        <Panel title="Governed export pathway" subtitle="Independent approvals before disclosure" tag="Not released" tagTone="violet">
          <ol className={cx("divide-y", CONSOLE.divideY)}>
            {[
              ["Specify scope & purpose", "Minimise the records included", "Required"],
              ["Resolve authorization & approval", "Export entitlement and an independent reviewer", "Gated"],
              ["Generate approved evidence package", "Receipt, expiry and access logging", "Not run"],
            ].map(([title, sub, tag], i) => (
              <li key={title} className="flex items-center gap-3 py-2.5">
                <span className={cx("grid h-7 w-7 shrink-0 place-items-center rounded-lg text-[12px] font-bold", CONSOLE.inset)}>{i + 1}</span>
                <span className="min-w-0 flex-1">
                  <span className={cx("block text-[13px] font-semibold", CONSOLE.heading)}>{title}</span>
                  <span className={cx("block text-[11.5px]", CONSOLE.faint)}>{sub}</span>
                </span>
                <Tag tone="violet">{tag}</Tag>
              </li>
            ))}
          </ol>
          <p className={cx("mt-2 text-[12px]", CONSOLE.muted)}>{data.export.message}</p>
          {exportInHeader ? (
            <p className={cx("mt-3 inline-flex items-center gap-1.5 text-[12px] font-semibold", CONSOLE.muted)}>
              <FiLock aria-hidden="true" /> Export requests are gated — no package can be generated yet.
            </p>
          ) : (
            <>
              <Button variant="secondary" size="sm" leftIcon={FiLock} disabled className="mt-3" aria-describedby="export-gated">
                Request evidence export · Gated
              </Button>
              <p id="export-gated" className="sr-only">Evidence export is not offered yet; no package can be generated.</p>
            </>
          )}
        </Panel>

        <Panel title="Live-operations safety record" subtitle="Signals that must be distinguishable" tag={s.high_impact ? "Recorded" : "Pre-live"} tagTone="violet">
          <FactList rows={[
            ["High-impact actions", s.high_impact ? `${s.high_impact} recorded separately` : "No broadcast actions yet"],
            ["Denied attempts", s.refused ? `${s.refused} · no state mutation` : "None · no state mutation"],
            ["Authorization decision", "Recorded with request ID"],
            ["Stream/device secrets", "Redacted or excluded"],
          ]} />
          <Footnote>Never use the audit view as a control for starting or ending streams or altering viewer access.</Footnote>
        </Panel>
      </div>
    </div>
  );
}
