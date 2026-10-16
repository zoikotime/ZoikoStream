// The reusable password re-confirmation dialog and its promise hook.
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../api", () => ({
  default: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() },
  errMsg: (e, fallback) => (typeof e?.response?.data?.detail === "string" ? e.response.data.detail : fallback),
}));

import api from "../../api";
import StepUpDialog from "./StepUpDialog";
import useStepUp, { isStepUpRequired } from "./useStepUp";

beforeEach(() => vi.clearAllMocks());

function renderDialog(props = {}) {
  const onConfirmed = vi.fn();
  const onCancel = vi.fn();
  render(<StepUpDialog purpose="high_risk_role_grant" onConfirmed={onConfirmed} onCancel={onCancel} {...props} />);
  return { onConfirmed, onCancel, dialog: screen.getByRole("dialog") };
}

describe("StepUpDialog", () => {
  it("is a labelled, described modal dialog with focus in the password field", () => {
    const { dialog } = renderDialog();
    expect(dialog).toHaveAttribute("aria-modal", "true");
    expect(dialog).toHaveAccessibleName("Confirm it's you");
    expect(dialog).toHaveAccessibleDescription(/Granting admin access gives full control of this organization/);
    const input = screen.getByLabelText("Your password");
    expect(input).toHaveAttribute("type", "password");
    expect(input).toHaveAttribute("autocomplete", "current-password");
    expect(input).toHaveFocus();
    expect(screen.getByRole("button", { name: "Confirm and grant access" })).toBeDisabled();
  });

  it("exchanges the password for a reference bound to the purpose", async () => {
    api.post.mockResolvedValueOnce({ data: { reference: "ref-9", purpose: "high_risk_role_grant", expires_in_minutes: 5 } });
    const { onConfirmed } = renderDialog();
    fireEvent.change(screen.getByLabelText("Your password"), { target: { value: "s3cret" } });
    fireEvent.click(screen.getByRole("button", { name: "Confirm and grant access" }));
    await waitFor(() => expect(onConfirmed).toHaveBeenCalledWith("ref-9"));
    expect(api.post).toHaveBeenCalledWith("/auth/step-up", { password: "s3cret", purpose: "high_risk_role_grant" });
  });

  it("a wrong password is reported inline, the field is cleared and refocused", async () => {
    api.post.mockRejectedValueOnce({ response: { status: 401, data: { detail: "Password re-verification failed." } } });
    const { onConfirmed, onCancel } = renderDialog();
    const input = screen.getByLabelText("Your password");
    fireEvent.change(input, { target: { value: "wrong" } });
    fireEvent.submit(input.closest("form"));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("That password is incorrect. Try again.");
    expect(input).toHaveAttribute("aria-invalid", "true");
    expect(input).toHaveAttribute("aria-describedby", alert.id);
    expect(input).toHaveValue("");
    expect(input).toHaveFocus();
    expect(onConfirmed).not.toHaveBeenCalled();
    expect(onCancel).not.toHaveBeenCalled();
    // Typing again clears the message.
    fireEvent.change(input, { target: { value: "x" } });
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("rate limiting (429) says to wait", async () => {
    api.post.mockRejectedValueOnce({ response: { status: 429, data: { detail: "Too Many Requests" } } });
    renderDialog();
    fireEvent.change(screen.getByLabelText("Your password"), { target: { value: "pw" } });
    fireEvent.click(screen.getByRole("button", { name: "Confirm and grant access" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Too many attempts. Wait a few minutes, then try again.");
  });

  it("Escape and Cancel cancel without a request", () => {
    const { onCancel } = renderDialog();
    fireEvent.keyDown(document, { key: "Escape" });
    expect(onCancel).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(onCancel).toHaveBeenCalledTimes(2);
    expect(api.post).not.toHaveBeenCalled();
  });

  it("keeps Tab inside the dialog", async () => {
    const user = userEvent.setup();
    renderDialog();
    fireEvent.change(screen.getByLabelText("Your password"), { target: { value: "pw" } });
    const input = screen.getByLabelText("Your password");
    const confirm = screen.getByRole("button", { name: "Confirm and grant access" });
    confirm.focus();
    await user.tab();
    expect(input).toHaveFocus();                  // wrapped from the last control to the first
    await user.tab({ shift: true });
    expect(confirm).toHaveFocus();                // and back
  });
});

function Harness({ onResult }) {
  const stepUp = useStepUp();
  const [opener] = useState(() => async () => onResult(await stepUp.request({ purpose: "high_risk_role_grant" })));
  return (
    <>
      <button type="button" onClick={() => opener()}>Promote</button>
      {stepUp.dialog}
    </>
  );
}

describe("useStepUp", () => {
  it("resolves with the reference, closes, and returns focus to the control that opened it", async () => {
    api.post.mockResolvedValueOnce({ data: { reference: "ref-1" } });
    const onResult = vi.fn();
    render(<Harness onResult={onResult} />);
    const promote = screen.getByRole("button", { name: "Promote" });
    promote.focus();
    fireEvent.click(promote);
    fireEvent.change(await screen.findByLabelText("Your password"), { target: { value: "pw" } });
    fireEvent.click(screen.getByRole("button", { name: "Confirm and grant access" }));
    await waitFor(() => expect(onResult).toHaveBeenCalledWith("ref-1"));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(promote).toHaveFocus();
  });

  it("resolves null on cancel", async () => {
    const onResult = vi.fn();
    render(<Harness onResult={onResult} />);
    fireEvent.click(screen.getByRole("button", { name: "Promote" }));
    fireEvent.click(await screen.findByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(onResult).toHaveBeenCalledWith(null));
  });

  it("resolves null if the page goes away mid-confirmation", async () => {
    const onResult = vi.fn();
    const { unmount } = render(<Harness onResult={onResult} />);
    fireEvent.click(screen.getByRole("button", { name: "Promote" }));
    await screen.findByRole("dialog");
    unmount();
    await act(async () => {});
    expect(onResult).toHaveBeenCalledWith(null);
  });

  it("recognises only the server's STEP_UP_REQUIRED refusal", () => {
    expect(isStepUpRequired({ response: { status: 403, data: { detail: { code: "STEP_UP_REQUIRED" } } } })).toBe(true);
    expect(isStepUpRequired({ response: { status: 403, data: { detail: "Forbidden" } } })).toBe(false);
    expect(isStepUpRequired({ response: { status: 400, data: { detail: { code: "STEP_UP_REQUIRED" } } } })).toBe(false);
    expect(isStepUpRequired(new Error("network"))).toBe(false);
  });
});
