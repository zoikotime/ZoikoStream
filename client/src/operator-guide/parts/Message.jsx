// One turn. Guide answers follow §7: direct answer → current state → why → next action →
// sources/freshness. Live Mode answers are terse and carry no suggestions. Facts and Guide's
// own interpretation are kept visibly apart (§6.3): the "why" and any inferred signal are
// labelled "Guide interpretation". A context change is a divider in the thread (§4.2).
import { useState } from "react";
import { ArrowRight, ChevronDown, Lightbulb, Radio, RefreshCw, ThumbsDown, ThumbsUp } from "lucide-react";
import Block, { TechnicalMeta } from "./Blocks";
import { safeHref } from "./links";
import { opGuide } from "../operatorGuideApi";

const CLASS_TEXT = {
  authoritative: "Platform state",
  operational: "Live telemetry",
  documentation: "Documentation",
  tenant: "Your content",
  inference: "Guide interpretation",
};

function Sources({ sources }) {
  const [open, setOpen] = useState(false);
  if (!sources?.length) return null;
  return (
    <div className="og-sources">
      <button type="button" className="og-link" aria-expanded={open} onClick={() => setOpen((v) => !v)}>
        Sources ({sources.length})
        <ChevronDown size={14} aria-hidden="true" className={open ? "og-rot" : ""} />
      </button>
      {!open && (
        <ul className="og-source-chips" aria-hidden="true">
          {sources.slice(0, 3).map((s) => (
            <li key={s.id} className={`og-source-chip${s.stale ? " og-stale" : ""}`}>
              {s.label}{s.age_text ? ` · ${s.age_text}` : ""}{s.stale ? " · stale" : ""}
            </li>
          ))}
        </ul>
      )}
      {open && (
        <ul className="og-evidence">
          {sources.map((s) => (
            <li key={s.id}>
              <p className="og-row-label">{s.label}</p>
              <p className="og-small og-muted">
                {CLASS_TEXT[s.class] || s.class} · {s.owner}
                {s.version ? ` · version ${s.version}` : ""}
                {s.updated_at ? ` · updated ${s.age_text}` : ""}
              </p>
              {s.stale && <p className="og-small og-tone-warn">Not current — older than its freshness limit.</p>}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function Feedback({ messageId }) {
  const [rating, setRating] = useState(null);
  if (!messageId || messageId.startsWith("local-")) return null;
  const rate = (value) => {
    setRating(value);
    opGuide.feedback(messageId, value).catch(() => setRating(null));
  };
  return (
    <div className="og-feedback" role="group" aria-label="Rate this answer">
      <button type="button" aria-pressed={rating === "up"} aria-label="Helpful" onClick={() => rate("up")}>
        <ThumbsUp size={14} aria-hidden="true" />
      </button>
      <button type="button" aria-pressed={rating === "down"} aria-label="Not helpful" onClick={() => rate("down")}>
        <ThumbsDown size={14} aria-hidden="true" />
      </button>
      {rating && <span className="og-small og-muted">Thanks for the feedback.</span>}
    </div>
  );
}

export default function Message({ message, handlers, pending, isLast }) {
  if (message.role === "user") {
    return <div className="og-msg og-msg-user"><p>{message.text}</p></div>;
  }
  if (message.intent === "context.change") {
    return (
      <p className="og-context-notice" role="status">
        <RefreshCw size={12} aria-hidden="true" /> {message.text}
      </p>
    );
  }
  const p = message.payload || {};
  const live = p.mode === "live";
  const actions = (p.actions || []).filter((a) => safeHref(a.href));
  return (
    <div className="og-msg og-msg-guide">
      {live && (
        <p className="og-live-tag"><Radio size={12} aria-hidden="true" /> Live Mode</p>
      )}
      <p className="og-answer">{p.answer ?? message.text}</p>
      <TechnicalMeta technical={p.technical} />
      {(p.blocks || []).map((b, i) => (
        <Block key={`${b.type}-${b.id || i}`} block={b} handlers={handlers} disabled={pending} />
      ))}
      {p.explanation && (
        <div className="og-why og-interpretation">
          <p className="og-why-label">
            <Lightbulb size={12} aria-hidden="true" /> Why · Guide interpretation
          </p>
          <p>{p.explanation}</p>
        </div>
      )}
      {p.inference && !p.explanation && (
        <p className="og-interpretation-note">
          <Lightbulb size={12} aria-hidden="true" /> Includes Guide interpretation — labelled in the sources.
        </p>
      )}
      {actions.length > 0 && (
        <div className="og-inline-actions">
          {actions.map((a) => (
            <button key={a.href} type="button" className="og-btn" onClick={() => handlers.navigate(a.href)}>
              {a.label} <ArrowRight size={14} aria-hidden="true" />
            </button>
          ))}
        </div>
      )}
      <Sources sources={p.sources} />
      {!live && <Feedback messageId={message.id} />}
      {isLast && !live && p.suggestions?.length > 0 && (
        <div className="og-suggestions" aria-label="Suggested questions">
          {p.suggestions.map((s) => (
            <button key={s} type="button" className="og-chip" disabled={pending} onClick={() => handlers.ask(s)}>{s}</button>
          ))}
        </div>
      )}
    </div>
  );
}
