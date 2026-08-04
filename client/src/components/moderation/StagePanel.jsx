// client/src/components/moderation/StagePanel.jsx
// Stage + raised hands. Two halves of one job: who is on the stage right now, and who is
// asking to be. A moderator works this panel top-down during Q&A.
//
// The hand queue is ordered by WHEN the hand went up, which the presence record does not
// carry — see `handOrder` below for why that is derived here rather than added to the server.
//
// Every action already existed: participant.stage, participant.mute, stage.camera,
// stage.share. Two are new and exist because the brief's hand queue needs them —
// participant.dismiss_hand (decline someone else's hand) and participant.notify (a private
// "we'll come to you next"), both in services/moderation.py.
import { useState } from "react";
import {
  FiArrowUpCircle, FiArrowDownCircle, FiMic, FiMicOff, FiVideo, FiVideoOff,
  FiMonitor, FiSend, FiX, FiCheck, FiUsers, FiMessageCircle,
} from "react-icons/fi";
import { cx, ACCENT } from "../../ui/tokens";
import Badge from "../../ui/Badge";
import Skeleton from "../../ui/Skeleton";
import { Input } from "../../ui/forms";
import EmptyState from "../organization/OrganizationEmptyState";
import Panel, { ActionButton } from "./Panel";
import { initials, accentFor, HAND_REPLIES } from "../../data/moderation";

const isStaged = (p) => p.on_stage || ["host", "speaker"].includes(p.role || "viewer");

/** Reply box for one raised hand. Kept inline (not a drawer) so the moderator's eyes never
 *  leave the queue while the host is talking. */
function ReplyBox({ person, onSend, onCancel }) {
  const [text, setText] = useState("");
  return (
    <form
      onSubmit={(e) => { e.preventDefault(); if (text.trim()) onSend(text.trim()); }}
      className="mt-2 space-y-1.5"
    >
      <div className="flex flex-wrap gap-1">
        {HAND_REPLIES.map((r) => (
          <button
            key={r}
            type="button"
            onClick={() => setText(r)}
            className="rounded-full border border-slate-200 px-2 py-0.5 text-[11px] font-medium text-slate-600 transition hover:border-emerald-300 hover:text-emerald-600 dark:border-slate-700 dark:text-slate-300 dark:hover:border-emerald-500/40 dark:hover:text-emerald-400"
          >
            {r}
          </button>
        ))}
      </div>
      <div className="flex items-center gap-1.5">
        <Input
          variant="console"
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder={`Reply privately to ${person.name || person.identity}…`}
          aria-label={`Private reply to ${person.name || person.identity}`}
          autoFocus
        />
        <button type="submit" className="grid h-9 w-9 shrink-0 place-items-center rounded-lg bg-emerald-600 text-white transition hover:bg-emerald-500" aria-label="Send reply">
          <FiSend className="text-sm" />
        </button>
        <button type="button" onClick={onCancel} className="shrink-0 rounded-lg px-2 py-1.5 text-xs text-slate-500 hover:bg-slate-100 dark:hover:bg-slate-800">
          Cancel
        </button>
      </div>
    </form>
  );
}

function Person({ p, children, note }) {
  return (
    <div className="flex items-center gap-2.5">
      <span className="relative shrink-0">
        <span className={cx("grid h-9 w-9 place-items-center rounded-full text-sm font-semibold", ACCENT[accentFor(p.identity)].chip)}>
          {initials(p.name)}
        </span>
        {p.speaking && <span className="absolute inset-0 rounded-full ring-2 ring-emerald-500 motion-safe:animate-pulse" aria-hidden="true" />}
      </span>
      <div className="min-w-0 flex-1">
        <p className="truncate text-sm font-medium text-slate-800 dark:text-slate-100">{p.name || p.identity}</p>
        <p className="truncate text-xs text-slate-400">{note}</p>
      </div>
      {children}
    </div>
  );
}

