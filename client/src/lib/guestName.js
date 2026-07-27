// Shared across the viewer chat panel and the video player's raise-hand control so a
// guest is only ever asked for their name once per browser, and both features agree on
// the same identity (see services/stage.resolve_identity on the backend).
import { useState } from "react";

const KEY = "chat_guest_name";

export function useGuestName() {
  const [name, setName] = useState(() => localStorage.getItem(KEY) || "");
  const save = (n) => {
    localStorage.setItem(KEY, n);
    setName(n);
  };
  return [name, save];
}
