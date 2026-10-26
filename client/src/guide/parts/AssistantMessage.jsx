import { useCallback, useId, useState } from "react";
import { AlertTriangle, Clock, ExternalLink, FileText, Info, ShieldAlert, ThumbsDown, ThumbsUp, WifiOff } from "lucide-react";
import Actions from "./Actions";
import { isSafeHref } from "./actionUtils";
import { useNestedLayer } from "./nested";
import { disclosureHead } from "./disclosure";
import { sendFeedback } from "../guideApi";
import { track } from "../guideAnalytics";

// Plain text in, React elements out: paragraphs and "- " bullet lists only. Model output is
// never interpreted as HTML (Security/Privacy §22).
export function RichText({ text }) {
  const blocks = (text || "").split(/\n{2,}/).filter((b) => b.trim());
  return blocks.map((block, i) => {
    const lines = block.split("\n");
    if (lines.every((l) => /^\s*[-•*]\s+/.test(l))) {
      return (
        <ul key={i}>
          {lines.map((l, j) => (
            <li key={j}>{l.replace(/^\s*[-•*]\s+/, "")}</li>
          ))}
        </ul>
      );
    }
    return (
      <p key={i}>
        {lines.map((l, j) => (
          <span key={j}>
            {j > 0 && <br />}
            {l}
          </span>
        ))}
      </p>
    );
  });
}

const BOUNDARY = {
  restricted: { icon: ShieldAlert, label: "Outside public Guide" },
  knowledge_gap: { icon: Info, label: "No approved answer" },
  degraded: { icon: WifiOff, label: "Guide is limited right now" },
};

// WF-17 "Optional: What could be better?" - exact labels. Codes are the API's reason_code
// vocabulary (schemas.FeedbackSubmit).
const REASONS = [
  ["incorrect", "Incorrect"],
  ["out_of_date", "Out of date"],
  ["unclear", "Not clear"],
  ["not_helpful", "Did not answer my question"],
];

function AnswerText({ text, expanded, onToggle }) {
  const id = useId();
  const head = disclosureHead(text);
  if (!head) return <RichText text={text} />;
  return (
    <>
      <div id={id}>
        <RichText text={expanded ? text : head} />
      </div>
      <button type="button" className="zg-link zg-more" aria-expanded={expanded} aria-controls={id} onClick={onToggle}>
        {expanded ? "Show less" : "Show more"}
      </button>
    </>
  );
}

// Generation progress (C2, Figma §5 Guide/Progress, §10.2 "Response started: subtle progress
// state; no prolonged fake human typing"). Stages come from the server's response.progress.
const PROGRESS_COPY = {
  sent: "Sending your question…",
  retrieving: "Searching ZoikoStream sources…",
  drafting: "Writing an answer…",
  checking: "Checking the answer against sources…",
};

// SourceChip variants (Figma §5): FreshnessWarning when the server marks the source as past
// its review date; External when the approved destination is a public page on another site
// (server-allowlisted https URL; opened in a new tab with rel=noopener noreferrer, WF UX-015).
const STALE = new Set(["stale", "warning", "review_due"]);
const isStale = (s) => s?.stale === true || STALE.has(s?.freshness);
function safeExternalUrl(s) {
  if (s?.external !== true || typeof s.external_url !== "string") return null;
  try {
    const u = new URL(s.external_url);
    return u.protocol === "https:" && !u.username && !u.password ? u.href : null;
  } catch {
    return null;
  }
}

