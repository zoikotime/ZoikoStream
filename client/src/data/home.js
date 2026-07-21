// ponytail: all homepage copy + dummy data lives here so sections stay presentational.
// TODO(backend): items marked below can be fed from a CMS / marketing API later.
import {
  FiServer, FiVideo, FiShield, FiBarChart2, FiRadio, FiCode, FiCpu,
  FiGlobe, FiLock, FiActivity, FiPlayCircle, FiUploadCloud,
  FiEye, FiArchive, FiZap, FiHeart, FiHome, FiBriefcase, FiAward,
  FiMic, FiBookOpen, FiFileText, FiLayers, FiTerminal,
} from "react-icons/fi";

export const NAV = [
  { label: "Platform", href: "#platform" },
  { label: "Solutions", href: "#pathways" },
  { label: "Live Events", href: "#live-events" },
  { label: "Developers", href: "#developers" },
  { label: "Pricing", href: "/pricing" }, // TODO(routes): real pricing page
  { label: "Resources", href: "#resources" },
  { label: "Company", href: "/company" }, // TODO(routes): real company page
];

export const PLATFORM_PILLARS = [
  { icon: FiServer, title: "Infrastructure", body: "Globally distributed ingest, transcode, and edge delivery you don't have to operate." },
  { icon: FiVideo, title: "Streaming", body: "Sub-second live and adaptive VOD from a single API, tuned per device and network." },
  { icon: FiShield, title: "Security", body: "End-to-end encryption, signed playback, DRM, and granular access policies by default." },
  { icon: FiBarChart2, title: "Analytics", body: "Real-time QoE, viewer engagement, and delivery metrics with exportable data." },
  { icon: FiRadio, title: "Live Events", body: "Production-grade broadcasts for one viewer or a million, with zero pre-provisioning." },
];

export const PATHWAYS = [
  {
    key: "developers",
    icon: FiCode,
    title: "Developers",
    body: "Ship video features in days. SDKs, typed APIs, webhooks, and a sandbox that mirrors production.",
    cta: "Read the docs",
    href: "#developers",
    accent: "emerald",
    points: ["REST + WebRTC APIs", "SDKs for every stack", "Live sandbox keys"],
  },
  {
    key: "enterprise",
    icon: FiBriefcase,
    title: "Enterprise",
    body: "Media operations at scale — monitoring, compliance, recording, and SLAs your platform team can trust.",
    cta: "Talk to an expert",
    href: "#enterprise",
    accent: "indigo",
    points: ["99.99% uptime SLA", "SOC 2 + audit logs", "Dedicated support"],
  },
  {
    key: "live-events",
    icon: FiRadio,
    title: "Live Events",
    body: "Broadcast memorials, worship, conferences, and ceremonies with a production UI anyone can run.",
    cta: "Explore live events",
    href: "#live-events",
    accent: "rose",
    points: ["One-click broadcasts", "Managed recording", "Global low latency"],
  },
];

// Media lifecycle stages — drives the animated pipeline.
export const LIFECYCLE = [
  { icon: FiMic, title: "Contribute", body: "Capture from studios, encoders, browsers, or mobile." },
  { icon: FiUploadCloud, title: "Ingest", body: "RTMP, SRT, WebRTC, and HLS accepted at the nearest edge." },
  { icon: FiCpu, title: "Produce", body: "Transcode, caption, and mix in real time." },
  { icon: FiLock, title: "Secure", body: "Encrypt, sign, and apply DRM before a single byte ships." },
  { icon: FiGlobe, title: "Deliver", body: "Adaptive playback over a global multi-CDN edge." },
  { icon: FiActivity, title: "Understand", body: "Measure QoE, engagement, and delivery in real time." },
  { icon: FiArchive, title: "Preserve", body: "Archive to durable storage with instant replay." },
];

