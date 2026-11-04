import api from "../../api";

// Browser half of Google sign-in (server: app/services/google_oauth.py).
//
//   startGoogleSignIn()  POST /auth/google/start -> keep {state, code_verifier} for THIS tab
//                        in sessionStorage -> send the browser to Google.
//   startGoogleLink()    the same for linking Google to the signed-in account
//                        (POST /auth/google/link/start, needs a password step-up).
//   peekPendingSignIn()  on /auth/google/callback: read what start() stored;
//   clearPendingSignIn() then delete it, so a reload or back-navigation can never replay it.
//
// Why sessionStorage: it is per-tab and per-origin, so a sign-in started in one tab can only be
// finished in that tab, and a page on another site can never plant a state for the callback to
// accept (login CSRF). The code_verifier never appears in a URL. Nothing here is a credential
// for ZoikoStream: the server verifies the state signature, the PKCE verifier and Google's ID
// token before it issues anything.

const KEY = "zs.google-sign-in";
// Matches the server's state lifetime; an older entry is ignored rather than trusted.
const MAX_AGE_MS = 10 * 60 * 1000;

const storage = () => {
  try {
    return window.sessionStorage;
  } catch {
    return null;
  }
};

// Indirection so tests can observe the redirect (jsdom cannot navigate).
export const browser = {
  redirect: (url) => window.location.assign(url),
};

const remember_ = (fields) => {
  const store = storage();
  if (!store) throw new Error("Google sign-in needs browser storage, which is blocked in this window.");
  store.setItem(KEY, JSON.stringify({ ...fields, startedAt: Date.now() }));
};

export async function startGoogleSignIn({ remember = false, from = null } = {}) {
  const { data } = await api.post("/auth/google/start");
  remember_({
    mode: "sign-in",
    state: data.state,
    codeVerifier: data.code_verifier,
    remember: !!remember,
    // Same "go back where you were" request password sign-in honours (resolvePostLogin).
    from: from?.pathname ? { pathname: from.pathname, search: from.search || "" } : null,
  });
  browser.redirect(data.authorization_url);
}

// Where a finished link returns to. Only an in-app path is honoured ("/x", never "//host" or a
// scheme), so a stored value can never become an open redirect.
export const LINK_RETURN_DEFAULT = "/organization/settings?tab=security";
export const safeReturnPath = (path) =>
  typeof path === "string" && path.startsWith("/") && !path.startsWith("//") && !path.includes("\\")
    ? path
    : LINK_RETURN_DEFAULT;

// Link a Google account to the SIGNED-IN account (Settings > Security). The server requires a
// fresh password step-up for purpose "google_link": without `stepUpReference` it answers
// 403 STEP_UP_REQUIRED, which the caller turns into a password prompt and retries with the
// reference (see GoogleSignInSettings.jsx). Same storage and callback as sign-in; the
// callback tells the two apart by `mode`, and the server by the state's purpose.
export async function startGoogleLink({ stepUpReference = null, returnTo = LINK_RETURN_DEFAULT } = {}) {
  const { data } = await api.post(
    "/auth/google/link/start",
    null,
    stepUpReference ? { headers: { "X-Step-Up": stepUpReference } } : undefined,
  );
  remember_({
    mode: "link",
    state: data.state,
    codeVerifier: data.code_verifier,
    returnTo: safeReturnPath(returnTo),
  });
  browser.redirect(data.authorization_url);
}

// Pure read — safe during render (StrictMode may call it twice). Returns null for anything
// missing, malformed or older than the server's state lifetime.
export function peekPendingSignIn() {
  const store = storage();
  const raw = store?.getItem(KEY);
  if (!raw) return null;
  try {
    const pending = JSON.parse(raw);
    if (!pending?.state || !pending?.codeVerifier) return null;
    if (!pending.startedAt || Date.now() - pending.startedAt > MAX_AGE_MS) return null;
    return pending;
  } catch {
    return null;
  }
}

// Called once the callback has read the attempt, so a reload or back-navigation can never
// replay it.
export function clearPendingSignIn() {
  storage()?.removeItem(KEY);
}
