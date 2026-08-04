// client/src/components/speaker/SpeakerNotes.jsx
// The speaker's private notes for this event.
//
// Stored on their own EventAssignment row (services/speaker._notes_save), so notes written at home
// are there in the console — and nowhere else. The save handler returns NO frames on purpose: the
// bus fans every returned frame to every subscriber, so echoing the text back would publish a
// speaker's prep to the whole room.
//
// Autosaved on a debounce rather than behind a Save button: nobody remembers to press save ninety
// seconds before going on air.
import { useEffect, useRef, useState } from "react";
import { FiCheck, FiEdit3, FiSave } from "react-icons/fi";
import Panel from "../moderation/Panel";

const SAVE_DEBOUNCE_MS = 1200;

export default function SpeakerNotes({ notes = "", canEdit, send, className }) {
  const [text, setText] = useState(notes);
  const [saved, setSaved] = useState(true);
  const timer = useRef(null);
  // The server copy only arrives once (in the snapshot), so adopt it when the identity of the
  // event changes rather than on every render — otherwise a reconnect mid-sentence would
  // overwrite what the speaker is typing.
  const seeded = useRef(notes);

  useEffect(() => {
    if (notes !== seeded.current) {
      seeded.current = notes;
      setText(notes);
      setSaved(true);
    }
  }, [notes]);

  useEffect(() => () => clearTimeout(timer.current), []);

  const onChange = (value) => {
    setText(value);
    setSaved(false);
    clearTimeout(timer.current);
    timer.current = setTimeout(() => {
      send("notes.save", { notes: value });
      setSaved(true);
    }, SAVE_DEBOUNCE_MS);
  };

  const saveNow = () => {
    clearTimeout(timer.current);
    send("notes.save", { notes: text });
    setSaved(true);
  };

  return (
    <Panel
      title="Speaker notes"
      scroll={false}
      className={className}
      badge={
        <span className="inline-flex items-center gap-1 text-[11px] text-slate-400">
          {saved ? <><FiCheck aria-hidden="true" /> saved</> : <><FiEdit3 aria-hidden="true" /> unsaved</>}
        </span>
      }
      action={!saved && canEdit && (
        <button
          type="button"
          onClick={saveNow}
          className="inline-flex items-center gap-1 rounded-lg border border-slate-200 px-2 py-1 text-xs font-medium text-slate-600 transition hover:bg-slate-50 dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-800"
        >
          <FiSave aria-hidden="true" /> Save now
        </button>
      )}
    >
      <textarea
        value={text}
        onChange={(e) => onChange(e.target.value)}
        readOnly={!canEdit}
        rows={12}
        aria-label="Your private speaker notes"
        placeholder={canEdit
          ? "Your talking points. Only you can see these — they're saved as you type."
          : "You aren't assigned to this event, so notes are read-only."}
        className="w-full resize-y rounded-xl border border-slate-200 bg-white p-3 text-sm leading-relaxed text-slate-800 placeholder:text-slate-400 focus:border-emerald-500 focus:outline-none focus:ring-1 focus:ring-emerald-500 read-only:bg-slate-50 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-100 dark:read-only:bg-slate-900"
      />
      <p className="mt-1 text-[11px] text-slate-400">
        Private to you. Press <kbd className="rounded bg-slate-100 px-1 dark:bg-slate-800">n</kbd> in
        presenter mode to read them beside your slide.
      </p>
    </Panel>
  );
}
