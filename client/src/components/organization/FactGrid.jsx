import { CONSOLE, cx } from "../../ui/tokens";

// A row of label-over-value figures.
//
// The counterpart to components/admin/StatRow, which puts the label left and the value hard
// right. That is the correct shape inside a 320px rail; in a full-width section it strands
// the two ends of every fact ~1200px apart and the reader has to track across the page to
// pair them up. Same information, arranged for the width it now has.
//
// The em-dash convention is carried over unchanged and is the point of this component:
// `value == null` renders a faint "—" with `reason` on hover — never a 0, which would read
// as a measured clean result rather than as an absent measurement.
//
//   <FactGrid facts={[{ label: "Unique attendees", value: 12 },
//                     { label: "Blocked joins", value: null, reason: "Not recorded" }]} />
const COLUMNS = {
  2: "sm:grid-cols-2",
  3: "sm:grid-cols-2 lg:grid-cols-3",
  4: "sm:grid-cols-2 lg:grid-cols-4",
};

export default function FactGrid({ facts = [], columns = 4, className = "" }) {
  return (
    <dl className={cx("grid grid-cols-1 gap-x-6 gap-y-5", COLUMNS[columns] || COLUMNS[4], className)}>
      {facts.map(({ label, value, reason, tone }) => {
        const missing = value == null;
        return (
          <div key={label} className="min-w-0">
            <dt className={cx("truncate text-[13px]", CONSOLE.muted)}>{label}</dt>
            <dd
              className={cx(
                "mt-1 truncate text-[20px] font-semibold tabular-nums",
                missing ? CONSOLE.faint : tone || CONSOLE.heading
              )}
              // The same hover explanation StatRow gives, so moving a figure between the two
              // shapes never loses the reason it is blank.
              title={missing ? reason : undefined}
            >
              {missing ? "—" : value}
            </dd>
          </div>
        );
      })}
    </dl>
  );
}