// Developer platform — code samples per language for the interactive editor.
export const CODE_SAMPLES = {
  JavaScript: `import { Zoiko } from "@zoikostream/sdk";

const zoiko = new Zoiko(process.env.ZOIKO_KEY);

// Create a secure live stream
const stream = await zoiko.streams.create({
  name: "keynote-2026",
  latency: "low",
  recording: true,
  drm: true,
});

console.log(stream.playbackUrl);`,
  Python: `from zoikostream import Zoiko

zoiko = Zoiko(api_key=os.environ["ZOIKO_KEY"])

# Create a secure live stream
stream = zoiko.streams.create(
    name="keynote-2026",
    latency="low",
    recording=True,
    drm=True,
)

print(stream.playback_url)`,
  cURL: `curl https://api.zoikostream.com/v1/streams \\
  -H "Authorization: Bearer $ZOIKO_KEY" \\
  -d name="keynote-2026" \\
  -d latency="low" \\
  -d recording=true \\
  -d drm=true`,
  Go: `package main

import "github.com/zoikostream/zoiko-go"

func main() {
    z := zoiko.New(os.Getenv("ZOIKO_KEY"))

    stream, _ := z.Streams.Create(&zoiko.StreamParams{
        Name:      "keynote-2026",
        Latency:   "low",
        Recording: true,
        DRM:       true,
    })

    fmt.Println(stream.PlaybackURL)
}`,
};

// TODO(backend): this response is illustrative — mirror real POST /v1/streams output.
export const API_RESPONSE = `{
  "id": "str_9fK2xQ",
  "name": "keynote-2026",
  "status": "ready",
  "latency": "low",
  "playbackUrl": "https://cdn.zoikostream.com/str_9fK2xQ.m3u8",
  "drm": true,
  "createdAt": "2026-07-21T09:14:00Z"
}`;

export const DEV_FEATURES = [
  { icon: FiTerminal, title: "SDKs", body: "Typed clients for JS, Python, Go, Swift, and Kotlin." },
  { icon: FiCode, title: "APIs", body: "Predictable REST + realtime APIs with webhooks." },
  { icon: FiLayers, title: "Examples", body: "Copy-paste starters for players, uploads, and auth." },
];

// Enterprise dashboard mockup — dummy metrics.
export const ENTERPRISE_METRICS = [
  { label: "Concurrent viewers", value: 84120, suffix: "", accent: "emerald" },
  { label: "Streams live", value: 37, suffix: "", accent: "indigo" },
  { label: "Avg. startup", value: 420, suffix: "ms", accent: "blue" },
  { label: "Delivery success", value: 99.98, suffix: "%", accent: "amber", decimals: 2 },
];

export const ENTERPRISE_ALERTS = [
  { level: "ok", text: "Edge region us-east-1 healthy", time: "just now" },
  { level: "warn", text: "Rebuffer ratio elevated in ap-south-1", time: "2m ago" },
  { level: "ok", text: "Recording pipeline caught up", time: "6m ago" },
];

export const ENTERPRISE_OPS = [
  "Media monitoring", "Analytics", "Alerts", "Recording", "Replay", "Viewer metrics",
];

// Live events categories — drives the interactive category selector.
export const EVENT_CATEGORIES = [
  { key: "memorials", icon: FiHeart, title: "Memorials", blurb: "Dignified, private streaming for services, with family access controls and lasting recordings." },
  { key: "worship", icon: FiHome, title: "Worship", blurb: "Weekly services to any device, with multi-camera production and giving overlays." },
  { key: "corporate", icon: FiBriefcase, title: "Corporate", blurb: "Town halls and product launches with SSO, moderation, and audience analytics." },
  { key: "graduations", icon: FiAward, title: "Graduations", blurb: "Campus ceremonies at scale with reliable capacity and instant replays." },
  { key: "conferences", icon: FiMic, title: "Conferences", blurb: "Multi-track sessions, live captions, and on-demand libraries after the event." },
  { key: "weddings", icon: FiHeart, title: "Weddings", blurb: "Beautiful private broadcasts for guests near and far, kept forever." },
];

export const TRUST = [
  { icon: FiLock, title: "Encryption", body: "AES-128/256 at rest and in transit, signed URLs, and studio-approved DRM." },
  { icon: FiPlayCircle, title: "Recording", body: "Tamper-evident recordings with configurable retention and legal hold." },
  { icon: FiEye, title: "Accessibility", body: "Captions, audio descriptions, and WCAG-conformant players out of the box." },
  { icon: FiActivity, title: "Resilience", body: "Multi-region failover with automated health checks and no single point of failure." },
  { icon: FiGlobe, title: "Global delivery", body: "Multi-CDN edge in 60+ regions for consistently low latency." },
  { icon: FiShield, title: "Compliance", body: "SOC 2 Type II, GDPR, and audit logging for every access." },
];

