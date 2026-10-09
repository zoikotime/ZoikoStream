// The one-time Country / Region prompt for a host-invited viewer, who never sees the
// registration form. Optional: "Not now" sends nothing; Save sends only an ISO code, with the
// registration credential in the request body (never the URL).
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../api", () => ({
  default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), patch: vi.fn(), delete: vi.fn() },
  errMsg: (e, fallback) => fallback ?? "Something went wrong.",
}));

import api from "../../api";
import CountryPrompt from "./CountryPrompt";

const EVENT_ID = "0d6f3c55-3d5c-4b0c-9a59-7f0a7c4a3e10";
const TOKEN = "opaque.registration.credential";
const input = () => screen.getByRole("combobox", { name: /country \/ region/i });

beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
  vi.mocked(api.put).mockResolvedValue({ data: { country_code: "IN" } });
});

describe("CountryPrompt", () => {
  it("asks where they are watching from, optionally, with the purpose", () => {
    render(<CountryPrompt eventId={EVENT_ID} regToken={TOKEN} />);
    expect(screen.getByText("Where are you watching from?")).toBeInTheDocument();
    expect(screen.getByText("Used for aggregate event audience analytics.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Not now" })).toBeInTheDocument();
  });

  it("saves the ISO code against their own registration, token in the body", async () => {
    const user = userEvent.setup();
    render(<CountryPrompt eventId={EVENT_ID} regToken={TOKEN} />);
    await user.type(input(), "India");
    await user.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(api.put).toHaveBeenCalled());
    expect(vi.mocked(api.put).mock.calls[0]).toEqual([
      `/events/${EVENT_ID}/registration/country`, { token: TOKEN, country: "IN" },
    ]);
    expect(await screen.findByText(/saved as India/i)).toBeInTheDocument();
  });

  it("prefills a remembered country but never submits it on its own", async () => {
    localStorage.setItem("zk_viewer_profile", JSON.stringify({ name: "Asha", country: "GB" }));
    render(<CountryPrompt eventId={EVENT_ID} regToken={TOKEN} />);
    expect(input()).toHaveValue("United Kingdom");
    expect(api.put).not.toHaveBeenCalled();
  });

  it("updates a remembered profile's country after saving", async () => {
    localStorage.setItem("zk_viewer_profile", JSON.stringify({ name: "Asha" }));
    const user = userEvent.setup();
    render(<CountryPrompt eventId={EVENT_ID} regToken={TOKEN} />);
    await user.type(input(), "India");
    await user.click(screen.getByRole("button", { name: "Save" }));
    await screen.findByText(/saved as India/i);
    expect(JSON.parse(localStorage.getItem("zk_viewer_profile"))).toEqual({ name: "Asha", country: "IN" });
  });

  it("refuses something that is not a country, without sending", async () => {
    const user = userEvent.setup();
    render(<CountryPrompt eventId={EVENT_ID} regToken={TOKEN} />);
    await user.type(input(), "Narnia");
    await user.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByText(/choose a country from the list/i)).toBeInTheDocument();
    expect(api.put).not.toHaveBeenCalled();
  });

  it("goes away on Not now, sends nothing, and stays away for this event", async () => {
    const user = userEvent.setup();
    const { unmount } = render(<CountryPrompt eventId={EVENT_ID} regToken={TOKEN} />);
    await user.click(screen.getByRole("button", { name: "Not now" }));
    expect(screen.queryByText("Where are you watching from?")).not.toBeInTheDocument();
    expect(api.put).not.toHaveBeenCalled();
    unmount();
    render(<CountryPrompt eventId={EVENT_ID} regToken={TOKEN} />);
    expect(screen.queryByText("Where are you watching from?")).not.toBeInTheDocument();
  });

  it("renders nothing without a registration credential", () => {
    const { container } = render(<CountryPrompt eventId={EVENT_ID} regToken={null} />);
    expect(container).toBeEmptyDOMElement();
  });
});
