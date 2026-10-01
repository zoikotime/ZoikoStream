// Embedded "in-app" browsers (a link tapped inside Facebook, Instagram, LINE, WeChat,
// LinkedIn, Snapchat, TikTok, or a bare Android WebView) are where live playback most often
// fails: autoplay blocked outright, no fullscreen, WebRTC cut down, storage wiped when the
// sheet closes. ZST-SPEC-VAP-001 §6.3 asks for an icon-led "Open in browser" prompt plus Copy
// Link in that case.
//
// Detection reads only the user-agent string, which these apps stamp themselves. It is a hint
// for a dismissible prompt, never a gate: a false positive costs one banner, and nothing is
// blocked on it.
const APPS = [
  ["Facebook", /\bFBAN\/|\bFBAV\/|\bFB_IAB\/|\bFBIOS\b/],
  ["Instagram", /\bInstagram\b/],
  ["LINE", /\bLine\//],
  ["WeChat", /\bMicroMessenger\//],
  ["LinkedIn", /\bLinkedInApp\b/],
  ["Snapchat", /\bSnapchat\b/],
  ["TikTok", /\bmusical_ly\b|\bBytedanceWebview\b|\bTikTok\b/],
];

export function detectInAppBrowser(ua = typeof navigator !== "undefined" ? navigator.userAgent : "") {
  const text = String(ua || "");
  const platform = /iPhone|iPad|iPod/i.test(text) ? "ios" : /Android/i.test(text) ? "android" : "other";
  for (const [app, pattern] of APPS) {
    if (pattern.test(text)) return { inApp: true, app, platform };
  }
  // A generic Android WebView marks itself with "; wv)".
  if (platform === "android" && /;\s?wv\)/.test(text)) return { inApp: true, app: "WebView", platform };
  return { inApp: false, app: null, platform };
}

/** An Android intent that asks the system to open this page in the default browser. Built
 *  from origin + path ONLY: never the query or fragment, where a credential could be. */
export function androidBrowserIntent(location = window.location) {
  const host = location.host;
  const path = location.pathname;
  const scheme = location.protocol.replace(":", "");
  return `intent://${host}${path}#Intent;scheme=${scheme};action=android.intent.action.VIEW;end`;
}
