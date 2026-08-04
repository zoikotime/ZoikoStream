import { useCallback, useEffect, useState } from "react";
import api, { errMsg } from "../api";
import { PREFERENCE_CLASSES } from "../data/attendee";

// The attendee's own settings, loaded once and saved as they change.
//
// The accessibility switches are applied to <html>, not to a page wrapper: modals, toasts and the
// player's fullscreen chrome all render through portals or fixed positioning, and a class further
// down the tree would miss every one of them.
//
// Saved on a debounce rather than behind a Save button — a switch that needs confirming reads as
// broken. The server whitelists every key (services/attendee.clean_preferences), so a stale client
// cannot write something unexpected.

const SAVE_DEBOUNCE_MS = 600;

// Mirrors services/attendee.DEFAULT_PREFERENCES so the first render is correct rather than empty.
// The server merges its own defaults too — this is only what to paint before the fetch lands.
const FALLBACK = {
  language: "en",
  text_size: "default",
  reduced_motion: false,
  high_contrast: false,
  captions: false,
  reminder_offset_minutes: 15,
  favorite_speakers: [],
  notify: {},
};

/** Apply the display preferences to the document root. Exported so a page that renders before this
 *  hook mounts can call it directly with a cached value if one is ever added. */
export function applyPreferenceClasses(prefs) {
  const root = document.documentElement;
  if (!root) return;
  // Clear every class this module owns first — switching from `larger` to `large` must not leave
  // both on.
  Object.values(PREFERENCE_CLASSES.text_size).forEach((c) => root.classList.remove(c));
  root.classList.remove(PREFERENCE_CLASSES.high_contrast, PREFERENCE_CLASSES.reduced_motion);

  const size = PREFERENCE_CLASSES.text_size[prefs?.text_size];
  if (size) root.classList.add(size);
  if (prefs?.high_contrast) root.classList.add(PREFERENCE_CLASSES.high_contrast);
  if (prefs?.reduced_motion) root.classList.add(PREFERENCE_CLASSES.reduced_motion);
}

export default function useAttendeePreferences() {
  const [prefs, setPrefs] = useState(FALLBACK);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);

  // Load once. setState here is inside an async callback, not the effect body, so it does not
  // cascade a render.
  useEffect(() => {
    let alive = true;
    api.get("/attendee/preferences")
      .then((r) => { if (alive) setPrefs({ ...FALLBACK, ...r.data }); })
      .catch(() => { /* the defaults above are a usable page; a failed load is not an error state */ });
    return () => { alive = false; };
  }, []);

  // Apply to <html> whenever they change, and strip them on unmount — these are the ATTENDEE's
  // preferences, and leaving them on the document after navigating into an admin console would
  // apply somebody's larger text to a surface that never asked for it.
  useEffect(() => {
    applyPreferenceClasses(prefs);
    return () => applyPreferenceClasses(null);
  }, [prefs]);

  // Debounced save of whatever changed. The patch is accumulated so flipping three switches
  // quickly is one request, not three.
  const [pending, setPending] = useState(null);
  useEffect(() => {
    if (!pending) return undefined;
    const id = setTimeout(() => {
      setSaving(true);
      api.patch("/attendee/preferences", pending)
        .then((r) => { setPrefs({ ...FALLBACK, ...r.data }); setError(null); })
        .catch((e) => setError(errMsg(e, "Couldn't save your settings")))
        .finally(() => { setSaving(false); setPending(null); });
    }, SAVE_DEBOUNCE_MS);
    return () => clearTimeout(id);
  }, [pending]);

  const set = useCallback((patch) => {
    setPrefs((prev) => ({ ...prev, ...patch }));
    setPending((prev) => ({ ...(prev || {}), ...patch }));
  }, []);

  const setNotify = useCallback((key, on) => {
    setPrefs((prev) => ({ ...prev, notify: { ...prev.notify, [key]: on } }));
    setPending((prev) => ({ ...(prev || {}), notify: { ...(prev?.notify || {}), [key]: on } }));
  }, []);

  return { prefs, saving, error, set, setNotify };
}
