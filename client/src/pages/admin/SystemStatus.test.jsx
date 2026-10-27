import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../api", () => ({
  default: { get: vi.fn() },
  diagnoseLoadError: (error) => error?.message ?? "failed",
}));

vi.mock("../../hooks/useApi", () => ({
  default: () => ({
    data: {
      overall: "ok",
      integrated: 7,
      not_configured: 2,
      services: [
        { id: "api", name: "API", status: "ok", note: "Health-check request processing time", latency_ms: 1.2 },
        { id: "auth", name: "Authentication", status: "ok", note: "Current request token and session verification", latency_ms: 38.8 },
        { id: "database", name: "Database", status: "ok", note: "SELECT 1", latency_ms: 1 },
        { id: "redis", name: "Redis", status: "ok", note: "PING", latency_ms: 2 },
        { id: "streaming", name: "Streaming", status: "ok", note: "Background probe", latency_ms: 3 },
        { id: "email", name: "Email", status: "ok", note: "Background probe", latency_ms: 4 },
        { id: "storage", name: "Storage", status: "ok", note: "Background probe", latency_ms: 5 },
        { id: "cdn", name: "CDN", status: "not_configured", note: "Not yet integrated" },
        { id: "workers", name: "Background Workers", status: "not_configured", note: "Not yet integrated" },
      ],
    },
    loading: false,
    error: null,
    reload: vi.fn(),
  }),
}));

import SystemStatus from "./SystemStatus";

beforeEach(() => vi.clearAllMocks());

describe("System Status latency", () => {
  it("renders API and authentication measurements from the health response", () => {
    render(<SystemStatus />);

    expect(screen.getByText("1.2ms")).toBeInTheDocument();
    expect(screen.getByText("38.8ms")).toBeInTheDocument();
    expect(screen.getByText("Operational with limitations · 2 not integrated")).toBeInTheDocument();
    expect(screen.getByText("7 of 9 services integrated")).toBeInTheDocument();
    expect(screen.queryByText("—")).not.toBeInTheDocument();
  });
});
