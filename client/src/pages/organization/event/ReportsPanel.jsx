import { useRef, useState } from "react";
import { FiCopy, FiFileText, FiPlus, FiSend } from "react-icons/fi";
import api, { errMsg } from "../../../api";
import useApi from "../../../hooks/useApi";
import { notify } from "../../../ui/Toast";
import OrganizationErrorState from "../../../components/organization/OrganizationErrorState";
import OrganizationEmptyState from "../../../components/organization/OrganizationEmptyState";
import SectionCard from "../../../components/admin/SectionCard";
import { ConsoleButton as Button } from "../../../ui/Button";
import Badge from "../../../ui/Badge";
import Modal from "../../../ui/Modal";
import { Input, Label } from "../../../ui/forms";
import { fmtDateTime } from "../../../data/events";

// Post-event report (BRD §18.2) — a generated audience + operations snapshot, released
// to the customer/family contact via the same controlled-delivery mechanism as recording
// export (services/report.py, services/delivery.py). Self-contained (own fetch), same
// posture as EventCommercial above: only takes `event`.
function ReleaseReportModal({ report, onClose }) {
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [expires, setExpires] = useState("14");
  const [sending, setSending] = useState(false);
  const [created, setCreated] = useState(null);
  // A ref, not `sending`: two clicks in one frame both see sending === false, and a second
  // release would mail the recipient a second link.
  const inFlight = useRef(false);

  const send = async () => {
    if (inFlight.current) return;
    inFlight.current = true;
    setSending(true);
    try {
      const { data } = await api.post(`/organization/reports/${report.id}/release`, {
        recipient_name: name.trim(), recipient_email: email.trim(),
        expires_in_days: expires ? Number(expires) : null,
      });
      setCreated(data);
    } catch (e) {
      notify.error(errMsg(e));
    } finally {
      inFlight.current = false;
      setSending(false);
    }
  };

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(created.delivery_url);
      notify.success("Report link copied");
    } catch {
      notify.error("Couldn't copy — select the link and copy it manually");
    }
  };

  return (
    <Modal
      open={!!report}
      onClose={onClose}
      title={created ? "Report released" : `Release report v${report.version}`}
      size="md"
      footer={
        created ? (
          <>
            <Button variant="secondary" size="sm" onClick={onClose}>Done</Button>
            <Button size="sm" leftIcon={FiCopy} onClick={copy}>Copy link</Button>
          </>
        ) : (
          <>
            <Button variant="secondary" size="sm" onClick={onClose} disabled={sending}>Cancel</Button>
            <Button size="sm" leftIcon={FiSend} disabled={!name.trim() || !email.trim() || sending} loading={sending} onClick={send}>
              Release
            </Button>
          </>
        )
      }
    >
      {created ? (
        <div className="space-y-3 text-sm text-slate-600 dark:text-slate-300">
          <p>
            An email was sent to <strong className="text-slate-800 dark:text-slate-100">{created.recipient_email}</strong>{" "}
            with this link. It won't be shown again after you close this dialog.
          </p>
          <code className="block break-all rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 text-xs dark:border-slate-700 dark:bg-white/[0.03]">
            {created.delivery_url}
          </code>
        </div>
      ) : (
        <div className="space-y-4">
          <div>
            <Label variant="console" htmlFor="rep-name">Recipient name</Label>
            <Input variant="console" id="rep-name" value={name} onChange={(e) => setName(e.target.value)} placeholder="Family contact name" />
          </div>
          <div>
            <Label variant="console" htmlFor="rep-email">Recipient email</Label>
            <Input variant="console" id="rep-email" type="email" value={email} onChange={(e) => setEmail(e.target.value)} placeholder="name@example.com" />
          </div>
          <div>
            <Label variant="console" htmlFor="rep-days">Expires in (days)</Label>
            <Input variant="console" id="rep-days" type="number" min="1" max="365" value={expires} onChange={(e) => setExpires(e.target.value)} />
          </div>
        </div>
      )}
    </Modal>
  );
}

export default function ReportsPanel({ event }) {
  const { data, loading, error, reload } = useApi(() =>
    api.get(`/organization/events/${event.id}/reports`).then((r) => r.data)
  );
  const reports = data || [];
  const [generating, setGenerating] = useState(false);
  const [releasing, setReleasing] = useState(null);
  const inFlight = useRef(false);

  const generate = async () => {
    if (inFlight.current) return;
    inFlight.current = true;
    setGenerating(true);
    try {
      await api.post(`/organization/events/${event.id}/reports`);
      notify.success("Report generated");
      reload();
    } catch (e) {
      notify.error(errMsg(e));
    } finally {
      inFlight.current = false;
      setGenerating(false);
    }
  };

  return (
    <SectionCard
      title="Reports"
      subtitle="Generated audience + operations snapshots — released to the event's customer contact."
      icon={FiFileText}
      action={
        <Button size="sm" leftIcon={FiPlus} loading={generating} onClick={generate}>
          Generate report
        </Button>
      }
    >
      {error ? (
        <OrganizationErrorState error={error} onRetry={reload} title="Couldn't load reports" />
      ) : !loading && reports.length === 0 ? (
        <OrganizationEmptyState
          icon={FiFileText}
          title="No reports yet"
          description="Generate a report once the event has run to see its audience and operations summary."
        />
      ) : (
        <ul className="divide-y divide-slate-100 dark:divide-slate-800">
          {reports.map((r) => (
            <li key={r.id} className="flex items-center justify-between gap-3 py-3">
              <div className="min-w-0">
                <p className="font-medium text-slate-800 dark:text-slate-100">Version {r.version}</p>
                <p className="text-xs text-slate-500 dark:text-slate-400">
                  Generated {fmtDateTime(r.created_at)}
                  {r.released_at ? ` · Released ${fmtDateTime(r.released_at)}` : ""}
                </p>
              </div>
              <div className="flex shrink-0 items-center gap-2">
                {r.released_at && <Badge tone="success" dot>Released</Badge>}
                <Button variant="secondary" size="sm" leftIcon={FiSend} onClick={() => setReleasing(r)}>
                  Release
                </Button>
              </div>
            </li>
          ))}
        </ul>
      )}
      {releasing && <ReleaseReportModal report={releasing} onClose={() => { setReleasing(null); reload(); }} />}
    </SectionCard>
  );
}