export default function StagePanel({ participants = [], canModerate, loading, send, className }) {
  const [replyTo, setReplyTo] = useState(null);

  const active = participants.filter((p) => !p.waiting);
  const staged = active.filter(isStaged);
  const hands = active.filter((p) => p.hand);

  // Queue ORDER comes from the server: presence carries `hand_at`, stamped when the hand went
  // up (services/moderation._participant_hand). Deriving it here instead — remembering the
  // order this console first saw each hand — would give every moderator a different queue and
  // reset it on every reconnect. Falls back to join time for a record stamped before this
  // shipped, rather than throwing the row to the front.
  const handOrder = [...hands].sort(
    (a, b) => (a.hand_at ?? a.joined_at ?? 0) - (b.hand_at ?? b.joined_at ?? 0)
  );

  const act = (action, p, payload) => send(action, { identity: p.identity, ...payload });

  return (
    <Panel
      title="Stage"
      count={staged.length}
      badge={hands.length > 0 && (
        <Badge tone="warning" size="sm" dot>{hands.length} hand{hands.length > 1 ? "s" : ""} up</Badge>
      )}
      className={className}
    >
      {loading && (
        <div className="space-y-2" aria-hidden="true">
          {[0, 1, 2].map((i) => <Skeleton key={i} className="h-14 rounded-xl" />)}
        </div>
      )}

      {!loading && (
        <div className="space-y-4">
          {/* ── raised hands ─────────────────────────────────────────────────── */}
          <div>
            <p className="mb-1.5 text-[11px] font-semibold uppercase tracking-wide text-slate-400">
              Raised hands {hands.length > 0 && `· ${hands.length}`}
            </p>
            {handOrder.length === 0 ? (
              <p className="rounded-xl border border-dashed border-slate-200 px-3 py-4 text-center text-xs text-slate-400 dark:border-slate-800">
                Nobody is asking to speak.
              </p>
            ) : (
              <ol className="space-y-1.5">
                {handOrder.map((p, i) => (
                  <li
                    key={p.identity}
                    className="rounded-xl border border-amber-200 bg-amber-50/60 px-2.5 py-2 motion-safe:animate-[zk-fade-in_.25s] dark:border-amber-500/30 dark:bg-amber-500/10"
                  >
                    <Person p={p} note={`#${i + 1} in the queue · ${p.role || "viewer"}`}>
                      {canModerate && (
                        <div className="flex shrink-0 items-center gap-1">
                          <ActionButton
                            icon={FiCheck}
                            label="Stage"
                            tone="emerald"
                            title={`Move ${p.name || p.identity} to the stage`}
                            // Accepting clears the hand as well as staging them, otherwise the
                            // queue keeps showing somebody who is already speaking.
                            onClick={() => {
                              act("participant.stage", p, { on_stage: true });
                              act("participant.dismiss_hand", p);
                            }}
                          />
                          <ActionButton
                            icon={FiMessageCircle}
                            tone="blue"
                            title={`Reply privately to ${p.name || p.identity}`}
                            active={replyTo === p.identity}
                            onClick={() => setReplyTo(replyTo === p.identity ? null : p.identity)}
                          />
                          <ActionButton
                            icon={FiX}
                            tone="rose"
                            title={`Decline ${p.name || p.identity}'s hand`}
                            onClick={() => act("participant.dismiss_hand", p)}
                          />
                        </div>
                      )}
                    </Person>
                    {replyTo === p.identity && (
                      <ReplyBox
                        person={p}
                        onSend={(text) => { act("participant.notify", p, { text }); setReplyTo(null); }}
                        onCancel={() => setReplyTo(null)}
                      />
                    )}
                  </li>
                ))}
              </ol>
            )}
          </div>

          {/* ── on stage ─────────────────────────────────────────────────────── */}
          <div>
            <p className="mb-1.5 text-[11px] font-semibold uppercase tracking-wide text-slate-400">
              On stage {staged.length > 0 && `· ${staged.length}`}
            </p>
            {staged.length === 0 ? (
              <EmptyState
                icon={FiUsers}
                title="Stage is empty"
                description="Hosts and speakers appear here. Move someone up from the hand queue or the roster."
                className="py-8"
              />
            ) : (
              <div className="space-y-1.5">
                {staged.map((p) => {
                  const host = (p.role || "viewer") === "host";
                  return (
                    <div key={p.identity} className="rounded-xl border border-slate-100 px-2.5 py-2 dark:border-slate-800">
                      <Person
                        p={p}
                        note={[
                          p.role || "viewer",
                          p.publishing ? "publishing" : "not publishing",
                          p.muted ? "muted" : null,
                        ].filter(Boolean).join(" · ")}
                      >
                        {canModerate && (
                          <div className="flex shrink-0 items-center gap-0.5">
                            <ActionButton
                              icon={p.muted ? FiMic : FiMicOff}
                              tone="amber"
                              title={p.muted ? `Unmute ${p.name}` : `Mute ${p.name}`}
                              onClick={() => act("participant.mute", p, { muted: !p.muted })}
                            />
                            <ActionButton
                              icon={p.camera_allowed === false ? FiVideoOff : FiVideo}
                              tone="amber"
                              active={p.camera_allowed === false}
                              title={p.camera_allowed === false ? `Allow ${p.name}'s camera` : `Disable ${p.name}'s camera`}
                              onClick={() => act("stage.camera", p, { allowed: p.camera_allowed === false })}
                            />
                            <ActionButton
                              icon={FiMonitor}
                              tone="amber"
                              active={p.share_allowed === false}
                              title={p.share_allowed === false ? `Allow ${p.name} to share` : `Stop ${p.name} sharing`}
                              onClick={() => act("stage.share", p, { allowed: p.share_allowed === false })}
                            />
                            {/* The host runs the broadcast; pulling them off their own stage is
                                the one stage action a moderator must not have. */}
                            {!host && (
                              <ActionButton
                                icon={FiArrowDownCircle}
                                tone="rose"
                                title={`Move ${p.name} back to the audience`}
                                onClick={() => act("participant.stage", p, { on_stage: false })}
                              />
                            )}
                          </div>
                        )}
                      </Person>
                    </div>
                  );
                })}
              </div>
            )}
          </div>

          {/* ── promote from the audience ─────────────────────────────────────── */}
          {canModerate && active.some((p) => !isStaged(p) && !p.hand) && (
            <details className="rounded-xl border border-slate-100 p-2.5 dark:border-slate-800">
              <summary className="cursor-pointer text-xs font-semibold text-slate-500 dark:text-slate-400">
                Move someone up from the audience
              </summary>
              <div className="mt-2 space-y-1">
                {active.filter((p) => !isStaged(p) && !p.hand).slice(0, 20).map((p) => (
                  <Person key={p.identity} p={p} note={p.role || "viewer"}>
                    <ActionButton
                      icon={FiArrowUpCircle}
                      label="Stage"
                      tone="emerald"
                      title={`Move ${p.name || p.identity} to the stage`}
                      onClick={() => act("participant.stage", p, { on_stage: true })}
                    />
                  </Person>
                ))}
              </div>
            </details>
          )}
        </div>
      )}
    </Panel>
  );
}
