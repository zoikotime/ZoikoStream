// client/src/components/moderation/SummaryCards.jsx
// Moderator KPI row — every value is live socket state, so these count up on their own.
// Reuses StatsCard (whose Counter animates numeric changes) — no bespoke tile here.
import { FiUsers, FiEye, FiHelpCircle, FiBarChart2, FiMessageSquare } from "react-icons/fi";
import StatsCard from "../../ui/StatsCard";

export default function SummaryCards({ participants, viewers, questions, polls, messages }) {
  const cards = [
    { title: "Participants", value: participants, icon: FiUsers, accent: "emerald", live: true },
    { title: "Viewers", value: viewers, icon: FiEye, accent: "indigo", live: true },
    { title: "Questions", value: questions, icon: FiHelpCircle, accent: "violet" },
    { title: "Polls", value: polls, icon: FiBarChart2, accent: "blue" },
    { title: "Chat Messages", value: messages, icon: FiMessageSquare, accent: "amber" },
  ];
  return (
    <div className="grid shrink-0 grid-cols-2 gap-4 xl:grid-cols-5">
      {cards.map((c) => (
        <StatsCard key={c.title} {...c} />
      ))}
    </div>
  );
}
