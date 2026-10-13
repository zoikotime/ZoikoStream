// Operational card components (ZST-UX-GUIDE-001 §8). Every state is rendered as text + icon
// + colour — never colour alone — and every card names its sources.
import { useState } from "react";
import {
  ArrowRight, CircleCheck, CircleDot, CircleHelp, CircleX, Copy, Info, Loader2, Lock,
  TriangleAlert,
} from "lucide-react";
import { safeHref } from "./links";

const STATE_META = {
  ready: { tone: "ok", icon: CircleCheck, text: "Ready" },
  stable: { tone: "ok", icon: CircleCheck, text: "Stable" },
  active: { tone: "ok", icon: CircleCheck, text: "On" },
  review: { tone: "warn", icon: TriangleAlert, text: "Needs review" },
  conditional: { tone: "warn", icon: TriangleAlert, text: "Conditional" },
  degraded: { tone: "warn", icon: TriangleAlert, text: "Degraded" },
  detected: { tone: "warn", icon: TriangleAlert, text: "Detected" },
  suspected: { tone: "warn", icon: CircleHelp, text: "Suspected" },
  blocked: { tone: "danger", icon: CircleX, text: "Blocked" },
  down: { tone: "danger", icon: CircleX, text: "Down" },
  confirmed: { tone: "danger", icon: CircleX, text: "Confirmed" },
  unknown: { tone: "muted", icon: CircleHelp, text: "Unknown" },
  inactive: { tone: "muted", icon: CircleDot, text: "Off" },
  info: { tone: "muted", icon: Info, text: "" },
};

function StateIcon({ state }) {
  const meta = STATE_META[state] || STATE_META.unknown;
  const Icon = meta.icon;
  return (
    <span className={`og-state og-tone-${meta.tone}`}>
      <Icon size={16} aria-hidden="true" />
      {meta.text && <span className="og-sr">{meta.text}: </span>}
    </span>
  );
}

function Rows({ rows }) {
  return (
    <ul className="og-rows">
      {rows.map((r, i) => (
        <li key={`${r.label}-${i}`} className="og-row">
          <StateIcon state={r.state} />
          <div className="og-row-main">
            <div className="og-row-line">
              <span className="og-row-label">{r.label}</span>
              {r.value != null && <span className="og-row-value">{r.value}</span>}
            </div>
            {r.detail && <p className="og-row-detail">{r.detail}</p>}
          </div>
        </li>
      ))}
    </ul>
  );
}

function Card({ title, subtitle, badge, children }) {
  return (
    <section className="og-card" aria-label={title}>
      <header className="og-card-head">
        <h3 className="og-card-title">{title}</h3>
        {badge}
      </header>
      {subtitle && <p className="og-card-sub">{subtitle}</p>}
      {children}
    </section>
  );
}

function StatusCard({ block }) {
  return <Card title={block.title} subtitle={block.subtitle}><Rows rows={block.rows} /></Card>;
}

function ReadinessCard({ block }) {
  const meta = STATE_META[block.overall.state] || STATE_META.unknown;
  return (
    <Card
      title={block.title}
      badge={<span className={`og-badge og-tone-${meta.tone}`}><meta.icon size={14} aria-hidden="true" />{block.overall.label}</span>}
    >
      <Rows rows={block.rows} />
      {block.note && <p className="og-card-note">{block.note}</p>}
    </Card>
  );
}

function MetricCard({ block }) {
  return (
    <Card title={block.title} subtitle={block.scope}>
      <dl className="og-metrics">
        {block.metrics.map((m) => (
          <div key={m.label} className="og-metric" title={m.definition}>
            <dt>{m.label}</dt>
            <dd>
              {m.value == null ? <span className="og-muted">Not available</span> : `${m.value}${m.unit ? ` ${m.unit}` : ""}`}
              {m.quality && m.quality !== "measured" && <span className="og-quality">{m.quality}</span>}
            </dd>
            <p className="og-metric-def">{m.definition}</p>
          </div>
        ))}
      </dl>
      {block.note && <p className="og-card-note">{block.note}</p>}
    </Card>
  );
}

