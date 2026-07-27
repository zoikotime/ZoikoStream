// client/src/pages/host/Dashboard.jsx
// Host Dashboard — broadcasting studio for an assigned host. Route: /host/dashboard
// Standalone full-screen page (NOT the Organization Dashboard).
import { useEffect, useRef, useState } from "react";
import { Room, Track, ParticipantEvent } from "livekit-client";
import HostHeader from "../../components/host/HostHeader";
import SummaryCards from "../../components/host/SummaryCards";
import StudioStage from "../../components/host/StudioStage";
import ControlBar from "../../components/host/ControlBar";
import HostPanel from "../../components/host/HostPanel";
import FeatureModal from "../../components/host/FeatureModal";
import { useAuth } from "../../auth/AuthContext";
import api, { errMsg } from "../../api";
import { notify } from "../../ui/Toast";
import { fmtDate } from "../../data/events";

// Events this host is actually assigned to broadcast (not every org event).
function useAssignedEvents(userId) {
  const [events, setEvents] = useState([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api
      .get("/streams")
      .then((res) => {
        setEvents(res.data.filter((e) => e.host_id === userId && !["completed", "canceled"].includes(e.status)));
      })
      .catch(() => setEvents([]))
      .finally(() => setLoading(false));
  }, [userId]);

  return { events, loading };
}

