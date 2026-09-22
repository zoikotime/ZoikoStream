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
// The artwork each reaction renders as. Microsoft Fluent Emoji 3D, MIT licensed and
// vendored under src/assets/reactions/ (see the note in that folder) rather than hotlinked,
// so the bundle carries them, Vite fingerprints them for cache-busting, and nothing at
// runtime depends on a third-party host staying up.
//
// The `emoji` character beside each one is NOT decoration: it is the fallback
// components/live/ReactionGlyph.jsx renders if the asset fails to load. Both belong to the
// same reaction, which is why they live on the same row.
import clapAsset from "../assets/reactions/clap.webp";
import fireAsset from "../assets/reactions/fire.webp";
import heartAsset from "../assets/reactions/heart.webp";
import likeAsset from "../assets/reactions/like.webp";
import partyAsset from "../assets/reactions/party.webp";

export const REACTIONS = [
  { key: "like", emoji: "👍", label: "Like", asset: likeAsset },
  { key: "heart", emoji: "❤️", label: "Love", asset: heartAsset },
  { key: "party", emoji: "🎉", label: "Celebrate", asset: partyAsset },
  { key: "fire", emoji: "🔥", label: "Hype", asset: fireAsset },
  { key: "clap", emoji: "👏", label: "Applause", asset: clapAsset },
];

// key -> emoji, for the overlay: it receives a wire key off the socket and has to render a
// glyph, and an O(1) lookup beats a find() per floating item on the busiest path either
// surface has. Anything not in here is dropped rather than rendered, so an unknown key
// from a newer server can never paint `undefined` over the video.
export const REACTION_EMOJI = Object.fromEntries(REACTIONS.map((r) => [r.key, r.emoji]));

// key -> the whole row, for the overlay: it gets a wire key off the socket and needs the
// artwork AND the fallback character together. Same O(1)-per-float reasoning as above, and
// the same rule about unknown keys — a miss here is dropped, never rendered.
export const REACTION_BY_KEY = Object.fromEntries(REACTIONS.map((r) => [r.key, r]));
