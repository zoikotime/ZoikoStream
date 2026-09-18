import { useEffect } from "react";

/**
 * Keep the Android app alive, with its camera and microphone, while `active`.
 *
 * ── THE FAILURE THIS PREVENTS ────────────────────────────────────────────────────────────
 * Android stops an app the moment it stops being visible: the process is frozen, the WebView
 * is suspended, and the camera and microphone are revoked. A host who locks their phone
 * mid-event — or switches to Messages for five seconds — therefore drops off air, because
 * LiveKit's peer connection dies with the capture it was publishing. The host sees nothing
 * (their screen is off); the audience sees the feed end.
 *
 * `useKeepAwake` is not the same thing and does not cover this. That holds the SCREEN on,
 * which helps only while the app is in front. This is about the app continuing to exist when
 * it is not.
 *
 * The fix is an Android foreground service (android/.../BroadcastService.java), which is the
 * only supported way to tell the OS that the user knows this is running. It costs a
 * persistent notification — which is also how a host with their phone on a stand confirms
 * they are still live, and how they get back to the studio in one tap.
 *
 * ── WHY THIS HOOK IS THE ONE THAT DECIDES ────────────────────────────────────────────────
 * The service holds no camera and knows nothing about LiveKit. It only changes Android's
 * opinion about whether the process may keep running, so the WebView stays the single owner
 * of the capture and the stream. That leaves exactly one thing to get right — the service
 * runs while, and only while, the studio holds a camera — and it is got right by driving it
 * from the same React state that owns the camera, rather than by a second copy of the rule
 * written in Java that could drift out of step with it.
 *
 * Silent everywhere else: on the web, and in the mobile build running in a desktop browser,
 * `window.Capacitor` is absent and every call below is a no-op.
 */
export default function useBroadcastKeepAlive(active, { title, text } = {}) {
  useEffect(() => {
    const p = typeof window !== "undefined" && window.Capacitor?.isNativePlatform?.()
      ? window.Capacitor.Plugins?.BroadcastKeepAlive
      : null;
    if (!p) return undefined;

    if (active) {
      // Deliberately not awaited and never surfaced. The plugin resolves {started:false}
      // rather than rejecting when Android refuses (the app was already backgrounded, or the
      // camera permission had not landed yet), because at this point the host is mid-
      // broadcast and the honest description of that state is "this may not survive a screen
      // lock" — not "something went wrong", which is what any error path here would be read
      // as.
      p.start({ title, text }).catch(() => {});
    } else {
      p.stop().catch(() => {});
    }

    // Stopping on cleanup as well as on `active` going false is what covers the case the
    // effect body cannot: the host navigating away, or the console unmounting after the
    // event ends. Without it the notification outlives the broadcast, advertising a camera
    // that was released minutes ago.
    return () => { p.stop().catch(() => {}); };
  }, [active, title, text]);
}
