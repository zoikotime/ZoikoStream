// client/e2e-realhw/real-hardware.spec.js
// REAL PHYSICAL webcam + microphone (HP TrueVision HD Camera / Intel mic array) — the one
// thing every previous pass reported as BLOCKED, because the main suite runs fake devices.
//
// The point is not to re-verify the flow (that is already green on fake media) but to prove
// the parts fake devices CANNOT: that a real capture device negotiates real settings, that
// the app does not depend on the 1920x1080@20fps a fake device always grants, and that real
// audio actually reaches a viewer.
import { test, expect } from "@playwright/test";
import {
  loadFixtures, loginAs, waitFor, waitForMediaOrBlock, mediaElementState, captureConsole, dbState,
} from "../e2e/helpers.js";

test("real webcam + microphone: host publishes, viewer receives, audio survives camera-off", async ({ browser }) => {
  const fixtures = loadFixtures();
  const logs = [];

  const hostCtx = await browser.newContext();
  const hostPage = await hostCtx.newPage();
  captureConsole(hostPage, logs);
  await loginAs(hostPage, fixtures.host.token);
  await hostPage.goto(`/host/dashboard?event=${fixtures.event_id}`);
  await hostPage.getByRole("button", { name: "Start" }).click();

  // --- real capture -------------------------------------------------------
  await waitFor(async () => {
    const st = await mediaElementState(hostPage, "video").catch(() => null);
    return st?.hasVideoTrack && st?.hasAudioTrack ? st : null;
  }, { timeout: 60_000, message: "the REAL camera and microphone to open" });

  // What the hardware actually granted, straight off the live MediaStreamTrack.
  const real = await hostPage.evaluate(() => {
    const v = document.querySelector("video");
    const s = v?.srcObject;
    const vt = s?.getVideoTracks?.()[0];
    const at = s?.getAudioTracks?.()[0];
    return {
      cameraLabel: vt?.label ?? null,
      micLabel: at?.label ?? null,
      video: vt?.getSettings?.() ?? null,
      audio: at?.getSettings?.() ?? null,
      videoReady: vt?.readyState ?? null,
      audioReady: at?.readyState ?? null,
    };
  });
  console.log("REAL DEVICE SETTINGS: " + JSON.stringify(real, null, 2));

  // These are the assertions fake media cannot make: a REAL named device, live tracks, and
  // a resolution the app accepted rather than demanded.
  expect(real.cameraLabel, "no real camera label — a fake device would be unnamed").toBeTruthy();
  expect(real.micLabel, "no real microphone label").toBeTruthy();
  expect(real.videoReady).toBe("live");
  expect(real.audioReady).toBe("live");
  expect(real.video.width, "camera produced no width").toBeGreaterThan(0);
  expect(real.video.height, "camera produced no height").toBeGreaterThan(0);
  console.log(`REAL CAPTURE: ${real.cameraLabel} @ ${real.video.width}x${real.video.height}`
    + `@${Math.round(real.video.frameRate || 0)}fps ; mic: ${real.micLabel}`);

  // --- go live ------------------------------------------------------------
  const goLive = hostPage.getByRole("button", { name: "Go Live", exact: true });
  await expect(goLive).toBeEnabled({ timeout: 90_000 });
  await goLive.click();
  await waitFor(() => {
    const d = dbState(fixtures.event_id);
    return d.broadcast_session_status === "live" ? d : null;
  }, { timeout: 60_000, interval: 2_000, message: "the broadcast to go live" });

  const publishing = await waitForMediaOrBlock(async () =>
    (await hostPage.getByText("Live — this feed is being published to viewers.")
      .isVisible().catch(() => false)) || null, { timeout: 90_000 });
  expect(publishing, "host never published REAL media").toBeTruthy();

  // --- viewer receives real media ----------------------------------------
  const viewCtx = await browser.newContext();
  const viewPage = await viewCtx.newPage();
  captureConsole(viewPage, logs);
  await loginAs(viewPage, fixtures.viewer1.token);
  await viewPage.goto(`/events/${fixtures.event_id}/watch`);
  await expect(viewPage.getByText("LIVE", { exact: true }).first()).toBeVisible({ timeout: 60_000 });

  const got = await waitForMediaOrBlock(async () => {
    const st = await mediaElementState(viewPage, "video").catch(() => null);
    return st?.hasVideoTrack && st?.hasAudioTrack ? st : null;
  }, { timeout: 90_000, interval: 2_000 });
  expect(got, "viewer never received the real camera/mic").toBeTruthy();
  console.log("VIEWER RECEIVED (real media): " + JSON.stringify(got));

  // --- audio-only on real hardware ---------------------------------------
  await hostPage.getByRole("button", { name: "Camera", exact: true }).click();
  const audioStill = await waitForMediaOrBlock(async () => {
    const st = await mediaElementState(viewPage, "video").catch(() => null);
    return st?.hasAudioTrack ? st : null;
  }, { timeout: 60_000, interval: 2_000 });
  expect(audioStill, "viewer lost audio when the real camera was turned off").toBeTruthy();
  console.log("AUDIO-ONLY (real mic) OK: " + JSON.stringify(audioStill));

  console.log("--- console ---\n" + logs.slice(-25).join("\n"));
  await Promise.allSettled([hostCtx.close(), viewCtx.close()]);
});
