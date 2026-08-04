import { useCallback, useRef, useState } from "react";
import {
  FiAlertTriangle, FiCheckCircle, FiHelpCircle, FiPlay, FiRefreshCw, FiVolume2, FiXCircle,
} from "react-icons/fi";
import useHostChecks, { HOST_CHECK_STATE as S } from "../../hooks/useHostChecks";
import Modal from "../../ui/Modal";
import Badge from "../../ui/Badge";
import { ConsoleButton as Button } from "../../ui/Button";
import { cx } from "../../ui/tokens";

// "Before you go live" — the host's pre-flight, run on demand from the control deck.
//
// Every row is a MEASUREMENT (see hooks/useHostChecks): the granted camera resolution, a real
// microphone level over a sampling window, a real RTT. A pre-flight that always reports green is
// worse than none, because the host whose mic is muted at the OS level is told they're ready.
//
// Warnings never block. A host with a 480p webcam should still be allowed to broadcast, having
// been told — only a hard failure (no secure context, blocked permissions, no WebRTC) does.

const ICON = {
  [S.OK]: { icon: FiCheckCircle, cls: "text-emerald-500" },
  [S.WARN]: { icon: FiAlertTriangle, cls: "text-amber-500" },
  [S.FAIL]: { icon: FiXCircle, cls: "text-rose-500" },
  [S.UNKNOWN]: { icon: FiHelpCircle, cls: "text-slate-400" },
  [S.CHECKING]: { icon: FiRefreshCw, cls: "text-slate-400 animate-spin motion-reduce:animate-none" },
};

const TONE = { [S.OK]: "success", [S.WARN]: "warning", [S.FAIL]: "danger", [S.UNKNOWN]: "neutral" };

/** A real 440Hz tone through the default output. The ONLY honest speaker test is a human
 *  confirming they heard it, so this plays and then asks. */
function SpeakerTest() {
  const ctxRef = useRef(null);
  const [playing, setPlaying] = useState(false);

  const play = useCallback(async () => {
    try {
      const Ctx = window.AudioContext || window.webkitAudioContext;
      const ctx = ctxRef.current || new Ctx();
      ctxRef.current = ctx;
      if (ctx.state === "suspended") await ctx.resume();
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.frequency.value = 440;
      // Ramped, not switched: a hard start on a full-volume sine is unpleasant on headphones.
      gain.gain.setValueAtTime(0, ctx.currentTime);
      gain.gain.linearRampToValueAtTime(0.15, ctx.currentTime + 0.05);
      gain.gain.linearRampToValueAtTime(0, ctx.currentTime + 0.9);
      osc.connect(gain).connect(ctx.destination);
      osc.start();
      osc.stop(ctx.currentTime + 1);
      setPlaying(true);
      osc.onended = () => setPlaying(false);
    } catch {
      setPlaying(false);
    }
  }, []);

  return (
    <Button variant="secondary" size="sm" leftIcon={FiVolume2} loading={playing} onClick={play}>
      Play test tone
    </Button>
  );
}

