// Guide home: what needs attention now (§20), suggested questions for this context, and
// recent conversations.
import { AttentionItems } from "./Blocks";

export default function Home({ boot, attention, onAsk, onNavigate, onOpenConversation }) {
  const name = boot?.user?.name?.split(" ")[0];
  return (
    <div className="og-home">
      <p className="og-greeting">{name ? `Hello, ${name}.` : "Hello."} What would you like to check?</p>

      <section aria-labelledby="og-attention-h">
        <h3 id="og-attention-h" className="og-section-title">
          What needs attention?
          {attention.count > 0 && <span className="og-count">{attention.count}</span>}
        </h3>
        {attention.items?.length
          ? <AttentionItems items={attention.items} onAsk={onAsk} onNavigate={onNavigate} />
          : <p className="og-muted og-small">Nothing needs your attention right now.</p>}
      </section>

      {boot?.suggestions?.length > 0 && (
        <section aria-labelledby="og-suggest-h">
          <h3 id="og-suggest-h" className="og-section-title">Suggested questions</h3>
          <div className="og-suggestions">
            {boot.suggestions.map((s) => (
              <button key={s} type="button" className="og-chip" onClick={() => onAsk(s)}>{s}</button>
            ))}
          </div>
        </section>
      )}

      {boot?.recent?.length > 0 && (
        <section aria-labelledby="og-recent-h">
          <h3 id="og-recent-h" className="og-section-title">Recent</h3>
          <ul className="og-list">
            {boot.recent.map((c) => (
              <li key={c.id}>
                <button type="button" className="og-list-item" onClick={() => onOpenConversation(c.id)}>
                  <span className="og-row-label">{c.title}</span>
                  {c.event && <span className="og-row-detail">{c.event.title}</span>}
                </button>
              </li>
            ))}
          </ul>
        </section>
      )}
      <p className="og-fineprint">
        Guide uses your permissions, shows its sources and asks before changing anything.
        Critical live controls stay in the host console.
      </p>
    </div>
  );
}
