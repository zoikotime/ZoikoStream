import { CONSOLE, cx } from "../../ui/tokens";

// A row of label-over-value figures.
//
// The counterpart to components/admin/StatRow, which puts the label left and the value hard
// right. That is the correct shape inside a 320px rail; in a full-width section it strands
// the two ends of every fact ~1200px apart and the reader has to track across the page to
// pair them up. Same information, arranged for the width it now has.
//
// The em-dash convention is carried over unchanged and is the point of this component:
// `value == null` renders a faint "—" — never a 0, which would read as a measured clean
// result rather than as an absent measurement.
//
// THE REASON IS NOW PRINTED, not just hovered. It was carried solely in `title`, so an
// absent figure reached the reader as a bare dash: the Audience page's "Not measured" panel
// knows exactly why each of its three figures is blank ("Per-viewer location is not
// collected"), and the attendance panel knows that a blank show-rate means "No private-event
// registrations in this window" — none of which was visible. A tooltip is unreachable on a
// touch device and undiscoverable on a desktop, so the page looked broken while it was
// actually being precise. `title` is kept as well, because `truncate` can still clip a long
// reason on a narrow column.
//
// A dash with its reason beside it is also what separates the three states the console has to
// keep apart: a measured 0, a figure that is not collected at all, and one that is collected
// but does not apply to this window.
//
//   <FactGrid facts={[{ label: "Unique attendees", value: 12 },
//                     { label: "Blocked joins", value: null, reason: "Not recorded" }]} />
//
// An optional `detail` is a short line under the value saying what it is made of
// ("3 of 12 registered viewers attended").
//
// A fact whose answer is not one figure (a breakdown, a list) passes `content` instead of
// `value`: it renders under the same label, in place of the figure.
const COLUMNS = {
  2: "sm:grid-cols-2",
  3: "sm:grid-cols-2 lg:grid-cols-3",
  4: "sm:grid-cols-2 lg:grid-cols-4",
};

export default function FactGrid({ facts = [], columns = 4, className = "" }) {
  return (
    <dl className={cx("grid grid-cols-1 gap-x-6 gap-y-5", COLUMNS[columns] || COLUMNS[4], className)}>
      {facts.map(({ label, value, reason, tone, detail, content }) => {
        const missing = value == null;
        if (content !== undefined) {
          return (
            <div key={label} className="min-w-0">
              <dt className={cx("truncate text-[13px]", CONSOLE.muted)}>{label}</dt>
              <dd className="mt-1 min-w-0">{content}</dd>
            </div>
          );
        }
        return (
          <div key={label} className="min-w-0">
            <dt className={cx("truncate text-[13px]", CONSOLE.muted)}>{label}</dt>
            <dd
              className={cx(
                "mt-1 truncate text-[20px] font-semibold tabular-nums",
                missing ? CONSOLE.faint : tone || CONSOLE.heading
              )}
              // Retained alongside the printed reason below: a long reason can still be
              // clipped in a narrow column, and this is the same hover explanation StatRow
              // gives, so moving a figure between the two shapes loses nothing.
              title={missing ? reason : undefined}
            >
              {missing ? "—" : value}
            </dd>
            {detail && <dd className={cx("mt-0.5 text-[12px]", CONSOLE.faint)}>{detail}</dd>}
            {missing && reason && (
              <p className={cx("mt-0.5 text-[11px] leading-snug", CONSOLE.faint)}>{reason}</p>
            )}
          </div>
        );
      })}
    </dl>
  );
}
