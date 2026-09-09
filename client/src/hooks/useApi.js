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

  useEffect(() => {
    let alive = true;
    fnRef
      .current()
      .then((d) => {
        if (alive) {
          setData(d);
          setError(null);
        }
      })
      .catch((e) => alive && setError(e))
      .finally(() => alive && setLoading(false));
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
  const reload = useCallback(() => {
    setLoading(true);
    setError(null);
    setTick((t) => t + 1);
  }, []);

  return { data, loading, error, reload };
}
