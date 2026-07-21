// client/src/data/moderation.js
// Dummy data for the Moderator Dashboard (/moderator/dashboard).
// ponytail: mock data — swap for the live moderation sockets when the backend lands.

export const initials = (name = "") =>
  name.trim().split(/\s+/).slice(0, 2).map((w) => w[0]).join("").toUpperCase() || "?";

export const currentEvent = {
  name: "Tech Summit 2026 — Live Keynote",
  host: "Ava Chen",
  status: "Live",
  viewers: 1284,
  startedAgo: 22 * 60 + 14, // seconds the event has been live — seeds the header timer
};

// `role` and `muted` are separate so muting never destroys a Speaker role.
export const participantsSeed = [
  { id: 1, name: "Priya Nair", email: "priya.nair@aurora.io", joined: "10:28 AM", role: "Speaker", muted: false, accent: "violet" },
  { id: 2, name: "Leo Fischer", email: "leo.fischer@nordwind.de", joined: "10:29 AM", role: "Speaker", muted: false, accent: "blue" },
  { id: 3, name: "Jordan Lee", email: "jordan.lee@gmail.com", joined: "10:31 AM", role: "Attendee", muted: false, accent: "emerald" },
  { id: 4, name: "Elena Rossi", email: "elena.rossi@rossi-labs.it", joined: "10:32 AM", role: "Attendee", muted: false, accent: "amber" },
  { id: 5, name: "Sam Rivera", email: "sam.rivera@outlook.com", joined: "10:33 AM", role: "Attendee", muted: true, accent: "rose" },
  { id: 6, name: "Noah Kim", email: "noah.kim@kim.dev", joined: "10:35 AM", role: "Attendee", muted: false, accent: "indigo" },
  { id: 7, name: "Taylor Quinn", email: "taylor.quinn@brightcast.co", joined: "10:36 AM", role: "Attendee", muted: false, accent: "blue" },
  { id: 8, name: "Morgan Lee", email: "morgan.lee@proton.me", joined: "10:38 AM", role: "Attendee", muted: true, accent: "violet" },
  { id: 9, name: "Dana White", email: "dana.white@whiteco.com", joined: "10:41 AM", role: "Attendee", muted: false, accent: "emerald" },
];

export const chatSeed = [
  { id: 101, name: "Moderator", text: "Welcome everyone! Drop your questions in the Q&A tab 👋", time: "10:30", status: "approved", pinned: true },
  { id: 102, name: "Jordan Lee", text: "Audio + video perfect from Berlin 👏", time: "10:31", status: "approved", pinned: false },
  { id: 103, name: "Elena Rossi", text: "This keynote is 🔥", time: "10:34", status: "approved", pinned: false },
  { id: 104, name: "Sam Rivera", text: "BUY CHEAP FOLLOWERS >> spam-link.example", time: "10:35", status: "pending", pinned: false, flagged: true },
  { id: 105, name: "Taylor Quinn", text: "Can we get the slides link please?", time: "10:36", status: "pending", pinned: false },
  { id: 106, name: "Noah Kim", text: "The latency numbers are wild.", time: "10:38", status: "approved", pinned: false },
  { id: 107, name: "Dana White", text: "Is there a recording afterwards?", time: "10:40", status: "pending", pinned: false },
];

export const questionsSeed = [
  { id: 201, name: "Elena Rossi", text: "Does Aurora support multi-region failover out of the box?", votes: 42, status: "approved" },
  { id: 202, name: "Noah Kim", text: "What's the enterprise pricing model?", votes: 28, status: "answered" },
  { id: 203, name: "Jordan Lee", text: "Can we self-host the recording pipeline?", votes: 19, status: "pending" },
  { id: 204, name: "Sam Rivera", text: "Is there a React Native SDK?", votes: 11, status: "pending" },
  { id: 205, name: "Dana White", text: "How do webhooks handle retries?", votes: 6, status: "approved" },
];

export const pollsSeed = [
  {
    id: 301,
    question: "Which feature are you most excited about?",
    status: "live",
    options: [
      { label: "Sub-second latency", votes: 340 },
      { label: "AI live captions", votes: 512 },
      { label: "Multi-region failover", votes: 208 },
    ],
  },
  {
    id: 302,
    question: "How are you watching today?",
    status: "closed",
    options: [
      { label: "Desktop", votes: 720 },
      { label: "Mobile", votes: 300 },
      { label: "TV / big screen", votes: 130 },
    ],
  },
  {
    id: 303,
    question: "Should we extend the Q&A by 15 minutes?",
    status: "draft",
    options: [
      { label: "Yes please", votes: 0 },
      { label: "No, keep to schedule", votes: 0 },
    ],
  },
];

export const announcementsSeed = [
  { id: 401, text: "Slides are available at zoikostream.dev/summit 📎", time: "10:33" },
  { id: 402, text: "We'll take live Q&A right after the demo.", time: "10:37" },
];

export const activitySeed = [
  { id: 501, kind: "poll", text: "Poll “Which feature…” launched", time: "10:36" },
  { id: 502, kind: "mod", text: "Sam Rivera was muted", time: "10:35" },
  { id: 503, kind: "qa", text: "New question from Jordan Lee", time: "10:34" },
  { id: 504, kind: "join", text: "Elena Rossi joined the event", time: "10:32" },
  { id: 505, kind: "chat", text: "A message was flagged for review", time: "10:35" },
  { id: 506, kind: "system", text: "Viewer count passed 1,000 🎉", time: "10:33" },
];
