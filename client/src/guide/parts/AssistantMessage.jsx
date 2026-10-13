import { useState } from "react";
import { FileText, Info, ShieldAlert, ThumbsDown, ThumbsUp, WifiOff } from "lucide-react";
import Actions from "./Actions";
import GuideAvatar from "./GuideAvatar";
import { isSafeHref } from "./actionUtils";
import { sendFeedback } from "../guideApi";

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

const REASONS = [
  ["not_helpful", "Not helpful"],
  ["incorrect", "Incorrect"],
  ["unclear", "Unclear"],
  ["too_long", "Too long"],
  ["other", "Other"],
];

function Sources({ sources, onAction }) {
  const [open, setOpen] = useState(null);
  if (!sources?.length) return null;
  const current = sources.find((s) => s.source_id === open);
  return (
    <div>
      <div className="zg-sources">
        {sources.map((s) => (
          <button
            key={s.source_id}
            type="button"
            className="zg-source"
            aria-expanded={open === s.source_id}
            onClick={() => setOpen(open === s.source_id ? null : s.source_id)}
          >
            <FileText aria-hidden="true" size={14} />
            <span>Source: {s.title}</span>
          </button>
        ))}
      </div>
      {current && (
        <div className="zg-source-details">
          <strong>{current.title}</strong>
          <span className="zg-meta">
            Public {current.source_type} source · Version {current.version}
            {current.updated_at ? ` · Updated ${new Date(current.updated_at + "T00:00:00").toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" })}` : ""}
          </span>
          {isSafeHref(current.canonical_path) && (
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

function Feedback({ messageId }) {
  const [state, setState] = useState("idle"); // idle | reasons | sent
  const [error, setError] = useState(false);
  const send = async (rating, reason) => {
    try {
      await sendFeedback({ target_type: "answer", target_id: messageId, rating, reason_code: reason || (rating === "up" ? "helpful" : undefined) });
      setState("sent");
    } catch {
      setError(true);
    }
  };
  if (state === "sent") return <p className="zg-feedback" role="status">Thanks for the feedback.</p>;
  if (state === "reasons") {
    return (
      <div className="zg-feedback" role="group" aria-label="What went wrong?">
        <span>What went wrong?</span>
        {REASONS.map(([code, label]) => (
          <button key={code} type="button" className="zg-chip" onClick={() => send("down", code)}>
            {label}
          </button>
        ))}
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
      {error && <span role="alert">Feedback couldn't be sent.</span>}
    </div>
  );
}

export default function AssistantMessage({ item, onAction, onResume, onSuggest }) {
  const { msg, streaming } = item;
  const boundary = BOUNDARY[msg.answer_class];
  const Icon = boundary?.icon;
  return (
    <div className="zg-row">
      <GuideAvatar />
      <div className="zg-msg">
      <span className="zg-sr">Guide said:</span>
      <div className={`zg-bubble zg-answer${boundary ? " zg-bubble--boundary" : ""}`}>
        {boundary && (
          // Gap/restricted/degraded are never styled as success (Figma DQ-09).
          <span className="zg-badge">
            <Icon aria-hidden="true" size={13} />
            {boundary.label}
          </span>
        )}
        {msg.text ? <RichText text={msg.text} /> : null}
        {streaming && !msg.text && (
          <span className="zg-typing" aria-label="Guide is replying">
            <span />
            <span />
            <span />
          </span>
        )}
      </div>
      {!streaming && (
        <>
          <Sources sources={msg.sources} onAction={onAction} />
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
            <div className="zg-actions" role="group" aria-label="Suggested topics">
              {msg.suggestions.map((s) => (
                <button key={s.intent_id} type="button" className="zg-chip" onClick={() => onSuggest(s)}>
                  {s.label}
                </button>
              ))}
            </div>
          )}
          {msg.message_id && !["degraded", "conversation"].includes(msg.answer_class) && (
            <Feedback messageId={msg.message_id} />
          )}
        </>
      )}
      </div>
    </div>
  );
}
