// The Live Producer Console's announcement queue: one record, one entry.
//
// Production report: one scheduled announcement showed twice — "Sent 10:20 · 1 connected"
// beside an identical entry still showing the pending clock. The server keeps ONE record and
// broadcasts it as `announcement.new` twice: when it is created (no sent_at) and when the
// scheduler sends it. The reducer prepended both. It now updates an id it already lists.
// (The server side — one record, sent once, even with several scheduler workers — is
// server/test_live_announcements.py.)
import { describe, expect, it } from "vitest";
import { INITIAL_LIVE_STATE, reducer } from "./useLiveEvent";

const env = (channel, type, data) => ({ channel, type, data });
const start = (announcements = []) =>
  reducer(INITIAL_LIVE_STATE, env("moderator", "snapshot", { event: { id: "e1" }, announcements }));

const scheduled = { id: "a1", text: "We're starting in 5 minutes", priority: "important",
  scheduled_at: "2026-10-28T10:20:00Z", sent_at: null, delivered_to: null, status: "scheduled" };
const sent = { ...scheduled, sent_at: "2026-10-28T10:20:01Z", delivered_to: 1, status: "sent" };

describe("announcement.new", () => {
  it("a scheduled announcement that is then sent is ONE entry, now showing as sent", () => {
    let s = reducer(start(), env("announcement", "announcement.new", scheduled));
    s = reducer(s, env("announcement", "announcement.new", sent));
    expect(s.announcements).toHaveLength(1);
    expect(s.announcements[0]).toMatchObject({ id: "a1", sent_at: sent.sent_at, delivered_to: 1, status: "sent" });
  });

  it("the same broadcast delivered twice (a reconnect, a second ticker) is still one entry", () => {
    let s = reducer(start(), env("announcement", "announcement.new", sent));
    s = reducer(s, env("announcement", "announcement.new", sent));
    expect(s.announcements).toHaveLength(1);
  });

  it("an update keeps the entry where it was in the queue", () => {
    const older = { ...sent, id: "a0", text: "Earlier" };
    let s = reducer(start([older]), env("announcement", "announcement.new", scheduled));
    expect(s.announcements.map((a) => a.id)).toEqual(["a1", "a0"]);     // newest first
    s = reducer(s, env("announcement", "announcement.new", sent));
    expect(s.announcements.map((a) => a.id)).toEqual(["a1", "a0"]);
  });

  it("two separate announcements — even with identical text — stay two entries", () => {
    let s = reducer(start(), env("announcement", "announcement.new", { ...sent, id: "a1" }));
    s = reducer(s, env("announcement", "announcement.new", { ...sent, id: "a2" }));
    expect(s.announcements.map((a) => a.id)).toEqual(["a2", "a1"]);
  });

  it("a snapshot after a reconnect replaces the list rather than adding to it", () => {
    let s = reducer(start(), env("announcement", "announcement.new", sent));
    s = reducer(s, env("moderator", "snapshot", { event: { id: "e1" }, announcements: [sent] }));
    expect(s.announcements).toHaveLength(1);
  });

  it("delete removes it", () => {
    let s = reducer(start(), env("announcement", "announcement.new", sent));
    s = reducer(s, env("announcement", "announcement.delete", { id: "a1" }));
    expect(s.announcements).toEqual([]);
  });
});
