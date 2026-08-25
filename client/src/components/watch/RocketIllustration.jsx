// client/src/components/watch/RocketIllustration.jsx
// Purely decorative — a small rocket-through-clouds SVG for the event header banner
// (WatchHeader.jsx), matching the reference design's "launch" motif. Static, no data/
// props, pointer-events-none so it never intercepts a click meant for the header below it.
export default function RocketIllustration({ className = "" }) {
  return (
    <svg
      viewBox="0 0 160 160"
      aria-hidden="true"
      className={className}
    >
      <ellipse cx="80" cy="132" rx="46" ry="10" fill="white" opacity="0.12" />
      <ellipse cx="40" cy="140" rx="22" ry="6" fill="white" opacity="0.1" />
      <ellipse cx="122" cy="138" rx="18" ry="5" fill="white" opacity="0.1" />
      <g transform="translate(80 78) rotate(-18)">
        <path d="M0 -54c11 10 17 26 17 42 0 8-2 16-6 22H-11c-4-6-6-14-6-22 0-16 6-32 17-42z" fill="white" opacity="0.92" />
        <circle cx="0" cy="-32" r="6" fill="white" opacity="0.5" />
        <path d="M-17 10c-8 2-14 9-16 18l14-4z" fill="white" opacity="0.75" />
        <path d="M17 10c8 2 14 9 16 18l-14-4z" fill="white" opacity="0.75" />
        <path d="M-8 10h16l-5 16c-1 3-5 3-6 0z" fill="white" opacity="0.85" />
        <path d="M-6 26c2 10 4 16 6 22 2-6 4-12 6-22z" fill="white" opacity="0.55" />
      </g>
    </svg>
  );
}
