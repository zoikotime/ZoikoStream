// Non-visual pieces shared by the Guide popup (GuideWidget) and the full Guide page (GuidePage):
// the quick-action routing table, flow names, and the online/offline hook.
import { useEffect, useState } from "react";

// Quick intents that start a typed flow directly instead of asking the model (Conversation
// Flow §4: quick actions route to the same engines as free text).
export const QUICK_FLOWS = {
  "I-VIEWER": ["support", "viewer_other"],
  "I-VIEWER-ACCESS": ["support", "viewer_access"],
  "I-PLAYBACK": ["support", "playback"],
  "I-CAPTIONS": ["support", "captions"],
  "I-REPLAY": ["support", "replay"],
  "I-DEMO": ["lead", "demo"],
  "I-SALES": ["lead", "sales"],
};

export const FLOW_NAMES = {
  viewer_access: "access help", playback: "playback help", captions: "captions help",
  replay: "replay help", viewer_other: "viewer help",
};

// Figma §14 "Offline: local offline state; underlying website remains available."
export const OFFLINE_TITLE = "You're offline.";
export const OFFLINE_TEXT = "Guide needs an internet connection. The website stays available, and Guide reconnects when you're back online.";

export function useOnline() {
  const [online, setOnline] = useState(() => typeof navigator === "undefined" || navigator.onLine !== false);
  useEffect(() => {
    const up = () => setOnline(true);
    const down = () => setOnline(false);
    window.addEventListener("online", up);
    window.addEventListener("offline", down);
    return () => {
      window.removeEventListener("online", up);
      window.removeEventListener("offline", down);
    };
  }, []);
  return online;
}
