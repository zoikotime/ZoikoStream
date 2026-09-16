// client/src/components/moderation/ParticipantsPanel.jsx
// Left panel — the live roster. Joins, leaves, mute, raised hands, speaking and
// connection quality all arrive over the socket; every action goes back out on it.
// The row stays compact (it's a 300px column): the full action set lives in the
// profile drawer, with mute/remove inline on hover as before.
import { useMemo, useState } from "react";
import {
  FiMic, FiMicOff, FiUserX, FiSlash, FiClock,
  FiArrowUpCircle, FiArrowDownCircle, FiMoreVertical, FiUsers,
} from "react-icons/fi";
import { cx, ACCENT, focusRing } from "../../ui/tokens";
import Badge from "../../ui/Badge";
import Drawer from "../../ui/Drawer";
import Skeleton from "../../ui/Skeleton";
import { Select } from "../../ui/forms";
import EmptyState from "../organization/OrganizationEmptyState";
import Panel, { ActionButton } from "./Panel";
import { PANEL } from "./panelTokens";
import SearchField from "./SearchField";
import {
  initials, accentFor, hhmm, ROLE_TONE, ROLE_ORDER,
  PARTICIPANT_FILTERS, PARTICIPANT_SORTS, QUALITY, TIMEOUT_OPTIONS,
} from "../../data/moderation";

const roleOf = (p) => p.role || "viewer";
const QUALITY_RANK = { poor: 0, lost: 1, good: 2, excellent: 3 };

// ── THE "SPEAKING" INDICATOR WAS REMOVED ────────────────────────────────────────────────
// `presence.speaking` is initialised False and written by exactly one thing —
// services/moderation._participant_state — which no client ever called. So the ring, the
// badge, the Status line and the filter below could never be anything but "not speaking",
// and a host reading "Listening" learned nothing about whether that person was talking.
//
// Deliberately NOT faked from `publishing`: a published microphone is somebody who CAN be
// heard, not somebody who is currently talking, and conflating the two is exactly what this
// audit asked not to do.
//
// Wiring it for real is possible and is the follow-up: livekit-client raises
// RoomEvent.ActiveSpeakersChanged, so a speaker's own client could report it through the
// participant.state action the self-mute fix now uses. It is left out here because it means
// a socket message per speech burst per speaker, which is a traffic decision rather than a
// bug fix.
const MATCHES = {
  all: () => true,
  hand: (p) => p.hand,
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
// row is how an operator bans the wrong person.
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
function ProfileDrawer({ p, open, onClose, canModerate, canHost, send }) {
  const [minutes, setMinutes] = useState(5);
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
          <span className="block truncate font-semibold text-slate-900 dark:text-white">{p.name || p.identity}</span>
          <div className="mt-1 flex items-center gap-2">
            <Badge tone={ROLE_TONE[role]} size="sm">{role}</Badge>
            {p.hand && <Badge tone="warning" size="sm">Hand raised</Badge>}
          </div>
        </div>
      </div>

      <div className="mt-5">
        {/* Muted vs not is real — presence.muted is written by the host's own mute and,
            since the self-mute fix, by the speaker reporting their own state. */}
        <Row label="Status" value={p.muted ? "Muted" : "Unmuted"} />
        <Row label="Network quality" value={<span className="inline-flex items-center gap-2"><QualityDot quality={p.quality} />{q.label}</span>} />
        <Row label="Publishing media" value={p.publishing ? "Yes" : "No"} />
        <Row label="On stage" value={onStage ? "Yes" : "No"} />
        <Row label="Joined" value={joinedLabel(p)} />
        {p.muted_until && <Row label="Muted until" value={hhmm(p.muted_until)} />}
      </div>

      {!canModerate ? (
        <p className="mt-5 rounded-lg bg-slate-50 px-3 py-2 text-xs text-slate-500 dark:bg-slate-800/60 dark:text-slate-400">
          You're viewing this console read-only.
        </p>
      ) : (
        <div className="mt-5 space-y-2">
          <p className="text-xs font-semibold uppercase tracking-wide text-slate-400">Actions</p>
          {/* The microphone action depends on whether there is a microphone to act on.
              LiveKit mutes PER TRACK, so "unmute" only means anything against a track that
              already exists. The old single button offered "Unmute" to a viewer publishing
              nothing: the server dutifully mutedNothing, answered success, and the console
              showed them live while the room stayed silent. A host cannot turn on somebody
              else's microphone — only ask for it — so each state now offers the action that
              can actually succeed.

                not on stage        -> Invite to speak     (a real publish grant)
                on stage, no track  -> Request microphone  (a request; they decide)
                publishing + muted  -> Request to unmute   (a request; they decide)
                publishing + live   -> Mute                (a real server-side mute) */}
          {!onStage ? (
            <Action
              icon={FiArrowUpCircle}
              label="Invite to speak"
              onClick={() => act("participant.stage", { on_stage: true })}
            />
          ) : !p.publishing ? (
            <Action
              icon={FiMic}
              label="Request microphone"
              onClick={() => act("participant.request_unmute")}
            />
          ) : p.muted ? (
            <Action
              icon={FiMic}
              label="Request to unmute"
              onClick={() => act("participant.request_unmute")}
            />
          ) : (
            <Action
              icon={FiMicOff}
              label="Mute"
              onClick={() => act("participant.mute", { muted: true })}
            />
          )}

          <div className="flex items-center gap-2">
            <Select variant="console" className="w-28" value={minutes} onChange={(e) => setMinutes(Number(e.target.value))} aria-label="Timeout length">
              {TIMEOUT_OPTIONS.map((m) => <option key={m} value={m}>{m} min</option>)}
            </Select>
            <div className="flex-1">
              <Action icon={FiClock} label="Temporary mute" onClick={() => act("participant.timeout", { minutes })} />
            </div>
          </div>

          <Action
            icon={onStage ? FiArrowDownCircle : FiArrowUpCircle}
            label={onStage ? "Remove from stage" : "Invite to stage"}
            onClick={() => act("participant.stage", { on_stage: !onStage })}
          />
          {/* The promotion ladder is viewer -> speaker -> host now that the moderator rung
              is retired. The top rung is rendered ONLY for canHost: promoting to host hands
              over broadcast control (go live / end / emergency stop / recording), and the
              server refuses it outright for anyone who does not already hold it
              (services/moderation._participant_action -> "Only the event host can grant host
              access"). Offering a button that is guaranteed to fail is worse than not
              offering it — and this is the visible half of a real authorization boundary,
              not decoration: someone whose can_moderate comes from a legacy moderator
              assignment has can_host false and must not be able to mint a host. */}
          {role === "viewer" ? (
            <Action
              icon={FiArrowUpCircle}
              label="Promote to speaker"
              onClick={() => act("participant.role", { role: "speaker" })}
            />
          ) : canHost ? (
            <Action
              icon={FiArrowUpCircle}
              label="Promote to host"
              onClick={() => act("participant.role", { role: "host" })}
            />
          ) : null}
          <Action icon={FiArrowDownCircle} label="Demote to viewer" onClick={() => act("participant.role", { role: "viewer" })} />
          <Action icon={FiUserX} label="Remove from event" tone="rose" onClick={() => { act("participant.remove"); onClose(); }} />
          <Action icon={FiSlash} label="Ban (cannot rejoin)" tone="rose" onClick={() => { act("participant.ban"); onClose(); }} />
        </div>
      )}
    </Drawer>
  );
}

