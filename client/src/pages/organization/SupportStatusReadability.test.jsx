// Support & Status: the incident counts are easy to read, and what is clickable looks it.
//
// Two production reports. (1) "0 incidents" in Service health read as faint. Its colour was
// never the problem — it measured 15:1 on the dark panel in the reporter's own screenshot — but
// it was the one cell in the row set in the system monospace (Consolas, at 13px), whose thin
// strokes read dimmer and smaller than the text around it. The count is now the table's own
// face with tabular figures, the number in the heading colour. (2) The footer links and the
// Trust & evidence cards gave little or no hover feedback, and plain-text footer items looked
// exactly like the links beside them.
//
// Contrast here is measured the way a browser paints it: Tailwind's oklch palette (read from
// tailwindcss/theme.css) → 8-bit sRGB → WCAG 2.2 relative luminance, against the surfaces the
// elements actually sit on, in both themes. The colours come from the classes the rendered
// elements carry, so a class change is measured, not assumed.
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useEffect } from "react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../api", () => ({
  default: { get: vi.fn() },
  errMsg: (e) => e?.message ?? "Something went wrong.",
}));

import api from "../../api";
import { ThemeProvider } from "../../theme/ThemeContext";
import SupportStatus from "./SupportStatus";

// ── colour arithmetic (as in ui/contrast.test.js) ────────────────────────────────────────
const theme = readFileSync(createRequire(import.meta.url).resolve("tailwindcss/theme.css"), "utf8");
function oklab(name) {
  if (name === "white") return { L: 1, a: 0, b: 0 };
  if (name === "black") return { L: 0, a: 0, b: 0 };
  const m = theme.match(new RegExp(`--color-${name}:\\s*oklch\\(([\\d.]+)%\\s+([\\d.]+)\\s+([\\d.]+|none)\\)`));
  if (!m) throw new Error(`no Tailwind colour ${name}`);
  const h = m[3] === "none" ? 0 : (Number(m[3]) * Math.PI) / 180;
  return { L: Number(m[1]) / 100, a: Number(m[2]) * Math.cos(h), b: Number(m[2]) * Math.sin(h) };
}
function rgb({ L, a, b }) {
  const l = (L + 0.3963377774 * a + 0.2158037573 * b) ** 3;
  const m = (L - 0.1055613458 * a - 0.0638541728 * b) ** 3;
  const s = (L - 0.0894841775 * a - 1.291485548 * b) ** 3;
  const lin = [
    4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
    -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
    -0.0041960863 * l - 0.7034186147 * m + 1.707614701 * s,
  ];
  const enc = (x) => (x <= 0.0031308 ? 12.92 * x : 1.055 * x ** (1 / 2.4) - 0.055);
  return lin.map((x) => Math.round(enc(Math.min(1, Math.max(0, x))) * 255));
}
const luminance = (c) => {
  const dec = (v) => ((v /= 255) <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4);
  return 0.2126 * dec(c[0]) + 0.7152 * dec(c[1]) + 0.0722 * dec(c[2]);
};
const ratio = (fg, bg) => {
  const [hi, lo] = [luminance(fg), luminance(bg)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
};
const over = (fill, alpha, under) => fill.map((v, i) => Math.round(v * alpha + under[i] * (1 - alpha)));
const c = (name) => rgb(oklab(name));
const WHITE = [255, 255, 255];
const BLACK = [0, 0, 0];

// The surfaces (ui/tokens CONSOLE): page slate-50 | black; panel white | white/5 over black;
// a hovered table row adds slate-50 | white/3; a hovered trust card slate-50 | white/8.
const SURFACE = {
  light: { page: c("slate-50"), panel: WHITE, rowHover: c("slate-50"), cardHover: c("slate-50") },
  dark: { page: BLACK, panel: over(WHITE, 0.05, BLACK) },
};
SURFACE.dark.rowHover = over(WHITE, 0.03, SURFACE.dark.panel);
SURFACE.dark.cardHover = over(WHITE, 0.08, BLACK);

// The text colour an element's classes give it in each theme (`text-x` / `dark:text-x`, or
// their `hover:` / `group-hover:` forms when `state` asks for them).
function textColour(el, mode, state = "") {
  const classes = el.className.split(/\s+/);
  const pick = (prefix) => {
    const hit = classes.find((k) => k.startsWith(`${prefix}text-`) && /^[a-z]+-\d{2,3}$|^(white|black)$/.test(k.slice(prefix.length + 5)));
    return hit && hit.slice(prefix.length + 5);
  };
  const dark = mode === "dark" ? "dark:" : "";
  const name = (state && pick(`${dark}${state}`)) || pick(dark);
  if (!name) throw new Error(`no ${mode}${state ? ` ${state}` : ""} text colour on <${el.tagName}> "${el.className}"`);
  return c(name);
}

// ── page harness ─────────────────────────────────────────────────────────────────────────
const svc = (id, name, over_ = {}) => ({
  id, name, summary: `${name} summary`, status: "ok", incidents_24h: 0, open_incidents: 0, incident: null, ...over_,
});
const OVERVIEW = {
  generated_at: "2026-09-22T11:30:00Z",
  organization: { region: "US East" },
  service_health: { status: "ok", label: "Operational", cause: null },
  security_support: { maintenance_window: null },
  lifecycle: [],
  service_status: {
    verdict: "ok",
    services: [
      svc("live_streaming", "Live streaming", { incidents_24h: 0 }),
      svc("recording", "Recording & replay", { incidents_24h: 1 }),
      svc("platform_api", "Management API", { incidents_24h: 3 }),
      svc("chat", "Chat", { incidents_24h: null }),
    ],
  },
  maintenance: { authoritative: false, upcoming: [] },
};

// The router's current location, recorded after each render.
let here;
function Where() {
  const location = useLocation();
  useEffect(() => {
    here = location;
  });
  return null;
}
const DEST = ["/privacy", "/trust", "/status", "/contact", "/organization/support/security", "/organization/support/data-residency"];
const show = () =>
  render(
    <ThemeProvider>
      <MemoryRouter initialEntries={["/organization/support"]}>
        <Where />
        <Routes>
          <Route path="/organization/support" element={<SupportStatus />} />
          {DEST.map((p) => <Route key={p} path={p} element={<h1>{`page ${p}`}</h1>} />)}
        </Routes>
      </MemoryRouter>
    </ThemeProvider>
  );
const loaded = () => screen.findByRole("heading", { name: /trust & evidence/i });
const rowFor = (name) => within(screen.getByRole("table")).getAllByRole("row").find((r) => r.textContent.includes(name));
const incidentCell = (name) => rowFor(name).querySelectorAll("td")[2];
const footer = () => document.querySelector("footer");
const footerLink = (name) => within(footer()).getByRole("link", { name });
const card = (name) => screen.getByRole("link", { name: new RegExp(`^${name}`, "i") });

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.get).mockResolvedValue({ data: OVERVIEW });
});

