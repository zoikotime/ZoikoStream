// client/src/pages/watch/EventWatch.jsx
// Viewer Portal — what attendees see from an event invite link.
// Route: /events/:eventId/watch. Standalone public page (NOT the Org Dashboard).
// No backend: video is a dummy surface, all interactions run on local state.
import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { FiRadio, FiSun, FiMoon } from "react-icons/fi";
import { useTheme } from "../../theme/ThemeContext";
import { getEvent } from "../../data/events";
import { startingViewers } from "../../data/watch";
import WatchHeader from "../../components/watch/WatchHeader";
import VideoPlayer from "../../components/watch/VideoPlayer";
import WatchPanel from "../../components/watch/WatchPanel";
import EventInfo from "../../components/watch/EventInfo";
import RelatedRecordings from "../../components/watch/RelatedRecordings";

export default function EventWatch() {
  const { eventId } = useParams();
  const { theme, toggle } = useTheme();
  const event = getEvent(eventId);

  const live = event?.status === "Live";
  const ended = event?.status === "Completed";

  // Live viewer count that gently drifts (setState only in the interval callback).
  const [viewers, setViewers] = useState(event?.viewers ?? startingViewers);
  useEffect(() => {
    if (!live) return;
    const t = setInterval(
      () => setViewers((v) => Math.max(0, v + Math.floor(Math.random() * 15) - 6)),
      3000
    );
    return () => clearInterval(t);
  }, [live]);

  if (!event)
    return (
      <div className="grid min-h-screen place-items-center bg-slate-50 dark:bg-slate-950">
        <div className="text-center">
          <p className="text-sm text-slate-500 dark:text-slate-400">This event could not be found.</p>
          <Link to="/" className="mt-2 inline-block text-sm font-medium text-emerald-600 hover:text-emerald-500 dark:text-emerald-400">
            Back to home
          </Link>
        </div>
      </div>
    );

  return (
    <div className="min-h-screen bg-slate-50 text-slate-800 dark:bg-slate-950 dark:text-slate-200">
      {/* Brand bar */}
      <header className="sticky top-0 z-30 border-b border-slate-200 bg-white/80 backdrop-blur dark:border-slate-800 dark:bg-slate-900/80">
        <div className="mx-auto flex max-w-7xl items-center justify-between px-4 py-3 sm:px-6">
          <Link to="/" className="text-lg font-bold tracking-tight text-slate-900 dark:text-white">
            Zoiko<span className="text-emerald-500">Stream</span>
          </Link>
          <div className="flex items-center gap-3">
            {live && (
              <span className="inline-flex items-center gap-1.5 rounded-full bg-rose-500/10 px-3 py-1 text-xs font-semibold text-rose-600 dark:text-rose-400">
                <FiRadio className="animate-pulse" /> Live now
              </span>
            )}
            <button
              onClick={toggle}
              className="grid h-9 w-9 place-items-center rounded-lg text-slate-500 hover:bg-slate-100 hover:text-slate-700 dark:text-slate-400 dark:hover:bg-slate-800 dark:hover:text-slate-100"
              aria-label="Toggle theme"
              title={theme === "dark" ? "Switch to light" : "Switch to dark"}
            >
              {theme === "dark" ? <FiSun className="text-lg" /> : <FiMoon className="text-lg" />}
            </button>
          </div>
        </div>
      </header>

      {/* Top section: banner */}
      <WatchHeader event={event} viewers={viewers} />

      {/* Main layout: player + info (70%) / chat panel (30%) */}
      <main className="mx-auto max-w-7xl px-4 py-6 sm:px-6">
        <div className="grid grid-cols-1 gap-6 lg:grid-cols-[minmax(0,1fr)_360px]">
          <div className="space-y-6">
            <VideoPlayer event={event} viewers={viewers} />
            <EventInfo event={event} />
          </div>

          <WatchPanel className="h-[70vh] self-start lg:sticky lg:top-20 lg:h-[calc(100vh-6rem)]" />
        </div>

        {/* Bottom section */}
        <div className="mt-10">
          <RelatedRecordings ended={ended} />
        </div>
      </main>

      <footer className="border-t border-slate-200 py-6 text-center text-xs text-slate-400 dark:border-slate-800">
        © 2024 ZoikoStream. All rights reserved.
      </footer>
    </div>
  );
}
