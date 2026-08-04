// client/src/components/moderation/ParticipantsPanel.jsx
// Left panel — the live roster. Joins, leaves, mute, raised hands, speaking and
// connection quality all arrive over the socket; every action goes back out on it.
// The row stays compact (it's a 300px column): the full action set lives in the
// profile drawer, with mute/remove inline on hover as before.
import { useMemo, useState } from "react";
import {
  FiMic, FiMicOff, FiUserX, FiSearch, FiSlash, FiClock,
  FiArrowUpCircle, FiArrowDownCircle, FiMoreVertical, FiUsers,
  FiVideo, FiVideoOff, FiMonitor, FiMessageSquare, FiAlertTriangle, FiX,
} from "react-icons/fi";
import { cx, ACCENT } from "../../ui/tokens";
import Badge from "../../ui/Badge";
import Drawer from "../../ui/Drawer";
import Skeleton from "../../ui/Skeleton";
import { Input, Select } from "../../ui/forms";
import EmptyState from "../organization/OrganizationEmptyState";
import Panel, { ActionButton } from "./Panel";
import {
  initials, accentFor, hhmm, ROLE_TONE, ROLE_ORDER,
  PARTICIPANT_FILTERS, PARTICIPANT_SORTS, QUALITY, TIMEOUT_OPTIONS,
} from "../../data/moderation";

const roleOf = (p) => p.role || "viewer";
const QUALITY_RANK = { poor: 0, lost: 1, good: 2, excellent: 3 };

const MATCHES = {
  all: () => true,
  hand: (p) => p.hand,
  speaking: (p) => p.speaking,
  muted: (p) => p.muted,
  stage: (p) => p.on_stage || ["host", "speaker"].includes(roleOf(p)),
};

const SORTERS = {
  role: (a, b) => (ROLE_ORDER[roleOf(a)] ?? 9) - (ROLE_ORDER[roleOf(b)] ?? 9),
  name: (a, b) => (a.name || "").localeCompare(b.name || ""),
  joined: (a, b) => (a.joined_at || 0) - (b.joined_at || 0),
  quality: (a, b) => (QUALITY_RANK[a.quality] ?? 9) - (QUALITY_RANK[b.quality] ?? 9),
};

// Presence stores joined_at as an epoch float (Redis-friendly); the roster shows a clock time.
const joinedLabel = (p) =>
  p.joined_at ? new Date(p.joined_at * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : "—";

function QualityDot({ quality }) {
  const q = QUALITY[quality] || QUALITY.excellent;
  return (
    <span
      className={cx("h-2 w-2 shrink-0 rounded-full", q.tone)}
      title={`Connection: ${q.label}`}
      aria-label={`Connection: ${q.label}`}
    />
  );
}

function Row({ label, value }) {
  return (
    <div className="flex items-center justify-between gap-3 border-b border-slate-100 py-2 text-sm last:border-0 dark:border-slate-800">
      <span className="text-slate-500 dark:text-slate-400">{label}</span>
      <span className="text-right font-medium text-slate-800 dark:text-slate-100">{value}</span>
    </div>
  );
}

// Full-width labelled action. Labels (not icons) because an icon-only "ban" in a 300px
// row is how a moderator bans the wrong person.
function Action({ icon: Icon, label, tone = "slate", onClick }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cx(
        "flex w-full items-center gap-2 rounded-lg border px-3 py-2 text-sm font-medium transition",
        tone === "rose"
          ? "border-rose-200 text-rose-600 hover:bg-rose-50 dark:border-rose-500/30 dark:text-rose-400 dark:hover:bg-rose-500/10"
          : "border-slate-200 text-slate-700 hover:bg-slate-50 dark:border-slate-700 dark:text-slate-200 dark:hover:bg-slate-800"
      )}
    >
      <Icon className="shrink-0 text-base" aria-hidden="true" />
      {label}
    </button>
  );
}

