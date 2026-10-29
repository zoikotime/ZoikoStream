import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { AudienceReach } from "./Analytics";
import { ThemeProvider } from "../../theme/ThemeContext";

const renderReach = (props) =>
  render(
    <ThemeProvider>
      <MemoryRouter>
        <AudienceReach {...props} />
      </MemoryRouter>
    </ThemeProvider>
  );

describe("AudienceReach component - Phase 3 states", () => {
  it("renders 1. Data unavailable state when error is true and no data is available", () => {
    renderReach({
      devices: null,
      locations: null,
      trafficSources: null,
      audienceViewers: null,
      error: true,
    });

    expect(screen.getByText("Unavailable")).toBeInTheDocument();
    expect(
      screen.getByText(/Audience reach telemetry is temporarily unavailable/i)
    ).toBeInTheDocument();
  });

  it("renders 2. Not configured state when breakdowns are null and no audience viewers", () => {
    renderReach({
      devices: null,
      locations: null,
      trafficSources: null,
      audienceViewers: null,
      note: "Device, location and traffic-source breakdowns need a metering/GeoIP pipeline that isn't integrated yet.",
    });

    expect(screen.getByText("Not measured")).toBeInTheDocument();
    expect(
      screen.getByText(/Device, location and traffic-source breakdowns need a metering/i)
    ).toBeInTheDocument();
  });

  it("renders 3. No activity state when viewers are 0 in the selected window", () => {
    renderReach({
      devices: [],
      locations: [],
      trafficSources: null,
      audienceViewers: 0,
      rangeLabel: "Last 30 days",
      note: "No audience telemetry recorded in this window.",
    });

    expect(screen.getByText("No activity")).toBeInTheDocument();
    expect(
      screen.getByText("No audience telemetry recorded in this window.")
    ).toBeInTheDocument();
  });

  it("renders 4. Partially measured state with real device and region metrics and unmeasured traffic sources note", () => {
    renderReach({
      devices: [
        { device_type: "desktop", label: "Desktop", viewers: 15, percentage: 75 },
        { device_type: "mobile", label: "Mobile", viewers: 5, percentage: 25 },
      ],
      locations: [
        { country_code: "US", country_name: "United States", viewers: 10, percentage: 67 },
        { country_code: "GB", country_name: "United Kingdom", viewers: 5, percentage: 33 },
      ],
      locationsKnown: 15,
      locationsUnknown: 5,
      trafficSources: null,
      audienceViewers: 20,
      rangeLabel: "Last 30 days",
      note: "Device classes are detected from viewer requests. Geographic distribution reflects registered viewer locations.",
    });

    expect(screen.getByText("Partially measured")).toBeInTheDocument();
    expect(screen.getByText("Device mix")).toBeInTheDocument();
    expect(screen.getByText("Desktop")).toBeInTheDocument();
    expect(screen.getByText("15 viewers · 75%")).toBeInTheDocument();
    expect(screen.getByText("Mobile")).toBeInTheDocument();
    expect(screen.getByText("5 viewers · 25%")).toBeInTheDocument();

    expect(screen.getByText("Top regions")).toBeInTheDocument();
    expect(screen.getByText("United States")).toBeInTheDocument();
    expect(screen.getByText("10 viewers · 67%")).toBeInTheDocument();
    expect(screen.getByText("United Kingdom")).toBeInTheDocument();
    expect(screen.getByText("5 viewers · 33%")).toBeInTheDocument();
    expect(screen.getByText("5 viewers did not share a country.")).toBeInTheDocument();

    // Explains unmeasured dimension
    expect(screen.getByText(/Traffic sources:/i)).toBeInTheDocument();
    expect(screen.getByText(/Not tracked to protect viewer link tokens/i)).toBeInTheDocument();
  });

  it("renders 5. Measured state when all dimensions including traffic sources are available", () => {
    renderReach({
      devices: [
        { device_type: "desktop", label: "Desktop", viewers: 20, percentage: 100 },
      ],
      locations: [
        { country_code: "US", country_name: "United States", viewers: 20, percentage: 100 },
      ],
      locationsKnown: 20,
      locationsUnknown: 0,
      trafficSources: [
        { key: "direct", label: "Direct link", viewers: 20, percentage: 100 },
      ],
      audienceViewers: 20,
      rangeLabel: "Last 30 days",
    });

    expect(screen.getByText("Measured")).toBeInTheDocument();
    expect(screen.getByText("Direct link")).toBeInTheDocument();
    expect(screen.getAllByText("20 viewers · 100%")).toHaveLength(3);
  });
});
