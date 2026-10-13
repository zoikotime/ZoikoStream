// Conversation history (§6.16): only the signed-in user's own conversations; search and
// permanent deletion.
import { useEffect, useId, useState } from "react";
import { Trash2 } from "lucide-react";
import { guideErrorMessage, opGuide } from "../operatorGuideApi";

export default function HistoryView({ onOpen, activeId, onDeleted }) {
  const searchId = useId();
  const [q, setQ] = useState("");
  const [items, setItems] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    let alive = true;
    const t = setTimeout(() => {
      opGuide.conversations(q)
        .then((rows) => alive && (setItems(rows), setError(null)))
        .catch((e) => alive && setError(guideErrorMessage(e)));
    }, 200);
    return () => {
      alive = false;
      clearTimeout(t);
    };
  }, [q]);

  const remove = async (id) => {
    try {
      await opGuide.deleteConversation(id);
      setItems((rows) => rows.filter((r) => r.id !== id));
      onDeleted(id);
    } catch (e) {
      setError(guideErrorMessage(e));
    }
  };

  return (
    <div className="og-pane">
      <label htmlFor={searchId} className="og-section-title">Search your conversations</label>
      <input id={searchId} type="search" className="og-input" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search" />
      {error && <p className="og-error og-small" role="alert">{error}</p>}
      {items && items.length === 0 && <p className="og-muted og-small">No conversations found.</p>}
      <ul className="og-list">
        {(items || []).map((c) => (
          <li key={c.id} className="og-list-row">
            <button type="button" className="og-list-item" aria-current={c.id === activeId || undefined} onClick={() => onOpen(c.id)}>
              <span className="og-row-label">{c.title}</span>
              <span className="og-row-detail">
                {c.event ? `${c.event.title} · ` : ""}{c.updated_at ? new Date(c.updated_at).toLocaleString() : ""}
              </span>
            </button>
            <button type="button" className="og-icon-btn" aria-label={`Delete conversation: ${c.title}`} onClick={() => remove(c.id)}>
              <Trash2 size={16} aria-hidden="true" />
            </button>
          </li>
        ))}
      </ul>
      <p className="og-fineprint">Conversations are private to you and deleted automatically after the retention period.</p>
    </div>
  );
}
