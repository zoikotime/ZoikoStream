---
source_id: viewer-playback-help
title: Viewer Help - Watching and Taking Part
source_type: help
canonical_path: "/events/:eventId/watch"
risk_class: LOW
authority_rank: 90
version: "2026.10.1"
origin_files: [client/src/pages/watch/EventWatch.jsx, client/src/pages/watch/viewerLanguage.jsx, client/src/pages/watch/controlPlaneNotice.js, client/src/components/watch/VideoPlayer.jsx, client/src/components/watch/WatchPanel.jsx, client/src/components/watch/WatchHeader.jsx, client/src/components/watch/ReactionBar.jsx, client/src/components/watch/IdentifyForm.jsx]
author: guide-engineering
owner_role: support
# RBAC §5-8 approval evidence. Not yet recorded: production ingest refuses this
# source until a designated approver signs off (see knowledge/governance.py).
approved_by: pending-approval
approved_at: null
---

# Viewer Help: Watching and Taking Part

## When the stream is live

While the event is live, the page shows a "Live now" badge, and the read-aloud instruction is "The broadcast is live."

## No sound when the stream starts

If the stream starts muted, the player shows "Click anywhere to unmute". Click the player to turn the sound on.

## Waiting for the host's video

If the event is live but the host's camera is not on yet, the player shows "Waiting for the host's camera…".

## Paused or reconnecting streams

- If the host pauses the broadcast, the page shows "The service is paused. Please stay on this page."
- If the video connection drops, the page shows "Reconnecting… The video will continue automatically."

In both cases, stay on the page.

## Chat and live updates connection

If the connection for live chat and updates drops, the page may show:

- "Reconnecting to live chat and updates… Video and audio are unaffected."
- "Live connection unavailable — still retrying in the background. Video and audio are unaffected."
- "Live chat and updates are unavailable for this session. Refresh the page, or sign in again."

## Player settings

Select the Settings button on the player to change:

- Quality: Auto, or a specific quality when the stream offers more than one. If only one is available, the player says "This stream is being sent as a single rendition, so only Auto is available." Before the host's video arrives, it says "Waiting for the host's video. Quality options appear once the stream starts."
- Playback speed: 0.5x, 0.75x, Normal, 1.25x, 1.5x or 2x.
- Picture-in-Picture: watch while using other apps.

The player also has play/pause, mute/unmute and volume controls.

## Watching full screen

Select the Fullscreen button in the player controls to watch full screen. Select "Exit fullscreen", or press Esc, to return to the event page.

## Chat, Q&A and polls

Depending on what the host enables, the event page can include Chat, Q&A and Polls tabs. To take part, enter your name and email when asked:

- "Enter your name and email to join the chat."
- "Enter your name and email to ask a question."
- "Enter your name and email to vote in polls."

In Q&A you can ask a question and upvote questions. Answered questions are marked as answered by the host.

If the host turns on slow mode, chat messages are limited to one every set number of seconds. Slow mode is set by the host.

## Reactions and raising your hand

When the host enables them, you can send emoji reactions and use "Raise Hand". Select it again to lower your hand.

## Being invited to speak

If the host invites you on stage, the player shows "You're invited to speak" (or that the host asked you to unmute) and "Turn your microphone on when you're ready. Nobody can turn it on for you."

1. Select "Enable microphone" to speak, or "Not now" to decline.
2. While your microphone is on, the player shows "You're on stage — the host and audience can hear you."
3. Use the microphone button on the player to mute or unmute yourself.

## Sharing, calendar and following

The event header offers:

- Share: copies the event link ("Link copied").
- Add to calendar: downloads a calendar invite.
- Follow: follows the event on this device. Select "Following" to stop following.

## Light and dark theme

Use the sun or moon button in the header to switch between light and dark theme.

## Leaving an event

Select "Leave Event" in the header to leave.
