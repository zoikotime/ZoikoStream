// Contact page (Billing's "Contact sales" lands here) — the space between the hero and the form
// on a phone.
//
// Reported: an excessive vertical gap between the hero and the contact form on mobile. Two
// paddings stacked across the section boundary — the hero content's pb-12 (48px of dark hero
// under its last line) and the form section's py-10 (40px of white above the card) — and both
// were the base values, overridden only at lg, so a phone got the tablet's 88px band. Phones
// now get pb-8 and pt-6 (56px together); tablet (sm, 640px+) and desktop (lg) values are
// unchanged.
//
// jsdom has no layout. The distances were measured in Chromium, web and mobile builds, at
// 320–1440px: 88px -> 56px at 320/360/390/412; 88px at 768 and 112px at 1024+ unchanged (the
// 1280px page pixel-identical); no overlap, no horizontal overflow. Pinned here is the
// structure those numbers come from, and that the hero and form are intact. Validation and
// submission are covered by Contact.test.jsx.
import { render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

vi.mock("../api", () => ({
  default: { get: vi.fn(), post: vi.fn() },
  errMsg: (e) => e?.message ?? "Something went wrong.",
}));

import Contact from "./Contact";

function renderAt(path = "/contact") {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Contact />
    </MemoryRouter>,
  );
}

/** The hero section, its padded content box, and the section holding the form. */
function sections() {
  const h1 = screen.getByRole("heading", { level: 1, name: /talk to the right expert/i });
  const hero = h1.closest("section");
  const heroContent = hero.querySelector(":scope > .relative.z-10");
  const form = document.querySelector("main form");
  return { hero, heroContent, formSection: form.closest("section"), form };
}

describe("Contact page — hero to form spacing", () => {
  it("renders the hero, then the form section directly after it", () => {
    renderAt();
    const { hero, formSection } = sections();
    expect(screen.getByText("Contact ZoikoStream")).toBeInTheDocument();
    expect(screen.getByText(/route your inquiry to the ZoikoStream team/i)).toBeInTheDocument();
    expect(within(hero).getByRole("button", { name: /planning a live event/i })).toBeInTheDocument();
    // Nothing sits between them to take up space: no wrapper, spacer or hidden element.
    expect(hero.nextElementSibling).toBe(formSection);
  });

  it("uses the tighter phone spacing and keeps the tablet and desktop values", () => {
    renderAt();
    const { heroContent, formSection } = sections();
    // Hero: 32px under its last line on a phone, 48px from sm, 56px from lg (unchanged above).
    expect(heroContent).toHaveClass("pb-8", "sm:pb-12", "lg:pb-14", "pt-28", "lg:pt-32");
    expect(heroContent).not.toHaveClass("pb-12");
    // Form section: 24px above the card on a phone, 40px from sm, 56px from lg; bottom unchanged.
    expect(formSection).toHaveClass("pt-6", "sm:pt-10", "pb-10", "lg:py-14");
    expect(formSection).not.toHaveClass("py-10");
  });

  it("closes the gap without hacks: no negative margins, fixed heights or absolute positioning", () => {
    renderAt();
    const { heroContent, formSection, form } = sections();
    for (const el of [heroContent, formSection, form]) {
      const cls = el.getAttribute("class");
      expect(cls).not.toMatch(/(^|\s)-m[tby]?-/);
      expect(cls).not.toMatch(/(^|\s)(h|min-h|max-h)-\[/);
      expect(cls).not.toMatch(/(^|\s)(absolute|fixed)(\s|$)/);
    }
  });

  it("separates the hero's two sentences on a phone, where the line break is hidden", () => {
    renderAt();
    const description = screen.getByText(/trying to accomplish/i);
    // textContent ignores the <br>, which is exactly what a phone shows (it is display:none
    // below sm). It read "accomplish.We’ll" before the explicit space.
    expect(description.textContent).toContain("accomplish. We’ll route");
    expect(description.textContent).not.toMatch(/accomplish\.\S/);
    // The desktop line break is still there, hidden only below sm.
    const br = description.querySelector("br");
    expect(br).toHaveClass("hidden", "sm:block");
    const beforeBreak = [...description.childNodes].slice(0, [...description.childNodes].indexOf(br)).map((n) => n.textContent).join("");
    expect(beforeBreak).toMatch(/accomplish\. $/);
  });

  it("keeps the form complete and usable", () => {
    renderAt();
    expect(screen.getByRole("heading", { name: /tell us what you need/i })).toBeInTheDocument();
    for (const label of [/first name/i, /last name/i, /work email/i, /how can we help/i]) {
      expect(screen.getByLabelText(label)).toBeEnabled();
    }
    expect(screen.getByTestId("contact-submit")).toBeEnabled();
  });
});
