// Toasts must be readable before they vanish, and closable once read.
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeAll, describe, expect, it } from "vitest";
import toast from "react-hot-toast";
import AppToaster from "./AppToaster";
import { TOAST_DURATIONS, toasterProps } from "./Toast";
import { edgeErrorMessage, errMsg } from "../api";

// react-hot-toast reads prefers-reduced-motion; jsdom has no matchMedia.
beforeAll(() => {
  window.matchMedia ??= () => ({ matches: false, addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {} });
});
afterEach(() => act(() => toast.remove()));

describe("toast timing", () => {
  it("keeps errors up long enough to read, warnings and successes shorter", () => {
    expect(TOAST_DURATIONS.error).toBeGreaterThanOrEqual(8000);
    expect(TOAST_DURATIONS.warning).toBeGreaterThanOrEqual(5000);
    expect(TOAST_DURATIONS.success).toBeGreaterThanOrEqual(3000);
    expect(TOAST_DURATIONS.success).toBeLessThanOrEqual(4000);
    expect(toasterProps.toastOptions.error.duration).toBe(TOAST_DURATIONS.error);
    expect(toasterProps.toastOptions.success.duration).toBe(TOAST_DURATIONS.success);
  });

  it("gives every toast a close button that dismisses it", async () => {
    render(<AppToaster />);
    act(() => { toast.error("Branding not saved: try again"); });
    expect(await screen.findByText("Branding not saved: try again")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Dismiss notification" }));
    // Dismissed at once; react-hot-toast removes the node after its ~1s exit animation.
    await waitFor(() => expect(screen.queryByText("Branding not saved: try again")).toBeNull(),
                  { timeout: 3000 });
  });
});

describe("edge errors", () => {
  it("reword Cloudflare's own responses and say whether anything was delivered", () => {
    const e525 = { response: { status: 525, data: { detail: "The SSL/TLS handshake between Cloudflare and the origin server failed." } } };
    expect(errMsg(e525)).toMatch(/edge error 525.*not delivered and nothing was changed/);
    expect(errMsg(e525)).not.toMatch(/Cloudflare/);
    expect(errMsg({ response: { status: 524 } })).toMatch(/may or may not have been applied/);
    expect(edgeErrorMessage({ response: { status: 500 } })).toBeNull();
    expect(errMsg({ response: { status: 409, data: { detail: "Slug already taken" } } })).toBe("Slug already taken");
  });
});