// ── 1. incident counts ───────────────────────────────────────────────────────────────────
describe("Service health incident counts", () => {
  it("reads as a count in the table's own face: the number set apart, every state covered", async () => {
    show();
    await loaded();
    const expected = { "Live streaming": "0 incidents", "Recording & replay": "1 incident", "Management API": "3 incidents", Chat: "—" };
    for (const [name, text] of Object.entries(expected)) {
      const cell = incidentCell(name);
      expect(cell).toHaveTextContent(text);
      expect(cell.className).not.toMatch(/\bfont-mono\b/);
      expect(cell.className).toContain("tabular-nums");
    }
    const [n, noun] = incidentCell("Management API").querySelectorAll("span");
    expect(n).toHaveTextContent("3");
    expect(n.className).toContain("font-semibold");
    expect(noun).toHaveTextContent("incidents");
  });

  it.each(["light", "dark"])("meets 4.5:1 in the %s theme, at rest and on a hovered row, for every state", async (mode) => {
    show();
    await loaded();
    const parts = [
      ...incidentCell("Live streaming").querySelectorAll("span"),
      ...incidentCell("Management API").querySelectorAll("span"),
      ...incidentCell("Chat").querySelectorAll("span"),
    ];
    expect(parts.length).toBe(5); // 0 + incidents, 3 + incidents, —
    for (const el of parts) {
      for (const bg of [SURFACE[mode].panel, SURFACE[mode].rowHover]) {
        expect(ratio(textColour(el, mode), bg), `"${el.textContent}" (${mode})`).toBeGreaterThanOrEqual(4.5);
      }
    }
  });

  it("leaves the service status colours alone", async () => {
    show();
    await loaded();
    expect(rowFor("Live streaming")).toHaveTextContent("Operational");
  });
});