export const PROOF = [
  { icon: FiServer, title: "Architecture", body: "See how ingest, transcode, and delivery fit together.", cta: "View architecture", href: "#platform" },
  { icon: FiActivity, title: "Status", body: "Live platform health and historical uptime.", cta: "Open status page", href: "/status" },
  { icon: FiZap, title: "Sandbox", body: "Free keys that mirror production behaviour.", cta: "Get sandbox keys", href: "/signup" },
  { icon: FiBookOpen, title: "Documentation", body: "Guides, references, and end-to-end tutorials.", cta: "Read the docs", href: "/docs" },
  { icon: FiCode, title: "Public APIs", body: "Explore every endpoint in the API reference.", cta: "Browse API", href: "/docs/api" },
];

export const FINAL_CTA = [
  {
    key: "developer", icon: FiCode, title: "For Developers",
    body: "Grab a sandbox key and stream your first video in minutes.",
    cta: "Start Building", href: "/signup", accent: "emerald",
  },
  {
    key: "enterprise", icon: FiBriefcase, title: "For Enterprise",
    body: "Design a media platform with our solutions engineers.",
    cta: "Talk to an Expert", href: "/contact", accent: "indigo",
  },
  {
    key: "live-events", icon: FiRadio, title: "For Live Events",
    body: "Run your next broadcast with a team that's done it before.",
    cta: "Plan an Event", href: "/live-events", accent: "rose",
  },
];

export const RESOURCES = [
  { icon: FiBookOpen, title: "Documentation", body: "Everything to build, ship, and scale on ZoikoStream.", href: "/docs" },
  { icon: FiServer, title: "Architecture", body: "Reference designs for common streaming workloads.", href: "/architecture" },
  { icon: FiFileText, title: "Guides", body: "Step-by-step tutorials from ingest to analytics.", href: "/guides" },
  { icon: FiCpu, title: "Blog", body: "Engineering deep-dives and product announcements.", href: "/blog" },
];

// TODO(backend): FAQ could come from a CMS; static for now.
export const FAQS = [
  { q: "What is ZoikoStream?", a: "ZoikoStream is secure video infrastructure — ingest, transcoding, DRM, global delivery, analytics, and live-event tooling behind a single API, so you don't operate any of it yourself." },
  { q: "How low is the latency?", a: "Sub-second glass-to-glass over WebRTC for interactive use, and 2–5s low-latency HLS for large broadcasts. You choose per stream." },
  { q: "Is my content secure?", a: "Yes. Content is encrypted at rest and in transit, playback URLs are signed and short-lived, and studio-approved DRM is available on every plan." },
  { q: "Can I record and replay events?", a: "Every live stream can be recorded automatically, with configurable retention, instant replay, and export to your own storage." },
  { q: "Do you offer an SLA?", a: "Enterprise plans include a 99.99% uptime SLA, multi-region failover, and dedicated support with response-time guarantees." },
  { q: "How do I get started?", a: "Create a free account for sandbox keys that mirror production, or talk to an expert to scope an enterprise deployment." },
  { q: "Which regions do you support?", a: "Delivery spans 60+ edge regions across a multi-CDN network, with ingest points on every continent." },
  { q: "Do you support accessibility requirements?", a: "Players ship WCAG-conformant with captions, keyboard control, and audio descriptions; recordings retain caption tracks." },
];

export const FOOTER = [
  { title: "Platform", links: ["Infrastructure", "Streaming", "Security", "Analytics", "Live Events"] },
  { title: "Developers", links: ["Documentation", "API Reference", "SDKs", "Sandbox", "Status"] },
  { title: "Solutions", links: ["Enterprise", "Media & Broadcast", "Education", "Worship", "Events"] },
  { title: "Resources", links: ["Guides", "Architecture", "Blog", "Changelog", "Support"] },
  { title: "Company", links: ["About", "Careers", "Customers", "Contact", "Legal"] },
];

export const HERO_STATS = [
  { value: 60, suffix: "+", label: "Edge regions" },
  { value: 99.99, suffix: "%", label: "Uptime SLA", decimals: 2 },
  { value: 420, prefix: "<", suffix: "ms", label: "Startup time" },
];
