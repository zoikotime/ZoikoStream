import { useCallback, useEffect, useRef, useState } from "react";

// Fetch-on-mount hook: standardizes the { data, loading, error, reload } lifecycle
// the org pages were missing. Pass a thunk that returns the parsed data.
//
//   const { data, loading, error, reload } = useApi(() => api.get("/events").then((r) => r.data));
//
// List screens fetch once and filter/sort/paginate client-side (see admin/DataTable),
// so there's no deps array — call reload() after a mutation to refresh.
// ponytail: this is the whole data layer for GET screens — no react-query until a
// screen measurably needs caching/dedupe.
export default function useApi(fn) {
  const fnRef = useRef(fn);
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [tick, setTick] = useState(0);

  // Keep latest fn without re-triggering the fetch effect (which runs on [tick] only).
  useEffect(() => {
    fnRef.current = fn;
  });

  // Whether a request is outstanding, and whether a reload arrived while one was. Refs, not
  // state: `reload` has to read and write them synchronously within one event, and a state
  // update would not be visible until the next render.
  const inFlight = useRef(false);
  const pending = useRef(false);

  useEffect(() => {
    let alive = true;
    inFlight.current = true;
    fnRef
      .current()
      .then((d) => {
        if (alive) {
          setData(d);
          setError(null);
        }
      })
      .catch((e) => alive && setError(e))
      .finally(() => {
        inFlight.current = false;
        if (!alive) return;
        // A reload asked for during this request: serve it now rather than clearing
        // `loading`, so the pending refresh is the thing that finally clears it.
        if (pending.current) {
          pending.current = false;
          setTick((t) => t + 1);
          return;
        }
        setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [tick]);

  // reload() runs from event handlers, so setting state here is allowed.
  //
  // useCallback with no dependencies because it closes over nothing but the three state
  // setters, whose identities React guarantees are stable. That matters to callers, not to
  // this hook: as a fresh arrow per render it could not honestly be listed in a dependency
  // array — an effect that re-fetches when a filter changes had to omit it, or loop for ever
  // (see pages/admin/Users.jsx). A stable identity lets that effect declare what it uses.
  //
  // THE STUCK-SKELETON BUG: bumping `tick` while a request was already outstanding started a
  // second one, and the FIRST to settle cleared `loading` — so a page that polls, or one that
  // reloads on a filter change, could clear its skeleton on a stale answer and then be
  // overwritten by the newer one. Worse, the superseded effect's cleanup set `alive = false`,
  // so when the older request lost the race nothing ever cleared `loading` at all and the
  // skeleton stayed up for good. A reload during a request is now remembered and run when
  // that request finishes, so exactly one request is ever in flight and the last one to
  // finish is the one that clears the flag.
  const reload = useCallback(() => {
    setLoading(true);
    setError(null);
    if (inFlight.current) {
      pending.current = true;
      return;
    }
    setTick((t) => t + 1);
  }, []);

  return { data, loading, error, reload };
}