export default function ParticipantsPanel({ participants, canModerate, canHost, loading, send, className }) {
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
          <SearchField
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search participants…"
            label="Search participants"
          />
          <div className="flex items-center gap-2">
            <Select variant="console" className="h-8 flex-1 py-0 text-[13px]" value={filter} onChange={(e) => setFilter(e.target.value)} aria-label="Filter participants">
              {PARTICIPANT_FILTERS.map((f) => <option key={f.key} value={f.key}>{f.label}</option>)}
            </Select>
            <Select variant="console" className="h-8 flex-1 py-0 text-[13px]" value={sort} onChange={(e) => setSort(e.target.value)} aria-label="Sort participants">
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
        <div className="space-y-0.5">
          {shown.map((p, i) => {
            const role = roleOf(p);
            const isSelected = selected === p.identity;
            return (
              // Sequential index comes from the RENDERED order, so it renumbers with the
              // active sort and filter rather than pretending to be a stable participant id.
              <div
                key={p.identity}
                className={cx(
                  "group relative flex items-center gap-2 px-2 py-1.5 motion-safe:animate-[zk-fade-in_.25s]",
                  PANEL.row,
                  PANEL.t150,
                  isSelected ? cx(PANEL.rowActive, PANEL.rowAccent) : PANEL.rowHover
                )}
              >
                <span
                  className={cx("w-4 shrink-0 text-right text-[10px] font-semibold tabular-nums", PANEL.faint)}
                  aria-hidden="true"
                >
                  {String(i + 1).padStart(2, "0")}
                </span>

                <span className="relative shrink-0">
                  <span className={cx("grid h-8 w-8 place-items-center rounded-full text-[11px] font-semibold", ACCENT[accentFor(p.identity)].chip)}>
                    {initials(p.name)}
                  </span>
                </span>

                <button
                  type="button"
                  onClick={() => setSelected(p.identity)}
                  className={cx("min-w-0 flex-1 rounded-md text-left", focusRing)}
                  aria-label={`Open ${p.name || p.identity}'s profile`}
                >
                  {/* <span>, not <p>: index.css's unlayered `p { text-wrap: pretty }`
                      outranks Tailwind's @layer utilities and strips `truncate`'s
                      white-space:nowrap, so a <p class="truncate"> wraps and is then clipped
                      mid-line. See the note in Panel.jsx. */}
                  <span className={cx("flex items-center gap-1.5 text-[13px] font-medium leading-tight", PANEL.body)}>
                    <QualityDot quality={p.quality} />
                    <span className="truncate">{p.name || p.identity}</span>
                    {p.hand && <span title="Hand raised" aria-label="Hand raised">✋</span>}
                  </span>
                  {/* Role lives in the badge on the right, so it is NOT repeated here —
                      carrying it twice was squeezing the join time into an ellipsis in a
                      380px rail. This line is the detail the badge can't show. */}
                  <span className={cx("block truncate text-[11px] leading-tight", PANEL.faint)}>
                    joined {joinedLabel(p)}
                  </span>
                </button>

                {/* Role first (what they are), then transient state (what they're doing).
                    Gated on the PANEL's width (@xs = 20rem), not the viewport: this roster is
                    380px in the host rail (badge fits) but narrower on small viewports,
                    where the badge stole enough room to crush names to "na…".
                    The gate lives on a WRAPPER, not on the Badge: Badge sets `inline-flex` in
                    its own base classes, and between two unprefixed display utilities the
                    stylesheet order decides — `inline-flex` wins, so `hidden` passed to Badge
                    is silently dead. On a plain span the container-query variant wins. */}
                <span className="hidden shrink-0 @xs:inline-flex">
                  <Badge tone={ROLE_TONE[role]} size="sm">{role}</Badge>
                </span>
                {p.muted && <Badge tone="danger" size="sm">Muted</Badge>}
                {p.publishing && !p.muted && <Badge tone="success" size="sm">Live mic</Badge>}

                {canModerate && (
                  <div className="flex shrink-0 items-center gap-0.5">
                    {/* Mute reveals on hover/focus — it's the frequent action but putting a
                        live mic control under every name at rest is visual noise. The overflow
                        menu stays PERSISTENT: it is the row's documented way in to the full
                        action set, and a menu you have to discover by hovering is not one.
                        focus-within keeps it keyboard-reachable, and it stays visible on
                        touch (no hover) below sm. */}
                    <span className="opacity-100 sm:opacity-0 sm:transition-opacity sm:group-focus-within:opacity-100 sm:group-hover:opacity-100 motion-reduce:sm:transition-none">
                      <ActionButton
                        icon={p.muted ? FiMic : FiMicOff}
                        title={!p.publishing
                          ? `${p.name || p.identity} isn't sending audio`
                          : p.muted
                            ? `Ask ${p.name || p.identity} to unmute`
                            : `Mute ${p.name || p.identity}`}
                        tone="amber"
                        // Same rule as the drawer: only a live track can be muted, and a
                        // silent participant is asked rather than acted upon.
                        onClick={() =>
                          p.publishing && !p.muted
                            ? send("participant.mute", { identity: p.identity, muted: true })
                            : send("participant.request_unmute", { identity: p.identity })
                        }
                      />
                    </span>
                    <ActionButton
                      icon={FiMoreVertical}
                      title={`More actions for ${p.name || p.identity}`}
                      onClick={() => setSelected(p.identity)}
                    />
                  </div>
                )}
              </div>
            );
          })}

          {shown.length === 0 && (
            <EmptyState
              icon={FiUsers}
              title={participants.length === 0 ? "No participants yet" : "No matches"}
              description={
                participants.length === 0
                  ? "Participants will appear here when they join."
                  : "Try a different search or filter."
              }
              className="py-10"
            />
          )}
        </div>
      )}

      <ProfileDrawer p={active} open={!!active} onClose={() => setSelected(null)} canModerate={canModerate} canHost={canHost} send={send} />
    </Panel>
  );
}