function Sources({ sources, onAction }) {
  const [open, setOpen] = useState(null);
  // An expanded source detail is a nested layer: Back/Escape closes it before Guide.
  const closeDetail = useCallback(() => setOpen(null), []);
  useNestedLayer(open !== null, closeDetail);
  if (!sources?.length) return null;
  const current = sources.find((s) => s.source_id === open);
  return (
    <div>
      <div className="zg-sources">
        {sources.map((s) => {
          const stale = isStale(s);
          const external = !!safeExternalUrl(s);
          const Icon = stale ? Clock : external ? ExternalLink : FileText;
          return (
            <button
              key={s.source_id}
              type="button"
              className={`zg-source${stale ? " zg-source--stale" : ""}${external ? " zg-source--external" : ""}`}
              aria-expanded={open === s.source_id}
              onClick={() => {
                if (open !== s.source_id) track("source_opened", { source_id: s.source_id });
                setOpen(open === s.source_id ? null : s.source_id);
              }}
            >
              <Icon aria-hidden="true" size={14} />
              <span>Source: {s.title}</span>
              {stale && <span className="zg-source-flag">May be out of date</span>}
              {external && <span className="zg-sr">, external site</span>}
            </button>
          );
        })}
      </div>
      {current && (
        <div className="zg-source-details">
          <strong>{current.title}</strong>
          <span className="zg-meta">
            Public {current.source_type} source · Version {current.version}
            {current.updated_at ? ` · Updated ${new Date(current.updated_at + "T00:00:00").toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" })}` : ""}
          </span>
          {isStale(current) && (
            <span className="zg-source-warning">
              <AlertTriangle aria-hidden="true" size={14} /> This source is due for review and may be out of date.
            </span>
          )}
          {safeExternalUrl(current) ? (
            <div style={{ marginTop: 6 }}>
              <a
                className="zg-link"
                href={safeExternalUrl(current)}
                target="_blank"
                rel="noopener noreferrer"
                onClick={() => track("cta_clicked", { cta_id: "source", cta_type: "external_source", source_id: current.source_id })}
              >
                Open public source <ExternalLink aria-hidden="true" size={14} />
                <span className="zg-sr"> (opens in a new tab)</span>
              </a>
            </div>
          ) : isSafeHref(current.canonical_path) && (
            <div style={{ marginTop: 6 }}>
              <button
                type="button"
                className="zg-link"
                onClick={() => onAction({ action_id: "source", type: "navigate", label: current.title, href: current.canonical_path })}
              >
                Open public source
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function Feedback({ messageId, sent, onSent }) {
  const [state, setState] = useState("idle"); // idle | reasons
  const [error, setError] = useState(false);
  const whyId = useId();
  const send = async (rating, reason) => {
    const reasonCode = reason || (rating === "up" ? "helpful" : undefined);
    try {
      await sendFeedback({ target_type: "answer", target_id: messageId, rating, reason_code: reasonCode });
      track("feedback_submitted", { response_id: messageId, helpful: rating === "up", rating, reason_code: reasonCode });
      onSent();
    } catch {
      setError(true);
    }
  };
  if (sent) return <p className="zg-feedback" role="status">Thanks for the feedback.</p>;
  if (state === "reasons") {
    // Reasons are optional (WF-17); "Skip" records the rating without one.
    return (
      <div className="zg-feedback" role="group" aria-labelledby={whyId}>
        <span id={whyId}>Optional: What could be better?</span>
        {REASONS.map(([code, label]) => (
          <button key={label} type="button" className="zg-chip" onClick={() => send("down", code)}>
            {label}
          </button>
        ))}
        <button type="button" className="zg-chip" onClick={() => send("down")}>
          Skip
        </button>
        {error && <span role="alert">Feedback couldn&apos;t be sent.</span>}
      </div>
    );
  }
  return (
    <div className="zg-feedback">
      <span>Was this helpful?</span>
      <button type="button" className="zg-iconbtn" aria-label="Yes, this was helpful" onClick={() => send("up")}>
        <ThumbsUp aria-hidden="true" />
      </button>
      <button type="button" className="zg-iconbtn" aria-label="No, this wasn't helpful" onClick={() => setState("reasons")}>
        <ThumbsDown aria-hidden="true" />
      </button>
      {error && <span role="alert">Feedback couldn&apos;t be sent.</span>}
    </div>
  );
}

// One material clarification as chips (C4, CF §6.1, Figma WF-04): choosing one sends its label
// as the visitor's next message; the set is then shown selected and disabled (GC-005). Typing
// freely instead is always possible (WF-04 A5).
function Clarification({ clarification, chosen, onChoose }) {
  const id = useId();
  const options = Array.isArray(clarification?.options) ? clarification.options.slice(0, 4) : [];
  if (options.length < 2 || typeof clarification.question !== "string") return null;
  return (
    <div className="zg-clarify">
      <p className="zg-clarify-q" id={id}>{clarification.question}</p>
      <div className="zg-chips" role="group" aria-labelledby={id}>
        {options.map((o) => (
          <button
            key={o.id}
            type="button"
            className={`zg-chip${chosen === o.id ? " is-selected" : ""}`}
            aria-pressed={chosen === o.id}
            disabled={!!chosen}
            onClick={() => onChoose(o)}
          >
            {o.label}
          </button>
        ))}
      </div>
    </div>
  );
}

// Assistant messages are clean reading blocks, not chat bubbles, with no avatar (Figma §7.2,
// WF-03): a small "Guide" label, then the answer, its source and its actions.
export default function AssistantMessage({ item, onAction, onResume, onSuggest, onClarify, onPatch = () => {} }) {
  const { msg, streaming, incomplete } = item;
  const boundary = BOUNDARY[msg.answer_class];
  const Icon = boundary?.icon;
  const chosen = item.chosenSuggestion;
  const pausedRoutes = Array.isArray(msg.paused?.routes) ? msg.paused.routes.filter((r) => r && typeof r === "object") : [];
  return (
    <div className="zg-msg zg-msg--assistant">
      <p className="zg-speaker" aria-hidden="true">Guide</p>
      <span className="zg-sr">Guide said:</span>
      <div className={`zg-block zg-answer${boundary ? " zg-block--boundary" : ""}`}>
        {boundary && (
          // Gap/restricted/degraded are never styled as success (Figma DQ-09).
          <span className="zg-badge">
            <Icon aria-hidden="true" size={13} />
            {boundary.label}
          </span>
        )}
        {incomplete && (
          // Interrupted stream: partial text is kept but never passed off as the answer (CF §24).
          <span className="zg-badge">
            <AlertTriangle aria-hidden="true" size={13} />
            Incomplete response
          </span>
        )}
        {msg.text ? (
          streaming || incomplete ? (
            <RichText text={msg.text} />
          ) : (
            <AnswerText text={msg.text} expanded={!!item.expanded} onToggle={() => onPatch(item.id, { expanded: !item.expanded })} />
          )
        ) : null}
        {streaming && !msg.text && (
          <p className="zg-progress" role="status">
            <span className="zg-progress-dot" aria-hidden="true" />
            {PROGRESS_COPY[item.progress] || PROGRESS_COPY.sent}
          </p>
        )}
      </div>
      {!streaming && !incomplete && (
        <>
          <Sources sources={msg.sources} onAction={onAction} />
          {msg.clarification && onClarify && (
            <Clarification
              clarification={msg.clarification}
              chosen={item.chosenClarification}
              onChoose={(o) => {
                onPatch(item.id, { chosenClarification: o.id });
                onClarify(o.label);
              }}
            />
          )}
          {msg.paused && typeof msg.paused.message === "string" && (
            // C7: a paused feature's own approved message and routes, verbatim.
            <>
              <div className="zg-notice zg-notice--warning" role="status" style={{ marginTop: 12 }}>
                <AlertTriangle aria-hidden="true" />
                <span>{msg.paused.message}</span>
              </div>
              <Actions actions={pausedRoutes} onAction={onAction} />
            </>
          )}
          <Actions actions={msg.actions} onAction={onAction} />
          {msg.resume && (
            <div className="zg-links">
              <button type="button" className="zg-link" onClick={() => onResume(msg.resume)}>
                {msg.resume.label}
              </button>
            </div>
          )}
          {Array.isArray(msg.suggestions) && msg.suggestions.length > 0 && onSuggest && (
            // Follow-up topics from the server's approved intent catalog.
            // GC-005: once one is chosen it shows selected and the set is disabled.
            <div className="zg-actions" role="group" aria-label="Suggested topics">
              {msg.suggestions.map((s) => (
                <button
                  key={s.intent_id}
                  type="button"
                  className={`zg-chip${chosen === s.intent_id ? " is-selected" : ""}`}
                  aria-pressed={chosen === s.intent_id}
                  disabled={!!chosen}
                  onClick={() => {
                    onPatch(item.id, { chosenSuggestion: s.intent_id });
                    onSuggest(s);
                  }}
                >
                  {s.label}
                </button>
              ))}
            </div>
          )}
          {msg.message_id && !["degraded", "conversation"].includes(msg.answer_class) && (
            <Feedback messageId={msg.message_id} sent={!!item.feedbackSent} onSent={() => onPatch(item.id, { feedbackSent: true })} />
          )}
        </>
      )}
    </div>
  );
}
