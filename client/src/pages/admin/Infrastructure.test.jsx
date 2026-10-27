import { render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../api", () => ({ default: { get: vi.fn() } }));
vi.mock("../../hooks/useApi", () => ({ default: vi.fn() }));
vi.mock("../../hooks/useInterval", () => ({ default: vi.fn() }));
vi.mock("../../components/admin/ConsoleScreen", () => ({
  default: ({ children }) => <main>{children}</main>,
}));
vi.mock("../../components/admin/sections/LifecycleRail", () => ({ default: () => null }));
vi.mock("../../components/admin/sections/StageMatrix", () => ({ default: () => null }));

import useApi from "../../hooks/useApi";
import Infrastructure from "./Infrastructure";

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(useApi)
    .mockReturnValueOnce({
      data: {
        overall: "ok",
        integrated: 1,
        not_configured: 2,
        services: [
          { id: "database", name: "Database", status: "ok", note: "Answered" },
          { id: "cdn", name: "CDN", status: "not_configured", note: "Not yet integrated" },
          { id: "workers", name: "Background Workers", status: "not_configured", note: "Not yet integrated" },
        ],
      },
      loading: false,
      error: null,
      reload: vi.fn(),
    })
    .mockReturnValueOnce({
      data: { lifecycle: [], regions: [], kpis: {} },
      loading: false,
      error: null,
      reload: vi.fn(),
    });
});

describe("Media Infrastructure estate summary", () => {
  it("states that edge utilization and transcode queue depth are not integrated", () => {
    render(<Infrastructure />);

    expect(screen.getByText("Operational with limitations · 2 not integrated")).toBeInTheDocument();
    for (const label of ["Edge PoP utilisation", "Transcode queue depth"]) {
      const row = screen.getByText(label).parentElement;
      expect(within(row).getByText("Not integrated")).toBeInTheDocument();
      expect(within(row).queryByText("—")).not.toBeInTheDocument();
    }
  });
});
