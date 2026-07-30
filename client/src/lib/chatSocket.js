// Thin wrapper around the Socket.IO connection shared by the Host and Viewer chat
// panels. Mirrors server/app/sockets.py's event contract exactly.
import { io } from "socket.io-client";

const SOCKET_URL = import.meta.env.VITE_API_URL || "http://localhost:8000";

// identity is either { token } (logged-in host/user) or { displayName, email } (guest
// viewer -- email is only needed for registration_required events, see EventWatch.jsx).
export function connectEventChat(
  streamId,
  identity,
  {
    onHistory, onNew, onUpdated, onDeleted, onError, onHandsQueue, onRoster,
    onQaInit, onQaNew, onQaUpdated, onQaDeleted,
    onPollsInit, onPollNew, onPollUpdated, onPollDeleted,
  }
) {
  const socket = io(SOCKET_URL, { transports: ["websocket", "polling"] });

  socket.on("connect", () => {
    socket.emit(
      "join",
      { stream_id: streamId, token: identity.token, display_name: identity.displayName, email: identity.email },
      (res) => {
        if (res?.error) onError?.(res.error);
        else {
          onHistory?.(res.history || []);
          if (res.stage) {
            onHandsQueue?.(res.stage.hands || []);
            onRoster?.(res.stage.roster || []);
          }
          onQaInit?.(res.qa || []);
          onPollsInit?.(res.polls || []);
        }
      }
    );
  });

  socket.on("chat:new", (msg) => onNew?.(msg));
  socket.on("chat:updated", (msg) => onUpdated?.(msg));
  socket.on("chat:deleted", ({ id }) => onDeleted?.(id));
  socket.on("stage:hands", (hands) => onHandsQueue?.(hands));
  socket.on("stage:roster", (roster) => onRoster?.(roster));
  socket.on("qa:new", (q) => onQaNew?.(q));
  socket.on("qa:updated", (q) => onQaUpdated?.(q));
  socket.on("qa:deleted", ({ id }) => onQaDeleted?.(id));
  socket.on("poll:new", (p) => onPollNew?.(p));
  socket.on("poll:updated", (p) => onPollUpdated?.(p));
  socket.on("poll:deleted", ({ id }) => onPollDeleted?.(id));

  return socket;
}

export function sendChatMessage(socket, text) {
  return new Promise((resolve, reject) => {
    socket.emit("chat:send", { text }, (res) => {
      if (res?.error) reject(new Error(res.error));
      else resolve(res);
    });
  });
}

export function raiseHand(socket) {
  return new Promise((resolve, reject) => {
    socket.emit("stage:raise_hand", {}, (res) => {
      if (res?.error) reject(new Error(res.error));
      else resolve(res);
    });
  });
}

export function lowerHand(socket) {
  return new Promise((resolve, reject) => {
    socket.emit("stage:lower_hand", {}, (res) => {
      if (res?.error) reject(new Error(res.error));
      else resolve(res);
    });
  });
}

export function askQuestion(socket, text) {
  return new Promise((resolve, reject) => {
    socket.emit("qa:ask", { text }, (res) => {
      if (res?.error) reject(new Error(res.error));
      else resolve(res);
    });
  });
}

export function voteQuestion(socket, questionId) {
  return new Promise((resolve, reject) => {
    socket.emit("qa:vote", { question_id: questionId }, (res) => {
      if (res?.error) reject(new Error(res.error));
      else resolve(res);
    });
  });
}

export function votePoll(socket, pollId, optionId) {
  return new Promise((resolve, reject) => {
    socket.emit("poll:vote", { poll_id: pollId, option_id: optionId }, (res) => {
      if (res?.error) reject(new Error(res.error));
      else resolve(res);
    });
  });
}
