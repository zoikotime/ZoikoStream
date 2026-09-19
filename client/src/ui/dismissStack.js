// The stack of things that "dismiss" before anything else does.
//
// ── WHAT PROBLEM THIS SOLVES ─────────────────────────────────────────────────────────────
// A modal, a drawer and the mobile sidebar are all REACT STATE, not history entries. Nothing
// in the browser or in Android knows they are open. So the two gestures a user expects to
// close the topmost thing — Escape on a keyboard, Back on Android — have no idea there is a
// topmost thing, and do something else instead.
//
// On Android that is the worse of the two. Press Back with the sidebar open and, without
// this, the router navigates to the previous PAGE while the sidebar stays on screen: the
// content behind the menu silently changes and the menu the user was trying to close is
// still there. It reads as the back button being broken, and it is the single most common
// complaint about a WebView app.
//
// ── WHY A STACK AND NOT A BOOLEAN ────────────────────────────────────────────────────────
// Layers nest. A confirm dialog opened from inside a drawer must close the DIALOG and leave
// the drawer standing, because the drawer is the operator's context and losing it means
// finding the row again. A flag can only answer "is something open"; only a stack can answer
// "which one is on top".
//
// Every listener is on `document`, so stopPropagation cannot separate them. The stack can.
const stack = [];

/**
 * Register a dismissable layer. Call on open; call the returned `release` on close.
 *
 * Returns `{ isTop, release }` rather than a bare remover because the caller usually needs
 * both: `isTop` to decide whether a global key event belongs to it, `release` to leave.
 */
export function register(onDismiss) {
  const entry = { onDismiss };
  stack.push(entry);
  return {
    isTop: () => stack[stack.length - 1] === entry,
    release: () => {
      // Located by identity rather than popped, because effects do not always unwind in the
      // order they ran — a parent unmounting takes its children's cleanups with it, and
      // popping blindly would remove somebody else's entry and leave this one stuck.
      const i = stack.indexOf(entry);
      if (i !== -1) stack.splice(i, 1);
    },
  };
}

/**
 * Dismiss the topmost layer. Returns true if there was one.
 *
 * The return value is the point: it lets a Back handler ask "did I just consume this press?"
 * and fall through to navigation only when nothing was open.
 *
 * ── THE ENTRY IS REMOVED HERE, BEFORE THE HANDLER RUNS ───────────────────────────────────
 * It would be tidier to leave removal to the component, whose effect cleanup calls `release`
 * when its `open` prop goes false, and that does happen a moment later. Relying on it is the
 * problem: if a handler throws, or a component is held open by a guard, or state simply does
 * not settle, the entry stays on top and EVERY subsequent Back press is swallowed by a layer
 * that is no longer on screen. The back button dies for the rest of the session, which is a
 * far worse failure than dismissing one layer too eagerly.
 *
 * Popping first makes each press strictly forward-moving: it either dismisses one layer or
 * falls through to navigation, and cannot do neither. The component's own `release` is
 * idempotent, so the ordinary path — pop here, release on cleanup — is harmless.
 */
export function dismissTop() {
  const top = stack.pop();
  if (!top) return false;
  top.onDismiss?.();
  return true;
}
