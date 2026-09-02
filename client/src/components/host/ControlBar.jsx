// client/src/components/host/ControlBar.jsx
// Broadcast control deck. Media toggles, engagement shortcuts, recording transport and the
// broadcast lifecycle (preview → countdown → live → pause/resume → end), plus emergency stop.
//
// Two deliberate safety choices, because this deck can end a live event in one click:
//   * End and Emergency stop ask for confirmation inline (a second click on the same
//     button) rather than firing immediately.
//   * Everything that changes the broadcast is disabled without `canHost` — the server
//     enforces the same rule, so an operator sees why instead of getting a silent rejection.
//
// ORDERING is the point of the layout. The tool keys form one compact cluster on the left,
// ordered by blast radius: CAPTURE (affects only this machine) → ENGAGEMENT (opens a modal,
// changes nothing) → RECORDING (writes a file). TRANSPORT (changes what the audience sees)
// sits apart in its own bordered track on the right, with the deck's only flexible space
// between the two, so the buttons that can end a live event are never adjacent to a
// harmless modal shortcut.
//
// Labels state the CURRENT state ("Muted", "Cam off") and the tooltip states the ACTION
// ("Unmute your microphone"), which is what `aria-pressed` already tells a screen reader —
// so the visual and the accessible name agree instead of contradicting each other.
import { useEffect, useState } from "react";
import {
  FiMic, FiMicOff, FiVideo, FiVideoOff, FiMonitor, FiRefreshCw, FiUserPlus,
  FiRadio, FiPhoneOff, FiCircle,
  FiPause, FiPlay, FiSettings, FiAlertOctagon, FiSquare, FiEye, FiLoader,
} from "react-icons/fi";
import { cx } from "../../ui/tokens";
import { STUDIO, DECK, TRANSPORT, focus, t150, t200, press, disabled as disabledCls } from "./studio";
import { COUNTDOWN_PRESETS } from "../../data/host";

// A deck button: icon over an 11px label, identical dimensions in all three tool groups so
// the deck reads as one instrument row rather than clusters of differently-sized keys.
//
// 64px wide at every breakpoint. It used to step 56px → 64px at 2xl, because back when the
// deck also carried People / Chat / Polls / Q&A it held up to 14 keys and 64px of each did
// not fit at 1280. With those four gone the worst case is 10 keys (a recording rolling),
// which needs ~700px of the ~876px available at 1280 — so one constant width is enough and
// every control stays visible AND labelled at every supported width, which is the one thing
// an operator deck can't trade.
function DeckButton({
  icon: Icon, label, tone = "brand", active = false, disabled = false, onClick, title,
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      title={title || label}
      aria-pressed={active}
      className={cx(
        "group flex h-14 w-16 shrink-0 flex-col items-center justify-center gap-1.5 rounded-lg text-[11px] font-medium leading-none",
        active ? DECK[tone] : DECK.rest,
        t200,
        press,
        focus,
        disabledCls
      )}
    >
      {/* The glyph grows slightly on hover — the key itself stays put (see studio.js `t150`),
          so the row can never re-wrap, but the control still answers the pointer. */}
      <Icon
        aria-hidden="true"
        className={cx(
          "text-[17px] transition-transform duration-200 ease-out",
          "group-hover:scale-110 group-disabled:group-hover:scale-100",
          "motion-reduce:transition-none motion-reduce:group-hover:scale-100"
        )}
      />
      <span className="max-w-full truncate px-0.5">{label}</span>
    </button>
  );
}