// The full action set for one participant, in a drawer so each action can be labelled.
function ProfileDrawer({ p, open, onClose, canModerate, send }) {
  const [minutes, setMinutes] = useState(5);
  const [warning, setWarning] = useState("");
  if (!p) return null;

  const act = (action, payload) => send(action, { identity: p.identity, ...payload });
  const role = roleOf(p);
  const q = QUALITY[p.quality] || QUALITY.excellent;
  const onStage = p.on_stage || ["host", "speaker"].includes(role);

  return (
    <Drawer open={open} onClose={onClose} title="Participant">
      <div className="flex items-center gap-3">
        <span className={cx("grid h-12 w-12 shrink-0 place-items-center rounded-full text-base font-semibold", ACCENT[accentFor(p.identity)].chip)}>
          {initials(p.name)}
        </span>
        <div className="min-w-0">
          <p className="truncate font-semibold text-slate-900 dark:text-white">{p.name || p.identity}</p>
          <div className="mt-1 flex items-center gap-2">
            <Badge tone={ROLE_TONE[role]} size="sm">{role}</Badge>
            {p.hand && <Badge tone="warning" size="sm">Hand raised</Badge>}
          </div>
        </div>
      </div>

      <div className="mt-5">
        <Row label="Status" value={p.speaking ? "Speaking" : p.muted ? "Muted" : "Listening"} />
        <Row label="Network quality" value={<span className="inline-flex items-center gap-2"><QualityDot quality={p.quality} />{q.label}</span>} />
        <Row label="Publishing media" value={p.publishing ? "Yes" : "No"} />
        <Row label="On stage" value={onStage ? "Yes" : "No"} />
        <Row label="Joined" value={joinedLabel(p)} />
        {p.muted_until && <Row label="Muted until" value={hhmm(p.muted_until)} />}
        {p.chat_muted && (
          <Row label="Chat" value={p.chat_muted_until ? `Muted until ${hhmm(p.chat_muted_until)}` : "Muted"} />
        )}
        {p.camera_allowed === false && <Row label="Camera" value="Disabled by a moderator" />}
        {p.share_allowed === false && <Row label="Screen share" value="Disabled by a moderator" />}
      </div>

      {!canModerate ? (
        <p className="mt-5 rounded-lg bg-slate-50 px-3 py-2 text-xs text-slate-500 dark:bg-slate-800/60 dark:text-slate-400">
          You're viewing this console read-only.
        </p>
      ) : (
        <div className="mt-5 space-y-2">
          <p className="text-xs font-semibold uppercase tracking-wide text-slate-400">Actions</p>
          <Action
            icon={p.muted ? FiMic : FiMicOff}
            label={p.muted ? "Unmute" : "Mute"}
            onClick={() => act("participant.mute", { muted: !p.muted })}
          />

          <div className="flex items-center gap-2">
            <Select variant="console" className="w-28" value={minutes} onChange={(e) => setMinutes(Number(e.target.value))} aria-label="Timeout length">
              {TIMEOUT_OPTIONS.map((m) => <option key={m} value={m}>{m} min</option>)}
            </Select>
            <div className="flex-1">
              <Action icon={FiClock} label="Temporary mute" onClick={() => act("participant.timeout", { minutes })} />
            </div>
          </div>

          {/* Chat mute, separate from the microphone above. The server refuses this against a
              host or moderator — staff bypass audience controls, so a mute on them would show
              in the roster while their messages kept landing. */}
          <Action
            icon={p.chat_muted ? FiMessageSquare : FiSlash}
            label={p.chat_muted ? "Unmute in chat" : `Mute in chat (${minutes} min)`}
            onClick={() => act("chat.mute", { muted: !p.chat_muted, minutes })}
          />

          <Action
            icon={p.camera_allowed === false ? FiVideo : FiVideoOff}
            label={p.camera_allowed === false ? "Allow camera" : "Disable camera"}
            onClick={() => act("stage.camera", { allowed: p.camera_allowed === false })}
          />
          <Action
            icon={FiMonitor}
            label={p.share_allowed === false ? "Allow screen share" : "Disable screen share"}
            onClick={() => act("stage.share", { allowed: p.share_allowed === false })}
          />

          <Action
            icon={onStage ? FiArrowDownCircle : FiArrowUpCircle}
            label={onStage ? "Remove from stage" : "Invite to stage"}
            onClick={() => act("participant.stage", { on_stage: !onStage })}
          />
          {p.hand && (
            <Action icon={FiX} label="Decline raised hand" onClick={() => act("participant.dismiss_hand")} />
          )}

          {/* Warn before you remove. A private notice is the only escalation step between
              "muted" and "gone", and it reaches only this person (routers/live.py narrows it). */}
          <form
            onSubmit={(e) => {
              e.preventDefault();
              if (!warning.trim()) return;
              act("participant.notify", { text: warning.trim() });
              setWarning("");
            }}
            className="flex items-center gap-2"
          >
            <Input
              variant="console"
              value={warning}
              onChange={(e) => setWarning(e.target.value)}
              placeholder="Warn privately…"
              aria-label={`Send ${p.name || p.identity} a private warning`}
            />
            <button
              type="submit"
              className="grid h-9 w-9 shrink-0 place-items-center rounded-lg bg-amber-600 text-white transition hover:bg-amber-500"
              aria-label="Send warning"
            >
              <FiAlertTriangle className="text-sm" />
            </button>
          </form>
          <Action
            icon={FiArrowUpCircle}
            label={role === "viewer" ? "Promote to speaker" : "Promote to moderator"}
            onClick={() => act("participant.role", { role: role === "viewer" ? "speaker" : "moderator" })}
          />
          <Action icon={FiArrowDownCircle} label="Demote to viewer" onClick={() => act("participant.role", { role: "viewer" })} />
          <Action icon={FiUserX} label="Remove from event" tone="rose" onClick={() => { act("participant.remove"); onClose(); }} />
          <Action icon={FiSlash} label="Ban (cannot rejoin)" tone="rose" onClick={() => { act("participant.ban"); onClose(); }} />
        </div>
      )}
    </Drawer>
  );
}

