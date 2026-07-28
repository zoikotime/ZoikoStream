// client/src/components/moderation/SummaryCards.jsx
// Moderator KPI row — live counts derived from page state (reuses StatsCard).
import { FiUsers, FiHelpCircle, FiBarChart2, FiMessageSquare } from "react-icons/fi";
import StatsCard from "../../ui/StatsCard";

export default function SummaryCards({ participants, questions, polls, messages }) {
  const cards = [
    { title: "Participants", value: participants, icon: FiUsers, accent: "emerald", live: true },
    { title: "Questions", value: questions, icon: FiHelpCircle, accent: "violet" },
    { title: "Polls", value: polls, icon: FiBarChart2, accent: "blue" },
    { title: "Chat Messages", value: messages, icon: FiMessageSquare, accent: "amber" },
  ];
  return (
    <div className="grid shrink-0 grid-cols-2 gap-4 xl:grid-cols-4">
      {cards.map((c) => (
        <StatsCard key={c.title} {...c} />
      ))}
    </div>
  );
}
