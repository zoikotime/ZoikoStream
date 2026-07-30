// client/src/data/host.js
// Dummy data for the Host Dashboard studio (/host/dashboard).
// ponytail: mock data — swap for live event/session APIs when the backend lands.
import { FiCalendar, FiClock, FiRadio, FiVideo } from "react-icons/fi";

// Initials for avatar tiles (mirrors the helper used in Topbar/MainLayout).
export const initials = (name = "") =>
  name.trim().split(/\s+/).slice(0, 2).map((w) => w[0]).join("").toUpperCase() || "?";

export const currentEvent = {
  name: "Product Launch — Aurora Platform",
  session: "Keynote & Live Demo",
  scheduledFor: "3:00 PM – 4:30 PM IST",
  viewers: 1284,
};

// Summary cards. `icon` is a react-icons component; `live` pulses (see StatsCard).
export const summaryStats = [
  { title: "Today's Events", value: 3, icon: FiCalendar, accent: "blue" },
  { title: "Upcoming Events", value: 7, icon: FiClock, accent: "violet" },
  { title: "Live Now", value: 1, icon: FiRadio, accent: "emerald", live: true },
  { title: "Recordings", value: 24, icon: FiVideo, accent: "amber" },
];

export const participants = [
  { id: 1, name: "Alex Morgan", role: "Host", speaking: true, muted: false, camOff: false, accent: "emerald" },
  { id: 2, name: "Priya Nair", role: "Co-host", speaking: false, muted: false, camOff: false, accent: "violet" },
  { id: 3, name: "Leo Fischer", role: "Speaker", speaking: false, muted: true, camOff: false, accent: "blue" },
  { id: 4, name: "Sofia Alvarez", role: "Speaker", speaking: false, muted: false, camOff: true, accent: "amber" },
  { id: 5, name: "Marcus Reed", role: "Moderator", speaking: false, muted: true, camOff: false, accent: "indigo" },
  { id: 6, name: "Jordan Lee", role: "Attendee", speaking: false, muted: true, camOff: true, accent: "rose" },
  { id: 7, name: "Noah Kim", role: "Attendee", speaking: false, muted: true, camOff: true, accent: "blue" },
  { id: 8, name: "Elena Rossi", role: "Attendee", speaking: false, muted: true, camOff: true, accent: "violet" },
];

export const chatMessages = [
  { id: 1, name: "Priya Nair", role: "Co-host", text: "We're live in 2 minutes — mic check done ✅", time: "2:58 PM" },
  { id: 2, name: "Jordan Lee", role: "Attendee", text: "Audio is crystal clear from Berlin 👏", time: "3:01 PM" },
  { id: 3, name: "Sofia Alvarez", role: "Speaker", text: "Pulling up the demo slides now.", time: "3:03 PM" },
  { id: 4, name: "Noah Kim", role: "Attendee", text: "Will the recording be available afterwards?", time: "3:04 PM" },
  { id: 5, name: "Marcus Reed", role: "Moderator", text: "Yes — recording link goes out by email tonight.", time: "3:05 PM" },
];

export const notifications = [
  { id: 1, kind: "record", text: "Recording started for this session", time: "3:00 PM" },
  { id: 2, kind: "join", text: "Sofia Alvarez joined as Speaker", time: "2:59 PM" },
  { id: 3, kind: "raise", text: "Jordan Lee raised their hand", time: "3:02 PM" },
  { id: 4, kind: "poll", text: "Poll “Which feature excites you most?” launched", time: "3:06 PM" },
  { id: 5, kind: "qa", text: "3 new questions in the Q&A queue", time: "3:07 PM" },
  { id: 6, kind: "system", text: "Viewer count passed 1,000 🎉", time: "3:08 PM" },
];
