import { useEffect, useMemo, useState } from "react";
import toast from "react-hot-toast";
import { FiKey, FiPlus, FiSlash } from "react-icons/fi";
import { Badge, Panel, Button, timeAgo } from "../../components/admin";
import { Select, Label } from "../../ui/forms";
import api, { errMsg } from "../../api";
import useApi from "../../hooks/useApi";
import Skeleton from "../../ui/Skeleton";
import GenerateApiKeyModal from "./GenerateApiKeyModal";

function useOrgsData() {
  return useApi(() => api.get("/admin/organizations", { params: { page_size: 100 } }).then((r) => r.data.items));
}

// Per-organization API key management — real GET/POST/DELETE against
// /admin/organizations/{id}/api-keys, backed by the existing Organization.api_keys column.
// The raw key is only ever visible once, right after generation.
export default function Developers() {
  const { data: organizations, loading: loadingOrgs, error: orgsError } = useOrgsData();
  const [chosenOrgId, setChosenOrgId] = useState("");
  const [keys, setKeys] = useState(null);
  const [modalOpen, setModalOpen] = useState(false);
  const [reloadNonce, setReloadNonce] = useState(0);

  // The selected organization DERIVES its default from the loaded list instead of an effect
  // writing it into state. `organizations` arrives asynchronously, so the effect that used to
  // do this could only run after a render in which nothing was selected — one guaranteed extra
  // render pass, and a frame in which the picker showed no organization at all.
  const orgId = chosenOrgId || organizations?.[0]?.id || "";

  // Which request the `keys` in state came from. `loadingKeys` is derived from a mismatch
  // rather than written by a synchronous setState at the top of the effect below: the render
  // that first sees a new `orgId` already knows the keys it holds belong to a different
  // request, so the skeleton appears on that same pass. Including the nonce means an explicit
  // reload (after generate/revoke) shows the skeleton too, as it did before.
  const [keysRequest, setKeysRequest] = useState(null);
  const request = orgId ? `${orgId}:${reloadNonce}` : null;
  const loadingKeys = !!request && keysRequest !== request;

  const reloadKeys = () => setReloadNonce((n) => n + 1);

  useEffect(() => {
    if (!request) return;
    let cancelled = false;
    (async () => {
      try {
        const { data } = await api.get(`/admin/organizations/${orgId}/api-keys`);
        if (cancelled) return;
        setKeys(data);
      } catch (e) {
        if (cancelled) return;
        toast.error(errMsg(e));
        // Never leave another organization's keys on screen under this one's heading.
        setKeys(null);
      } finally {
        // Marking the request done in `finally` is what clears the skeleton on failure too —
        // otherwise a rejected fetch would leave it spinning for ever.
        if (!cancelled) setKeysRequest(request);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [request, orgId]);

  const selectedOrg = useMemo(() => organizations?.find((o) => o.id === orgId), [organizations, orgId]);
  const activeKeys = (keys || []).filter((k) => !k.revoked);
  const revokedKeys = (keys || []).filter((k) => k.revoked);

  const revoke = async (key) => {
    if (!window.confirm(`Revoke "${key.label}"? Any integration using it will stop working.`)) return;
    try {
      await api.delete(`/admin/organizations/${orgId}/api-keys/${key.id}`);
      toast.success(`${key.label} revoked`);
      reloadKeys();
    } catch (e) {
      toast.error(errMsg(e));
    }
  };

  if (orgsError) {
    return (
      <div className="mx-auto max-w-[900px] rounded-xl border border-rose-200 bg-rose-50 px-5 py-4 text-sm text-rose-700 dark:border-rose-500/20 dark:bg-rose-500/10 dark:text-rose-300">
        Couldn't load organizations. Try refreshing the page.
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-[900px] space-y-6">
      <div>
        <h1 className="text-[24px] font-semibold tracking-tight text-slate-900 dark:text-white">Developers</h1>
        <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">API keys, per organization</p>
      </div>

      {loadingOrgs ? (
        <Skeleton variant="block" className="h-10 w-64" />
      ) : organizations.length === 0 ? (
        <p className="text-sm text-slate-500 dark:text-slate-400">No organizations yet.</p>
      ) : (
        <div className="max-w-xs">
          <Label>Organization</Label>
          <Select variant="console" value={orgId} onChange={(e) => setChosenOrgId(e.target.value)}>
            {organizations.map((o) => <option key={o.id} value={o.id}>{o.name}</option>)}
          </Select>
        </div>
      )}

      {selectedOrg && (
        <Panel
          eyebrow={selectedOrg.name}
          title="API Keys"
          action={<Button size="sm" leftIcon={FiPlus} onClick={() => setModalOpen(true)}>Generate Key</Button>}
          flush
        >
          {loadingKeys ? (
            <div className="space-y-3 px-5 py-5">
              {Array.from({ length: 2 }).map((_, i) => <div key={i} className="zk-skeleton h-12 rounded-lg bg-slate-200 dark:bg-slate-800" />)}
            </div>
          ) : !keys || keys.length === 0 ? (
            <div className="px-5 py-16 text-center">
              <div className="mx-auto mb-3 grid h-10 w-10 place-items-center rounded-full bg-slate-100 text-slate-400 dark:bg-slate-800">
                <FiKey className="text-lg" />
              </div>
              <p className="text-sm font-semibold text-slate-700 dark:text-slate-200">No API keys yet</p>
              <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">Generate one for {selectedOrg.name} to get started.</p>
            </div>
          ) : (
            <ul className="divide-y divide-slate-100 dark:divide-slate-800">
              {[...activeKeys, ...revokedKeys].map((k) => (
                <li key={k.id} className="flex items-center justify-between gap-3 px-5 py-3.5">
                  <div className="min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="font-medium text-slate-800 dark:text-slate-100">{k.label}</span>
                      {k.revoked && <Badge tone="neutral">Revoked</Badge>}
                    </div>
                    <p className="mt-0.5 font-mono text-xs text-slate-400">{k.prefix}••••••••••••••••</p>
                  </div>
                  <div className="flex shrink-0 items-center gap-3">
                    <span className="text-xs text-slate-400">{timeAgo(k.created_at)}</span>
                    {!k.revoked && (
                      <Button variant="ghost" size="sm" iconOnly title={`Revoke ${k.label}`} leftIcon={FiSlash} className="hover:text-rose-600 dark:hover:text-rose-400" onClick={() => revoke(k)} />
                    )}
                  </div>
                </li>
              ))}
            </ul>
          )}
        </Panel>
      )}

      {orgId && modalOpen && (
        <GenerateApiKeyModal open onClose={() => setModalOpen(false)} orgId={orgId} onCreated={reloadKeys} />
      )}
    </div>
  );
}