export default function PreLiveCheck({ open, onClose, settings, onGoLive, canHost }) {
  const { checks, running, run, ready, blocking } = useHostChecks({ settings });
  const [ran, setRan] = useState(false);

  const start = useCallback(async () => {
    setRan(true);
    await run();
  }, [run]);

  const warnings = checks.filter((c) => c.state === S.WARN);

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Pre-live check"
      size="xl"
      footer={
        <>
          <Button variant="secondary" size="sm" onClick={onClose} disabled={running}>Close</Button>
          <Button
            variant="secondary"
            size="sm"
            leftIcon={FiRefreshCw}
            loading={running}
            disabled={running}
            onClick={start}
          >
            {ran ? "Run again" : "Run checks"}
          </Button>
          {onGoLive && (
            <Button
              size="sm"
              leftIcon={FiPlay}
              disabled={!canHost || running || (ran && blocking.length > 0)}
              title={
                !canHost ? "Only an assigned host can start the broadcast"
                  : blocking.length ? "Fix the failed checks first"
                    : undefined
              }
              onClick={() => { onGoLive(); onClose(); }}
            >
              Go live
            </Button>
          )}
        </>
      }
    >
      {!ran ? (
        <div className="py-2">
          <p className="text-sm text-slate-600 dark:text-slate-300">
            This checks your camera, microphone, speakers, permissions and connection against what
            this event is configured to broadcast
            {settings?.resolution ? ` (${settings.resolution} at ${settings.framerate || 30}fps)` : ""}.
          </p>
          <p className="mt-2 text-xs text-slate-500 dark:text-slate-400">
            It briefly opens your camera and microphone to measure what your hardware actually
            delivers, then releases them. Nothing is broadcast.
          </p>
          <div className="mt-4 flex flex-wrap items-center gap-2">
            <Button size="sm" leftIcon={FiRefreshCw} onClick={start}>Run checks</Button>
            <SpeakerTest />
          </div>
        </div>
      ) : (
        <div className="space-y-3">
          <ul className="divide-y divide-slate-100 dark:divide-slate-800">
            {checks.map((c) => {
              const { icon: Icon, cls } = ICON[c.state] || ICON[S.UNKNOWN];
              return (
                <li key={c.id} className="flex items-start gap-3 py-2.5">
                  <Icon className={cx("mt-0.5 shrink-0 text-base", cls)} aria-hidden="true" />
                  <div className="min-w-0 flex-1">
                    <p className="text-sm font-medium text-slate-800 dark:text-slate-100">{c.label}</p>
                    {c.detail && (
                      <p className="text-xs text-slate-500 dark:text-slate-400">{c.detail}</p>
                    )}
                    {/* Honest caveats, e.g. that browsers expose no uplink estimate. */}
                    {c.note && (
                      <p className="mt-0.5 text-xs text-slate-400 dark:text-slate-500">{c.note}</p>
                    )}
                    {/* A measured mic level is worth showing as a bar — it is the difference
                        between "the track exists" and "sound is arriving". */}
                    {c.id === "microphone" && c.meta?.level != null && (
                      <div className="mt-1.5 h-1.5 w-32 overflow-hidden rounded-full bg-slate-200 dark:bg-slate-700">
                        <div
                          className={cx("h-full rounded-full transition-[width]",
                            c.meta.level > 1 ? "bg-emerald-500" : "bg-slate-400")}
                          style={{ width: `${Math.min(100, c.meta.level * 2)}%` }}
                        />
                      </div>
                    )}
                  </div>
                  <Badge tone={TONE[c.state] || "neutral"} size="sm">
                    {c.state === S.CHECKING ? "checking" : c.state}
                  </Badge>
                </li>
              );
            })}
          </ul>

          {!running && (
            <div className="flex flex-wrap items-center gap-2 border-t border-slate-100 pt-3 dark:border-slate-800">
              <SpeakerTest />
              <span className="text-xs text-slate-500 dark:text-slate-400">
                Speakers can only be confirmed by ear — play the tone.
              </span>
            </div>
          )}

          {!running && blocking.length > 0 && (
            <p className="rounded-lg bg-rose-50 px-3 py-2 text-xs text-rose-700 dark:bg-rose-500/10 dark:text-rose-300">
              {blocking.length} check{blocking.length === 1 ? "" : "s"} must be fixed before you can
              broadcast: {blocking.map((b) => b.label).join(", ")}.
            </p>
          )}
          {!running && !blocking.length && warnings.length > 0 && (
            <p className="rounded-lg bg-amber-50 px-3 py-2 text-xs text-amber-700 dark:bg-amber-500/10 dark:text-amber-300">
              You can go live, but {warnings.length} thing{warnings.length === 1 ? "" : "s"} may
              affect quality: {warnings.map((w) => w.label).join(", ")}.
            </p>
          )}
          {!running && ready && !warnings.length && (
            <p className="rounded-lg bg-emerald-50 px-3 py-2 text-xs text-emerald-700 dark:bg-emerald-500/10 dark:text-emerald-300">
              Everything checks out — you're ready to broadcast.
            </p>
          )}
        </div>
      )}
    </Modal>
  );
}
