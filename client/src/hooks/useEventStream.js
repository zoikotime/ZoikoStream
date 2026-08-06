import { useCallback, useEffect, useRef, useState } from "react";

import { API_BASE } from "../api";

// ONE WebSocket to a live event, carrying every logical channel (chat, participants,
// polls, q&a, announcements, activity, moderator). See server/app/services/bus.py.
//
//   const { status, latency, send } = useEventStream(eventId, onEnvelope);
//
// Handles the three things that decide whether "never refresh the page" is actually true:
//   * reconnect — exponential backoff with jitter, so 500 consoles don't retry in lockstep
//   * liveness  — a ping every 15s; its pong gives the header a real latency number
//   * resync    — the server resends a full snapshot on every connect, so a reconnect
//                 repairs whatever was missed while offline instead of leaving stale panels
// ponytail: raw WebSocket, no socket.io — the server speaks plain JSON frames and the
// browser API already does framing. socket.io would add a client dep AND a server dep.

const PING_MS = 15000;
const MAX_BACKOFF_MS = 15000;
// 1008 = policy violation: the server rejected the token / event / a ban. Retrying that
// just loops, so we stop and surface it.
const FATAL_CODES = new Set([1008]);

const wsUrl = (eventId, token) => {
  const origin = API_BASE.replace(/^http/, "ws").replace(/\/$/, "");
  return `${origin}/api/live/events/${eventId}/ws?token=${encodeURIComponent(token)}`;
};

export default function useEventStream(eventId, onEnvelope) {
  // Read the session once at init: with no token there is nothing to connect to, and
  // starting in "unauthorized" avoids a pointless "connecting" flash.
  const [token] = useState(() => localStorage.getItem("token"));
  const [status, setStatus] = useState(token ? "connecting" : "unauthorized"); // connecting | open | reconnecting | offline | unauthorized
  const [latency, setLatency] = useState(null);
  const [attempt, setAttempt] = useState(0);          // surfaces "retrying…" in the header

  const socket = useRef(null);
  const handler = useRef(onEnvelope);
  // Keep the latest callback without re-running the connect effect (which would drop
  // and re-open the socket on every parent render).
  useEffect(() => {
    handler.current = onEnvelope;
  });

  useEffect(() => {
    if (!eventId || !token) return undefined;

    let closed = false;     // component unmounted / deps changed — stop reconnecting
    let retries = 0;
    let retryTimer;
    let pingTimer;

    const connect = () => {
      const ws = new WebSocket(wsUrl(eventId, token));
      socket.current = ws;

      ws.onopen = () => {
        retries = 0;
        setAttempt(0);
        setStatus("open");
        pingTimer = setInterval(() => {
          if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ action: "ping", t: Date.now() }));
        }, PING_MS);
      };

      ws.onmessage = (e) => {
        let env;
        try {
          env = JSON.parse(e.data);
        } catch {
          return; // a frame we can't parse is a frame we can't act on
        }
        if (env.type === "pong") {
          setLatency(Date.now() - (env.data?.t ?? Date.now()));
          return;
        }
        handler.current?.(env);
      };

      ws.onclose = (e) => {
        clearInterval(pingTimer);
        setLatency(null);
        if (closed) return;
        if (FATAL_CODES.has(e.code)) {
          setStatus("unauthorized");
          return;
        }
        retries += 1;
        setAttempt(retries);
        setStatus(retries > 4 ? "offline" : "reconnecting");
        // Jitter matters at scale: without it every console that dropped on the same
        // server blip reconnects in the same millisecond and knocks it over again.
        const wait = Math.min(1000 * 2 ** (retries - 1), MAX_BACKOFF_MS) * (0.7 + Math.random() * 0.6);
        retryTimer = setTimeout(connect, wait);
      };
    };

    connect();
    return () => {
      closed = true;
      clearTimeout(retryTimer);
      clearInterval(pingTimer);
      socket.current?.close();
      socket.current = null;
    };
  }, [eventId, token]);

  const send = useCallback((action, payload = {}) => {
    const ws = socket.current;
    if (ws?.readyState !== WebSocket.OPEN) return false;
    ws.send(JSON.stringify({ action, payload }));
    return true;
  }, []);

  return { status, latency, attempt, send };
}
