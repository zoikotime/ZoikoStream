// client/src/components/host/FeatureModal.jsx
// One modal, three faces — Invite Speaker / Create Poll / Q&A queue — driven by
// the `modal` key from the Host Dashboard control bar. Reuses the shared Modal.
import { useEffect, useState } from "react";
import Modal from "../../ui/Modal";
import Button from "../../ui/Button";
import api, { errMsg } from "../../api";
import { notify } from "../../ui/Toast";
import { connectEventChat } from "../../lib/chatSocket";

const control =
  "w-full rounded-xl border border-slate-200 bg-white px-3.5 py-2 text-sm text-slate-700 outline-none focus:border-emerald-400 focus:ring-2 focus:ring-emerald-500/20 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200";
const label = "mb-1 block text-xs font-semibold uppercase tracking-wide text-slate-400";

// Invites go straight onto the stage (POST .../stage/promote with email, no identity
// yet) -- see routers/stage.py: "host inviting anyone straight from the Registrations
// list". The stream must actually be live (LiveKit room open) or this 400s.
function InviteSpeaker({ streamId, onClose }) {
  const [email, setEmail] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [busy, setBusy] = useState(false);

  const submit = async (e) => {
    e.preventDefault();
    setBusy(true);
    try {
      await api.post(`/streams/${streamId}/stage/promote`, {
        email,
        display_name: displayName.trim() || "Guest",
      });
      notify.success(`Invited ${displayName || email} to the stage`);
      onClose();
    } catch (err) {
      notify.error(errMsg(err, "Failed to invite speaker"));
    } finally {
      setBusy(false);
    }
  };

  return (
    <form onSubmit={submit} className="space-y-4">
      <div>
        <label className={label}>Email address</label>
        <input
          type="email"
          required
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          placeholder="speaker@company.com"
          className={control}
        />
      </div>
      <div>
        <label className={label}>Display name</label>
        <input
          required
          value={displayName}
          onChange={(e) => setDisplayName(e.target.value)}
          placeholder="Jane Doe"
          className={control}
        />
      </div>
      <div className="flex justify-end gap-2 pt-1">
        <Button variant="secondary" size="sm" onClick={onClose} disabled={busy}>Cancel</Button>
        <Button type="submit" size="sm" disabled={busy}>{busy ? "Sending…" : "Send invite"}</Button>
      </div>
    </form>
  );
}

function CreatePoll({ streamId, onClose }) {
  const [question, setQuestion] = useState("");
  const [options, setOptions] = useState(["", ""]);
  const [busy, setBusy] = useState(false);
  const setAt = (i, v) => setOptions((prev) => prev.map((o, j) => (j === i ? v : o)));

  const submit = async (e) => {
    e.preventDefault();
    const cleanOptions = options.map((o) => o.trim()).filter(Boolean);
    if (cleanOptions.length < 2) {
      notify.error("Add at least 2 options");
      return;
    }
    setBusy(true);
    try {
      await api.post(`/streams/${streamId}/polls`, { question: question.trim(), options: cleanOptions });
      notify.success("Poll launched");
      onClose();
    } catch (err) {
      notify.error(errMsg(err, "Failed to launch poll"));
    } finally {
      setBusy(false);
    }
  };

  return (
    <form onSubmit={submit} className="space-y-4">
      <div>
        <label className={label}>Question</label>
        <input
          required
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          placeholder="Which feature are you most excited about?"
          className={control}
        />
      </div>
      <div className="space-y-2">
        <label className={label}>Options</label>
        {options.map((o, i) => (
          <input key={i} value={o} onChange={(e) => setAt(i, e.target.value)} placeholder={`Option ${i + 1}`} className={control} />
        ))}
        {options.length < 8 && (
          <button type="button" onClick={() => setOptions((o) => [...o, ""])} className="text-sm font-medium text-emerald-600 hover:underline dark:text-emerald-400">
            + Add option
          </button>
        )}
      </div>
      <div className="flex justify-end gap-2 pt-1">
        <Button variant="secondary" size="sm" onClick={onClose} disabled={busy}>Cancel</Button>
        <Button type="submit" size="sm" disabled={busy}>{busy ? "Launching…" : "Launch poll"}</Button>
      </div>
    </form>
  );
}

// Live view of the real Q&A stream (same join/qa:new/qa:updated/qa:deleted contract as
// the viewer's Q&A tab); moderation itself (answer/unanswer) goes through the REST
// endpoints in routers/qa.py, whose "qa:updated" broadcast then updates this list too.
function QAQueue({ streamId }) {
  const [items, setItems] = useState([]);
  const [busy, setBusy] = useState(null);

  useEffect(() => {
    if (!streamId) return;
    const token = localStorage.getItem("token");
    const socket = connectEventChat(streamId, { token }, {
      onQaInit: setItems,
      onQaNew: (q) => setItems((list) => [...list, q]),
      onQaUpdated: (q) => setItems((list) => list.map((x) => (x.id === q.id ? q : x))),
      onQaDeleted: (id) => setItems((list) => list.filter((x) => x.id !== id)),
      onError: (err) => notify.error(err),
    });
    return () => socket.disconnect();
  }, [streamId]);

  const toggleAnswered = async (q) => {
    setBusy(q.id);
    try {
      if (q.answered) await api.delete(`/streams/${streamId}/qa/${q.id}/answer`);
      else await api.post(`/streams/${streamId}/qa/${q.id}/answer`);
    } catch (err) {
      notify.error(errMsg(err, "Failed to update question"));
    } finally {
      setBusy(null);
    }
  };

  const sorted = [...items].sort((a, b) => b.votes - a.votes);

  return (
    <div className="space-y-3">
      {sorted.map((q) => (
        <div key={q.id} className="rounded-xl border border-slate-200 p-3 dark:border-slate-800">
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0">
              <p className="text-sm text-slate-700 dark:text-slate-200">{q.text}</p>
              <p className="mt-1 text-xs text-slate-400">
                {q.display_name} · {q.votes} upvotes{q.answered ? " · Answered" : ""}
              </p>
            </div>
            <button
              onClick={() => toggleAnswered(q)}
              disabled={busy === q.id}
              className="shrink-0 rounded-lg border border-slate-200 px-2.5 py-1 text-xs font-medium text-slate-600 hover:bg-slate-50 dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-800 disabled:opacity-50"
            >
              {q.answered ? "Unanswer" : "Answer"}
            </button>
          </div>
        </div>
      ))}
      {items.length === 0 && <p className="text-sm text-slate-400">No questions yet.</p>}
    </div>
  );
}

const CONFIG = {
  invite: { title: "Invite a speaker", size: "md", Body: InviteSpeaker },
  poll: { title: "Create a poll", size: "md", Body: CreatePoll },
  qa: { title: "Q&A queue", size: "lg", Body: QAQueue },
};

export default function FeatureModal({ modal, streamId, onClose }) {
  const cfg = modal && CONFIG[modal];
  const Body = cfg?.Body;
  return (
    <Modal open={!!cfg} onClose={onClose} title={cfg?.title} size={cfg?.size || "md"}>
      {Body && <Body streamId={streamId} onClose={onClose} />}
    </Modal>
  );
}
