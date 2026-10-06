// Custom-domain mode: when the server marks index.html as served on an organization's ACTIVE
// custom domain, the SPA renders the event page and nothing else, and help links go to the
// platform. Without the marker everything is exactly as before.
import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("../pages/watch/EventWatch", () => ({ default: () => <p>event page</p> }));

import App from "../App";
import ViewerErrorState from "../components/watch/ViewerErrorState";
import { MemoryRouter } from "react-router-dom";
import { isCustomDomain, platformHref } from "./hostMode";

const mark = () => {
  for (const [name, content] of [["zk-host-mode", "custom-domain"], ["zk-platform-origin", "https://get.zoikostream.com"]]) {
    const m = document.createElement("meta");
    m.setAttribute("name", name);
    m.setAttribute("content", content);
    document.head.appendChild(m);
  }
};

afterEach(() => {
  document.head.querySelectorAll('meta[name^="zk-"]').forEach((m) => m.remove());
  window.history.pushState({}, "", "/");
});

describe("hostMode", () => {
  it("is off without the server's marker", () => {
    expect(isCustomDomain()).toBe(false);
    expect(platformHref("/status")).toBe("/status");
  });

  it("is on with it, and platform links become absolute", () => {
    mark();
    expect(isCustomDomain()).toBe(true);
    expect(platformHref("/status")).toBe("https://get.zoikostream.com/status");
  });
});

describe("the app on a custom domain", () => {
  it("renders the event page", async () => {
    mark();
    window.history.pushState({}, "", "/events/5e1f2a3b-4c5d-4e6f-8a9b-0c1d2e3f4a5b/watch");
    render(<App />);
    expect(await screen.findByText("event page")).toBeInTheDocument();
  });

  it.each(["/", "/login", "/organization/settings", "/admin/dashboard"])("renders nothing of the platform at %s", async (path) => {
    mark();
    window.history.pushState({}, "", path);
    render(<App />);
    expect(await screen.findByText("Page not available")).toBeInTheDocument();
    expect(screen.queryByRole("textbox")).toBeNull();
  });
});

describe("viewer help links", () => {
  it("point at the platform on a custom domain", () => {
    mark();
    render(<MemoryRouter><ViewerErrorState onRetry={() => {}} /></MemoryRouter>);
    const hrefs = screen.getAllByRole("link").map((a) => a.getAttribute("href"));
    expect(hrefs).toEqual(["https://get.zoikostream.com/contact", "https://get.zoikostream.com/status"]);
  });

  it("stay relative on the platform", () => {
    render(<MemoryRouter><ViewerErrorState onRetry={() => {}} /></MemoryRouter>);
    expect(screen.getAllByRole("link").map((a) => a.getAttribute("href"))).toEqual(["/contact", "/status"]);
  });
});
