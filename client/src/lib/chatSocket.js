// Thin wrapper around the Socket.IO connection shared by the Host and Viewer chat
// panels. Mirrors server/app/sockets.py's event contract exactly.
import { io } from "socket.io-client";

const SOCKET_URL = import.meta.env.VITE_API_URL || "http://localhost:8000";

// identity is either { token } (logged-in host/user) or { displayName, email } (guest
// viewer -- email is only needed for registration_required events, see EventWatch.jsx).
export function connectEventChat(streamId, identity, { onHistory, onNew, onUpdated, onDeleted, onError }) {
  const socket = io(SOCKET_URL, { transports: ["websocket", "polling"] });

  socket.on("connect", () => {
    socket.emit(
      "join",
      { stream_id: streamId, token: identity.token, display_name: identity.displayName, email: identity.email },
      (res) => {
        if (res?.error) onError?.(res.error);
        else onHistory?.(res.history || []);
      }
    );
  });

  socket.on("chat:new", (msg) => onNew?.(msg));
  socket.on("chat:updated", (msg) => onUpdated?.(msg));
  socket.on("chat:deleted", ({ id }) => onDeleted?.(id));

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
