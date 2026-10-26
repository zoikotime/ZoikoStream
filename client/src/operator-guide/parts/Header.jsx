// Panel header: who/where Guide is working (§6.1 — organization · workspace · event ·
// environment) and the controls to switch view, expand and close.
import { useEffect, useId, useState } from "react";
import { History, Maximize2, Minimize2, MessageSquarePlus, Settings, X } from "lucide-react";
import { opGuide } from "../operatorGuideApi";

function EventSelect({ context, onSelect }) {
  const id = useId();
  const [events, setEvents] = useState(null);
  useEffect(() => {
    let alive = true;
    opGuide.events().then((items) => alive && setEvents(items)).catch(() => alive && setEvents([]));
    return () => {
      alive = false;
    };
  }, [context?.organization?.id]);
  const current = context?.event?.id || "";
  const options = events || [];
  const known = !current || options.some((e) => e.id === current);
  return (
    <div className="og-event-select">
      <label htmlFor={id} className="og-sr">Event context</label>
      <select
        id={id}
        value={current}
        onChange={(e) => {
          const picked = options.find((ev) => ev.id === e.target.value);
          onSelect(e.target.value || null, picked?.title ?? null);
        }}
      >
        <option value="">No event selected</option>
        {!known && <option value={current}>{context.event.title}</option>}
        {options.map((e) => (
          <option key={e.id} value={e.id}>{e.title}{e.status === "live" ? " · LIVE" : ""}</option>
        ))}
      </select>
    </div>
  );
}

export default function Header({ titleId, context, view, setView, expanded, setExpanded, onClose, onNew, onSelectEvent, canSettings }) {
  const crumbs = [context?.organization?.name, context?.workspace].filter(Boolean);
  return (
    <header className="og-head">
      <div className="og-head-top">
        <div className="og-head-title">
          <h2 id={titleId}>Guide</h2>
          {context?.environment === "test" && <span className="og-badge og-tone-warn">Test environment</span>}
        </div>
        <div className="og-head-tools">
          <button type="button" className="og-icon-btn" aria-label="New conversation" title="New conversation" onClick={onNew}>
            <MessageSquarePlus size={18} aria-hidden="true" />
          </button>
          <button type="button" className="og-icon-btn" aria-label="Conversation history" aria-pressed={view === "history"} title="History" onClick={() => setView(view === "history" ? "chat" : "history")}>
            <History size={18} aria-hidden="true" />
          </button>
          {canSettings && (
            <button type="button" className="og-icon-btn" aria-label="Guide settings" aria-pressed={view === "settings"} title="Guide settings" onClick={() => setView(view === "settings" ? "chat" : "settings")}>
              <Settings size={18} aria-hidden="true" />
            </button>
          )}
          <button type="button" className="og-icon-btn og-desktop-only" aria-label={expanded ? "Collapse panel" : "Expand panel"} title={expanded ? "Collapse" : "Expand"} onClick={() => setExpanded((v) => !v)}>
            {expanded ? <Minimize2 size={18} aria-hidden="true" /> : <Maximize2 size={18} aria-hidden="true" />}
          </button>
          <button type="button" className="og-icon-btn" aria-label="Close Guide" title="Close (Esc)" onClick={onClose}>
            <X size={18} aria-hidden="true" />
          </button>
        </div>
      </div>
      <div className="og-context">
        <p className="og-crumbs">{crumbs.join(" · ")}</p>
        <EventSelect context={context} onSelect={onSelectEvent} />
      </div>
    </header>
  );
}
