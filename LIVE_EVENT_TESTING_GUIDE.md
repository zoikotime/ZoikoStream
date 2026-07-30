# Testing a Live Event End-to-End

A step-by-step walkthrough for testing the full live-streaming flow: creating an
organization, assigning a host, going live, and watching as a viewer — with chat,
Q&A, and polls working.

## 1. Start both servers

**Backend** (from `server/`, with the venv active):

```
uvicorn app.main:socket_app --reload
```

> **Important:** it must be `app.main:socket_app`, not the more common `app.main:app`.
> `socket_app` is the ASGI wrapper that mounts the Socket.IO layer chat, Q&A, polls,
> and live viewer-presence all depend on. Running `app.main:app` boots fine and looks
> normal, but every chat/Q&A/poll message will silently fail to send — the frontend
> lets you type, the message just never goes anywhere.

**Frontend** (from `client/`):

```
npm run dev
```

Confirm both are up:
- Backend: `http://localhost:8000/docs` should load.
- Frontend: `http://localhost:5173` should load.

## 2. Create an organization + admin account

1. Go to `http://localhost:5173/signup`.
2. Fill in an organization name, your name, a work email, and a password.
3. You land on the Org Admin dashboard.

## 3. Invite a Host

The account that manages the org (**Org Admin**) is a separate role from the one
that's allowed to broadcast (**Host**) — an Org Admin can't assign themselves as a
stream's host, they have to invite a Host account (this can be a second email you
control, or a colleague).

1. Go to **Users** → **Invite member**.
2. Enter an email you can access, set **Role = Host**.
3. Check that inbox for the invite email and accept it to set a password.

You now have two accounts: the Org Admin (manages events) and the Host (broadcasts).

## 4. Create the event

As the **Org Admin**:

1. Go to **Events** → **Create Event**.
2. Fill in a title. Visibility **Public** and Registration **Open** (not required)
   are the simplest settings for a test — no registration form in the way.
3. In **Assignments**, set **Host** to the account from step 3.
   - If the Host doesn't show up in the dropdown yet, use **+ Invite a new host**
     right there instead of leaving the form — same invite as step 3, one less trip.
4. Click **Publish Event**.

You can also assign (or change) the host later from the event's **Team** tab.

## 5. Go live

1. Log in as the **Host** account (use a different browser, an incognito window, or
   log out/in — see the warning below about why *not* to just open a second tab).
2. Go to `http://localhost:5173/host/dashboard` — it shows the assigned event as
   "Ready to go live".
3. Click **Go Live**. Your browser will prompt for camera/mic access — click **Allow**.
4. Once connected, the studio shows **LIVE** and your own camera preview.

> **Don't preview your own stream from the same logged-in browser as the host.**
> LiveKit only allows one connection per identity per room. If you open the public
> watch link in another tab while still logged in as the Host, that viewer tab
> shares the same identity as your broadcast connection and will silently kick it —
> your camera goes dark with no error, and the Host Studio still says "LIVE" as if
> nothing happened. Use a different browser, an incognito window, or a different
> device to watch as a viewer.

## 6. Get the viewer link

Back on the **Org Admin** side, on the Events page (or the event detail page), use
the row's **Copy Link** action:

- **Registration Open** events → link goes straight into the watch page, no friction.
- **Registration Required** events → link goes to a registration form first, then
  into the watch page once submitted.

## 7. Watch as a viewer

1. Open the copied link in an **incognito window / different browser / different
   device** — not the same logged-in session as the Host (see the warning above).
2. The stream should connect within a couple of seconds and show the Host's live
   video.
3. Try the chat: enter a display name, **Join Chat**, then send a message. It should
   appear immediately in both the viewer's and the Host's chat panel.
4. Try **Raise Hand** as the viewer, and check the Host Studio's People panel shows
   the raised hand.

## 8. End the event

In the Host Studio, click **End Event**. The event moves to **Completed**, and the
viewer's page shows a "This event has ended" state.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| Chat: can type but message never sends/appears | Backend isn't running `app.main:socket_app` — see step 1. |
| Viewer stuck on "Connecting…" forever | Confirm the backend really is `socket_app` and both servers are up. If both check out and it's still stuck, it may be a stale dev build — hard-refresh the viewer tab. |
| Host's camera goes dark mid-stream, no error | Almost always the identity-collision case in step 5's warning — check whether the same browser/session opened the watch link. If not, the camera/mic device may have been interrupted (another app grabbed it, permission revoked); the Host Studio should now show a toast telling you to click Camera/Mic to reconnect. |
| "Stream already live" error when clicking Go Live | The event was left live from a previous test session without clicking End Event. Have the Org Admin cancel/complete it, or click **End Event** from the Host Studio if you still have that session open. |
| Recording won't start | `GCS_BUCKET` / `GCS_CREDENTIALS_FILE` aren't configured in `.env` — this only affects recording uploads, not live streaming itself. |
