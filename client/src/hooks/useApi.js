import { useEffect, useRef, useState } from "react";

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
  const reload = () => {
    setLoading(true);
    setError(null);
    setTick((t) => t + 1);
  };

  return { data, loading, error, reload };
}
