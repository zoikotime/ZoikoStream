// client/src/pages/host/Dashboard.jsx
// Host Dashboard — broadcasting studio for an assigned host. Route: /host/dashboard
// Standalone full-screen page (NOT the Organization Dashboard). No backend —
// all controls drive local UI state only.
import { useState } from "react";
import HostHeader from "../../components/host/HostHeader";
import SummaryCards from "../../components/host/SummaryCards";
import StudioStage from "../../components/host/StudioStage";
import ControlBar from "../../components/host/ControlBar";
import HostPanel from "../../components/host/HostPanel";
import FeatureModal from "../../components/host/FeatureModal";
import { currentEvent } from "../../data/host";

export default function HostDashboard() {
  const [live, setLive] = useState(false);
  const [camera, setCamera] = useState(true);
  const [mic, setMic] = useState(true);
  const [screenShare, setScreenShare] = useState(false);
  const [recording, setRecording] = useState(false);
  const [tab, setTab] = useState("participants"); // right-sidebar tab
  const [modal, setModal] = useState(null); // "invite" | "poll" | "qa" | null

  return (
    <div className="flex min-h-screen flex-col bg-slate-50 text-slate-800 lg:h-screen lg:overflow-hidden dark:bg-slate-950 dark:text-slate-200">
      <HostHeader event={currentEvent} live={live} />

      <div className="flex flex-1 flex-col lg:min-h-0 lg:flex-row">
        {/* Studio: summary + stage above a pinned control bar */}
        <main className="flex min-w-0 flex-1 flex-col">
          <div className="flex-1 space-y-6 p-4 sm:p-6 lg:overflow-auto">
            <SummaryCards />
            <StudioStage live={live} camera={camera} screenShare={screenShare} recording={recording} event={currentEvent} />
          </div>
          <ControlBar
            live={live}
            camera={camera}
            mic={mic}
            screenShare={screenShare}
            recording={recording}
            onGoLive={() => setLive(true)}
            onEnd={() => { setLive(false); setRecording(false); }}
            onToggleCamera={() => setCamera((v) => !v)}
            onToggleMic={() => setMic((v) => !v)}
            onToggleScreen={() => setScreenShare((v) => !v)}
            onToggleRecording={() => setRecording((v) => !v)}
            onChat={() => setTab("chat")}
            onParticipants={() => setTab("participants")}
            onInvite={() => setModal("invite")}
            onPolls={() => setModal("poll")}
            onQA={() => setModal("qa")}
          />
        </main>

        {/* Right sidebar: participants / chat / notifications */}
        <HostPanel
          tab={tab}
          setTab={setTab}
          className="min-h-[70vh] w-full border-t border-slate-200 lg:min-h-0 lg:w-[360px] lg:border-l lg:border-t-0 dark:border-slate-800"
        />
      </div>

      <FeatureModal modal={modal} onClose={() => setModal(null)} />
    </div>
  );
}
