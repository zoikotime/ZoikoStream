// CheckField — the drawn checkbox behind "Remember Me" (sign-in) and the terms consent
// (Create Organization). In the Android app the 44px touch-target floor (index.css) used to
// stretch its box into a 20×44 bar, because this input IS the painted box.
//
// jsdom has no layout engine and this config loads no CSS, so nothing here can measure the
// box. The square was verified in Chromium against the mobile build (20×20 at 320–768px,
// coarse pointer, `html.native`); what this file pins is the opt-out that keeps it square,
// and that the control still behaves as a real checkbox.
import { useState } from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { CheckField } from "./index";

function Controlled({ initial = false, onToggle = () => {} }) {
  const [checked, setChecked] = useState(initial);
  return (
    <CheckField
      label="Remember Me"
      checked={checked}
      onChange={(e) => {
        setChecked(e.target.checked);
        onToggle(e.target.checked);
      }}
    />
  );
}

describe("CheckField box", () => {
  it("is square and opts out of the native min-height that stretched it", () => {
    render(<Controlled />);
    const classes = screen.getByRole("checkbox", { name: "Remember Me" }).className.split(/\s+/);
    expect(classes).toEqual(expect.arrayContaining(["h-5", "w-5", "!min-h-0"]));
  });
});

describe("CheckField behaviour", () => {
  it("toggles when the box is tapped", async () => {
    const user = userEvent.setup();
    const onToggle = vi.fn();
    render(<Controlled initial onToggle={onToggle} />);
    const box = screen.getByRole("checkbox", { name: "Remember Me" });
    expect(box).toBeChecked();
    await user.click(box);
    expect(box).not.toBeChecked();
    await user.click(box);
    expect(box).toBeChecked();
    expect(onToggle.mock.calls).toEqual([[false], [true]]);
  });

  it("toggles when the label text is tapped", async () => {
    const user = userEvent.setup();
    render(<Controlled />);
    await user.click(screen.getByText("Remember Me"));
    expect(screen.getByRole("checkbox", { name: "Remember Me" })).toBeChecked();
  });

  it("toggles from the keyboard", async () => {
    const user = userEvent.setup();
    render(<Controlled />);
    await user.tab();
    const box = screen.getByRole("checkbox", { name: "Remember Me" });
    expect(box).toHaveFocus();
    await user.keyboard(" ");
    expect(box).toBeChecked();
  });

  it("shows its error under the control", () => {
    render(<CheckField label="I agree" error="Please accept the terms." checked={false} onChange={() => {}} />);
    expect(screen.getByText("Please accept the terms.")).toBeInTheDocument();
  });
});
