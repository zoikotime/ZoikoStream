// client/src/data/reactions.js
// Shared reaction key/emoji/label list — the single source of truth for both
// components/watch/ReactionBar.jsx (the tap targets + authoritative counts) and
// components/watch/FloatingReactions.jsx's callers (EventWatch.jsx looks up an emoji by
// key here when spawning a burst from a real count delta). Server-side keys
// (like/heart/clap/fire/party) and emoji must not change without a data migration —
// order/labels are display-only and match the reference design (Like/Love/Celebrate/
// Hype/Applause).
export const REACTIONS = [
  { key: "like", emoji: "👍", label: "Like" },
  { key: "heart", emoji: "❤️", label: "Love" },
  { key: "party", emoji: "🎉", label: "Celebrate" },
  { key: "fire", emoji: "🔥", label: "Hype" },
  { key: "clap", emoji: "👏", label: "Applause" },
];
