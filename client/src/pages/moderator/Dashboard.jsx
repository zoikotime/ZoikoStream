// client/src/pages/moderator/Dashboard.jsx
// Moderator Dashboard — real-time audience management during a live event.
// Route: /moderator/dashboard. Standalone console (NOT the Org Dashboard).
// No backend: all moderation actions mutate local state and log to the activity feed.
import { useEffect, useRef, useState } from "react";
import ModeratorHeader from "../../components/moderation/ModeratorHeader";
import SummaryCards from "../../components/moderation/SummaryCards";
import ParticipantsPanel from "../../components/moderation/ParticipantsPanel";
import ChatQAPanel from "../../components/moderation/ChatQAPanel";
import ModeratorSidebar from "../../components/moderation/ModeratorSidebar";
import {
  currentEvent, participantsSeed, chatSeed, questionsSeed,
  pollsSeed, announcementsSeed, activitySeed,
} from "../../data/moderation";

export default function ModeratorDashboard() {
  const [participants, setParticipants] = useState(participantsSeed);
  const [messages, setMessages] = useState(chatSeed);
  const [questions, setQuestions] = useState(questionsSeed);
  const [polls, setPolls] = useState(pollsSeed);
  const [announcements, setAnnouncements] = useState(announcementsSeed);
  const [activity, setActivity] = useState(activitySeed);
  const [viewers, setViewers] = useState(currentEvent.viewers);

  // Unique ids + timestamps for new items — only ever read/written in handlers.
  const idRef = useRef(9000);
  const nextId = () => (idRef.current += 1);
  const nowLabel = () => new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  const logActivity = (kind, text) => {
    const entry = { id: nextId(), kind, text, time: nowLabel() };
    setActivity((a) => [entry, ...a]);
  };

  // Live viewer count drift (setState only inside the interval callback).
  useEffect(() => {
    if (currentEvent.status !== "Live") return;
    const t = setInterval(() => setViewers((v) => Math.max(0, v + Math.floor(Math.random() * 13) - 6)), 3000);
    return () => clearInterval(t);
  }, []);

  // Participants
  const muteParticipant = (id) => {
    const p = participants.find((x) => x.id === id);
    // Toggle mute only — role (e.g. Speaker) is preserved.
    setParticipants((list) => list.map((x) => (x.id === id ? { ...x, muted: !x.muted } : x)));
    if (p) logActivity("mod", `${p.name} was ${p.muted ? "unmuted" : "muted"}`);
  };
  const removeParticipant = (id) => {
    const p = participants.find((x) => x.id === id);
    setParticipants((list) => list.filter((x) => x.id !== id));
    if (p) logActivity("mod", `${p.name} was removed from the event`);
  };

  // Chat
  const approveMessage = (id) => {
    const m = messages.find((x) => x.id === id);
    setMessages((list) => list.map((x) => (x.id === id ? { ...x, status: "approved", flagged: false } : x)));
    if (m) logActivity("chat", `Approved message from ${m.name}`);
  };
  const deleteMessage = (id) => {
    const m = messages.find((x) => x.id === id);
    setMessages((list) => list.filter((x) => x.id !== id));
    if (m) logActivity("chat", `Deleted a message from ${m.name}`);
  };
  const pinMessage = (id) => {
    const m = messages.find((x) => x.id === id);
    // Pin the target; unpin everything else so only one message is pinned.
    setMessages((list) => list.map((x) => (x.id === id ? { ...x, pinned: !x.pinned } : { ...x, pinned: false })));
    if (m) logActivity("chat", m.pinned ? "Unpinned a message" : `Pinned message from ${m.name}`);
  };

  // Q&A
  const approveQuestion = (id) => {
    const q = questions.find((x) => x.id === id);
    setQuestions((list) => list.map((x) => (x.id === id ? { ...x, status: "approved" } : x)));
    if (q) logActivity("qa", `Approved question from ${q.name}`);
  };
  const answerQuestion = (id) => {
    const q = questions.find((x) => x.id === id);
    setQuestions((list) => list.map((x) => (x.id === id ? { ...x, status: "answered" } : x)));
    if (q) logActivity("qa", `Marked a question from ${q.name} as answered`);
  };
  const deleteQuestion = (id) => {
    const q = questions.find((x) => x.id === id);
    setQuestions((list) => list.filter((x) => x.id !== id));
    if (q) logActivity("qa", `Deleted a question from ${q.name}`);
  };

  // Polls
  const launchPoll = (id) => {
    const p = polls.find((x) => x.id === id);
    setPolls((list) => list.map((x) => (x.id === id ? { ...x, status: "live" } : x)));
    if (p) logActivity("poll", `Launched poll: ${p.question}`);
  };
  const closePoll = (id) => {
    const p = polls.find((x) => x.id === id);
    setPolls((list) => list.map((x) => (x.id === id ? { ...x, status: "closed" } : x)));
    if (p) logActivity("poll", `Closed poll: ${p.question}`);
  };
  const createPoll = (question, options) => {
    const poll = { id: nextId(), question, status: "live", options: options.map((label) => ({ label, votes: 0 })) };
    setPolls((list) => [poll, ...list]);
    logActivity("poll", `Launched poll: ${question}`);
  };

  // Announcements
  const postAnnouncement = (text) => {
    const item = { id: nextId(), text, time: nowLabel() };
    setAnnouncements((list) => [item, ...list]);
    logActivity("system", "Announcement broadcast to viewers");
  };

  return (
    <div className="flex min-h-screen flex-col bg-slate-50 text-slate-800 lg:h-screen lg:overflow-hidden dark:bg-slate-950 dark:text-slate-200">
      <ModeratorHeader event={currentEvent} viewers={viewers} />

      <div className="flex flex-1 flex-col gap-4 p-4 sm:p-6 lg:min-h-0">
        <SummaryCards
          participants={participants.length}
          questions={questions.length}
          polls={polls.length}
          messages={messages.length}
        />

        <div className="flex flex-1 flex-col gap-4 lg:min-h-0 lg:flex-row">
          <ParticipantsPanel
            className="min-h-[420px] lg:min-h-0 lg:w-[300px] lg:shrink-0"
            participants={participants}
            onMute={muteParticipant}
            onRemove={removeParticipant}
          />
          <ChatQAPanel
            className="min-h-[520px] lg:min-h-0 lg:min-w-0 lg:flex-1"
            messages={messages}
            questions={questions}
            onApproveMessage={approveMessage}
            onDeleteMessage={deleteMessage}
            onPinMessage={pinMessage}
            onApproveQuestion={approveQuestion}
            onAnswerQuestion={answerQuestion}
            onDeleteQuestion={deleteQuestion}
          />
          <ModeratorSidebar
            className="min-h-[520px] lg:min-h-0 lg:w-[360px] lg:shrink-0"
            polls={polls}
            announcements={announcements}
            activity={activity}
            onLaunchPoll={launchPoll}
            onClosePoll={closePoll}
            onCreatePoll={createPoll}
            onPostAnnouncement={postAnnouncement}
          />
        </div>
      </div>
    </div>
  );
}
