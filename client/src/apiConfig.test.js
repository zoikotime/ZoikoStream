// Where the client sends API calls (apiBase.js) and what it says when nothing answers (api.js),
// in a development build and in a production build. The design these pin, unchanged:
//
//   VITE_API_URL set      -> that address, in any build
//   development, unset    -> http://localhost:8001 (the local API's port; Vite serves the client on another)
//   production, unset     -> the page's own origin (the API and the SPA are one origin)
//
// and the unreachable message names the API address only in development, where a client pointed
// at the wrong local server (another project's API on port 8000) is otherwise indistinguishable
// from a dropped connection.
import { afterEach, describe, expect, it, vi } from "vitest";

afterEach(() => {
  vi.unstubAllEnvs();
  vi.resetModules();
});

async function load(env) {
  vi.resetModules();
  for (const [key, value] of Object.entries(env)) vi.stubEnv(key, value);
  return {
    base: (await import("./apiBase")).API_BASE,
    api: await import("./api"),
  };
}

const noResponse = () => Object.assign(new Error("Network Error"), { code: "ERR_NETWORK", request: {} });

describe("development build", () => {
  it("uses VITE_API_URL when it is set", async () => {
    const { base, api } = await load({ DEV: true, PROD: false, VITE_API_URL: "http://127.0.0.1:8001" });
    expect(base).toBe("http://127.0.0.1:8001");
    expect(api.default.defaults.baseURL).toBe("http://127.0.0.1:8001/api");
  });

  it("falls back to the local API on :8001 when it is not", async () => {
    const { base } = await load({ DEV: true, PROD: false, VITE_API_URL: "" });
    expect(base).toBe("http://localhost:8001");
  });

  it("an unreachable or CORS-blocked call names the address it went to", async () => {
    const { api } = await load({ DEV: true, PROD: false, VITE_API_URL: "http://127.0.0.1:8001" });
    const msg = api.errMsg(noResponse());
    expect(msg).toMatch(/^Couldn't reach the ZoikoStream server, so nothing was submitted\./);
    expect(msg).toContain("the request went to http://127.0.0.1:8001");
  });
});

describe("production build", () => {
  it("defaults to the page's own origin", async () => {
    const { base, api } = await load({ DEV: false, PROD: true, VITE_API_URL: "" });
    expect(base).toBe(window.location.origin);
    expect(api.default.defaults.baseURL).toBe(`${window.location.origin}/api`);
  });

  it("still honours an explicit VITE_API_URL", async () => {
    const { base } = await load({ DEV: false, PROD: true, VITE_API_URL: "https://api.example.test" });
    expect(base).toBe("https://api.example.test");
  });

  it("keeps the plain connection message — no address, no development hint", async () => {
    const { api } = await load({ DEV: false, PROD: true, VITE_API_URL: "" });
    expect(api.errMsg(noResponse()))
      .toBe("Couldn't reach the ZoikoStream server, so nothing was submitted. Check your connection and try again.");
  });
});

describe("either build", () => {
  it("never reports a server's answer as unreachable, and sends cookies with every call", async () => {
    const { api } = await load({ DEV: true, PROD: false, VITE_API_URL: "http://127.0.0.1:8001" });
    const refused = Object.assign(new Error("Request failed"), {
      response: { status: 401, data: { detail: "Incorrect email or password" } }, request: {} });
    expect(api.errMsg(refused)).toBe("Incorrect email or password");
    expect(api.default.defaults.withCredentials).toBe(true);
  });
});
