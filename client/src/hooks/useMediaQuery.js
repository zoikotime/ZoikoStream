import { useCallback, useSyncExternalStore } from "react";

// Subscribe to a CSS media query from React.
//
// `useSyncExternalStore`, not `useState` + `useEffect`: matchMedia IS an external store, and
// mirroring it into state would be a setState inside an effect — which this repo treats as
// an error (react-hooks/set-state-in-effect) for the cascading-render reason. This shape
// also gets the first render right instead of painting the wrong branch and correcting it.
//
// Used where a layout difference has to change WHAT IS RENDERED rather than how it looks:
// a Tailwind `sm:` class can restyle a node, but it cannot move a popover out of the video
// element and into a portalled sheet, and rendering both would put two copies of the same
// controls in the accessibility tree.
const noop = () => () => {};

export default function useMediaQuery(query) {
  const subscribe = useCallback(
    (onChange) => {
      if (typeof window === "undefined" || !window.matchMedia) return noop();
      const mql = window.matchMedia(query);
      // addEventListener is the modern API; addListener is Safari < 14, which is still a
      // meaningful share of iOS devices watching a stream.
      if (mql.addEventListener) {
        mql.addEventListener("change", onChange);
        return () => mql.removeEventListener("change", onChange);
      }
      mql.addListener(onChange);
      return () => mql.removeListener(onChange);
    },
    [query]
  );

  const getSnapshot = useCallback(
    () => (typeof window !== "undefined" && window.matchMedia
      ? window.matchMedia(query).matches
      : false),
    [query]
  );

  // Server snapshot is always false: there is no viewport to measure, and guessing "mobile"
  // would hydrate into the wrong branch.
  return useSyncExternalStore(subscribe, getSnapshot, () => false);
}
