// client/src/components/watch/AudioAssistButton.jsx
// ZST-SPEC-VAP-001 §6.4: "A persistent speaker control reads the current instruction aloud
// after tap." The browser's own speech synthesis (Web Speech API), so nothing leaves the
// device, no service is added, and no audio plays until the viewer asks for it.
//
// It speaks `text` in `lang`, which comes from the same string table the screen shows, so
// what is heard always matches what is written, in every supported language. If the browser
// has no speech synthesis, the control is not shown at all. A button that cannot work is not
// offered.
import { useCallback, useEffect, useState } from "react";
import { FiVolume2, FiSquare } from "react-icons/fi";
import { cx } from "../../ui/tokens";

const supported = () => typeof window !== "undefined" && "speechSynthesis" in window
  && typeof window.SpeechSynthesisUtterance === "function";

function pickVoice(lang) {
  try {
    const voices = window.speechSynthesis.getVoices() || [];
    return voices.find((v) => v.lang?.toLowerCase() === lang)
      || voices.find((v) => v.lang?.toLowerCase().startsWith(`${lang}-`))
      || null;
  } catch {
    return null;
  }
}

export default function AudioAssistButton({ text, lang = "en", label, stopLabel, className }) {
  // WHAT is being spoken, not just whether: a new instruction or a new language is a new
  // thing to read, so the control is back to "Read aloud" for it rather than stuck on "Stop".
  const key = `${lang}|${text}`;
  const [speakingKey, setSpeakingKey] = useState(null);
  const speaking = speakingKey === key;
  const setSpeaking = useCallback((on) => setSpeakingKey(on ? key : null), [key]);

  const stop = useCallback(() => {
    try {
      window.speechSynthesis.cancel();
    } catch { /* nothing was speaking */ }
    setSpeaking(false);
  }, [setSpeaking]);

  // A new instruction (the state changed) or a new language makes the old speech stale.
  // Stopping it when that happens is the cleanup of an external system, which is
  // what an effect is for; no state is set here.
  useEffect(() => () => {
    try {
      window.speechSynthesis.cancel();
    } catch { /* nothing was speaking */ }
  }, [text, lang]);

  if (!supported() || !text) return null;

  const speak = () => {
    if (speaking) {
      stop();
      return;
    }
    try {
      window.speechSynthesis.cancel();
      const utterance = new window.SpeechSynthesisUtterance(text);
      utterance.lang = lang;
      const voice = pickVoice(lang);
      if (voice) utterance.voice = voice;
      utterance.onend = () => setSpeaking(false);
      utterance.onerror = () => setSpeaking(false);
      window.speechSynthesis.speak(utterance);
      setSpeaking(true);
    } catch {
      setSpeaking(false);
    }
  };

  return (
    <button
      type="button"
      onClick={speak}
      aria-pressed={speaking}
      aria-label={speaking ? stopLabel : label}
      title={speaking ? stopLabel : label}
      data-testid="audio-assist"
      className={cx(
        "inline-flex min-h-12 min-w-12 items-center justify-center gap-2 rounded-xl px-3 text-sm font-semibold",
        "text-slate-700 ring-1 ring-slate-200 transition hover:bg-slate-100 dark:text-slate-200 dark:ring-white/15 dark:hover:bg-white/10",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-500",
        className
      )}
    >
      {speaking ? <FiSquare aria-hidden="true" className="text-base" /> : <FiVolume2 aria-hidden="true" className="text-lg" />}
      <span className="hidden sm:inline">{speaking ? stopLabel : label}</span>
    </button>
  );
}
