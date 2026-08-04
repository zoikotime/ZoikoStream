// client/src/components/speaker/TechnicalHealth.jsx
// The speaker's own device and connection status, plus the button that tells the people running
// the show when something is wrong.
//
// Every figure is MEASURED, and the ones this platform cannot measure say so rather than showing a
// plausible number. Sources, and their honest limits:
//
//   camera / mic / speakers / network  — hooks/useHostChecks, which acquires the real devices and
//                                        reads what the hardware actually granted (a webcam asked
//                                        for 1080p that yields 640x480 is the thing a speaker
//                                        needs to know BEFORE they go on).
//   bitrate / packet loss / RTT / fps  — hooks/usePublisher, off the live peer connection. The
//                                        only place outbound figures exist.
//   memory / UI load / cores           — hooks/useSystemStats. `memory` is the JS HEAP, not system
//                                        RAM, and `load` is measured frame budget, NOT OS CPU —
//                                        no browser exposes process CPU to a page. Both are
//                                        labelled for what they are.
//   browser compatibility              — feature detection, not a user-agent guess.
import { useState } from "react";
import {
  FiActivity, FiAlertTriangle, FiCheck, FiCpu, FiHardDrive, FiRefreshCw, FiSend, FiWifi, FiX,
} from "react-icons/fi";
import { cx } from "../../ui/tokens";
import Badge from "../../ui/Badge";
import { Input, Select } from "../../ui/forms";
import Panel from "../moderation/Panel";
import useHostChecks, { HOST_CHECK_STATE as S } from "../../hooks/useHostChecks";
import useSystemStats from "../../hooks/useSystemStats";
import { ISSUE_KINDS } from "../../data/speaker";

const TONE = {
  [S.OK]: { icon: FiCheck, cls: "text-emerald-500", label: "OK" },
  [S.WARN]: { icon: FiAlertTriangle, cls: "text-amber-500", label: "Check" },
  [S.FAIL]: { icon: FiX, cls: "text-rose-500", label: "Problem" },
  [S.UNKNOWN]: { icon: FiAlertTriangle, cls: "text-slate-400", label: "Unknown" },
  [S.CHECKING]: { icon: FiRefreshCw, cls: "text-slate-400 animate-spin motion-reduce:animate-none", label: "Checking" },
};

/** Feature detection, so "your browser can't do this" is a fact rather than a UA string match. */
function compatibility() {
  const missing = [];
  if (!window.isSecureContext) missing.push("secure connection (https)");
  if (!navigator.mediaDevices?.getUserMedia) missing.push("camera/microphone capture");
  if (typeof RTCPeerConnection === "undefined") missing.push("WebRTC");
  if (!navigator.mediaDevices?.getDisplayMedia) missing.push("screen sharing");
  return missing;
}

function Row({ label, value, tone, hint }) {
  const t = TONE[tone] || null;
  const Icon = t?.icon;
  return (
    <div className="flex items-start justify-between gap-3 border-b border-slate-100 py-1.5 text-sm last:border-0 dark:border-slate-800">
      <span className="min-w-0 text-slate-500 dark:text-slate-400" title={hint}>{label}</span>
      <span className="flex shrink-0 items-center gap-1.5 text-right font-medium text-slate-800 dark:text-slate-100">
        {Icon && <Icon className={cx("shrink-0", t.cls)} aria-hidden="true" />}
        {value}
      </span>
    </div>
  );
}

