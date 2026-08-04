// client/src/components/moderation/LobbyPanel.jsx
// The waiting room, as a first-class panel. The host console has a compact strip of this
// inside its People tab (components/host/HostPanel.WaitingRoom); a moderator OWNS the lobby,
// so here it gets search, sort, waiting time and bulk decisions.
//
// Every action is an existing socket action — stage.admit and stage.admit_all (which takes
// `admit: false` for the bulk reject). Nothing new was added for this panel.
import { useMemo, useState } from "react";
import { FiCheck, FiX, FiSearch, FiClock, FiUserCheck, FiMonitor, FiGlobe } from "react-icons/fi";
import { cx, ACCENT } from "../../ui/tokens";
import Badge from "../../ui/Badge";
import Skeleton from "../../ui/Skeleton";
import { Input, Select } from "../../ui/forms";
import EmptyState from "../organization/OrganizationEmptyState";
import Panel, { ActionButton } from "./Panel";
import useInterval from "../../hooks/useInterval";
import { initials, accentFor, LOBBY_SORTS } from "../../data/moderation";

// Waiting time is the whole reason this panel sorts: the person who has been staring at a
// "you'll be let in shortly" screen for six minutes is the one to admit next.
const waitedSeconds = (p, now) => (p.joined_at ? Math.max(0, Math.round(now / 1000 - p.joined_at)) : 0);

const fmtWaited = (s) => (s >= 60 ? `${Math.floor(s / 60)}m ${s % 60}s` : `${s}s`);

// Over this long in the queue, the row is flagged — it is the honest signal that the lobby is
// being neglected rather than worked.
const STALE_SECONDS = 180;

const SORTERS = {
  waiting: (a, b) => (a.joined_at || 0) - (b.joined_at || 0),   // longest wait first
  recent: (a, b) => (b.joined_at || 0) - (a.joined_at || 0),
  name: (a, b) => (a.name || "").localeCompare(b.name || ""),
};

export default function LobbyPanel({ waiting = [], canModerate, loading, send, className }) {
  const [query, setQuery] = useState("");
  const [sort, setSort] = useState("waiting");
  const [confirmDenyAll, setConfirmDenyAll] = useState(false);

  // One second tick, only while somebody is actually waiting — otherwise this panel would
  // re-render forever on an empty lobby.
  const [now, setNow] = useState(() => Date.now());
  useInterval(() => setNow(Date.now()), 1000, waiting.length > 0);

  const shown = useMemo(() => {
    const q = query.trim().toLowerCase();
    return waiting
      .filter((p) => !q || `${p.name || ""} ${p.identity}`.toLowerCase().includes(q))
      .sort(SORTERS[sort]);
  }, [waiting, query, sort]);

  const longest = waiting.length
    ? Math.max(...waiting.map((p) => waitedSeconds(p, now)))
    : 0;

  return (
    <Panel
      title="Lobby"
      count={waiting.length}
      badge={longest >= STALE_SECONDS && (
        <Badge tone="warning" size="sm" dot>{fmtWaited(longest)} longest wait</Badge>
      )}
      className={className}
      action={canModerate && waiting.length > 0 && (
        <div className="flex items-center gap-1">
          <ActionButton
            icon={FiUserCheck}
            label="Admit all"
            tone="emerald"
            onClick={() => { setConfirmDenyAll(false); send("stage.admit_all", { admit: true }); }}
          />
          {/* Two-step, because rejecting a whole queue disconnects real people and there is
              no undo — the same arm-then-confirm shape as the host's mute-all. */}
          <ActionButton
            icon={FiX}
            label={confirmDenyAll ? "Confirm reject all" : "Reject all"}
            tone="rose"
            active={confirmDenyAll}
            onClick={() =>
              confirmDenyAll
                ? (setConfirmDenyAll(false), send("stage.admit_all", { admit: false }))
                : setConfirmDenyAll(true)}
          />
        </div>
      )}
      toolbar={waiting.length > 0 && (
        <div className="flex items-center gap-2">
          <div className="relative flex-1">
            <FiSearch className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" aria-hidden="true" />
            <Input
              variant="console"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search the queue…"
              aria-label="Search the lobby queue"
              className="pl-9"
            />
          </div>
          <Select variant="console" className="w-36" value={sort} onChange={(e) => setSort(e.target.value)} aria-label="Sort the lobby queue">
            {LOBBY_SORTS.map((s) => <option key={s.key} value={s.key}>{s.label}</option>)}
          </Select>
        </div>
      )}
    >
      {loading && (
        <div className="space-y-2" aria-hidden="true">
          {[0, 1, 2].map((i) => <Skeleton key={i} className="h-14 rounded-xl" />)}
        </div>
      )}

      {!loading && (
        <div className="space-y-1.5">
          {shown.map((p) => {
            const waited = waitedSeconds(p, now);
            const stale = waited >= STALE_SECONDS;
            return (
              <div
                key={p.identity}
                className={cx(
                  "flex items-center gap-2.5 rounded-xl border px-2.5 py-2 transition motion-safe:animate-[zk-fade-in_.25s]",
                  stale
                    ? "border-amber-300 bg-amber-50/60 dark:border-amber-500/40 dark:bg-amber-500/10"
                    : "border-slate-100 dark:border-slate-800"
                )}
              >
                <span className={cx("grid h-9 w-9 shrink-0 place-items-center rounded-full text-sm font-semibold", ACCENT[accentFor(p.identity)].chip)}>
                  {initials(p.name)}
                </span>
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm font-medium text-slate-800 dark:text-slate-100">
                    {p.name || p.identity}
                  </p>
                  {/* Participant details, from the socket handshake — nothing inferred beyond
                      what the user agent stated (services/broadcast.classify_ua). */}
                  <p className="flex flex-wrap items-center gap-x-2 gap-y-0.5 truncate text-xs text-slate-400">
                    <span className="inline-flex items-center gap-1">
                      <FiClock aria-hidden="true" /> waiting {fmtWaited(waited)}
                    </span>
                    {p.device && <span className="inline-flex items-center gap-1"><FiMonitor aria-hidden="true" />{p.device}</span>}
                    {p.browser && <span className="inline-flex items-center gap-1"><FiGlobe aria-hidden="true" />{p.browser}</span>}
                  </p>
                </div>
                {canModerate && (
                  <div className="flex shrink-0 items-center gap-1">
                    <ActionButton
                      icon={FiCheck}
                      label="Admit"
                      tone="emerald"
                      title={`Admit ${p.name || p.identity}`}
                      onClick={() => send("stage.admit", { identity: p.identity, admit: true })}
                    />
                    <ActionButton
                      icon={FiX}
                      tone="rose"
                      title={`Reject ${p.name || p.identity}`}
                      onClick={() => send("stage.admit", { identity: p.identity, admit: false })}
                    />
                  </div>
                )}
              </div>
            );
          })}

          {shown.length === 0 && (
            <EmptyState
              icon={FiUserCheck}
              title={waiting.length === 0 ? "Nobody waiting" : "No matches"}
              description={
                waiting.length === 0
                  ? "With the waiting room on, arrivals queue here for you to admit. It's off unless the host enabled it."
                  : "Try a different search."
              }
              className="py-10"
            />
          )}
        </div>
      )}
    </Panel>
  );
}
