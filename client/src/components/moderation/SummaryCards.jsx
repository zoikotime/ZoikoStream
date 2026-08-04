// client/src/components/moderation/SummaryCards.jsx
// Moderator KPI row — every value is live socket state, so these count up on their own.
// Reuses StatsCard (whose Counter animates numeric changes) — no bespoke tile here.
//
// `waiting` and `hands` are optional: they are the two figures a MODERATOR is measured on, and
// they are rendered only when the caller passes them so the row doesn't grow two permanent
// zeroes on a console that doesn't use a waiting room.
import {
  FiUsers, FiEye, FiHelpCircle, FiBarChart2, FiMessageSquare, FiUserCheck, FiMic,
} from "react-icons/fi";
import StatsCard from "../../ui/StatsCard";

export default function SummaryCards({
  participants, viewers, questions, polls, messages, waiting, hands,
}) {
  const cards = [
    { title: "Participants", value: participants, icon: FiUsers, accent: "emerald", live: true },
    { title: "Viewers", value: viewers, icon: FiEye, accent: "indigo", live: true },
    waiting != null && { title: "In lobby", value: waiting, icon: FiUserCheck, accent: "amber", live: true },
    hands != null && { title: "Hands up", value: hands, icon: FiMic, accent: "rose", live: true },
    { title: "Questions", value: questions, icon: FiHelpCircle, accent: "violet" },
    { title: "Polls", value: polls, icon: FiBarChart2, accent: "blue" },
    { title: "Chat Messages", value: messages, icon: FiMessageSquare, accent: "amber" },
  ].filter(Boolean);
  return (
    <div className="grid shrink-0 grid-cols-2 gap-4 sm:grid-cols-3 xl:grid-cols-5 2xl:grid-cols-7">
      {cards.map((c) => (
        <StatsCard key={c.title} {...c} />
      ))}
    </div>
  );
}
