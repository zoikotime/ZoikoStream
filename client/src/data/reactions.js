// client/src/data/reactions.js
// Shared reaction key/emoji/label list — the single source of truth for every reaction
// surface: components/watch/ReactionBar.jsx (the viewer's tap targets) and
// components/live/ReactionOverlay.jsx (the floating emoji over the viewer's player AND
// over the host's Producer Console monitor).
//
// The `key` values are WIRE PROTOCOL: they are what the viewer socket sends as
// `reaction.add {key}` and what the server echoes back as `reaction.burst {reaction}`,
// validated there against server/app/services/moderation.py REACTION_KEYS. A key added on
// one side only renders nothing at all, so the two lists must move together. Order and
// labels are display-only (Like/Love/Celebrate/Hype/Applause).
//
// There are deliberately no counts anywhere in this module: a reaction is an ephemeral
// event that animates once and is forgotten, not a number that accumulates.
export const REACTIONS = [
  { key: "like", emoji: "👍", label: "Like" },
  { key: "heart", emoji: "❤️", label: "Love" },
  { key: "party", emoji: "🎉", label: "Celebrate" },
  { key: "fire", emoji: "🔥", label: "Hype" },
  { key: "clap", emoji: "👏", label: "Applause" },
];

// key -> emoji, for the overlay: it receives a wire key off the socket and has to render a
// glyph, and an O(1) lookup beats a find() per floating item on the busiest path either
// surface has. Anything not in here is dropped rather than rendered, so an unknown key
// from a newer server can never paint `undefined` over the video.
export const REACTION_EMOJI = Object.fromEntries(REACTIONS.map((r) => [r.key, r.emoji]));
