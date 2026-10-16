// The floating Guide launchers' safe area — ONE mechanism shared by both Guides (the public
// website Guide and the console Operator Guide), so neither can sit on top of page controls
// and the two can never occupy the same spot.
//
// A mounted launcher registers its element here. While at least one is registered, this module
// publishes on <html>:
//
//   --guide-safe-bottom  px from the viewport's bottom edge to the top of the launcher stack.
//                        Scroll containers reserve it at their end (index.css .zk-guide-safe),
//                        so the last row of a page — a table's "Next page", a form's Save —
//                        can always be scrolled clear of the launcher.
//   --guide-safe-right   px from the viewport's right edge to the stack's left edge, for
//                        anything docked bottom-right that has to step around it.
//   --guide-lift         px the launchers are raised by to clear bottom-docked chrome.
//   data-guide-yield     present while opted-in content is passing under a launcher (below).
//
// and on each launcher element `--guide-stack`: the offset that stacks it above any launcher
// registered with a lower `order`. Everything is removed when the last launcher goes, so each
// `var(--guide-*, 0px)` falls back to 0 and the page is exactly as it was.
//
// Controls that must never be covered opt in with the attribute `data-guide-avoid` (the host
// console's control deck: End Event, Emergency stop). Two cases:
//   * DOCKED — touching the bottom edge of the viewport and staying there (fixed, sticky, or in
//     a full-height layout that does not scroll): the launchers LIFT above it. A docked bar
//     reserves the whole bottom band, not just its own width: in a full-height console the band
//     is chrome all the way across (the producer console's side panel ends in its own composer).
//   * IN FLOW — the same deck scrolling with an ordinary page (phones, tablets): while it passes
//     under a launcher the launcher YIELDS (fades out and takes no pointer input) and comes back
//     once it has scrolled on. Lifting there would make the launcher ride up with the deck and
//     drop back, a jump on every scroll; yielding uses opacity only and moves nothing.
//
// Measured, not assumed: launcher geometry differs per breakpoint (16/20/24px insets, 44 vs
// 56px tall), so footprints come from each launcher's resolved `bottom`/`right` and its
// layout box (offset sizes, which ignore the hover/bounce transforms).
import { useLayoutEffect } from "react";

export const GUIDE_AVOID_ATTR = "data-guide-avoid";
const YIELD_ATTR = "data-guide-yield";
const VARS = { bottom: "--guide-safe-bottom", right: "--guide-safe-right", lift: "--guide-lift" };
const STACK_VAR = "--guide-stack";
const STACK_GAP = 12;        // between stacked launchers
const DOCK_TOLERANCE = 48;   // a docked bar may sit on a thin status strip, not only on the edge
const MAX_LIFT = 0.5;        // never lift past half the viewport, whatever is marked

const launchers = new Map(); // element -> { order, seq }
let seq = 0;
let frame = 0;
let resizeObserver = null;
let mutationObserver = null;
let watchingAvoid = false;

const root = () => document.documentElement;
const px = (n) => `${Math.max(0, Math.round(n))}px`;

function setVar(el, name, value) {
  if (el.style.getPropertyValue(name) !== value) el.style.setProperty(name, value);
}

function viewport() {
  const r = root();
  return {
    vw: r.clientWidth || window.innerWidth || 0,
    vh: r.clientHeight || window.innerHeight || 0,
  };
}

function pinned(el) {
  for (let n = el; n && n !== document.body; n = n.parentElement) {
    const pos = getComputedStyle(n).position;
    if (pos === "fixed" || pos === "sticky") return true;
  }
  return false;
}

function documentScrolls() {
  const s = document.scrollingElement || root();
  return s.scrollHeight > s.clientHeight + 1;
}

const overlaps = (a, b) => a.left < b.right && a.right > b.left && a.top < b.bottom && a.bottom > b.top;

function visible(el) {
  return el.isConnected && (el.offsetWidth > 0 || el.offsetHeight > 0);
}

function clear() {
  const r = root();
  Object.values(VARS).forEach((name) => r.style.removeProperty(name));
  r.removeAttribute(YIELD_ATTR);
}

