// Nested Guide layers (header menu, expanded source detail). Back and Escape close the
// top-most nested layer first, then Guide itself (WF-18 A5, Figma §9.1-9.2, §10.1).
import { createContext, useContext, useEffect, useRef } from "react";

export const NestedLayers = createContext(null);

/** While `active`, register `close` as the top-most nested layer. */
export function useNestedLayer(active, close) {
  const layers = useContext(NestedLayers);
  const closeRef = useRef(close);
  useEffect(() => {
    closeRef.current = close;
  });
  useEffect(() => {
    if (!active || !layers) return undefined;
    const entry = { close: () => closeRef.current() };
    layers.push(entry);
    return () => layers.remove(entry);
  }, [active, layers]);
}

export function createLayerStack() {
  const stack = [];
  return {
    push: (entry) => stack.push(entry),
    remove: (entry) => {
      const i = stack.indexOf(entry);
      if (i >= 0) stack.splice(i, 1);
    },
    /** Close the top-most layer; false when there is none. */
    closeTop: () => {
      const top = stack.pop();
      if (!top) return false;
      top.close();
      return true;
    },
    get size() {
      return stack.length;
    },
  };
}
