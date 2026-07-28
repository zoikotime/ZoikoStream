// client/src/data/watch.js
// Dummy data for the Viewer Portal (/events/:eventId/watch).
// ponytail: mock data — swap for the live stream + chat/Q&A/poll sockets later.

export const initials = (name = "") =>
  name.trim().split(/\s+/).slice(0, 2).map((w) => w[0]).join("").toUpperCase() || "?";

export const startingViewers = 1284;

// Enriches the plain speaker-name list on an event with a title + accent.
export const speakerProfiles = {
  "Ava Chen": { title: "VP of Engineering", org: "ZoikoStream", accent: "emerald" },
  "Priya Nair": { title: "Principal Architect", org: "ZoikoStream", accent: "violet" },
  "Leo Fischer": { title: "Staff Engineer, Media", org: "ZoikoStream", accent: "blue" },
  "Sofia Alvarez": { title: "Product Lead, Live", org: "ZoikoStream", accent: "amber" },
  "Marcus Reed": { title: "Head of Developer Relations", org: "ZoikoStream", accent: "indigo" },
  "Noah Kim": { title: "Solutions Engineer", org: "ZoikoStream", accent: "rose" },
};

export const agenda = [
  { time: "10:00", title: "Doors open & networking", speaker: null },
  { time: "10:30", title: "Opening keynote: the state of live", speaker: "Ava Chen" },
  { time: "11:15", title: "Panel — where streaming infra is heading", speaker: "Priya Nair" },
  { time: "12:00", title: "Live demo: sub-second latency at scale", speaker: "Leo Fischer" },
  { time: "12:45", title: "Audience Q&A", speaker: "Sofia Alvarez" },
  { time: "13:15", title: "Closing remarks", speaker: "Ava Chen" },
];

export const chatSeed = [
  { id: 1, name: "Jordan Lee", text: "Audio + video are perfect from Berlin 👏", time: "10:31" },
  { id: 2, name: "Elena Rossi", text: "This keynote is 🔥", time: "10:34" },
  { id: 3, name: "Sam Rivera", text: "Can someone drop the docs link?", time: "10:36" },
  { id: 4, name: "Moderator", text: "Docs are here → zoikostream.dev/docs 📚", time: "10:37", pinned: true },
  { id: 5, name: "Taylor Quinn", text: "Those latency numbers are wild.", time: "10:39" },
  { id: 6, name: "Morgan Lee", text: "Replay will be available after, right?", time: "10:41" },
];

export const qaSeed = [
  { id: 1, name: "Elena Rossi", text: "Does Aurora support multi-region failover out of the box?", votes: 42, answered: false },
  { id: 2, name: "Noah Kim", text: "What's the enterprise pricing model?", votes: 28, answered: true },
  { id: 3, name: "Jordan Lee", text: "Can we self-host the recording pipeline?", votes: 19, answered: false },
  { id: 4, name: "Sam Rivera", text: "Is there a React Native SDK?", votes: 11, answered: false },
];

export const pollsSeed = [
  {
    id: 1,
    question: "Which feature are you most excited about?",
    options: [
      { id: "a", label: "Sub-second latency", votes: 340 },
      { id: "b", label: "AI live captions", votes: 512 },
      { id: "c", label: "Multi-region failover", votes: 208 },
      { id: "d", label: "Interactive polls", votes: 96 },
    ],
  },
  {
    id: 2,
    question: "How are you watching today?",
    options: [
      { id: "a", label: "Desktop", votes: 720 },
      { id: "b", label: "Mobile", votes: 300 },
      { id: "c", label: "TV / big screen", votes: 130 },
    ],
  },
];

// Recordings surfaced under the player once an event has ended.
export const relatedRecordings = [
  { id: 6, title: "Cloud Technology Webinar", duration: "1:02:14", date: "May 14, 2024", views: 3420, accent: "rose" },
  { id: 7, title: "Customer Meet 2024", duration: "2:48:30", date: "May 10, 2024", views: 890, accent: "emerald" },
  { id: 8, title: "Security & Compliance Briefing", duration: "58:20", date: "Apr 30, 2024", views: 512, accent: "blue" },
  { id: 5, title: "Developer Deep Dive", duration: "1:24:05", date: "Jun 5, 2024", views: 1210, accent: "indigo" },
];
