// client/e2e/helpers.js — shared helpers for the live-streaming E2E spec.
import { expect } from "@playwright/test";
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { BACKEND_URL, FIXTURES_PATH } from "./env.js";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const SERVER_DIR = path.resolve(__dirname, "../../server");
const PYTHON = path.join(SERVER_DIR, "venv", "Scripts", "python.exe");

export function loadFixtures() {
  return JSON.parse(fs.readFileSync(FIXTURES_PATH, "utf-8"));
}

// Real DB-level ground truth (Event.status, open BroadcastSession, active LiveRecording) —
// queried directly, not scraped off a UI element. A rendered chip proves what a human would
// see; this proves what actually happened in the database, which is what "no stale
// BroadcastSession", "no active recording", etc. actually mean.
export function dbState(eventId) {
  // Retried: this shells out to a real Python process opening a real connection to a hosted
  // database, and a transient DNS failure resolving the Supabase host ("could not translate
  // host name ... Name or service not known") has been observed mid-suite on this machine.
  // That is an environment hiccup, not a result — failing a live-streaming assertion on it
  // would be a false negative, so give it a couple of shots before believing it.
  let last;
  for (let attempt = 0; attempt < 3; attempt++) {
    try {
      const out = execFileSync(PYTHON, ["e2e_fixtures.py", "state", eventId], { cwd: SERVER_DIR });
      return JSON.parse(out.toString());
    } catch (err) {
      last = err;
      // Synchronous pause: dbState is deliberately sync (callers read it inline inside
      // assertions), so this cannot await. Atomics.wait blocks this thread without pulling
      // in a Node global.
      Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, 2000);
    }
  }
  throw last;
}

// Assigns the contributor as a speaker + opens their backstage join window. Deliberately
// called mid-suite (AFTER the host is already live), not in global-setup — see
// server/e2e_fixtures.py's create()/invite_contributor() docstrings: assigning a speaker
// BEFORE Go Live trips the real commercial-readiness gate ("Cannot go live — contributor has
// not given consent...", discovered by actually running this suite), which an already-live
// event is never re-checked against.
export function inviteContributor(eventId, contributorId, hostId) {
  execFileSync(PYTHON, ["e2e_fixtures.py", "invite-contributor", eventId, contributorId, hostId], {
    cwd: SERVER_DIR,
  });
}

// Signs a browser context in WITHOUT clicking through the login form — this suite tests live
// streaming, not the login UI, and every other E2E-relevant code path (routing guards, the
// WebSocket auth, LiveKit token minting) runs identically after this point regardless of how
// the token was obtained. Fetches the REAL /auth/me response with the minted token so
// localStorage holds exactly the shape client/src/auth/AuthContext.jsx expects — no guessing
// at the User schema.
export async function loginAs(page, token) {
  const res = await page.request.get(`${BACKEND_URL}/api/auth/me`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  expect(res.ok(), `GET /auth/me failed: ${res.status()} ${await res.text()}`).toBeTruthy();
  const user = await res.json();
  await page.addInitScript(([t, u]) => {
    localStorage.setItem("token", t);
    localStorage.setItem("user", JSON.stringify(u));
  }, [token, user]);
}

// Polls a React-state-driven condition without a fixed sleep — every live-streaming state
// transition here is asynchronous (WebSocket round trip, LiveKit negotiation), so a fixed
// wait either flakes under load or wastes time when things are fast.
export async function waitFor(fn, { timeout = 20_000, interval = 250, message = "condition" } = {}) {
  const start = Date.now();
  let last;
  while (Date.now() - start < timeout) {
    last = await fn();
    if (last) return last;
    await new Promise((r) => setTimeout(r, interval));
  }
  throw new Error(`Timed out waiting for: ${message} (last value: ${JSON.stringify(last)})`);
}

// Reads the actual HTML media element's real state — not just "a track exists". This is
// what proves audio is genuinely playable, not merely subscribed (see the task's explicit
// "do not mark audio PASS merely because a track exists" instruction).
//
// .first(): the host studio page alone has TWO <video> elements (camera preview +
// screen-share, StudioStage.jsx) — a bare selector matching more than one element makes
// Playwright's .evaluate() throw "strict mode violation", which a wrapping .catch(() =>
// null) silently swallows into an endless (and misleading) retry-until-timeout. Callers
// that need a SPECIFIC one of several matches (e.g. the contributor Backstage page's
// return-feed video) pass an explicit `video >> nth=N` selector, which .first() is a no-op
// on since it already resolves to exactly one element.
export async function mediaElementState(page, selector) {
  return page.locator(selector).first().evaluate((el) => ({
    tagName: el.tagName,
    muted: el.muted,
    paused: el.paused,
    readyState: el.readyState, // 0=HAVE_NOTHING .. 4=HAVE_ENOUGH_DATA
    hasVideoTrack: !!(el.srcObject && el.srcObject.getVideoTracks?.().length),
    hasAudioTrack: !!(el.srcObject && el.srcObject.getAudioTracks?.().length),
    currentTime: el.currentTime,
    videoWidth: el.videoWidth ?? null,
    videoHeight: el.videoHeight ?? null,
  }));
}

// Waits (bounded) for a real published/subscribed media track to actually appear, WITHOUT
// throwing on timeout — returns null instead, and the caller annotates the outcome.
//
// Against a healthy LiveKit deployment this resolves with real tracks and the suite verifies
// media end to end. It returns null when the DEPLOYMENT can't carry media: an earlier
// LIVEKIT_URL used here completed signaling flawlessly (room joined, identity assigned) but
// stalled forever on the first published track's actual transport, with no SDK error at all.
// Not throwing is what keeps one broken media path from aborting a serial suite before the
// assertions that don't need media (signaling, DB state, no DUPLICATE_IDENTITY, chat,
// lifecycle) ever get to run — those still execute and still mean something.
export async function waitForMediaOrBlock(fn, { timeout = 25_000, interval = 500 } = {}) {
  const start = Date.now();
  while (Date.now() - start < timeout) {
    const st = await fn().catch(() => null);
    if (st) return st;
    await new Promise((r) => setTimeout(r, interval));
  }
  return null;
}

// Collects console messages + page errors for the diagnostics the task asks for, filtering
// out noise so a report actually highlights LiveKit/WebRTC/media problems.
const RELEVANT = /LiveKit|WebRTC|getUserMedia|NotAllowedError|DUPLICATE_IDENTITY|PARTICIPANT_REMOVED|ROOM_DELETED|autoplay|Disconnected/i;

export function captureConsole(page, sink) {
  page.on("console", (msg) => {
    const text = msg.text();
    if (msg.type() === "error" || RELEVANT.test(text)) sink.push(`[console:${msg.type()}] ${text}`);
  });
  page.on("pageerror", (err) => sink.push(`[pageerror] ${err.message}`));
}