function DiagnosticCard({ block }) {
  return (
    <Card title={block.title}>
      <p className="og-assessment">{block.assessment}</p>
      <Rows rows={block.rows} />
    </Card>
  );
}

function TimelineCard({ block }) {
  return (
    <Card title={block.title}>
      <ol className="og-timeline">
        {block.items.map((it, i) => (
          <li key={i}><time>{it.at}</time> {it.text}</li>
        ))}
      </ol>
    </Card>
  );
}

function Notice({ block }) {
  const kind = { blocker: ["danger", CircleX, "Blocked"], warning: ["warn", TriangleAlert, "Warning"], info: ["muted", Info, "Note"] }[block.type];
  const [tone, Icon, label] = kind;
  return (
    <div className={`og-notice og-tone-${tone}`} role={block.type === "blocker" ? "alert" : undefined}>
      <Icon size={16} aria-hidden="true" />
      <p><span className="og-sr">{label}: </span>{block.text}{block.rule && <span className="og-rule"> · {block.rule}</span>}</p>
    </div>
  );
}

function DocsCard({ block, onNavigate }) {
  return (
    <Card title={block.title}>
      <div className="og-docs">
        {block.items.map((d) => (
          <article key={d.source_id} className="og-doc">
            <h4>{d.heading} <span className="og-muted">· {d.doc}</span></h4>
            <p>{d.text}</p>
            {safeHref(d.path) && (
              <button type="button" className="og-link" onClick={() => onNavigate(d.path)}>
                Open page <ArrowRight size={14} aria-hidden="true" />
              </button>
            )}
          </article>
        ))}
      </div>
    </Card>
  );
}

function AttentionList({ block, onAsk, onNavigate }) {
  if (!block.items.length) return <p className="og-muted">Nothing needs your attention right now.</p>;
  return <AttentionItems items={block.items} onAsk={onAsk} onNavigate={onNavigate} />;
}

export function AttentionItems({ items, onAsk, onNavigate }) {
  const tone = { blocker: "danger", warning: "warn", info: "muted" };
  const icon = { blocker: CircleX, warning: TriangleAlert, info: Info };
  return (
    <ul className="og-attention">
      {items.map((it) => {
        const Icon = icon[it.severity];
        return (
          <li key={it.id} className={`og-attention-item og-tone-${tone[it.severity]}`}>
            <Icon size={16} aria-hidden="true" />
            <div className="og-row-main">
              <p className="og-row-label"><span className="og-sr">{it.severity}: </span>{it.title}</p>
              <p className="og-row-detail">{it.detail}</p>
              <div className="og-inline-actions">
                <button type="button" className="og-chip" onClick={() => onAsk(it.ask, it.event?.id)}>{it.ask}</button>
                {safeHref(it.href) && (
                  <button type="button" className="og-link" onClick={() => onNavigate(it.href)}>
                    Open <ArrowRight size={14} aria-hidden="true" />
                  </button>
                )}
              </div>
            </div>
          </li>
        );
      })}
    </ul>
  );
}

function Choices({ block, onChoose, disabled }) {
  return (
    <Card title={block.title}>
      <ul className="og-choices">
        {block.options.map((o, i) => (
          <li key={i}>
            <button type="button" className="og-choice" disabled={disabled} onClick={() => onChoose(o, block)}>
              <span className="og-row-label">{o.label}</span>
              {o.detail && <span className="og-row-detail">{o.detail}</span>}
            </button>
          </li>
        ))}
      </ul>
    </Card>
  );
}

function DraftCard({ block, onApply, disabled }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(block.subject ? `${block.subject}\n\n${block.body}` : block.body);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch { /* clipboard blocked — the text stays selectable */ }
  };
  return (
    <Card title="Draft" badge={<span className="og-badge og-tone-muted">{block.label}</span>}>
      {block.subject && <p className="og-draft-subject"><span className="og-muted">Subject:</span> {block.subject}</p>}
      <p className="og-draft-body">{block.body}</p>
      <div className="og-inline-actions">
        <button type="button" className="og-btn" onClick={copy}>
          <Copy size={14} aria-hidden="true" /> {copied ? "Copied" : "Copy draft"}
        </button>
        {block.apply && (
          <button type="button" className="og-btn" disabled={disabled} onClick={() => onApply(block.apply)}>
            {block.apply.label}
          </button>
        )}
      </div>
    </Card>
  );
}

