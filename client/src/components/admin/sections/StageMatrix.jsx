import { useState } from "react";
import { CONSOLE, cx, heatClass, type } from "../../../ui/tokens";
import Panel from "../Panel";

// Availability by lifecycle stage × delivery region. Each cell is measured: 100 minus the
// share of the window covered by a recorded incident touching that stage in that region
// (overlapping incidents are unioned server-side, so a cell can never read below 0).
//
// A region with no delivery footprint reads "n/a" rather than 100% — "we have no data" and
// "everything was fine" are different facts and the console must not conflate them.
export default function StageMatrix({ stages = [], regions = [] }) {
  const [table, setTable] = useState(false);

  if (!stages.length) return null;

  return (
    <Panel
      title="Service health by lifecycle stage"
      action={
        <button onClick={() => setTable((v) => !v)} className={cx("text-[12px] font-semibold", CONSOLE.link)}>
          {table ? "Matrix view" : "Table view"}
        </button>
      }
      flush
    >
      <div className="overflow-x-auto px-4 pb-4 pt-1 sm:px-5">
        {table ? (
          <table className="w-full min-w-[22rem] text-left">
            <thead>
              <tr className={cx("border-b", CONSOLE.divider)}>
                {["Stage", "Status", "Worst region", "Open"].map((h) => (
                  <th key={h} scope="col" className={cx("py-2 pr-3 text-[10px] font-semibold uppercase tracking-[0.1em]", CONSOLE.faint)}>
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className={cx("divide-y", CONSOLE.divideY)}>
              {stages.map((s) => (
                <tr key={s.stage}>
                  <td className={cx("py-2.5 pr-3 text-[13px] font-medium", CONSOLE.heading)}>{s.label}</td>
                  <td className={cx("py-2.5 pr-3 text-[12px] capitalize", CONSOLE.body)}>
                    {s.status.replace("_", " ")}
                  </td>
                  <td className={cx("py-2.5 pr-3 text-[12px]", type.mono, CONSOLE.body)}>
                    {s.availability == null ? "—" : `${s.availability.toFixed(2)}%`}
                  </td>
                  <td className={cx("py-2.5 text-[12px]", type.mono, s.open_incidents ? "text-rose-500" : CONSOLE.faint)}>
                    {s.open_incidents || 0}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <table className="w-full min-w-[24rem] border-separate border-spacing-y-1.5 text-left">
            <thead>
              <tr>
                <th scope="col" className="w-[34%]" />
                {regions.map((r) => (
                  <th
                    key={r.code}
                    scope="col"
                    className={cx("pb-1 text-center text-[10px] font-semibold uppercase tracking-[0.1em]", CONSOLE.faint)}
                  >
                    {r.label}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {stages.map((s) => (
                <tr key={s.stage}>
                  <th
                    scope="row"
                    className={cx("py-1 pr-3 text-left text-[13px] font-medium", CONSOLE.heading)}
                  >
                    {s.label}
                  </th>
                  {regions.map((r) => {
                    const pct = s.regions?.[r.code];
                    return (
                      <td key={r.code} className="px-0.5">
                        <span
                          className={cx(
                            "block rounded-md py-1.5 text-center text-[12px] font-semibold tabular-nums",
                            heatClass(pct)
                          )}
                          title={`${s.label} · ${r.label}`}
                        >
                          {pct == null ? "n/a" : pct.toFixed(1)}
                        </span>
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </Panel>
  );
}
