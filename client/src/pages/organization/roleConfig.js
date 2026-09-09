// Event role configuration, in its own module so it can be imported by components, by
// non-component code, and by tests without any of them importing a React component.
//
// It used to be exported from AssignPeopleModal.jsx, which broke Fast Refresh: a file that
// exports anything other than components loses hot-module replacement for the whole file, so
// editing that modal did a full page reload instead of preserving state.

// Maps a role tab to its /events/{id}/<path> endpoint.
//
// PATCH /events/{id}/{hosts|speakers} replaces the WHOLE assignee set in one call, so callers
// submit a full selection rather than diffing adds/removes against the server.
//
// "Moderator" is absent because the endpoint is gone (routers/events.py) — the role is retired.
export const ROLE_PATH = { Host: "hosts", Speaker: "speakers" };
