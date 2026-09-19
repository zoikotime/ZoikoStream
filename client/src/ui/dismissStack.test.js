// The layer stack that Escape and Android's Back both dismiss through.
//
// Worth testing on its own rather than only through the components that use it: the ordering
// rules here are what stop a confirm dialog taking its parent drawer down with it, and that
// is a behaviour nobody notices until it is wrong, at which point it costs the operator the
// row they were working on.
import { beforeEach, describe, expect, it, vi } from "vitest";

import { dismissTop, register } from "./dismissStack";

// The stack is module state, so anything a test leaves behind is visible to the next one.
// Drained rather than reset, because there is deliberately no exported way to clear it — a
// test that needs one is describing a leak.
beforeEach(() => {
  while (dismissTop()) { /* drain */ }
});

describe("dismissTop", () => {
  it("reports that there was nothing to dismiss", () => {
    // The return value is the whole contract for a Back handler: false means "I did not
    // consume this press", which is what lets it fall through to navigation.
    expect(dismissTop()).toBe(false);
  });

  it("dismisses the innermost layer and leaves the one underneath standing", () => {
    const drawer = vi.fn();
    const dialog = vi.fn();
    register(drawer);
    register(dialog);

    expect(dismissTop()).toBe(true);

    // The case this exists for: a confirm dialog opened from inside a drawer closes the
    // dialog only. Taking the drawer with it loses the operator's context.
    expect(dialog).toHaveBeenCalledTimes(1);
    expect(drawer).not.toHaveBeenCalled();
  });

  it("reaches the outer layer once the inner one is gone", () => {
    const drawer = vi.fn();
    const dialog = vi.fn();
    register(drawer);
    const inner = register(dialog);

    inner.release();

    expect(dismissTop()).toBe(true);
    expect(drawer).toHaveBeenCalledTimes(1);
    expect(dialog).not.toHaveBeenCalled();
  });
});

describe("release", () => {
  it("leaves no entry behind for a layer that has closed", () => {
    // REGRESSION. Overlay used to push on open and never remove on close, so the array grew
    // for the life of the tab and — the visible half — a closed dialog stayed on top of the
    // stack. Escape then stopped closing the drawer beneath it, because the top belonged to
    // something no longer on screen.
    const closed = vi.fn();
    const layer = register(closed);
    layer.release();

    expect(dismissTop()).toBe(false);
    expect(closed).not.toHaveBeenCalled();
  });

  it("removes the right entry when layers close out of order", () => {
    // Effects do not always unwind in the order they ran: a parent unmounting takes its
    // children's cleanups with it. A stack that popped blindly would remove somebody else's
    // entry here and strand this one forever.
    const a = vi.fn();
    const b = vi.fn();
    const c = vi.fn();
    const first = register(a);
    register(b);
    register(c);

    first.release();

    expect(dismissTop()).toBe(true);
    expect(c).toHaveBeenCalledTimes(1);
    expect(dismissTop()).toBe(true);
    expect(b).toHaveBeenCalledTimes(1);
    expect(dismissTop()).toBe(false);
    expect(a).not.toHaveBeenCalled();
  });

  it("is safe to call twice", () => {
    // An unmount after an explicit close is ordinary, not exceptional.
    const other = vi.fn();
    register(other);
    const layer = register(vi.fn());

    layer.release();
    layer.release();

    expect(dismissTop()).toBe(true);
    expect(other).toHaveBeenCalledTimes(1);
  });
});

describe("isTop", () => {
  it("is true only for the innermost layer", () => {
    const outer = register(vi.fn());
    const inner = register(vi.fn());

    expect(inner.isTop()).toBe(true);
    expect(outer.isTop()).toBe(false);

    inner.release();

    expect(outer.isTop()).toBe(true);
  });
});
