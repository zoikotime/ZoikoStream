// client/src/components/host/HostPanel.jsx
// Right sidebar for the Host Dashboard — tabbed: Participants, Chat, Live
// Notifications. Tab state is lifted to the page so the control bar can switch it.
import { useEffect, useRef, useState } from "react";
import {
  FiSend, FiMic, FiMicOff, FiVideoOff, FiMoreVertical, FiUserPlus,
  FiVideo, FiRadio, FiBarChart2, FiHelpCircle, FiBell,
} from "react-icons/fi";
import { cx, ACCENT } from "../../ui/tokens";
import Badge from "../../ui/Badge";
import { notify } from "../../ui/Toast";
import { connectEventChat, sendChatMessage } from "../../lib/chatSocket";
import { participants, notifications, initials } from "../../data/host";

const TABS = [
  { key: "participants", label: "People" },
  { key: "chat", label: "Chat" },
  { key: "notifications", label: "Alerts" },
];

const ROLE_STATUS = { Host: "success", "Co-host": "info", Speaker: "info", Moderator: "warning", Attendee: "neutral" };

const NOTE_ICON = {
  join: { icon: FiUserPlus, accent: "emerald" },
  raise: { icon: FiBell, accent: "amber" },
  record: { icon: FiRadio, accent: "rose" },
  poll: { icon: FiBarChart2, accent: "violet" },
  qa: { icon: FiHelpCircle, accent: "blue" },
  system: { icon: FiVideo, accent: "indigo" },
};

function Participants() {
  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center justify-between px-1 pb-2">
        <p className="text-xs font-semibold uppercase tracking-wide text-slate-400">In this event · {participants.length}</p>
        <button className="text-xs font-medium text-emerald-600 hover:underline dark:text-emerald-400">Mute all</button>
      </div>
      <div className="-mr-1 flex-1 space-y-1 overflow-y-auto pr-1">
        {participants.map((p) => (
          <div key={p.id} className="group flex items-center gap-3 rounded-xl px-2 py-2 hover:bg-slate-50 dark:hover:bg-slate-800/60">
            <span className={cx("relative grid h-9 w-9 shrink-0 place-items-center rounded-full text-sm font-semibold", ACCENT[p.accent].chip)}>
              {initials(p.name)}
              {p.speaking && <span className="absolute inset-0 animate-pulse rounded-full ring-2 ring-emerald-500" />}
            </span>
            <div className="min-w-0 flex-1">
              <p className="truncate text-sm font-medium text-slate-800 dark:text-slate-100">{p.name}</p>
              <Badge status={ROLE_STATUS[p.role]}>{p.role}</Badge>
            </div>
            <div className="flex items-center gap-2 text-slate-400">
              {p.camOff && <FiVideoOff className="text-sm" />}
              {p.muted ? <FiMicOff className="text-sm text-rose-400" /> : <FiMic className="text-sm text-emerald-500" />}
              <button className="opacity-0 transition group-hover:opacity-100" aria-label={`Manage ${p.name}`}>
                <FiMoreVertical />
              </button>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function Chat({ streamId }) {
  const [msgs, setMsgs] = useState([]);
  const [text, setText] = useState("");
  const socketRef = useRef(null);

  useEffect(() => {
    if (!streamId) return;
    const token = localStorage.getItem("token");
    const socket = connectEventChat(
      streamId,
      { token },
      {
        onHistory: setMsgs,
        onNew: (msg) => setMsgs((m) => [...m, msg]),
        onUpdated: (msg) => setMsgs((m) => m.map((x) => (x.id === msg.id ? msg : x))),
        onDeleted: (id) => setMsgs((m) => m.filter((x) => x.id !== id)),
        onError: (err) => notify.error(err),
      }
    );
    socketRef.current = socket;
    return () => socket.disconnect();
  }, [streamId]);

  const send = async (e) => {
    e.preventDefault();
    const t = text.trim();
    if (!t || !socketRef.current) return;
    try {
      await sendChatMessage(socketRef.current, t);
      setText("");
    } catch {
      notify.error("Failed to send message");
    }
  };

  return (
    <div className="flex h-full flex-col">
      <div className="flex-1 space-y-3 overflow-y-auto pr-1">
        {msgs.map((m) => (
          <div key={m.id}>
            <div className="flex items-baseline gap-2">
              <span className="text-sm font-semibold text-slate-800 dark:text-slate-100">{m.display_name}</span>
              <span className="text-[11px] text-slate-400">
                {new Date(m.created_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
              </span>
            </div>
            <p className="text-sm text-slate-600 dark:text-slate-300">{m.text}</p>
          </div>
        ))}
        {msgs.length === 0 && <p className="text-center text-sm text-slate-400">No messages yet.</p>}
      </div>
      <form onSubmit={send} className="mt-3 flex items-center gap-2">
        <input
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="Send a message…"
          className="min-w-0 flex-1 rounded-xl border border-slate-200 bg-white px-3.5 py-2 text-sm text-slate-700 outline-none focus:border-emerald-400 focus:ring-2 focus:ring-emerald-500/20 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200"
        />
        <button type="submit" className="grid h-9 w-9 shrink-0 place-items-center rounded-xl bg-emerald-600 text-white hover:bg-emerald-500" aria-label="Send message">
          <FiSend />
        </button>
      </form>
    </div>
  );
}

function Notifications() {
  return (
    <div className="h-full space-y-2 overflow-y-auto">
      {notifications.map((n) => {
        const { icon: Icon, accent } = NOTE_ICON[n.kind] || NOTE_ICON.system;
        return (
          <div key={n.id} className="flex items-start gap-3 rounded-xl border border-slate-100 px-3 py-2.5 dark:border-slate-800">
            <span className={cx("grid h-8 w-8 shrink-0 place-items-center rounded-lg", ACCENT[accent].chip)}>
              <Icon className="text-sm" />
            </span>
            <div className="min-w-0 flex-1">
              <p className="text-sm text-slate-700 dark:text-slate-200">{n.text}</p>
              <p className="text-[11px] text-slate-400">{n.time}</p>
            </div>
          </div>
        );
      })}
    </div>
  );
}

export default function HostPanel({ tab, setTab, streamId, className = "" }) {
  return (
    <aside className={cx("flex flex-col bg-white dark:bg-slate-900", className)}>
      <div className="flex shrink-0 border-b border-slate-200 dark:border-slate-800">
        {TABS.map((t) => (
          <button
            key={t.key}
            onClick={() => setTab(t.key)}
            className={cx(
              "flex-1 border-b-2 px-3 py-3 text-sm font-medium transition",
              tab === t.key
                ? "border-emerald-500 text-emerald-600 dark:text-emerald-400"
                : "border-transparent text-slate-500 hover:text-slate-800 dark:text-slate-400 dark:hover:text-slate-100"
            )}
          >
            {t.label}
          </button>
        ))}
      </div>
      <div className="flex min-h-0 flex-1 flex-col p-3">
        {tab === "participants" && <Participants />}
        {tab === "chat" && <Chat streamId={streamId} />}
        {tab === "notifications" && <Notifications />}
      </div>
    </aside>
  );
}
