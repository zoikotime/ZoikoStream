// Guide home: a live quick view when the context event is on air (§6.9), what needs attention
// now (§20), suggested questions for this page and role (§6.1), and recent conversations.
import { Radio } from "lucide-react";
import { AttentionItems } from "./Blocks";

const LIVE_ASK = "How is the broadcast doing?";

function LiveQuickView({ event, onAsk }) {
  const degraded = event.status === "degraded";
  return (
    <section className="og-live-quick" aria-labelledby="og-live-h">
      <h3 id="og-live-h" className="og-section-title">
        <Radio size={14} aria-hidden="true" /> {degraded ? "Live · degraded" : "Live now"}
      </h3>
      <p className="og-row-label">{event.title}</p>
      <div className="og-inline-actions">
        <button type="button" className="og-btn og-btn-primary" onClick={() => onAsk(LIVE_ASK, event.id)}>{LIVE_ASK}</button>
        <button type="button" className="og-btn" onClick={() => onAsk("Why are viewers dropping?", event.id)}>Check audience</button>
      </div>
    </section>
  );
}

// Super Admin welcome: the platform at a glance. Each tile is a button that asks the question
// which explains it, so the numbers are never a dead end. Text carries the state, never colour alone.
const STATE_TEXT = { ready: "Healthy", review: "Needs a look", blocked: "Problem", active: "Active", inactive: "Idle" };

function PlatformTile({ tile, onAsk }) {
  return (
    <button type="button" className={`og-tile og-tile-${tile.state}`} onClick={() => onAsk(tile.ask)}
      aria-label={`${tile.label}: ${tile.value}. ${tile.detail}. Ask for details.`}>
      <span className="og-tile-label">{tile.label}</span>
      <span className="og-tile-value">{tile.value}</span>
      <span className="og-tile-detail">{tile.detail}</span>
      <span className="og-tile-state">{STATE_TEXT[tile.state] || ""}</span>
    </button>
  );
}

function PlatformHome({ boot, attention, onAsk, onNavigate, onOpenConversation }) {
  const name = boot?.user?.name?.split(" ")[0];
  return (
    <div className="og-home og-home-platform">
      <header className="og-hero">
        <p className="og-hero-kicker">Platform overview</p>
        <h2 className="og-hero-title">{name ? `Welcome back, ${name}.` : "Welcome back."}</h2>
        <p className="og-hero-sub">Ask about platform health, live events, support and analytics across every organization.</p>
      </header>

      <section aria-label="Platform at a glance">
        <div className="og-tiles">
          {boot.platform.map((t) => <PlatformTile key={t.id} tile={t} onAsk={onAsk} />)}
        </div>
      </section>

      <section aria-labelledby="og-attention-h">
        <h3 id="og-attention-h" className="og-section-title">
          What needs attention?
          {attention.count > 0 && <span className="og-count">{attention.count}</span>}
        </h3>
        {attention.items?.length
          ? <AttentionItems items={attention.items} onAsk={onAsk} onNavigate={onNavigate} />
          : <p className="og-muted og-small">Nothing needs your attention right now.</p>}
      </section>

      {boot.suggestions?.length > 0 && (
        <section aria-labelledby="og-suggest-h">
          <h3 id="og-suggest-h" className="og-section-title">Try asking</h3>
          <div className="og-ask-grid">
            {boot.suggestions.map((s) => (
              <button key={s} type="button" className="og-ask-card" onClick={() => onAsk(s)}>{s}</button>
            ))}
          </div>
        </section>
      )}

      {boot.recent?.length > 0 && (
        <section aria-labelledby="og-recent-h">
          <h3 id="og-recent-h" className="og-section-title">Recent</h3>
          <ul className="og-list">
            {boot.recent.map((c) => (
              <li key={c.id}>
                <button type="button" className="og-list-item" onClick={() => onOpenConversation(c.id)}>
                  <span className="og-row-label">{c.title}</span>
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

export default function Home({ boot, context, attention, onAsk, onNavigate, onOpenConversation }) {
  if (boot?.platform?.length) {
    return <PlatformHome boot={boot} attention={attention} onAsk={onAsk} onNavigate={onNavigate}
      onOpenConversation={onOpenConversation} />;
  }
  const name = boot?.user?.name?.split(" ")[0];
  const event = context?.event;
  const live = ["live", "degraded"].includes(event?.status);
  return (
    <div className="og-home">
      <p className="og-greeting">{name ? `Hello, ${name}.` : "Hello."} What would you like to check?</p>

      {live && <LiveQuickView event={event} onAsk={onAsk} />}

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
            {boot.suggestions.filter((s) => !(live && s === LIVE_ASK)).map((s) => (
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
