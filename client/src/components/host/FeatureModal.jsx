// client/src/components/host/FeatureModal.jsx
// One modal, three faces — Invite Speaker / Create Poll / Q&A queue — driven by
// the `modal` key from the Host Dashboard control bar. Reuses the shared Modal.
import { useState } from "react";
import Modal from "../../ui/Modal";
import Button from "../../ui/Button";
import { qaQueue } from "../../data/host";

const control =
  "w-full rounded-xl border border-slate-200 bg-white px-3.5 py-2 text-sm text-slate-700 outline-none focus:border-emerald-400 focus:ring-2 focus:ring-emerald-500/20 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200";
const label = "mb-1 block text-xs font-semibold uppercase tracking-wide text-slate-400";

function InviteSpeaker({ onClose }) {
  return (
    <form onSubmit={(e) => { e.preventDefault(); onClose(); }} className="space-y-4">
      <div>
        <label className={label}>Email address</label>
        <input type="email" required placeholder="speaker@company.com" className={control} />
      </div>
      <div>
        <label className={label}>Role on stage</label>
        <select className={control} defaultValue="Speaker">
          {["Speaker", "Co-host", "Moderator"].map((r) => <option key={r}>{r}</option>)}
        </select>
      </div>
      <div>
        <label className={label}>Personal note (optional)</label>
        <textarea rows={3} placeholder="Join us on stage for the live demo…" className={control} />
      </div>
      <div className="flex justify-end gap-2 pt-1">
        <Button variant="secondary" size="sm" onClick={onClose}>Cancel</Button>
        <Button size="sm">Send invite</Button>
      </div>
    </form>
  );
}

function CreatePoll({ onClose }) {
  const [options, setOptions] = useState(["", ""]);
  const setAt = (i, v) => setOptions((prev) => prev.map((o, j) => (j === i ? v : o)));

  return (
    <form onSubmit={(e) => { e.preventDefault(); onClose(); }} className="space-y-4">
      <div>
        <label className={label}>Question</label>
        <input required placeholder="Which feature are you most excited about?" className={control} />
      </div>
      <div className="space-y-2">
        <label className={label}>Options</label>
        {options.map((o, i) => (
          <input key={i} value={o} onChange={(e) => setAt(i, e.target.value)} placeholder={`Option ${i + 1}`} className={control} />
        ))}
        <button type="button" onClick={() => setOptions((o) => [...o, ""])} className="text-sm font-medium text-emerald-600 hover:underline dark:text-emerald-400">
          + Add option
        </button>
      </div>
      <div className="flex justify-end gap-2 pt-1">
        <Button variant="secondary" size="sm" onClick={onClose}>Cancel</Button>
        <Button size="sm">Launch poll</Button>
      </div>
    </form>
  );
}

function QAQueue() {
  return (
    <div className="space-y-3">
      {qaQueue.map((q) => (
        <div key={q.id} className="rounded-xl border border-slate-200 p-3 dark:border-slate-800">
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0">
              <p className="text-sm text-slate-700 dark:text-slate-200">{q.text}</p>
              <p className="mt-1 text-xs text-slate-400">{q.name} · {q.votes} upvotes</p>
            </div>
            <button className="shrink-0 rounded-lg border border-slate-200 px-2.5 py-1 text-xs font-medium text-slate-600 hover:bg-slate-50 dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-800">
              Answer
            </button>
          </div>
        </div>
      ))}
    </div>
  );
}

const CONFIG = {
  invite: { title: "Invite a speaker", size: "md", Body: InviteSpeaker },
  poll: { title: "Create a poll", size: "md", Body: CreatePoll },
  qa: { title: "Q&A queue", size: "lg", Body: QAQueue },
};

export default function FeatureModal({ modal, onClose }) {
  const cfg = modal && CONFIG[modal];
  const Body = cfg?.Body;
  return (
    <Modal open={!!cfg} onClose={onClose} title={cfg?.title} size={cfg?.size || "md"}>
      {Body && <Body onClose={onClose} />}
    </Modal>
  );
}
