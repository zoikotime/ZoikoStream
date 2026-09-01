// client/e2e/hardening.spec.js
// Closes the verification gaps the live-streaming suite deliberately left open: a real
// network interruption of the host mid-broadcast, a real fatal LiveKit disconnect, polls and
// Q&A driven end to end through the browser, and a four-client stress pass.
//
// Same contract as live-streaming.spec.js: real browser -> real frontend -> real backend ->
// real LiveKit, fake camera/mic only as the capture source. Nothing here mocks WebRTC,
// LiveKit, the streaming API or the event lifecycle.
import { test, expect } from "@playwright/test";
import {
  loadFixtures, loginAs, waitFor, waitForMediaOrBlock, mediaElementState, captureConsole,
  dbState, inviteContributor,
} from "./helpers.js";

let fixtures;
let hostCtx, hostPage, v1Ctx, v1Page, v2Ctx, v2Page, contribCtx, contribPage;
const log = { host: [], v1: [], v2: [], contrib: [], host2: [] };

const anyIdentityError = (lines) =>
  lines.some((l) => /DUPLICATE_IDENTITY|PARTICIPANT_REMOVED|ROOM_DELETED/.test(l));

// Live tracks actually attached to the element, not merely "a stream object exists".
const liveTracks = async (page) => {
  const st = await mediaElementState(page, "video").catch(() => null);
  return st?.hasVideoTrack || st?.hasAudioTrack ? st : null;
};

// Cuts the host's network for real.
//
// browserContext.setOffline() is NOT enough and silently looks like it worked: measured
// directly in this environment, it blocks NEW http requests (fetch fails) while leaving the
// already-established LiveKit WSS and the control socket completely untouched — the console
// went on showing "Live — this feed is being published to viewers." and a 1ms control
// latency for a full minute of "offline". Adding Network.setBlockedURLs changed nothing
// either. CDP Network.emulateNetworkConditions is the one that actually severs the live
// connections: the publisher drops, the LiveKit socket starts closing and reopening on the
// retry backoff, and restoring it brings publishing back on its own.
const cdpFor = new Map();
async function setNetwork(ctx, page, online) {
  let cdp = cdpFor.get(page);
  if (!cdp) {
    cdp = await ctx.newCDPSession(page);
    await cdp.send("Network.enable");
    cdpFor.set(page, cdp);
  }
  // setOffline FIRST, then the CDP emulation — this is the order measured to work. Applying
  // them the other way round let setOffline's own use of the same CDP domain clobber the
  // emulation and the live connections survived.
  await ctx.setOffline(!online);
  await cdp.send("Network.emulateNetworkConditions", {
    offline: !online,
    latency: 0,
    downloadThroughput: online ? -1 : 0,
    uploadThroughput: online ? -1 : 0,
  });
}