// ── 2. footer ────────────────────────────────────────────────────────────────────────────
describe("the footer", () => {
  const LINKS = { Privacy: "/privacy", "Trust Center": "/trust", "Incident History": "/status", Contact: "/contact" };

  it("styles each real link as one: pointer, underline, hover, press and a keyboard focus ring", async () => {
    show();
    await loaded();
    for (const [name, href] of Object.entries(LINKS)) {
      const a = footerLink(name);
      expect(a).toHaveAttribute("href", href);
      for (const cls of ["cursor-pointer", "underline", "hover:text-slate-900", "dark:hover:text-white", "hover:decoration-current",
        "active:text-violet-700", "transition-colors", "focus-visible:ring-2"]) {
        expect(a.className.split(/\s+/), `${name}: ${cls}`).toContain(cls);
      }
    }
  });

  it("leaves Terms, which has no page, as plain text that does not look or act clickable", async () => {
    show();
    await loaded();
    const terms = within(footer()).getByText("Terms");
    expect(terms.tagName).toBe("SPAN");
    expect(within(footer()).queryByRole("link", { name: "Terms" })).toBeNull();
    expect(terms.className).not.toMatch(/cursor-pointer|underline|hover:/);
    expect(terms).not.toHaveAttribute("tabindex");
  });

  it.each(["light", "dark"])("stays readable in the %s theme: at rest and on hover", async (mode) => {
    show();
    await loaded();
    const page = SURFACE[mode].page;
    for (const name of Object.keys(LINKS)) {
      const a = footerLink(name);
      expect(ratio(textColour(a, mode), page), `${name} at rest`).toBeGreaterThanOrEqual(4.5);
      expect(ratio(textColour(a, mode, "hover:"), page), `${name} hovered`).toBeGreaterThanOrEqual(4.5);
    }
    expect(ratio(textColour(within(footer()).getByText("Terms"), mode), page)).toBeGreaterThanOrEqual(4.5);
  });

  it("is reachable from the keyboard in order, skipping the plain text", async () => {
    const u = userEvent.setup();
    show();
    await loaded();
    footerLink("Privacy").focus();
    const order = [document.activeElement.textContent];
    for (let i = 0; i < 3; i += 1) {
      await u.tab();
      order.push(document.activeElement.textContent);
    }
    expect(order).toEqual(["Privacy", "Trust Center", "Incident History", "Contact"]);
  });

  it.each(Object.entries(LINKS))("%s opens %s in the app", async (name, href) => {
    const u = userEvent.setup();
    show();
    await loaded();
    await u.click(footerLink(name));
    expect(await screen.findByRole("heading", { name: `page ${href}` })).toBeInTheDocument();
    expect(here.pathname).toBe(href);
  });

  it("leaves a modified click (new tab or window) to the browser", async () => {
    const u = userEvent.setup();
    show();
    await loaded();
    await u.keyboard("{Control>}");
    await u.click(footerLink("Privacy"));
    await u.keyboard("{/Control}");
    expect(here.pathname).toBe("/organization/support");
  });
});

// ── 3. Trust & evidence cards ─────────────────────────────────────────────────────────────
describe("the Trust & evidence cards", () => {
  const CARDS = {
    "Security overview": ["/organization/support/security", ""],
    "Data residency": ["/organization/support/data-residency", ""],
    "Compliance documents": ["/trust", "#compliance"],
    "Report a vulnerability": ["/trust", "#report"],
  };

  it("each one is a link that answers hover, press and keyboard focus", async () => {
    show();
    await loaded();
    for (const name of Object.keys(CARDS)) {
      const a = card(name);
      for (const cls of ["cursor-pointer", "group", "hover:bg-slate-50", "dark:hover:bg-white/[0.08]", "hover:border-slate-300",
        "active:bg-slate-100", "transition-colors", "focus-visible:ring-2"]) {
        expect(a.className.split(/\s+/), `${name}: ${cls}`).toContain(cls);
      }
      const title = within(a).getByText(name);
      expect(title.className).toContain("group-hover:text-violet-700");
    }
  });

  it.each(["light", "dark"])("its title stays readable on the hovered card in the %s theme", async (mode) => {
    show();
    await loaded();
    for (const name of Object.keys(CARDS)) {
      const title = within(card(name)).getByText(name);
      expect(ratio(textColour(title, mode, "group-hover:"), SURFACE[mode].cardHover), name).toBeGreaterThanOrEqual(4.5);
    }
  });

  it.each(Object.entries(CARDS))("%s opens its page", async (name, [path, hash]) => {
    const u = userEvent.setup();
    show();
    await loaded();
    await u.click(card(name));
    expect(here.pathname).toBe(path);
    expect(here.hash).toBe(hash);
  });
});
