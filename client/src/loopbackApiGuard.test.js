// @vitest-environment node
// A deployable bundle never carries a development API address (loopbackApiGuard.js, wired in
// vite.config.js). The shared repo-root .env's VITE_API_URL is the local API, and `vite build`
// reads that same file, so before this every build made from a developer's checkout, or from a
// server .env copied from .env.example, sent every visitor's API calls to their own machine.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import viteConfig from "../vite.config.js";
import {
  ALLOW_LOOPBACK_API_ENV,
  findLoopbackUrls,
  isLoopbackUrl,
  loopbackApiGuard,
} from "./loopbackApiGuard.js";

const GUARD = "zoiko:no-loopback-api-in-build";

describe("which API addresses count as this machine", () => {
  it.each([
    "http://127.0.0.1:8001", // .env.example's development value
    "http://localhost:8000",
    "http://LOCALHOST:8000",
    "http://localhost.:8000",
    "http://app.localhost:5173",
    "http://127.1:8000", // normalised to 127.0.0.1
    "http://0.0.0.0:8080",
    "http://[::1]:8000",
    "http://[::]:8000",
    "127.0.0.1:8001", // no scheme
    "http://127.0.0.1", // no port
  ])("%s is loopback", (url) => {
    expect(isLoopbackUrl(url)).toBe(true);
  });

  it.each([
    "https://get.zoikostream.com", // .env.mobile
    "https://api.example.com:8443",
    "http://192.168.1.10:8001", // a LAN address is deliberately out of scope
    "http://localhost.example.com:8000",
    "",
    undefined,
    "not a url",
  ])("%s is not", (url) => {
    expect(isLoopbackUrl(url)).toBe(false);
  });
});

describe("the output check", () => {
  it("finds loopback URLs that carry a port, once each", () => {
    const code =
      'var a="http://127.0.0.1:8001",b="http://127.0.0.1:8001";' +
      'c=`ws://localhost:7880`;d="http://[::1]:8000/api";e="http://localhost.:5173"';
    expect(findLoopbackUrls(code)).toEqual([
      "http://127.0.0.1:8001",
      "ws://localhost:7880",
      "http://[::1]:8000",
      "http://localhost.:5173",
    ]);
  });

  it("leaves library fallbacks alone: a bare http://localhost and an SDP address", () => {
    const code =
      'let r=`http://localhost`;new URL(p,"http://localhost");' +
      "o=`IN IP4 127.0.0.1\\r\\n`;q=\"https://get.zoikostream.com\"";
    expect(findLoopbackUrls(code)).toEqual([]);
  });

  const run = (plugin, bundle) => {
    const ctx = { error: vi.fn(), warn: vi.fn() };
    plugin.generateBundle.call(ctx, {}, bundle);
    return ctx;
  };

  it("fails the build when an emitted chunk or text asset names one", () => {
    const ctx = run(loopbackApiGuard(), {
      "assets/index-abc.js": { type: "chunk", code: 'const API="http://127.0.0.1:8001";' },
      "index.html": { type: "asset", source: "<script>fetch('http://localhost:8000/x')</script>" },
      "assets/logo.png": { type: "asset", source: new Uint8Array([0x89, 0x50, 0x4e, 0x47]) },
    });
    expect(ctx.error).toHaveBeenCalledTimes(1);
    const message = ctx.error.mock.calls[0][0];
    expect(message).toContain("assets/index-abc.js: http://127.0.0.1:8001");
    expect(message).toContain("index.html: http://localhost:8000");
    expect(message).toContain(ALLOW_LOOPBACK_API_ENV);
  });

  it("reads text assets that arrive as bytes", () => {
    const ctx = run(loopbackApiGuard(), {
      "manifest.webmanifest": { type: "asset", source: new TextEncoder().encode('{"start_url":"http://127.0.0.1:5173/"}') },
    });
    expect(ctx.error).toHaveBeenCalledTimes(1);
  });

  it("passes a clean bundle", () => {
    const ctx = run(loopbackApiGuard(), {
      "assets/index-abc.js": { type: "chunk", code: "const API=window.location.origin;" },
    });
    expect(ctx.error).not.toHaveBeenCalled();
  });

  it("stands aside for a deliberate local build", () => {
    const ctx = run(loopbackApiGuard({ allow: true }), {
      "assets/index-abc.js": { type: "chunk", code: 'const API="http://127.0.0.1:8001";' },
    });
    expect(ctx.error).not.toHaveBeenCalled();
  });
});

describe("vite.config.js", () => {
  // The guard and Vite both read process.env before the env files, so every case pins
  // VITE_API_URL here: the developer's own .env cannot change the outcome.
  beforeEach(() => vi.stubEnv(ALLOW_LOOPBACK_API_ENV, ""));
  afterEach(() => vi.unstubAllEnvs());

  const configure = (command, mode = "production") => viteConfig({ command, mode });
  const guardOf = (config) => config.plugins.flat().find((p) => p?.name === GUARD);
  const warningOf = (plugin) => {
    const ctx = { warn: vi.fn() };
    plugin.buildStart.call(ctx);
    return ctx.warn.mock.calls[0]?.[0];
  };

  it("a build does not embed a development VITE_API_URL: the bundle calls its own origin", () => {
    vi.stubEnv("VITE_API_URL", "http://127.0.0.1:8001");
    const guard = guardOf(configure("build"));
    expect(process.env.VITE_API_URL).toBe(""); // what Vite then hands the bundle
    expect(guard).toBeDefined();
    expect(warningOf(guard)).toContain("VITE_API_URL=http://127.0.0.1:8001 is a development address");
  });

  it("the mobile build is guarded too", () => {
    vi.stubEnv("VITE_API_URL", "http://localhost:8000");
    expect(guardOf(configure("build", "mobile"))).toBeDefined();
    expect(process.env.VITE_API_URL).toBe("");
  });

  it("a real API address is kept, with no warning", () => {
    vi.stubEnv("VITE_API_URL", "https://get.zoikostream.com");
    const guard = guardOf(configure("build"));
    expect(process.env.VITE_API_URL).toBe("https://get.zoikostream.com");
    expect(warningOf(guard)).toBeUndefined();
  });

  it("unset stays unset (the Docker and CI builds)", () => {
    vi.stubEnv("VITE_API_URL", "");
    expect(guardOf(configure("build"))).toBeDefined();
    expect(process.env.VITE_API_URL).toBe("");
  });

  it("the dev server keeps using the local API, and is not guarded", () => {
    vi.stubEnv("VITE_API_URL", "http://127.0.0.1:8001");
    expect(guardOf(configure("serve", "development"))).toBeUndefined();
    expect(process.env.VITE_API_URL).toBe("http://127.0.0.1:8001");
  });

  it(`${ALLOW_LOOPBACK_API_ENV}=1 keeps the local address for a deliberate local build`, () => {
    vi.stubEnv("VITE_API_URL", "http://127.0.0.1:8001");
    vi.stubEnv(ALLOW_LOOPBACK_API_ENV, "1");
    const guard = guardOf(configure("build"));
    expect(process.env.VITE_API_URL).toBe("http://127.0.0.1:8001");
    expect(warningOf(guard)).toBeUndefined();
  });
});
