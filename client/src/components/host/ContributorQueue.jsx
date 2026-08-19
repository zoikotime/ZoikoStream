// client/src/components/host/ContributorQueue.jsx
// The operator's view of the backstage roster — every assigned speaker + their
// ContributorSession, if invited (see services/contributor.py's snapshot_extra). Kept as
// its own small component rather than folded into ParticipantsPanel: the data shape
// ({user_id, name, session}) and the action set (contributor.*) are both different from
// the live participant roster's, and a backstage roster is typically a handful of people,
// not hundreds — none of ParticipantsPanel's search/filter/sort machinery earns its keep
// here. Mirrors HostPanel.jsx's WaitingRoom in spirit: a short list, inline actions.
import { FiUserCheck, FiMic, FiMicOff, FiArrowUpCircle, FiArrowDownCircle, FiUserX, FiClipboard } from "react-icons/fi";
import { cx, ACCENT } from "../../ui/tokens";
import Badge from "../../ui/Badge";
import EmptyState from "../organization/OrganizationEmptyState";
import { STUDIO, focus, t150 } from "./studio";
import { initials, accentFor, CONTRIBUTOR_STATE_TONE, CONTRIBUTOR_STATE_LABEL } from "../../data/host";

// What's blocking an admit — mirrors services/contributor.py's _admit gate exactly, so the
// button being disabled always has a stated reason instead of just not doing anything.
function admitBlockers(session) {
  const blockers = [];
  if (!session.consent_given) blockers.push("consent");
  if (!(session.preflight_result || {}).passed) blockers.push("preflight");
  return blockers;
}

function QueueButton({ tone = "slate", children, ...props }) {
  const tones = {
    emerald: "bg-emerald-600 text-white hover:bg-emerald-500 disabled:bg-slate-100 disabled:text-slate-400 dark:disabled:bg-slate-800 dark:disabled:text-slate-600",
    violet: "bg-violet-600 text-white hover:bg-violet-500",
    amber: "bg-amber-600 text-white hover:bg-amber-500",
    rose: "text-rose-600 hover:bg-rose-50 dark:text-rose-400 dark:hover:bg-rose-500/10",
    slate: cx("border", STUDIO.divider, STUDIO.body),
  };
  return (
    <button
      type="button"
      className={cx("rounded-md px-2 py-1 text-[11px] font-semibold disabled:cursor-not-allowed", tones[tone], t150, focus)}
      {...props}
    >
      {children}
    </button>
  );
}

export default function ContributorQueue({ contributors = [], canModerate, send }) {
  if (!contributors.length) {
    return (
      <EmptyState
        icon={FiUserCheck}
        title="No speakers assigned"
        description="Assign speakers to this event from the organization console to see them here."
        className="py-8"
      />
    );
  }

  return (
    <div className="space-y-2">
      {contributors.map((c) => {
        const s = c.session;
        const state = s?.state;
        const identity = s?.identity || String(c.user_id);
        const act = (action, payload) => send(action, { identity, ...payload });
        const blockers = s ? admitBlockers(s) : [];

        return (
          <div key={c.user_id} className={cx("rounded-lg border p-2", STUDIO.divider)}>
            <div className="flex items-center gap-2">
              <span className={cx("grid h-8 w-8 shrink-0 place-items-center rounded-full text-[11px] font-semibold", ACCENT[accentFor(c.user_id)].chip)}>
                {initials(c.name)}
              </span>
              <div className="min-w-0 flex-1">
                <p className={cx("truncate text-[13px] font-medium", STUDIO.body)}>{c.name || "Unnamed speaker"}</p>
                {state ? (
                  <span className="flex flex-wrap items-center gap-1">
                    <Badge tone={CONTRIBUTOR_STATE_TONE[state] || "neutral"} size="sm">
                      {CONTRIBUTOR_STATE_LABEL[state] || state}
                    </Badge>
                    {/* Rehearsal isn't a session state — it's an independent readiness-gate
                        flag (see crud.commercial.contributor_readiness_reasons), so it needs
                        its own indicator rather than folding into the state badge above. */}
                    {s?.rehearsal_complete && <Badge tone="success" size="sm">Rehearsed</Badge>}
                  </span>
                ) : (
                  <span className={cx("text-[11px]", STUDIO.faint)}>Not invited yet</span>
                )}
              </div>
            </div>

            {canModerate && s && !["removed", "waiting", "failed"].includes(state) && (
              <div className="mt-2 flex flex-wrap gap-1.5">
                {state === "connected" && (
                  <QueueButton
                    tone="emerald"
                    disabled={blockers.length > 0}
                    title={blockers.length ? `Waiting on: ${blockers.join(", ")}` : "Admit to backstage"}
                    onClick={() => act("contributor.admit")}
                  >
                    <FiUserCheck className="mr-1 inline" aria-hidden="true" />Admit
                  </QueueButton>
                )}
                {(state === "ready" || state === "on_standby") && (
                  <QueueButton tone="violet" onClick={() => act("contributor.bring_live")}>
                    <FiArrowUpCircle className="mr-1 inline" aria-hidden="true" />Bring live
                  </QueueButton>
                )}
                {(state === "live" || state === "muted") && (
                  <>
                    <QueueButton tone="amber" onClick={() => act("contributor.mute", { muted: state !== "muted" })}>
                      {state === "muted" ? <FiMic className="mr-1 inline" aria-hidden="true" /> : <FiMicOff className="mr-1 inline" aria-hidden="true" />}
                      {state === "muted" ? "Unmute" : "Mute"}
                    </QueueButton>
                    <QueueButton onClick={() => act("contributor.standby")}>
                      <FiArrowDownCircle className="mr-1 inline" aria-hidden="true" />Standby
                    </QueueButton>
                  </>
                )}
                {!s.rehearsal_complete && (
                  <QueueButton onClick={() => act("contributor.mark_rehearsed")}>
                    <FiClipboard className="mr-1 inline" aria-hidden="true" />Mark rehearsed
                  </QueueButton>
                )}
                <QueueButton tone="rose" onClick={() => act("contributor.remove")}>
                  <FiUserX className="mr-1 inline" aria-hidden="true" />Remove
                </QueueButton>
              </div>
            )}

            {s?.state === "reconnecting" && (
              <p className={cx("mt-1.5 text-[11px]", STUDIO.faint)}>
                Lost connection — waiting for them to come back before you can bring them live again.
              </p>
            )}
          </div>
        );
      })}
    </div>
  );
}
