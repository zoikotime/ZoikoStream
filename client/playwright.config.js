// client/playwright.config.js
// Real-browser E2E for the live-streaming path: producer publish, viewer subscribe, the two
// LiveKit identity-collision scenarios (host-watch-page, contributor Backstage monitor), and
// the recording/degraded-state separation. Runs against the ACTUAL frontend + backend +
// LiveKit — nothing here mocks WebRTC, LiveKit, or the streaming API.
//
// Chromium only, with fake camera/mic (--use-fake-device-for-media-stream): this box has no
// real hardware, and CI never will either. Fake devices still negotiate a REAL WebRTC
// connection and publish REAL (synthetic) media into a REAL LiveKit room — only the capture
// source is fake, not the pipeline being tested.
import { defineConfig, devices } from "@playwright/test";

// Matches VITE_API_URL / the frontend's own default dev port pairing (see vite.config.js's
// envDir: '..' — it reads the repo-root .env, which sets VITE_API_URL=http://127.0.0.1:8000).
const FRONTEND_URL = process.env.E2E_FRONTEND_URL || "http://127.0.0.1:5173";
const BACKEND_URL = process.env.E2E_BACKEND_URL || "http://127.0.0.1:8000";

export default defineConfig({
  testDir: "./e2e",
  // Generous, not arbitrary: each dbState() call shells out to a real Python process that
  // opens a real Supabase connection (~2.5s measured directly), several tests make multiple
  // such calls, and the media-transport-dependent assertions each carry their own bounded
  // ~25s wait (waitForMediaOrBlock) on top of that. 60s was cutting into real, non-fabricated
  // wall-clock time this environment genuinely needs — not padding around a bug.
  timeout: 180_000,
  expect: { timeout: 15_000 },
  fullyParallel: false, // these tests share one backend event/room; running serially avoids cross-test collisions
  retries: 0,           // a flaky pass would defeat the point of this suite — see real failures, don't hide them
  reporter: [
    ["list"],
    ["html", { open: "never", outputFolder: "playwright-report" }],
    // json: the media-transport / GCS-capture annotations this suite attaches (see the spec)
    // are the honest record of what this environment could and could not physically verify —
    // the list reporter drops them, so they'd otherwise only exist in a scrollback.
    ["json", { outputFile: "playwright-report/results.json" }],
  ],
  globalSetup: "./e2e/global-setup.js",
  globalTeardown: "./e2e/global-teardown.js",
  // reuseExistingServer: if you already have `npm run dev` / uvicorn running locally, this
  // suite attaches to them instead of spawning a second copy — only spawns its own when
  // nothing answers on the port yet (e.g. a clean CI runner).
  webServer: [
    {
      command:
        "..\\server\\venv\\Scripts\\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000",
      cwd: "../server",
      url: `${BACKEND_URL}/health`,
      reuseExistingServer: true,
      timeout: 30_000,
    },
    {
      // --host 127.0.0.1: Vite's default (bare `localhost`) binds IPv6 loopback only on
      // this machine, so both this health check and every LiveKit/API request the pages
      // make would silently fail against a server that Node considers "up".
      command: "npm run dev -- --host 127.0.0.1 --port 5173 --strictPort",
      url: FRONTEND_URL,
      reuseExistingServer: true,
      timeout: 30_000,
    },
  ],
  use: {
    baseURL: FRONTEND_URL,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "retain-on-failure",
    permissions: ["camera", "microphone"],
  },
  projects: [
    {
      name: "chromium-fake-media",
      use: {
        ...devices["Desktop Chrome"],
        launchOptions: {
          args: [
            "--use-fake-device-for-media-stream",
            "--use-fake-ui-for-media-stream", // auto-grants the getUserMedia prompt instead of blocking on it
            "--disable-features=IsolateOrigins,site-per-process", // multiple LiveKit peer connections across tabs in one process, no cross-origin isolation issues
          ],
        },
      },
    },
  ],
});
