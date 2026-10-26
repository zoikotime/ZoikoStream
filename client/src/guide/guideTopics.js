// The six Guide help topics, shared by the full Guide page (/guide) and the homepage band
// (GuideTopicsBand). Viewer topics start the server's deterministic flows; the other two ask
// Guide with their catalog intent. `id` is also the /guide?topic= deep-link value.
import { CalendarDays, Captions, ChartColumn, History, Info, Play } from "lucide-react";

export const GUIDE_TOPICS = [
  { id: "access", tone: "blue", Icon: CalendarDays, intent: "I-VIEWER-ACCESS", title: "Join or Access an Event",
    text: "Get help finding your event, accessing your link, and joining the live stream." },
  { id: "playback", tone: "pink", Icon: Play, intent: "I-PLAYBACK", title: "Fix Playback Issues",
    text: "Troubleshoot buffering, video, audio, or other playback problems." },
  { id: "captions", tone: "green", Icon: Captions, intent: "I-CAPTIONS", title: "Captions & Accessibility",
    text: "Learn about closed captions, accessibility features, and supported devices." },
  { id: "replay", tone: "violet", Icon: History, intent: "I-REPLAY", title: "Replay & Watch Again",
    text: "Find out how to watch replays, access past events, and view on-demand content." },
  { id: "about", tone: "amber", Icon: Info, intent: "I-PRODUCT", ask: "What is ZoikoStream?", title: "About Zoiko Stream",
    text: "Get answers about our platform, features, supported devices, and general questions." },
  { id: "pricing", tone: "rose", Icon: ChartColumn, intent: "I-PRICING", ask: "What plans and pricing are available?",
    title: "Plans, Pricing or Request a Demo",
    text: "Learn about plans, pricing, solutions, or request a demo for your organization." },
];

export const guideTopicById = (id) => GUIDE_TOPICS.find((t) => t.id === id) || null;