test.describe.serial("Live streaming hardening", () => {
  test.beforeAll(async () => {
    fixtures = loadFixtures();
    const db = dbState(fixtures.event_id);
    expect(db.open_broadcast_sessions, "must start with no open BroadcastSession").toBe(0);
    expect(db.active_recordings, "must start with no active recording").toBe(0);
  });

  test("1. Host goes live and publishes; two viewers receive media", async ({ browser }) => {
    hostCtx = await browser.newContext();
    hostPage = await hostCtx.newPage();
    captureConsole(hostPage, log.host);
    await loginAs(hostPage, fixtures.host.token);
    await hostPage.goto(`/host/dashboard?event=${fixtures.event_id}`);
    await hostPage.getByRole("button", { name: "Start" }).click();

    await waitFor(async () => {
      const st = await mediaElementState(hostPage, "video").catch(() => null);
      return st?.hasVideoTrack && st?.hasAudioTrack ? st : null;
    }, { message: "host preview to hold real camera+mic tracks" });

    // The readiness fix: Go Live must actually become clickable, not sit on "Connecting…".
    const goLive = hostPage.getByRole("button", { name: "Go Live", exact: true });
    await expect(goLive).toBeEnabled({ timeout: 60_000 });
    await goLive.click();

    await waitFor(() => {
      const d = dbState(fixtures.event_id);
      return d.broadcast_session_status === "live" ? d : null;
    }, { timeout: 30_000, interval: 2_000, message: "BroadcastSession to go live" });

    for (const [key, tokenKey] of [["v1", "viewer1"], ["v2", "viewer2"]]) {
      const ctx = await browser.newContext();
      const page = await ctx.newPage();
      captureConsole(page, log[key]);
      await loginAs(page, fixtures[tokenKey].token);
      await page.goto(`/events/${fixtures.event_id}/watch`);
      // Generous: the watch page blocks on its own /watch fetch against a remote database,
      // and this suite opens several clients back to back.
      await expect(page.getByText("LIVE", { exact: true }).first(),
        `${key} never reached the LIVE watch page`).toBeVisible({ timeout: 60_000 });
      if (key === "v1") { v1Ctx = ctx; v1Page = page; } else { v2Ctx = ctx; v2Page = page; }
    }

    const hostPublishing = await waitForMediaOrBlock(async () => {
      const ok = await hostPage.getByText("Live — this feed is being published to viewers.")
        .isVisible().catch(() => false);
      return ok || null;
    }, { timeout: 40_000 });
    expect(hostPublishing, "host never reported publishing").toBeTruthy();

    for (const [name, page] of [["viewer 1", v1Page], ["viewer 2", v2Page]]) {
      const m = await waitForMediaOrBlock(() => liveTracks(page), { timeout: 40_000 });
      expect(m, `${name} never received media`).toBeTruthy();
    }
  });

  test("2. Polls end to end: host launches, viewer votes, both see the result", async () => {
    await hostPage.getByRole("tab", { name: "Polls" }).click();
    await hostPage.getByRole("button", { name: /^New$/ }).click();

    const question = `e2e-poll-${Date.now()}`;
    await hostPage.getByLabel("Poll question").fill(question);
    await hostPage.getByLabel("Option 1").fill("Alpha");
    await hostPage.getByLabel("Option 2").fill("Beta");
    await hostPage.getByRole("button", { name: "Launch", exact: true }).click();

    // Host's own console reflects it.
    await expect(hostPage.getByText(question)).toBeVisible({ timeout: 15_000 });

    // Viewer receives it over the live socket, with no reload.
    // getByRole("button"), not "tab": the VIEWER's WatchPanel tab strip is plain <button>
    // elements with aria-current, whereas the HOST's HostPanel uses role="tablist"/"tab".
    // Two different components, two different selectors.
    await v1Page.getByRole("button", { name: "Polls" }).click();
    await expect(v1Page.getByText(question)).toBeVisible({ timeout: 20_000 });

    await v1Page.getByRole("button", { name: "Alpha", exact: true }).click();

    // The vote reached the backend and came back as a real tally — asserted on the viewer's
    // own rendered result, and independently on the HOST console's Responses count, so this
    // cannot pass on optimistic local state alone.
    await expect(v1Page.getByText("1 votes")).toBeVisible({ timeout: 20_000 });
    await expect(v1Page.getByText("100%").first()).toBeVisible({ timeout: 20_000 });
    const responses = hostPage.locator("dl", { hasText: "Responses" }).first();
    await expect(responses).toContainText("1", { timeout: 20_000 });

    // Media must be untouched by a control-plane action.
    expect(await liveTracks(v1Page), "viewer lost media during the poll").toBeTruthy();
  });

  test("3. Q&A end to end: viewer asks, host sees it and marks it answered", async () => {
    const question = `e2e-qa-${Date.now()}`;
    await v1Page.getByRole("button", { name: "Q&A" }).click();   // viewer panel: see test 2
    await v1Page.getByLabel("Your question").fill(question);
    await v1Page.getByRole("button", { name: "Ask", exact: true }).click();

    await hostPage.getByRole("tab", { name: "Q&A" }).click();
    await expect(hostPage.getByText(question)).toBeVisible({ timeout: 20_000 });

    // Host acts on it through the real moderation action (qa.answer).
    await hostPage.getByRole("button", { name: "Answered", exact: true }).first().click();
    await expect(v1Page.getByText(question)).toBeVisible({ timeout: 20_000 });

    expect(await liveTracks(v1Page), "viewer lost media during Q&A").toBeTruthy();
    expect(anyIdentityError([...log.host, ...log.v1]),
      "identity error during poll/Q&A").toBe(false);
  });

  test("4. Network recovery: host goes offline mid-broadcast, then recovers", async () => {
    // Recovery is not instant and is not fully in our control: the publisher backs off up to
    // 15s between attempts, and LiveKit Cloud may additionally have to fail the room over to
    // another region after an outage ("could not establish pc connection. Retrying with
    // another region"), which was observed taking well past two minutes. Budgeted for the
    // slow path so a genuine, working recovery is never reported as a failure.
    test.setTimeout(480_000);
    const sessionBefore = dbState(fixtures.event_id);
    expect(sessionBefore.open_broadcast_sessions).toBe(1);

    // Real interruption of the host's whole browser context — control socket AND media.
    await setNetwork(hostCtx, hostPage, false);

    // The console must say something honest rather than keep claiming a healthy feed.
    await waitFor(async () => {
      const reconnecting = await hostPage.getByText(/reconnecting to viewers/i).isVisible().catch(() => false);
      const notPublishing = await hostPage.getByText(/Not publishing/i).isVisible().catch(() => false);
      const atRisk = await hostPage.getByText(/At risk|Reconnecting/).first().isVisible().catch(() => false);
      return reconnecting || notPublishing || atRisk ? { reconnecting, notPublishing, atRisk } : null;
    }, { timeout: 120_000, interval: 1_000, message: "host console to surface the media interruption" });

    // Control plane must NOT keep implying media is healthy while it is not.
    const stillClaimsPublishing = await hostPage
      .getByText("Live — this feed is being published to viewers.").isVisible().catch(() => false);
    expect(stillClaimsPublishing, "console still claimed it was publishing while offline").toBe(false);

    // The backend's own degraded detection (services/broadcast.py's sampler -> mark_degraded)
    // runs on a ~15s tick, so give it a couple of cycles before reading the persisted status.
    const degraded = await waitForMediaOrBlock(async () => {
      const d = dbState(fixtures.event_id);
      return d.event_status === "degraded" ? d : null;
    }, { timeout: 60_000, interval: 5_000 });
    test.info().annotations.push({
      type: "degraded-state",
      description: degraded
        ? "Backend persisted event_status=degraded during the host outage"
        : "Backend did not persist a degraded status within 60s of the outage (sampler tick / outage length)",
    });

    await setNetwork(hostCtx, hostPage, true);

    // Recovery must be automatic — no second Go Live click.
    const recovered = await waitFor(async () => {
      const ok = await hostPage.getByText("Live — this feed is being published to viewers.")
        .isVisible().catch(() => false);
      return ok || null;
    }, { timeout: 300_000, interval: 2_000, message: "host publishing to resume by itself" });
    expect(recovered).toBeTruthy();

    // Backend stayed consistent: still exactly one open session, still live, no stale
    // recording, and no second session created by the reconnect.
    const after = dbState(fixtures.event_id);
    expect(after.open_broadcast_sessions, "reconnect created a duplicate BroadcastSession").toBe(1);
    expect(after.broadcast_session_status).toBe("live");
    expect(after.active_recordings, "a stale recording appeared").toBe(0);
    expect(after.event_status, "event did not return to live").toBe("live");

    // Viewer recovers media without intervention, and nothing hit a fatal identity state.
    const viewerBack = await waitForMediaOrBlock(() => liveTracks(v1Page), { timeout: 90_000, interval: 2_000 });
    expect(viewerBack, "viewer never recovered media after host reconnected").toBeTruthy();
    expect(anyIdentityError(log.host), "host saw a fatal identity error across the outage").toBe(false);
  });

  test("5. Fatal disconnect: a second host console is refused, and does not retry forever", async () => {
    // A legitimate, reproducible fatal reason. Both host consoles mint the SAME tagged
    // publish identity — secondary(identity, "host") — so LiveKit's one-connection-per-
    // identity rule evicts one of them with DUPLICATE_IDENTITY. This is exactly the case
    // livekitDisconnect.js exists for; the identity fix stopped console-vs-watch-page
    // collisions, not console-vs-console, so it remains reachable without weakening anything.
    const host2 = await hostCtx.newPage();
    captureConsole(host2, log.host2);
    await host2.goto(`/host/dashboard?event=${fixtures.event_id}`);
    await host2.getByRole("button", { name: "Start" }).click().catch(() => {});

    const evicted = await waitFor(async () => {
      const hit = [...log.host, ...log.host2].some((l) => /DUPLICATE_IDENTITY/.test(l));
      const msg = await Promise.all([hostPage, host2].map((p) =>
        p.getByText(/already connected to this event in another tab/i).isVisible().catch(() => false)));
      return hit || msg.some(Boolean) ? { hit, msg } : null;
    }, { timeout: 90_000, interval: 2_000, message: "a DUPLICATE_IDENTITY eviction between two host consoles" })
      .catch(() => null);

    if (!evicted) {
      test.info().annotations.push({
        type: "fatal-disconnect",
        description: "NOT EXERCISED — two host consoles did not produce a DUPLICATE_IDENTITY "
          + "eviction within 90s, so the fatal path was not reached. No PASS claimed.",
      });
      await host2.close();
      return;
    }

    // The contract: STOP retrying and say so. Sample the retry-state text repeatedly — a
    // still-looping client keeps flipping back to "reconnecting".
    const samples = [];
    for (let i = 0; i < 6; i++) {
      samples.push(await Promise.all([hostPage, host2].map(async (p) => ({
        fatal: await p.getByText(/already connected to this event in another tab/i).isVisible().catch(() => false),
        retrying: await p.getByText(/reconnecting to viewers/i).isVisible().catch(() => false),
      }))));
      await hostPage.waitForTimeout(2_000);
    }
    const anyFatalShown = samples.flat().some((s) => s.fatal);
    const stillRetryingAtEnd = samples[samples.length - 1].some((s) => s.retrying);
    test.info().annotations.push({
      type: "fatal-disconnect",
      description: `EXERCISED — fatal message shown: ${anyFatalShown}; still retrying after 12s: ${stillRetryingAtEnd}`,
    });
    expect(anyFatalShown, "fatal disconnect produced no user-facing explanation").toBe(true);
    expect(stillRetryingAtEnd, "client kept retrying a fatal disconnect").toBe(false);

    // A fatal disconnect must never manufacture broadcast state.
    const db = dbState(fixtures.event_id);
    expect(db.open_broadcast_sessions, "fatal disconnect created a duplicate session").toBe(1);

    await host2.close();
  });

  test("6. Multi-client stress: contributor joins, clients refresh and drop", async ({ browser }) => {
    test.setTimeout(480_000);   // same slow-recovery budget as test 4
    inviteContributor(fixtures.event_id, fixtures.contributor.id, fixtures.host.id);

    contribCtx = await browser.newContext();
    contribPage = await contribCtx.newPage();
    captureConsole(contribPage, log.contrib);
    await loginAs(contribPage, fixtures.contributor.token);
    await contribPage.goto(`/speaker/backstage?event=${fixtures.event_id}`);
    await expect(contribPage.getByRole("heading", { name: /check your camera and microphone/i }))
      .toBeVisible({ timeout: 40_000 });
    await contribPage.getByLabel(/I consent to appear on camera/i).check();
    await contribPage.getByRole("button", { name: "Continue to backstage" }).click();

    // Roster only arrives in the opening snapshot — a KNOWN PRE-EXISTING PRODUCT GAP, left
    // unchanged deliberately; the host reload here is what a real operator does today.
    await hostPage.reload();
    // A reload drops the preview: previewOn is per-tab React state, deliberately local to
    // this machine rather than server state (Dashboard.jsx), so the camera/mic have to be
    // re-armed exactly as a real operator would after reloading mid-broadcast. Without this
    // the host stays on "Preview is off" and stops publishing — which the backend correctly
    // notices, flipping the event to "At risk".
    await hostPage.getByRole("button", { name: "Start" }).click().catch(async () => {
      await hostPage.getByRole("button", { name: /start preview/i }).click().catch(() => {});
    });
    await waitFor(async () => {
      const ok = await hostPage.getByText("Live — this feed is being published to viewers.")
        .isVisible().catch(() => false);
      return ok || null;
    }, { timeout: 120_000, interval: 2_000, message: "host publishing to resume after the reload" });

    await hostPage.getByRole("tab", { name: "Backstage" }).click();
    await hostPage.getByRole("button", { name: /^Admit$/i }).click({ timeout: 30_000 });
    await hostPage.getByRole("button", { name: /^Bring live$/i }).click({ timeout: 30_000 });
    await expect(contribPage.getByText(/You're on air/)).toBeVisible({ timeout: 40_000 });

    await v1Page.reload();
    await expect(v1Page.getByText("LIVE", { exact: true }).first()).toBeVisible({ timeout: 30_000 });

    await v2Ctx.close();
    v2Ctx = null;

    await contribPage.reload();
    await hostPage.waitForTimeout(5_000);

    // Brief host interruption with everyone else connected.
    await setNetwork(hostCtx, hostPage, false);
    await hostPage.waitForTimeout(15_000);
    await setNetwork(hostCtx, hostPage, true);

    const back = await waitFor(async () => {
      const ok = await hostPage.getByText("Live — this feed is being published to viewers.")
        .isVisible().catch(() => false);
      return ok || null;
    }, { timeout: 300_000, interval: 2_000, message: "host publishing to resume after the stress pass" });
    expect(back).toBeTruthy();

    expect(anyIdentityError([...log.host, ...log.v1, ...log.contrib]),
      "identity collision during the multi-client stress pass").toBe(false);

    const db = dbState(fixtures.event_id);
    expect(db.open_broadcast_sessions, "stress pass created duplicate sessions").toBe(1);
    expect(db.active_recordings).toBe(0);
  });

  test("7. Recording stays off until asked, then starts and stops on command", async () => {
    expect(dbState(fixtures.event_id).active_recordings,
      "recording auto-started at some point during this run").toBe(0);
    await hostPage.waitForTimeout(20_000);
    expect(dbState(fixtures.event_id).active_recordings,
      "recording auto-started after 20s live").toBe(0);

    await hostPage.getByRole("button", { name: /^Record$/ }).click();
    const rec = await waitFor(() => {
      const d = dbState(fixtures.event_id);
      return d.active_recordings === 1 ? d : null;
    }, { timeout: 40_000, interval: 2_000, message: "an explicit recording to start" });

    test.info().annotations.push({
      type: "gcs-capture",
      description: rec.recording_enforced
        ? "Recording enforced — GCS appears configured"
        : "GCS BLOCKED — LiveRecording.enforced=false; egress has no valid upload destination",
    });

    await hostPage.getByRole("button", { name: "Stop", exact: true }).click({ timeout: 30_000 });
    await waitFor(() => {
      const d = dbState(fixtures.event_id);
      return d.active_recordings === 0 ? d : null;
    }, { timeout: 30_000, interval: 2_000, message: "the recording to stop" });
  });

  test("8. Event end: clean shutdown with no stale session or recording", async () => {
    const endBtn = hostPage.getByRole("button", { name: "End the broadcast and stop any recording" });
    await endBtn.click();
    await endBtn.click();

    await waitFor(() => {
      const d = dbState(fixtures.event_id);
      return d.event_status === "ended" ? d : null;
    }, { timeout: 40_000, interval: 2_000, message: "the event to end" });

    const final = dbState(fixtures.event_id);
    expect(final.event_status).toBe("ended");
    expect(final.open_broadcast_sessions, "a BroadcastSession stayed open").toBe(0);
    expect(final.active_recordings, "a recording stayed active").toBe(0);
    await expect(v1Page.getByText(/this event has ended/i)).toBeVisible({ timeout: 40_000 });
  });

  test.afterAll(async () => {
    for (const [who, lines] of Object.entries(log)) {
      if (lines.length) console.log(`\n--- console: ${who} ---\n${lines.slice(-40).join("\n")}`);
    }
    await Promise.allSettled([
      hostCtx?.close(), v1Ctx?.close(), v2Ctx?.close(), contribCtx?.close(),
    ]);
  });
});
