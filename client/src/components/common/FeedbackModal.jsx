// client/src/components/common/FeedbackModal.jsx
// Shared feedback prompt for the two moments an attendee leaves a live event:
//   - the host, right after confirming "End Event" (pages/host/Dashboard.jsx)
//   - a viewer, right after clicking "Leave Event" (pages/watch/EventWatch.jsx)
//
// Deliberately dumb: it only asks a star rating + optional comment and always calls
// onDone() when it's finished, whether the submission succeeded, failed, or was
// skipped — so it never blocks the exit flow it's attached to. `onSubmit` is expected
// to be fire-and-forget (a socket `send`, not an awaited network call) since the
// underlying connection is about to be torn down anyway.
import { useState } from "react";
import { FiStar } from "react-icons/fi";
import Modal from "../../ui/Modal";
import { cx } from "../../ui/tokens";

export default function FeedbackModal({ open, role = "viewer", onSubmit, onDone }) {
  const [rating, setRating] = useState(0);
  const [hover, setHover] = useState(0);
  const [comment, setComment] = useState("");

  const title = role === "host" ? "How did the event go?" : "Thanks for watching!";
  const subtitle =
    role === "host"
      ? "Your feedback helps improve future broadcasts."
      : "Let us know what you thought of this event.";

  const reset = () => {
    setRating(0);
    setHover(0);
    setComment("");
  };

  const finish = () => {
    reset();
    onDone?.();
  };

  const handleSubmit = () => {
    if (rating > 0 || comment.trim()) {
      onSubmit?.({ rating: rating || null, comment: comment.trim() || null });
    }
    finish();
  };

  return (
    <Modal open={open} onClose={finish} title={title} size="sm">
      <div className="space-y-4">
        <p className="text-sm text-slate-500 dark:text-slate-400">{subtitle}</p>

        <div className="flex items-center justify-center gap-1.5 py-2">
          {[1, 2, 3, 4, 5].map((n) => (
            <button
              key={n}
              type="button"
              aria-label={`${n} star${n > 1 ? "s" : ""}`}
              onClick={() => setRating(n)}
              onMouseEnter={() => setHover(n)}
              onMouseLeave={() => setHover(0)}
              className="p-1 transition active:scale-90"
            >
              <FiStar
                className={cx(
                  "h-7 w-7 transition-colors",
                  (hover || rating) >= n
                    ? "fill-amber-400 text-amber-400"
                    : "text-slate-300 dark:text-slate-600"
                )}
              />
            </button>
          ))}
        </div>

        <textarea
          value={comment}
          onChange={(e) => setComment(e.target.value)}
          placeholder="Any thoughts you'd like to share? (optional)"
          rows={3}
          className="w-full resize-none rounded-xl border border-slate-200 bg-white px-3 py-2 text-sm text-slate-800 placeholder:text-slate-400 focus:border-emerald-500 focus:outline-none focus:ring-1 focus:ring-emerald-500 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-100"
        />

        <div className="flex justify-end gap-2 pt-1">
          <button
            type="button"
            onClick={finish}
            className="rounded-xl px-4 py-2 text-sm font-medium text-slate-500 transition hover:bg-slate-100 hover:text-slate-800 dark:text-slate-400 dark:hover:bg-white/10 dark:hover:text-white"
          >
            Skip
          </button>
          <button
            type="button"
            onClick={handleSubmit}
            className="rounded-xl bg-emerald-600 px-4 py-2 text-sm font-semibold text-white shadow-sm shadow-emerald-600/20 transition hover:bg-emerald-500"
          >
            Submit
          </button>
        </div>
      </div>
    </Modal>
  );
}
