// client/playwright.realhw.config.js
// Real PHYSICAL camera and microphone — NOT the fake devices the main suite uses.
//
// Separate file, deliberately: playwright.config.js is the verified configuration for the
// live-streaming and hardening suites and is left untouched. The only difference here is the
// absence of --use-fake-device-for-media-stream, so Chromium opens the machine's actual
// webcam and microphone. `permissions` still grants access without a prompt, which is a
// permission grant, not a fake device — the media is real.
import base from "./playwright.config.js";

export default {
  ...base,
  testDir: "./e2e-realhw",
  timeout: 300_000,
  reporter: [["list"]],
  projects: [
    {
      name: "chromium-real-media",
      use: {
        ...base.use,
        // HEADED: headless Chromium refuses real device capture outright — measured, it
        // fails getUserMedia with "Not supported" regardless of permissions. A real webcam
        // needs a real browser window.
        headless: false,
        launchOptions: {
          args: [
            // Autoplay so the viewer's <video> can start without a gesture; this affects
            // playback policy only, never the capture source.
            "--autoplay-policy=no-user-gesture-required",
          ],
        },
      },
    },
  ],
};