export default function TechnicalHealth({ settings, stats, quality, speakingSeconds, send, className }) {
  const { checks, running, run, blocking } = useHostChecks({ settings });
  const system = useSystemStats(true);
  const [issueOpen, setIssueOpen] = useState(false);
  const [kind, setKind] = useState("audio");
  const [detail, setDetail] = useState("");

  const missing = compatibility();

  const raise = (e) => {
    e.preventDefault();
    send("speaker.issue", { kind, detail: detail.trim() });
    setDetail("");
    setIssueOpen(false);
  };

  return (
    <Panel
      title="Technical status"
      scroll={false}
      className={className}
      badge={blocking.length > 0 && <Badge tone="danger" size="sm" dot>{blocking.length} blocking</Badge>}
      action={
        <button
          type="button"
          onClick={run}
          disabled={running}
          className="inline-flex items-center gap-1 rounded-lg border border-slate-200 px-2 py-1 text-xs font-medium text-slate-600 transition hover:bg-slate-50 disabled:opacity-50 dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-800"
        >
          <FiRefreshCw className={running ? "animate-spin motion-reduce:animate-none" : ""} aria-hidden="true" />
          {running ? "Testing…" : "Run device test"}
        </button>
      }
    >
      <div className="space-y-4">
        {/* Device diagnostics — nothing here is reported until it has been measured. */}
        <div>
          <p className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-slate-400">Devices</p>
          {checks.length === 0 ? (
            <p className="py-3 text-xs text-slate-400">
              Run the device test to check your camera, microphone, speakers and network. Nothing is
              reported until it has actually been measured.
            </p>
          ) : (
            checks.map((c) => (
              <Row key={c.id} label={c.label} tone={c.state} hint={c.note}
                   value={<span className="text-xs">{c.detail || TONE[c.state]?.label}</span>} />
            ))
          )}
          {checks.some((c) => c.note) && (
            <p className="mt-1 text-[11px] text-slate-400">
              {checks.filter((c) => c.note).map((c) => c.note).join(" ")}
            </p>
          )}
        </div>

        {/* Live encoder figures — present only while actually publishing. */}
        <div>
          <p className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-slate-400">
            Your outgoing stream
          </p>
          <Row label="Connection quality" value={quality || "—"} />
          <Row label="Bitrate" value={stats?.bitrateKbps == null ? "—" : `${stats.bitrateKbps} kbps`} />
          <Row label="Packet loss" value={stats?.packetLoss == null ? "—" : `${stats.packetLoss}%`} />
          <Row label="Round trip" value={stats?.rttMs == null ? "—" : `${stats.rttMs} ms`} />
          <Row label="Frame rate" value={stats?.fps == null ? "—" : `${stats.fps} fps`} />
          <Row
            label="Sent resolution"
            value={stats?.width ? `${stats.width}×${stats.height}` : "—"}
            hint="What the encoder is actually sending, which can be below your camera's capability"
          />
          {stats?.qualityLimitation && (
            <p className="mt-1 flex items-center gap-1.5 text-[11px] text-amber-600 dark:text-amber-400">
              <FiAlertTriangle aria-hidden="true" />
              The encoder is being held back by {stats.qualityLimitation} — the picture will look
              softer than your settings ask for.
            </p>
          )}
          <Row label="Speaking time" value={speakingSeconds ? `${Math.floor(speakingSeconds / 60)}m ${speakingSeconds % 60}s` : "—"} />
        </div>

        {/* Machine. Labelled precisely, because "CPU" here would be a lie. */}
        <div>
          <p className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-slate-400">This machine</p>
          <Row
            label="UI load"
            hint="Measured main-thread frame budget — NOT operating-system CPU, which no browser exposes to a page"
            value={
              <span className="inline-flex items-center gap-1.5">
                <FiCpu className="text-slate-400" aria-hidden="true" />
                {system.load == null ? "—" : `${system.load}%`}
                {system.frameMs != null && <span className="text-xs text-slate-400">({system.frameMs}ms/frame)</span>}
              </span>
            }
          />
          <Row
            label="JS heap"
            hint="performance.memory — the page's JavaScript heap, not system RAM. Chromium only."
            value={
              <span className="inline-flex items-center gap-1.5">
                <FiHardDrive className="text-slate-400" aria-hidden="true" />
                {system.memory ? `${system.memory.usedMb} / ${system.memory.limitMb} MB` : "not reported"}
              </span>
            }
          />
          <Row label="Logical cores" value={system.cores ?? "—"} />
          <Row
            label="Network (browser estimate)"
            hint="navigator.connection — a downlink estimate. There is no uplink figure in any browser, which is what a publisher would actually want."
            value={
              <span className="inline-flex items-center gap-1.5">
                <FiWifi className="text-slate-400" aria-hidden="true" />
                {system.network
                  ? `${system.network.effectiveType || "?"}${system.network.downlinkMbps != null ? ` · ${system.network.downlinkMbps} Mbps down` : ""}`
                  : "not reported"}
              </span>
            }
          />
          <Row
            label="Browser support"
            tone={missing.length ? S.FAIL : S.OK}
            value={<span className="text-xs">{missing.length ? `missing: ${missing.join(", ")}` : "everything needed"}</span>}
          />
        </div>

        {/* Raise a technical issue — to the CONSOLES, not the public chat. */}
        <div>
          {issueOpen ? (
            <form onSubmit={raise} className="space-y-2 rounded-xl border border-amber-200 p-2.5 dark:border-amber-500/30">
              <Select variant="console" value={kind} onChange={(e) => setKind(e.target.value)}
                      aria-label="What kind of problem">
                {ISSUE_KINDS.map((k) => <option key={k.key} value={k.key}>{k.label}</option>)}
              </Select>
              <Input
                variant="console"
                value={detail}
                onChange={(e) => setDetail(e.target.value)}
                placeholder="What's happening? (optional)"
                aria-label="Describe the problem"
              />
              <p className="text-[11px] text-slate-400">
                Goes to the host and moderators with your measured figures attached. The audience
                doesn't see it.
              </p>
              <div className="flex items-center gap-1.5">
                <button type="submit" className="inline-flex items-center gap-1 rounded-lg bg-amber-600 px-2.5 py-1 text-xs font-semibold text-white transition hover:bg-amber-500">
                  <FiSend aria-hidden="true" /> Send
                </button>
                <button type="button" onClick={() => setIssueOpen(false)}
                        className="rounded-lg px-2 py-1 text-xs text-slate-500 hover:bg-slate-100 dark:hover:bg-slate-800">
                  Cancel
                </button>
              </div>
            </form>
          ) : (
            <button
              type="button"
              onClick={() => setIssueOpen(true)}
              className="inline-flex w-full items-center justify-center gap-1.5 rounded-lg border border-amber-300 px-3 py-2 text-sm font-medium text-amber-700 transition hover:bg-amber-50 dark:border-amber-500/40 dark:text-amber-300 dark:hover:bg-amber-500/10"
            >
              <FiActivity aria-hidden="true" /> Raise a technical issue
            </button>
          )}
        </div>
      </div>
    </Panel>
  );
}