function EventPicker({ events, loading, onSelect }) {
  return (
    <div className="grid min-h-screen place-items-center bg-slate-50 p-6 dark:bg-slate-950">
      <div className="w-full max-w-md rounded-2xl border border-slate-200 bg-white p-6 shadow-sm dark:border-slate-800 dark:bg-slate-900">
        <h1 className="text-xl font-bold text-slate-900 dark:text-white">Host Studio</h1>
        <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
          {loading ? "Loading your events…" : "Choose an event to broadcast."}
        </p>
        <div className="mt-4 space-y-2">
          {!loading && events.length === 0 && (
            <p className="text-sm text-slate-400">
              No events are assigned to you yet — ask your organization admin to set you as the host on an event.
            </p>
          )}
          {events.map((e) => (
            <button
              key={e.id}
              onClick={() => onSelect(e.id)}
              className="flex w-full items-center justify-between rounded-xl border border-slate-200 px-4 py-3 text-left transition hover:border-emerald-400 hover:bg-emerald-50/40 dark:border-slate-700 dark:hover:border-emerald-500/50 dark:hover:bg-emerald-500/10"
            >
              <div className="min-w-0">
                <p className="truncate font-medium text-slate-800 dark:text-slate-100">{e.title}</p>
                <p className="text-xs text-slate-500 dark:text-slate-400">
                  {e.status === "live" ? "Live now" : fmtDate(e.scheduled_date)}
                </p>
              </div>
              {e.status === "live" && (
                <span className="inline-flex items-center gap-1 rounded-full bg-rose-100 px-2 py-0.5 text-[11px] font-semibold text-rose-700 dark:bg-rose-500/15 dark:text-rose-400">
                  LIVE
                </span>
              )}
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}

export default function HostDashboard() {
  const { user } = useAuth();
  const { events, loading } = useAssignedEvents(user?.id);
  const [selectedId, setSelectedId] = useState(null);

  const [live, setLive] = useState(false);
  const [camera, setCamera] = useState(true);
  const [mic, setMic] = useState(true);
  const [screenShare, setScreenShare] = useState(false);
  const [connecting, setConnecting] = useState(false);
  const [recording, setRecording] = useState(false);
  const [recordingId, setRecordingId] = useState(null);
  const [tab, setTab] = useState("participants");
  const [modal, setModal] = useState(null);
  const [room, setRoom] = useState(null);

  const roomRef = useRef(null);
  const videoRef = useRef(null);

  // Auto-select if this host only has exactly one assignable event — derived, not
  // stored, so there's no setState-in-effect render cascade.
  const effectiveSelectedId = selectedId || (!loading && events.length === 1 ? events[0].id : null);
  const selectedEvent = events.find((e) => e.id === effectiveSelectedId) || null;

  // Never leave a LiveKit connection open if the host navigates away mid-broadcast.
  useEffect(() => () => roomRef.current?.disconnect(), []);

  const attachCameraTrack = (r) => {
    const pub = r.localParticipant.getTrackPublication(Track.Source.Camera);
    if (pub?.track && videoRef.current) pub.track.attach(videoRef.current);
  };

  const goLive = async () => {
    if (!selectedEvent || connecting) return;
    setConnecting(true);
    try {
      const { data } = await api.post(`/streams/${selectedEvent.id}/start`);
      const r = new Room();

      r.localParticipant.on(ParticipantEvent.LocalTrackPublished, (pub) => {
        if (pub.source === Track.Source.Camera && videoRef.current) pub.track.attach(videoRef.current);
      });
      r.localParticipant.on(ParticipantEvent.LocalTrackUnpublished, (pub) => {
        if (pub.source === Track.Source.Camera) pub.track?.detach();
      });

      await r.connect(data.livekit_url, data.token);
      await r.localParticipant.setMicrophoneEnabled(mic);
      await r.localParticipant.setCameraEnabled(camera);

      roomRef.current = r;
      setRoom(r);
      attachCameraTrack(r);
      setLive(true);
      notify.success("You're live!");
    } catch (err) {
      roomRef.current?.disconnect();
      roomRef.current = null;
      setRoom(null);
      notify.error(errMsg(err, "Failed to go live — check camera/mic permissions and try again"));
    } finally {
      setConnecting(false);
    }
  };

  const endEvent = async () => {
    if (recordingId) {
      try {
        await api.post(`/streams/${selectedEvent.id}/recordings/${recordingId}/stop`);
      } catch {
        /* the event is ending regardless -- surface the stream-stop error below if that fails too */
      }
      setRecording(false);
      setRecordingId(null);
    }

    try {
      await roomRef.current?.disconnect();
    } catch {
      /* already gone */
    }
    roomRef.current = null;
    setRoom(null);

    try {
      await api.post(`/streams/${selectedEvent.id}/stop`);
      notify.success("Event ended");
    } catch (err) {
      notify.error(errMsg(err, "Disconnected, but failed to mark the event ended on the backend"));
    } finally {
      setLive(false);
      setScreenShare(false);
    }
  };

  const toggleRecording = async () => {
    if (!recording) {
      try {
        const { data } = await api.post(`/streams/${selectedEvent.id}/recordings/start`);
        setRecordingId(data.id);
        setRecording(true);
        notify.success("Recording started");
      } catch (err) {
        notify.error(errMsg(err, "Failed to start recording"));
      }
    } else {
      try {
        await api.post(`/streams/${selectedEvent.id}/recordings/${recordingId}/stop`);
        notify.success("Recording stopped — processing");
      } catch (err) {
        notify.error(errMsg(err, "Failed to stop recording"));
      } finally {
        setRecording(false);
        setRecordingId(null);
      }
    }
  };

  const toggleCamera = async () => {
    const next = !camera;
    setCamera(next);
    if (!roomRef.current) return;
    try {
      await roomRef.current.localParticipant.setCameraEnabled(next);
      if (next) attachCameraTrack(roomRef.current);
    } catch {
      setCamera(!next);
      notify.error("Could not toggle camera");
    }
  };

  const toggleMic = async () => {
    const next = !mic;
    setMic(next);
    if (!roomRef.current) return;
    try {
      await roomRef.current.localParticipant.setMicrophoneEnabled(next);
    } catch {
      setMic(!next);
      notify.error("Could not toggle microphone");
    }
  };

  const toggleScreenShare = async () => {
    const next = !screenShare;
    setScreenShare(next);
    if (!roomRef.current) return;
    try {
      await roomRef.current.localParticipant.setScreenShareEnabled(next);
    } catch {
      setScreenShare(!next);
      notify.error("Could not toggle screen share");
    }
  };

  if (!selectedEvent) {
    return <EventPicker events={events} loading={loading} onSelect={setSelectedId} />;
  }

  const eventForUI = {
    name: selectedEvent.title,
    session: selectedEvent.category || "Live Session",
    scheduledFor: selectedEvent.scheduled_date
      ? `${fmtDate(selectedEvent.scheduled_date)} · ${selectedEvent.start_time || "—"}–${selectedEvent.end_time || "—"} ${selectedEvent.timezone}`
      : "Not scheduled",
    viewers: 0, // ponytail: no real live-viewer-count tracking yet
  };

  return (
    <div className="flex min-h-screen flex-col bg-slate-50 text-slate-800 lg:h-screen lg:overflow-hidden dark:bg-slate-950 dark:text-slate-200">
      <HostHeader event={eventForUI} live={live} />

      <div className="flex flex-1 flex-col lg:min-h-0 lg:flex-row">
        <main className="flex min-w-0 flex-1 flex-col">
          <div className="flex-1 space-y-6 p-4 sm:p-6 lg:overflow-auto">
            <SummaryCards />
            <StudioStage live={live} camera={camera} screenShare={screenShare} recording={recording} event={eventForUI} videoRef={videoRef} room={room} />
          </div>
          <ControlBar
            live={live}
            camera={camera}
            mic={mic}
            screenShare={screenShare}
            recording={recording}
            connecting={connecting}
            onGoLive={goLive}
            onEnd={endEvent}
            onToggleCamera={toggleCamera}
            onToggleMic={toggleMic}
            onToggleScreen={toggleScreenShare}
            onToggleRecording={toggleRecording}
            onChat={() => setTab("chat")}
            onParticipants={() => setTab("participants")}
            onInvite={() => setModal("invite")}
            onPolls={() => setModal("poll")}
            onQA={() => setModal("qa")}
          />
        </main>

        <HostPanel
          tab={tab}
          setTab={setTab}
          streamId={selectedEvent.id}
          className="min-h-[70vh] w-full border-t border-slate-200 lg:min-h-0 lg:w-[360px] lg:border-l lg:border-t-0 dark:border-slate-800"
        />
      </div>

      <FeatureModal modal={modal} onClose={() => setModal(null)} />
    </div>
  );
}
