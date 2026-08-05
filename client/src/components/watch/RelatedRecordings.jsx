// client/src/components/watch/RelatedRecordings.jsx
// Bottom section — recordings surfaced under the player (after an event ends).
import { Link } from "react-router-dom";
import { FiPlay, FiEye, FiClock } from "react-icons/fi";
import { cx } from "../../ui/tokens";
import { relatedRecordings } from "../../data/watch";

const THUMB = {
  violet: "from-violet-600 to-slate-900",
  emerald: "from-emerald-600 to-slate-900",
  blue: "from-blue-600 to-slate-900",
  amber: "from-amber-500 to-slate-900",
  indigo: "from-indigo-600 to-slate-900",
  rose: "from-rose-600 to-slate-900",
};

export default function RelatedRecordings({ ended }) {
  return (
    <section>
      <div className="mb-4">
        <h2 className="text-lg font-semibold text-slate-900 dark:text-white">Related Recordings</h2>
        <p className="text-sm text-slate-500 dark:text-slate-400">
          {ended ? "Now that this event has wrapped, revisit these sessions." : "More sessions from this organization."}
        </p>
      </div>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {relatedRecordings.map((r) => (
          <Link
            key={r.id}
            to={`/events/${r.id}/watch`}
            className="group overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm transition hover:-translate-y-1 hover:shadow-lg dark:border-slate-800 dark:bg-slate-900 dark:hover:shadow-black/40"
          >
            <div className={cx("relative grid aspect-video place-items-center bg-gradient-to-br", THUMB[r.accent] || THUMB.emerald)}>
              <span className="grid h-12 w-12 place-items-center rounded-full bg-white/15 text-white backdrop-blur transition group-hover:scale-110 group-hover:bg-white/25">
                <FiPlay className="ml-0.5 text-xl" />
              </span>
              <span className="absolute bottom-2 right-2 rounded bg-black/60 px-1.5 py-0.5 text-[11px] font-medium text-white">
                {r.duration}
              </span>
            </div>
            <div className="p-4">
              <p className="truncate font-medium text-slate-800 group-hover:text-emerald-600 dark:text-slate-100 dark:group-hover:text-emerald-400">
                {r.title}
              </p>
              <div className="mt-1.5 flex items-center gap-3 text-xs text-slate-500 dark:text-slate-400">
                <span className="inline-flex items-center gap-1"><FiEye /> {r.views.toLocaleString()}</span>
                <span className="inline-flex items-center gap-1"><FiClock /> {r.date}</span>
              </div>
            </div>
          </Link>
        ))}
      </div>
    </section>
  );
}