// Recompute everything now. Exported for tests and for callers that just changed layout.
export function refreshGuideSafeArea() {
  if (frame) {
    cancelAnimationFrame(frame);
    frame = 0;
  }
  if (!launchers.size) {
    clear();
    return;
  }
  const r = root();
  const { vw, vh } = viewport();
  const scrolls = documentScrolls();

  // Docked chrome lifts the launchers; in-flow chrome is checked against them further down.
  let lift = 0;
  const avoid = [...document.querySelectorAll(`[${GUIDE_AVOID_ATTR}]`)];
  const inFlow = [];
  for (const el of avoid) {
    const box = el.getBoundingClientRect();
    if (box.width <= 0 || box.height <= 0 || box.top >= vh || box.bottom <= 0) continue;
    const docked = box.bottom >= vh - DOCK_TOLERANCE && (pinned(el) || !scrolls);
    if (docked) lift = Math.max(lift, Math.min(vh - box.top, vh * MAX_LIFT));
    else inFlow.push(box);
  }
  setVar(r, VARS.lift, px(lift));
  syncAvoidObservers(avoid);

  // Stack in order: the lowest order owns the corner, the next sits above it, and so on.
  const ordered = [...launchers.entries()].sort((a, b) => a[1].order - b[1].order || a[1].seq - b[1].seq);
  let stack = 0;
  let safeBottom = 0;
  let safeRight = 0;
  let yieldNow = false;
  for (const [el] of ordered) {
    setVar(el, STACK_VAR, px(stack));
    if (!visible(el)) continue;
    const cs = getComputedStyle(el);
    const bottom = parseFloat(cs.bottom) || 0;   // resolved px, lift + stack included
    const right = parseFloat(cs.right) || 0;
    stack += el.offsetHeight + STACK_GAP;
    safeBottom = Math.max(safeBottom, bottom + el.offsetHeight);
    safeRight = Math.max(safeRight, right + el.offsetWidth);
    if (inFlow.length) {
      const spot = { left: vw - right - el.offsetWidth, right: vw - right, top: vh - bottom - el.offsetHeight, bottom: vh - bottom };
      if (inFlow.some((box) => overlaps(box, spot))) yieldNow = true;
    }
  }
  setVar(r, VARS.bottom, px(Math.min(safeBottom, vh)));
  setVar(r, VARS.right, px(Math.min(safeRight, vw)));
  if (yieldNow !== r.hasAttribute(YIELD_ATTR)) r.toggleAttribute(YIELD_ATTR, yieldNow);
}

function schedule() {
  if (frame || !launchers.size) return;
  frame = requestAnimationFrame(() => {
    frame = 0;
    refreshGuideSafeArea();
  });
}

// Scrolling only matters while opted-in chrome is on the page (it may pass under a launcher).
function onScroll() {
  if (watchingAvoid) schedule();
}

const observedAvoid = new Set();
function syncAvoidObservers(list) {
  watchingAvoid = list.length > 0;
  if (!resizeObserver) return;
  const now = new Set(list);
  observedAvoid.forEach((el) => {
    if (!now.has(el)) {
      resizeObserver.unobserve(el);
      observedAvoid.delete(el);
    }
  });
  now.forEach((el) => {
    if (!observedAvoid.has(el)) {
      resizeObserver.observe(el);
      observedAvoid.add(el);
    }
  });
}

const touchesAvoid = (node) =>
  node.nodeType === 1 && (node.hasAttribute(GUIDE_AVOID_ATTR) || !!node.querySelector(`[${GUIDE_AVOID_ATTR}]`));

function onMutations(records) {
  // While opted-in chrome is on the page any DOM change can move it (a status strip appearing
  // under it), so every change is worth one throttled look. Otherwise only the arrival of a
  // marked element matters.
  if (watchingAvoid) return schedule();
  for (const rec of records) {
    if (rec.type === "attributes" || [...rec.addedNodes].some(touchesAvoid)) return schedule();
  }
  return undefined;
}

function start() {
  if (typeof ResizeObserver === "function") resizeObserver = new ResizeObserver(schedule);
  if (typeof MutationObserver === "function" && document.body) {
    mutationObserver = new MutationObserver(onMutations);
    mutationObserver.observe(document.body, {
      childList: true, subtree: true, attributes: true, attributeFilter: [GUIDE_AVOID_ATTR],
    });
  }
  window.addEventListener("resize", schedule);
  window.addEventListener("orientationchange", schedule);
  document.addEventListener("scroll", onScroll, { capture: true, passive: true });
}

function stop() {
  resizeObserver?.disconnect();
  mutationObserver?.disconnect();
  resizeObserver = null;
  mutationObserver = null;
  observedAvoid.clear();
  watchingAvoid = false;
  window.removeEventListener("resize", schedule);
  window.removeEventListener("orientationchange", schedule);
  document.removeEventListener("scroll", onScroll, { capture: true });
  if (frame) cancelAnimationFrame(frame);
  frame = 0;
  clear();
}

// Register a floating launcher. Lower `order` takes the corner; higher ones stack above it.
// Returns the unregister function.
export function registerGuideLauncher(el, { order = 0 } = {}) {
  if (!el || typeof document === "undefined") return () => {};
  if (!launchers.size) start();
  seq += 1;
  launchers.set(el, { order, seq });
  resizeObserver?.observe(el);
  refreshGuideSafeArea();
  return () => {
    if (!launchers.delete(el)) return;
    resizeObserver?.unobserve(el);
    el.style.removeProperty(STACK_VAR);
    if (!launchers.size) stop();
    else refreshGuideSafeArea();
  };
}

// React binding: registers `ref.current` while `enabled`, before paint (so a lifted launcher
// never flashes at its unlifted position).
export function useGuideLauncher(ref, { order = 0, enabled = true } = {}) {
  useLayoutEffect(() => {
    if (!enabled || !ref.current) return undefined;
    return registerGuideLauncher(ref.current, { order });
  }, [ref, order, enabled]);
}