export default function ParticipantsPanel({ participants, canModerate, loading, send, className }) {
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState("all");
  const [sort, setSort] = useState("role");
  const [selected, setSelected] = useState(null);

  const shown = useMemo(() => {
    const q = query.trim().toLowerCase();
    return participants
      .filter(MATCHES[filter])
      .filter((p) => !q || `${p.name || ""} ${p.identity}`.toLowerCase().includes(q))
      .sort(SORTERS[sort]);
  }, [participants, query, filter, sort]);

  const hands = participants.filter((p) => p.hand).length;
  // Keep the drawer's data live: `selected` holds an identity, never a stale snapshot.
  const active = selected ? participants.find((p) => p.identity === selected) : null;

  return (
    <Panel
      title="Participants"
      count={participants.length}
      badge={hands > 0 && <Badge tone="warning" size="sm" dot>{hands} hand{hands > 1 ? "s" : ""}</Badge>}
      className={className}
      toolbar={
        <>
          <div className="relative">
            <FiSearch className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" aria-hidden="true" />
            <Input
              variant="console"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search participants…"
              aria-label="Search participants"
              className="pl-9"
            />
          </div>
          <div className="flex items-center gap-2">
            <Select variant="console" className="flex-1" value={filter} onChange={(e) => setFilter(e.target.value)} aria-label="Filter participants">
              {PARTICIPANT_FILTERS.map((f) => <option key={f.key} value={f.key}>{f.label}</option>)}
            </Select>
            <Select variant="console" className="flex-1" value={sort} onChange={(e) => setSort(e.target.value)} aria-label="Sort participants">
              {PARTICIPANT_SORTS.map((s) => <option key={s.key} value={s.key}>{s.label}</option>)}
            </Select>
          </div>
        </>
      }
    >
      {loading && (
        <div className="space-y-2" aria-hidden="true">
          {[0, 1, 2, 3, 4].map((i) => <Skeleton key={i} className="h-14 rounded-xl" />)}
        </div>
      )}

      {!loading && (
        <div className="space-y-1">
          {shown.map((p) => {
            const role = roleOf(p);
            return (
              <div
                key={p.identity}
                className={cx(
                  "group flex items-center gap-2.5 rounded-xl px-2 py-2 transition motion-safe:animate-[zk-fade-in_.25s]",
                  p.speaking ? "bg-emerald-50 dark:bg-emerald-500/10" : "hover:bg-slate-50 dark:hover:bg-slate-800/60"
                )}
              >
                <span className="relative shrink-0">
                  <span className={cx("grid h-9 w-9 place-items-center rounded-full text-sm font-semibold", ACCENT[accentFor(p.identity)].chip)}>
                    {initials(p.name)}
                  </span>
                  {/* Speaking ring — the fastest read in a long roster. */}
                  {p.speaking && <span className="absolute inset-0 rounded-full ring-2 ring-emerald-500 motion-safe:animate-pulse" aria-hidden="true" />}
                </span>

                <button
                  type="button"
                  onClick={() => setSelected(p.identity)}
                  className="min-w-0 flex-1 text-left"
                  aria-label={`Open ${p.name || p.identity}'s profile`}
                >
                  <p className="flex items-center gap-1.5 truncate text-sm font-medium text-slate-800 dark:text-slate-100">
                    <QualityDot quality={p.quality} />
                    <span className="truncate">{p.name || p.identity}</span>
                    {p.hand && <span title="Hand raised" aria-label="Hand raised">✋</span>}
                  </p>
                  <p className="truncate text-xs text-slate-400">
                    {role} · joined {joinedLabel(p)}
                  </p>
                </button>

                {p.muted && <Badge tone="danger" size="sm">Muted</Badge>}
                {p.chat_muted && <Badge tone="warning" size="sm" title="Muted in chat">No chat</Badge>}
                {!p.muted && p.speaking && <Badge tone="success" size="sm">Speaking</Badge>}

                {canModerate && (
                  <div className="flex items-center gap-0.5 opacity-100 sm:opacity-0 sm:transition sm:group-focus-within:opacity-100 sm:group-hover:opacity-100">
                    <ActionButton
                      icon={p.muted ? FiMic : FiMicOff}
                      title={p.muted ? `Unmute ${p.name}` : `Mute ${p.name}`}
                      tone="amber"
                      onClick={() => send("participant.mute", { identity: p.identity, muted: !p.muted })}
                    />
                    <ActionButton icon={FiMoreVertical} title={`More actions for ${p.name}`} onClick={() => setSelected(p.identity)} />
                  </div>
                )}
              </div>
            );
          })}

          {shown.length === 0 && (
            <EmptyState
              icon={FiUsers}
              title={participants.length === 0 ? "Nobody here yet" : "No matches"}
              description={
                participants.length === 0
                  ? "Participants appear the moment they join the event."
                  : "Try a different search or filter."
              }
              className="py-10"
            />
          )}
        </div>
      )}

      <ProfileDrawer p={active} open={!!active} onClose={() => setSelected(null)} canModerate={canModerate} send={send} />
    </Panel>
  );
}
