// When a speaker on the Backstage page is genuinely presenting — the condition that keeps
// their sign-in alive without them touching the mouse (auth/useSessionKeeper useSessionHold).
//
// Built only from state the page already has, and nothing that merely shows the page is open:
//
//   on stage       the host brought them live: contributor state "live", or "muted" (the
//                  host silenced their microphone; they are still on stage and on camera).
//   published      LiveKit ACKNOWLEDGED the track (useLiveKitPublish publishedAudio /
//                  publishedVideo — derived from the room's real publications, not from a
//                  connect call that may have published nothing).
//   switched on    the speaker has not turned that device off themselves, and for audio the
//                  host has not muted them.
//
// So a speaker counts as presenting while on stage with their camera, or their unmuted
// microphone, actually reaching the room. Being in the waiting area, being sent back off
// stage, turning both devices off, or losing the publish connection all end it. Socket
// heartbeats, the return feed and the event merely being live never count.
export function isActivelyPresenting({ contributorState, publishedAudio, publishedVideo, micOn, cameraOn }) {
  const onStage = contributorState === "live" || contributorState === "muted";
  if (!onStage) return false;
  const speaking = Boolean(publishedAudio && micOn && contributorState !== "muted");
  const onCamera = Boolean(publishedVideo && cameraOn);
  return speaking || onCamera;
}
