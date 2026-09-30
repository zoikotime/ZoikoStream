// useApi's loading lifecycle, and the overlap bug that left polling screens on skeletons.
//
// ── THE BUG ─────────────────────────────────────────────────────────────────────────────
// The fetch effect ran per `tick`, and its cleanup set `alive = false` so a superseded
// response could not overwrite a newer one. That guard also disabled the superseded
// request's `.finally()`, which is what cleared `loading` — so a superseded request could
// never clear it, and handed that job to its successor.
//
// Ten screens poll with `useInterval(reload, 20_000)`. Whenever a response takes longer than
// the poll interval, EVERY request is superseded before it settles, each deferring to a
// successor that is superseded in turn, and `loading` stays true for as long as the page is
// open. No error, no failed request, nothing in the console — just grey skeletons that never
// resolve, on a page that works fine whenever the backend happens to be quicker than the
// timer. That is the "sometimes it loads, sometimes it doesn't" these pin.
import { act, renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import useApi from "./useApi";

/** A fetch whose promises are resolved by the test, one per call, in order. */
function controllable() {
  const calls = [];
  const fn = vi.fn(() => {
    let settle;
    const promise = new Promise((resolve, reject) => {
      settle = { resolve, reject };
    });
    calls.push(settle);
    return promise;
  });
  return { fn, calls };
}

describe("the ordinary lifecycle", () => {
  it("starts loading and resolves on success", async () => {
    const { fn, calls } = controllable();
    const { result } = renderHook(() => useApi(fn));

    expect(result.current.loading).toBe(true);
    await act(async () => calls[0].resolve({ ok: true }));

    expect(result.current.loading).toBe(false);
    expect(result.current.data).toEqual({ ok: true });
    expect(result.current.error).toBeNull();
  });

  it("resolves on failure too, and reports the error", async () => {
    const { fn, calls } = controllable();
    const { result } = renderHook(() => useApi(fn));

    await act(async () => {
      calls[0].reject(new Error("boom"));
      // let the rejection settle without failing the test
      await Promise.resolve();
    });

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.error).toBeInstanceOf(Error);
  });

  it("treats an empty response as data, not as still-loading", async () => {
    const { fn, calls } = controllable();
    const { result } = renderHook(() => useApi(fn));

    await act(async () => calls[0].resolve({ items: [] }));

    expect(result.current.loading).toBe(false);
    expect(result.current.data).toEqual({ items: [] });
  });
});

describe("overlapping reloads — the stuck-skeleton bug", () => {
  it("clears loading even when a reload arrives while a request is in flight", async () => {
    const { fn, calls } = controllable();
    const { result } = renderHook(() => useApi(fn));

    // A poll tick lands before the first response.
    act(() => result.current.reload());
    // Coalesced, not stacked: still exactly one request in flight.
    expect(fn).toHaveBeenCalledTimes(1);

    // The first lands; the deferred reload runs.
    await act(async () => calls[0].resolve({ n: 1 }));
    await waitFor(() => expect(fn).toHaveBeenCalledTimes(2));
    // Still loading — a refetch is genuinely in progress, so the skeleton is honest.
    expect(result.current.loading).toBe(true);

    await act(async () => calls[1].resolve({ n: 2 }));

    // THE ASSERTION THAT WOULD HAVE FAILED BEFORE: loading resolves.
    expect(result.current.loading).toBe(false);
    expect(result.current.data).toEqual({ n: 2 });
  });

  it("never leaves loading true under a poll faster than the response", async () => {
    // The production shape: reload() every tick while a slow request is outstanding.
    const { fn, calls } = controllable();
    const { result } = renderHook(() => useApi(fn));

    for (let i = 0; i < 5; i += 1) act(() => result.current.reload());
    // Five poll ticks, still one request — concurrency is bounded.
    expect(fn).toHaveBeenCalledTimes(1);

    await act(async () => calls[0].resolve({ n: 1 }));
    await waitFor(() => expect(fn).toHaveBeenCalledTimes(2));
    await act(async () => calls[1].resolve({ n: 2 }));

    expect(result.current.loading).toBe(false);
    expect(result.current.data).toEqual({ n: 2 });
  });

  it("resolves loading when the coalesced refetch itself fails", async () => {
    const { fn, calls } = controllable();
    const { result } = renderHook(() => useApi(fn));

    act(() => result.current.reload());
    await act(async () => calls[0].resolve({ n: 1 }));
    await waitFor(() => expect(fn).toHaveBeenCalledTimes(2));

    await act(async () => {
      calls[1].reject(new Error("later failure"));
      await Promise.resolve();
    });

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.error).toBeInstanceOf(Error);
  });
});

describe("a stale response never overwrites a newer one", () => {
  it("keeps the newest data when an older request lands last", async () => {
    // The protection the original `alive` guard existed for — kept intact.
    const { fn, calls } = controllable();
    const { result } = renderHook(() => useApi(fn));

    await act(async () => calls[0].resolve({ range: "24h" }));
    expect(result.current.data).toEqual({ range: "24h" });

    // A filter change: a second request starts and lands.
    act(() => result.current.reload());
    await waitFor(() => expect(fn).toHaveBeenCalledTimes(2));
    await act(async () => calls[1].resolve({ range: "7d" }));
    expect(result.current.data).toEqual({ range: "7d" });

    // The first request finally answers, late. It must be ignored.
    await act(async () => calls[0].resolve({ range: "24h" }));
    expect(result.current.data).toEqual({ range: "7d" });
  });
});

describe("unmounting", () => {
  it("does not update state after the component has gone", async () => {
    const { fn, calls } = controllable();
    const { result, unmount } = renderHook(() => useApi(fn));
    const before = result.current.data;

    unmount();
    await act(async () => calls[0].resolve({ late: true }));

    expect(result.current.data).toBe(before);
  });
});