const RISK_TEXT = { R2: "Draft", R3: "Low-risk change", R4: "Material change" };

function ProposalCard({ block, onConfirm, onCancel }) {
  const [busy, setBusy] = useState(false);
  const receipt = block.receipt;
  const run = async (fn) => {
    setBusy(true);
    try {
      await fn(block);
    } finally {
      setBusy(false);
    }
  };
  const done = ["succeeded", "failed", "unknown", "cancelled", "expired"].includes(block.status);
  return (
    <Card
      title={block.title}
      badge={<span className={`og-badge og-tone-${block.risk === "R4" ? "warn" : "muted"}`}><Lock size={12} aria-hidden="true" />{RISK_TEXT[block.risk] || block.risk}</span>}
    >
      {block.target && <p className="og-card-sub">{block.target.label}</p>}
      {block.change?.from != null && (
        <p className="og-change">
          <span className="og-muted">{block.change.label}:</span> <s>{block.change.from}</s>
          <ArrowRight size={14} aria-label="changes to" /> <strong>{block.change.to}</strong>
        </p>
      )}
      {block.change?.from == null && block.change?.to && <p className="og-change"><strong>{block.change.to}</strong></p>}
      {block.impact?.length > 0 && (
        <ul className="og-impact">{block.impact.map((line) => <li key={line}>{line}</li>)}</ul>
      )}

      {block.status === "proposed" && (
        <div className="og-inline-actions">
          <button type="button" className="og-btn og-btn-primary" disabled={busy} onClick={() => run(onConfirm)}>
            {busy ? <><Loader2 size={14} className="og-spin" aria-hidden="true" /> Applying…</> : block.confirm_label}
          </button>
          <button type="button" className="og-btn" disabled={busy} onClick={() => run(onCancel)}>{block.cancel_label || "Cancel"}</button>
          <span className="og-muted og-small">Nothing changes until you confirm.</span>
        </div>
      )}

      {done && receipt && (
        <div className={`og-receipt og-tone-${block.status === "succeeded" ? "ok" : block.status === "failed" ? "danger" : "warn"}`} role="status">
          {block.status === "succeeded" ? <CircleCheck size={16} aria-hidden="true" /> : <TriangleAlert size={16} aria-hidden="true" />}
          <div>
            <p>{receipt.message}{receipt.reason ? ` ${receipt.reason}` : ""}</p>
            <p className="og-small og-muted">
              {receipt.reference && <>Reference {receipt.reference} · </>}
              {receipt.reconciled && <>Confirmed by re-checking current state · </>}
              Receipt {receipt.correlation_id}
            </p>
          </div>
        </div>
      )}
      {block.status === "cancelled" && <p className="og-muted og-small">Cancelled. Nothing was changed.</p>}
      {block.status === "expired" && <p className="og-muted og-small">This proposal expired. Nothing was changed.</p>}
      {block.error && <p className="og-error og-small" role="alert">{block.error}</p>}
    </Card>
  );
}

export default function Block({ block, handlers, disabled }) {
  switch (block.type) {
    case "status": return <StatusCard block={block} />;
    case "readiness": return <ReadinessCard block={block} />;
    case "metric": return <MetricCard block={block} />;
    case "diagnostic": return <DiagnosticCard block={block} />;
    case "timeline": return <TimelineCard block={block} />;
    case "warning":
    case "blocker":
    case "info": return <Notice block={block} />;
    case "docs": return <DocsCard block={block} onNavigate={handlers.navigate} />;
    case "attention": return <AttentionList block={block} onAsk={handlers.ask} onNavigate={handlers.navigate} />;
    case "choices": return <Choices block={block} onChoose={handlers.choose} disabled={disabled} />;
    case "draft": return <DraftCard block={block} onApply={handlers.apply} disabled={disabled} />;
    case "proposal": return <ProposalCard block={block} onConfirm={handlers.confirm} onCancel={handlers.cancel} />;
    default: return null; // unknown block types are ignored, never rendered raw
  }
}