// Two-step confirm: the first click arms, the second commits, and it disarms itself after a
// few seconds so a stale armed button can't be hit by accident later.
function DangerButton({
  icon: Icon, label, armedLabel, title, disabled, onConfirm, iconOnly = false, className = "",
}) {
  const [armed, setArmed] = useState(false);
  useEffect(() => {
    if (!armed) return undefined;
    const t = setTimeout(() => setArmed(false), 4000);
    return () => clearTimeout(t);
  }, [armed]);
  return (
    <button
      type="button"
      disabled={disabled}
      title={armed ? "Click again to confirm" : title || label}
      // The accessible name always carries the full intent, even when the face is icon-only
      // or has switched to its armed wording.
      aria-label={armed ? `${title || label} — click again to confirm` : title || label}
      onClick={() => (armed ? (setArmed(false), onConfirm()) : setArmed(true))}
      className={cx(
        "inline-flex h-10 items-center justify-center gap-2 rounded-lg text-[13px] font-semibold",
        iconOnly ? "w-10" : "px-3.5",
        armed ? TRANSPORT.dangerArmed : TRANSPORT.danger,
        t200,
        press,
        focus,
        disabledCls,
        className
      )}
    >
      <Icon aria-hidden="true" className="text-base" />
      {!iconOnly && <span>{armed ? armedLabel : label}</span>}
      {iconOnly && armed && <span className="sr-only">{armedLabel}</span>}
    </button>
  );
}

