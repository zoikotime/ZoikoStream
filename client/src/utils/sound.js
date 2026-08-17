// client/src/utils/sound.js
// A tiny notification chime, synthesized with the Web Audio API — no audio file to bundle
// or fetch, so it plays instantly and works offline. Used to alert the host/moderator
// console when a viewer raises a new Q&A question (see hooks/useLiveEvent.js), so a host
// mid-broadcast doesn't have to keep glancing at the Q&A tab to notice one came in.

let ctx = null;
// Browsers refuse to start an AudioContext until a user gesture has happened on the page
// (autoplay policy) — `new AudioContext()` itself is fine without one, but it comes up
// "suspended" and stays that way until something resumes it inside a gesture handler.
function getCtx() {
  if (ctx) return ctx;
  const AudioCtx = window.AudioContext || window.webkitAudioContext;
  if (!AudioCtx) return null;
  ctx = new AudioCtx();
  return ctx;
}

function scheduleChime(audioCtx) {
  // Two quick, gentle tones (not a harsh single beep) — enough to catch the host's ear
  // without being jarring if several questions arrive close together.
  const notes = [880, 1174.66]; // A5 then D6 — a short, friendly "ping-ping"
  const start = audioCtx.currentTime;
  notes.forEach((freq, i) => {
    const osc = audioCtx.createOscillator();
    const gain = audioCtx.createGain();
    osc.type = "sine";
    osc.frequency.value = freq;
    const t = start + i * 0.14;
    // Fast attack, gentle decay — a "ding", not a click or a drone.
    gain.gain.setValueAtTime(0, t);
    gain.gain.linearRampToValueAtTime(0.22, t + 0.02);
    gain.gain.exponentialRampToValueAtTime(0.0001, t + 0.32);
    osc.connect(gain);
    gain.connect(audioCtx.destination);
    osc.start(t);
    osc.stop(t + 0.34);
  });
}

// Try to bring a suspended context to "running". Called both eagerly (see unlockAudio
// below) and defensively inside playQuestionAlert itself, since a context can also drift
// back to "suspended" (some browsers do this on tab visibility changes).
//
// THE BUG THIS FIXES: `audioCtx.resume()` returns a Promise — it does not resume
// synchronously. The previous version called `resume()` and, without waiting for it,
// immediately scheduled the oscillators against `audioCtx.currentTime` on a context that
// was often STILL suspended at that exact instant. On a context that had never been
// resumed yet (the common case: a host who hasn't clicked anything since the page loaded
// when the first question comes in), those scheduled nodes silently never sounded in
// several browsers — the call didn't throw, so nothing looked wrong, the chime just never
// played. Awaiting the resume before scheduling anything is what actually fixes that.
function ensureRunning(audioCtx) {
  if (audioCtx.state === "running") return Promise.resolve(audioCtx);
  return audioCtx.resume().then(() => audioCtx).catch(() => audioCtx);
}

// Warms the AudioContext up on the very first interaction anywhere on the page, well
// before any question is likely to arrive — so by the time playQuestionAlert actually
// needs to make a sound, the context is already "running" instead of racing a resume().
// Safe to call multiple times; only ever attaches its listeners once.
let unlocked = false;
export function unlockAudio() {
  if (unlocked || typeof window === "undefined") return;
  unlocked = true;
  const tryUnlock = () => {
    const audioCtx = getCtx();
    if (audioCtx) ensureRunning(audioCtx);
  };
  ["pointerdown", "keydown", "touchstart"].forEach((evt) =>
    window.addEventListener(evt, tryUnlock, { once: true, passive: true })
  );
}

export function playQuestionAlert() {
  try {
    const audioCtx = getCtx();
    if (!audioCtx) return;
    ensureRunning(audioCtx).then((running) => {
      if (running.state === "running") scheduleChime(running);
    });
  } catch {
    // Never let a notification sound break the feature it's attached to.
  }
}