export default function ControlBar({
  broadcast, recording, canHost, previewOn, camera, mic, screenShare, media,
  onTogglePreview, onToggleCamera, onFlipCamera, onToggleMic, onToggleScreen,
  onGoLive, onPause, onResume, onEnd, onEmergencyStop, onCountdown,
  onRecord, onPauseRecord, onStopRecord,
  onInvite, onSettings,
  // Go Live click -> server round trip. goLiveError is the last readiness rejection (see
  // hooks/useLiveEvent.js "host/broadcast.error") — shown here, next to the control that
  // failed, rather than only as a toast that scrolls away.
  goLivePending = false, goLiveError = null,
  // Whether the opening moderator/snapshot has arrived (hooks/useLiveEvent state.ready).
  // Defaults true so nothing that renders this deck without the flag silently reads as
  // permanently connecting.
  ready = true,
}) {
  const status = broadcast?.status || "preview";
  const live = status === "live";
  const paused = status === "paused";
  const ended = status === "ended";
  // "We don't know yet" is not "you may not". canHost starts false in the reducer's EMPTY
  // state and only becomes true when the snapshot lands, so until then every permission-
  // derived control here was reporting a REFUSAL it had no basis for: Go Live sat disabled
  // telling an org_admin "Only the event host can start or pause the broadcast". Measured on
  // a real console against a remote database, that window was ~6s on a clean load, and it
  // lasts as long as the socket keeps failing to connect — i.e. indefinitely on a bad
  // network, with the host staring at a permission error that was never true.
  const connecting = !ready;
  const rec = recording?.status;
  const shareBlocked = broadcast?.settings?.allow_screen_share === false;

  return (
    <div className={cx("shrink-0 border-t", STUDIO.chrome)}>
      <div className="flex flex-wrap items-center gap-2 px-3 py-2 sm:px-4">
        {/* All tool keys in ONE compact group at a uniform 8px gap. The only flexible space
            in the deck is between this group and the transport track — the transport's
            `ml-auto` owns it — so the keys stay together as a single instrument cluster
            instead of drifting apart as the viewport widens. flex-wrap is the narrow-width
            fallback, so this can never force horizontal overflow. */}
        <div className="flex flex-wrap items-center gap-2">
        {/* ── capture — local only. Enabled regardless of host rights: checking your own
               camera is not a broadcast action. ─────────────────────────────────────── */}
        {/* Label stays "Preview" in both states: "Preview on" overflowed the key and
            truncated to "Preview …". The violet fill plus aria-pressed already carry the
            state, and the tooltip carries the action. */}
        <DeckButton
          icon={FiEye}
          label="Preview"
          active={previewOn}
          onClick={onTogglePreview}
          title={previewOn
            ? "Stop the local camera preview"
            : "Preview your camera and mic before going live"}
        />
        <DeckButton
          icon={mic ? FiMic : FiMicOff}
          label={mic ? "Mic" : "Muted"}
          tone="danger"
          active={!mic}
          disabled={!previewOn}
          onClick={onToggleMic}
          title={previewOn
            ? (mic ? "Mute your microphone" : "Unmute your microphone")
            : "Start the preview first"}
        />
        <DeckButton
          icon={camera ? FiVideo : FiVideoOff}
          label={camera ? "Camera" : "Cam off"}
          tone="danger"
          active={!camera}
          disabled={!previewOn}
          onClick={onToggleCamera}
          title={previewOn
            ? (camera ? "Turn your camera off" : "Turn your camera on")
            : "Start the preview first"}
        />
        <DeckButton
          icon={FiRefreshCw}
          label="Flip"
          disabled={!previewOn || !camera || (media?.devices?.cameras?.length || 0) < 2}
          onClick={onFlipCamera}
          title={
            !previewOn
              ? "Start the preview first"
              : !camera
                ? "Turn the camera on first"
                : (media?.devices?.cameras?.length || 0) < 2
                  ? "Only one camera is available"
                  : "Switch between available cameras"
          }
        />
        <DeckButton
          icon={FiMonitor}
          label="Share"
          active={screenShare}
          onClick={onToggleScreen}
          disabled={shareBlocked}
          title={shareBlocked
            ? "Screen sharing is disabled for this event"
            : screenShare ? "Stop sharing your screen" : "Share your screen"}
        />


        {/* ── engagement — opens a modal; changes nothing on air. ─────────────────
               People / Chat / Polls / Q&A deliberately do NOT live here: they only ever
               switched the right-hand panel's tab, and that panel already has its own tab
               rail for exactly that. Two controls for one action meant the deck carried four
               keys that told an operator nothing the rail wasn't already showing. */}
        <DeckButton icon={FiUserPlus} label="Invite" onClick={onInvite} title="Invite viewers to this event" />
        <DeckButton icon={FiSettings} label="Settings" onClick={onSettings} title="Broadcast, chat and media settings" />


        {/* ── recording — host-only; pause/stop appear only while a file is rolling. ─ */}
        {/* Constant label, like Preview: "Recording"/"Rec paused" were the two longest
            strings in the deck and drove the key width for all fourteen. The red fill,
            aria-pressed, the adjacent Pause/Stop keys and the header's REC badge already
            say it's rolling — four signals, none of them this label. */}
        <DeckButton
          icon={FiCircle}
          label="Record"
          tone="danger"
          active={!!rec}
          disabled={!canHost || ended}
          onClick={rec ? undefined : onRecord}
          title={!canHost
            ? "Only the host can control recording"
            : rec ? "Recording in progress — use pause or stop" : "Start recording"}
        />
        {rec && (
          <>
            <DeckButton
              icon={rec === "paused" ? FiPlay : FiPause}
              label={rec === "paused" ? "Resume" : "Pause"}
              tone="warn"
              active
              disabled={!canHost}
              onClick={onPauseRecord}
              title={rec === "paused" ? "Resume recording" : "Pause recording"}
            />
            <DeckButton
              icon={FiSquare}
              label="Stop"
              tone="danger"
              disabled={!canHost}
              onClick={onStopRecord}
              title="Stop and finalise the recording"
            />
          </>
        )}

        </div>

        {/* ── transport — the only group that changes what the audience sees, so it gets
               its own bordered track and sits apart from everything else.
               Below 2xl it takes a deliberate second row (full width, right-aligned)
               rather than half-wrapping mid-group: the tool groups plus transport need
               ~1100px, so on a 1280/1440 laptop a two-tier deck is the honest layout and
               a trailing "Stop" orphaned onto its own line is not. ────────────────── */}
        <div
          className={cx(
            "ml-auto flex w-full items-center justify-end gap-2 p-1.5 2xl:w-auto",
            STUDIO.inset
          )}
        >
          {!live && !paused && !ended && (
            <>
              <label htmlFor="zk-countdown" className="sr-only">Countdown before going live</label>
              <select
                id="zk-countdown"
                onChange={(e) => e.target.value && onCountdown(Number(e.target.value))}
                defaultValue=""
                disabled={!canHost}
                title="Show a shared countdown to every console before going live"
                className={cx(
                  "hidden h-10 cursor-pointer rounded-lg border px-2.5 text-[13px] sm:block",
                  "border-slate-200 bg-white text-slate-600 hover:border-slate-300 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-300 dark:hover:border-slate-600",
                  t150,
                  focus,
                  disabledCls
                )}
              >
                <option value="">Countdown…</option>
                {COUNTDOWN_PRESETS.map((s) => <option key={s} value={s}>{s}s</option>)}
              </select>
            </>
          )}

          {paused ? (
            <button
              type="button"
              onClick={onResume}
              disabled={!canHost}
              title={!canHost ? "Only the event host can resume the broadcast" : "Resume the broadcast"}
              className={cx(
                "inline-flex h-10 items-center gap-2 rounded-lg px-4 text-[13px] font-semibold",
                TRANSPORT.primary, t200, press, focus, disabledCls
              )}
            >
              <FiPlay aria-hidden="true" className="text-base" /> Resume
            </button>
          ) : (
            <button
              type="button"
              onClick={live ? onPause : onGoLive}
              disabled={connecting || !canHost || ended || (!live && goLivePending)}
              title={connecting
                ? "Connecting to the studio — this control unlocks once the console is in sync"
                : !canHost
                  ? "Only the event host can start or pause the broadcast"
                  : live ? "Pause the broadcast"
                    : ended ? "This event has ended"
                      : goLivePending ? "Waiting for the server to confirm…"
                        : "Start broadcasting"}
              className={cx(
                "inline-flex h-10 items-center gap-2 rounded-lg px-4 text-[13px] font-semibold",
                live ? TRANSPORT.hold : TRANSPORT.primary,
                t200, press, focus, disabledCls
              )}
            >
              {live
                ? <><FiPause aria-hidden="true" className="text-base" /> Pause</>
                : connecting
                  // Distinct from "Starting…" below, which means the go-live request itself
                  // is in flight. This one means the console is not in sync yet, so the
                  // host can tell "wait a moment" apart from "your click is being processed".
                  ? <><FiLoader aria-hidden="true" className="text-base animate-spin" /> Connecting…</>
                  : goLivePending
                    ? <><FiLoader aria-hidden="true" className="text-base animate-spin" /> Starting…</>
                    : (
                    <>
                      {/* Go Live is the one action the whole deck exists for, so its glyph
                          transmits while the broadcast is still idle. */}
                      <FiRadio
                        aria-hidden="true"
                        className={cx("text-base", !ended && "animate-pulse motion-reduce:animate-none")}
                      />
                      {ended ? "Ended" : "Go Live"}
                    </>
                  )}
            </button>
          )}

          <DangerButton
            icon={FiPhoneOff}
            label="End Event"
            armedLabel="Confirm end"
            title="End the broadcast and stop any recording"
            disabled={!canHost || ended || (!live && !paused)}
            onConfirm={onEnd}
          />
          <DangerButton
            icon={FiAlertOctagon}
            iconOnly
            label="Emergency stop"
            armedLabel="Confirm emergency stop"
            title="Emergency stop — end the broadcast, stop recording and disconnect everyone from the room"
            disabled={!canHost || ended}
            onConfirm={onEmergencyStop}
          />
        </div>
      </div>

      {/* Hidden while a fresh attempt is in flight — the pending state above already covers
          "trying again", and re-showing the previous rejection mid-retry reads as if the new
          attempt already failed. */}
      {!goLivePending && !live && goLiveError && (
        // goLiveError.message is the server's own readiness-gate string (already reads
        // "Cannot go live — <reason>; <reason>...", see services/broadcast.py::_golive_gate)
        // — shown verbatim, never replaced with a generic message, so the host sees the
        // actual blocking_reasons rather than a guess.
        <p
          role="alert"
          className="border-t border-rose-200 bg-rose-50 px-3 py-1.5 text-[11px] font-medium text-rose-700 sm:px-4 dark:border-rose-500/30 dark:bg-rose-500/10 dark:text-rose-300"
        >
          {goLiveError.message}
        </p>
      )}

      {media?.error && (
        <p
          role="status"
          className="border-t border-rose-200 bg-rose-50 px-3 py-1.5 text-[11px] font-medium text-rose-700 sm:px-4 dark:border-rose-500/30 dark:bg-rose-500/10 dark:text-rose-300"
        >
          {media.error}
        </p>
      )}
    </div>
  );
}
