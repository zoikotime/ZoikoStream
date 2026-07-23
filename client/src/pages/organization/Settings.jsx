// client/src/pages/organization/Settings.jsx
// Organization Settings — profile, branding, security, permissions, notifications,
// developer (API keys + integrations), and danger zone. Route: /organization/settings.
// Rendered inside OrganizationLayout. No backend: form edits gate behind Save (local
// state + a dirty flag); immediate actions (integrations, keys, danger) toast on their own.
import { useRef, useState } from "react";
import {
  FiUser, FiImage, FiShield, FiBell, FiCode, FiAlertTriangle,
  FiUploadCloud, FiGlobe, FiCheck, FiCopy, FiEye, FiEyeOff, FiTrash2, FiPlus, FiSave,
  FiHash, FiVideo, FiCloud, FiZap, FiBarChart2, FiLink,
} from "react-icons/fi";
import { cx, ACCENT } from "../../ui/tokens";
import Card from "../../ui/Card";
import Button from "../../ui/Button";
import Badge from "../../ui/Badge";
import { Input, Textarea, Select, Switch, Checkbox } from "../../ui/forms";
import Modal from "../../ui/Modal";
import { notify } from "../../ui/Toast";
import { fmtDate } from "../../data/events";
import {
  orgProfile, INDUSTRIES, COMPANY_SIZES, domain, ACCENTS,
  securityDefaults, SESSION_TIMEOUTS, PASSWORD_LENGTHS,
  ROLES, PERMISSIONS, permissionDefaults,
  notificationGroups, notificationDefaults, apiKeysSeed, integrationsSeed,
} from "../../data/orgSettings";

const TABS = [
  { key: "general", label: "General", icon: FiUser },
  { key: "branding", label: "Branding", icon: FiImage },
  { key: "security", label: "Security", icon: FiShield },
  { key: "notifications", label: "Notifications", icon: FiBell },
  { key: "developer", label: "Developer", icon: FiCode },
  { key: "danger", label: "Danger Zone", icon: FiAlertTriangle },
];
const INT_ICON = { slack: FiHash, zoom: FiVideo, salesforce: FiCloud, zapier: FiZap, ga: FiBarChart2, webhooks: FiLink };

const mask = (t) => `${t.slice(0, 8)}••••••••${t.slice(-4)}`;

// ── module-scope primitives (stable identity → inputs keep focus) ─────────────
function Panel({ title, desc, action, className, children }) {
  return (
    <Card padding="lg" className={className}>
      <div className="mb-5 flex items-start justify-between gap-3">
        <div>
          <h2 className="font-semibold text-slate-900 dark:text-white">{title}</h2>
          {desc && <p className="mt-0.5 text-sm text-slate-500 dark:text-slate-400">{desc}</p>}
        </div>
        {action}
      </div>
      {children}
    </Card>
  );
}
function Field({ label, hint, className, children }) {
  return (
    <div className={className}>
      <label className="mb-1.5 block text-sm font-medium text-slate-700 dark:text-slate-300">{label}</label>
      {children}
      {hint && <p className="mt-1 text-xs text-slate-400">{hint}</p>}
    </div>
  );
}
function SettingRow({ title, desc, children }) {
  return (
    <div className="flex items-center justify-between gap-4 border-b border-slate-100 py-3.5 last:border-0 dark:border-slate-800">
      <div className="min-w-0">
        <p className="text-sm font-medium text-slate-800 dark:text-slate-100">{title}</p>
        {desc && <p className="text-xs text-slate-500 dark:text-slate-400">{desc}</p>}
      </div>
      <div className="shrink-0">{children}</div>
    </div>
  );
}
function IconButton({ icon: Icon, title, danger, onClick }) {
  return (
    <button
      onClick={onClick}
      title={title}
      aria-label={title}
      className={cx(
        "grid h-8 w-8 place-items-center rounded-lg text-slate-400 transition",
        danger
          ? "hover:bg-rose-50 hover:text-rose-600 dark:hover:bg-rose-500/10 dark:hover:text-rose-400"
          : "hover:bg-slate-100 hover:text-slate-700 dark:hover:bg-slate-800 dark:hover:text-slate-200"
      )}
    >
      <Icon />
    </button>
  );
}

const initialSettings = () => ({
  profile: { ...orgProfile },
  customDomain: domain.custom,
  domainStatus: domain.status,
  accent: "violet",
  logoName: "",
  security: { ...securityDefaults },
  permissions: Object.fromEntries(Object.entries(permissionDefaults).map(([r, a]) => [r, [...a]])),
  notifs: { ...notificationDefaults },
});

export default function OrganizationSettings() {
  const [tab, setTab] = useState("general");
  const [settings, setSettings] = useState(initialSettings);
  const [dirty, setDirty] = useState(false);
  const savedRef = useRef(null);
  if (savedRef.current === null) savedRef.current = settings;

  // Immediate (non-Save) state.
  const [integrations, setIntegrations] = useState(integrationsSeed);
  const [apiKeys, setApiKeys] = useState(apiKeysSeed);
  const [revealed, setRevealed] = useState({});
  const [transferOpen, setTransferOpen] = useState(false);
  const [newOwner, setNewOwner] = useState("");
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [confirmName, setConfirmName] = useState("");

  const patch = (updater) => { setSettings(updater); setDirty(true); };
  const setProfile = (k, v) => patch((s) => ({ ...s, profile: { ...s.profile, [k]: v } }));
  const setSec = (k, v) => patch((s) => ({ ...s, security: { ...s.security, [k]: v } }));
  const setDomain = (v) => patch((s) => ({ ...s, customDomain: v, domainStatus: "Pending" }));
  const toggleNotif = (k) => patch((s) => ({ ...s, notifs: { ...s.notifs, [k]: !s.notifs[k] } }));
  const togglePerm = (role, key) =>
    patch((s) => {
      const cur = s.permissions[role];
      const next = cur.includes(key) ? cur.filter((x) => x !== key) : [...cur, key];
      return { ...s, permissions: { ...s.permissions, [role]: next } };
    });

  const save = () => { savedRef.current = settings; setDirty(false); notify.success("Settings saved"); };
  const verifyDomain = () => { patch((s) => ({ ...s, domainStatus: "Verified" })); notify.success("Domain verified"); };

  const toggleIntegration = (key) => {
    const i = integrations.find((x) => x.key === key);
    setIntegrations((list) => list.map((x) => (x.key === key ? { ...x, connected: !x.connected } : x)));
    notify.success(`${i.name} ${i.connected ? "disconnected" : "connected"}`);
  };

  const copyKey = (t) => { navigator.clipboard?.writeText(t); notify.success("API key copied to clipboard"); };
  const revokeKey = (id) => { setApiKeys((k) => k.filter((x) => x.id !== id)); notify.success("API key revoked"); };
  const generateKey = () => {
    const token = `zk_live_${Math.random().toString(16).slice(2, 18)}`;
    const key = { id: Date.now(), name: "New key", token, created: "2026-07-21", lastUsed: "—" };
    setApiKeys((k) => [key, ...k]);
    setRevealed((r) => ({ ...r, [key.id]: true }));
    notify.success("New API key generated — copy it now");
  };

  const orgName = settings.profile.name;
  const doTransfer = () => {
    if (!newOwner.trim()) return;
    setTransferOpen(false);
    notify.success(`Ownership transfer invite sent to ${newOwner.trim()}`);
    setNewOwner("");
  };
  const doDelete = () => {
    if (confirmName !== orgName) return;
    setDeleteOpen(false);
    setConfirmName("");
    notify.success("Organization scheduled for deletion");
  };

  return (
    <div className="space-y-6">
      {/* Header + Save */}
      <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-slate-900 dark:text-white">Settings</h1>
          <p className="text-sm text-slate-500 dark:text-slate-400">Manage your organization's profile, security, and integrations</p>
        </div>
        <div className="flex items-center gap-3">
          {dirty && <span className="text-xs font-medium text-amber-600 dark:text-amber-400">Unsaved changes</span>}
          <Button size="sm" onClick={save} disabled={!dirty}>
            <FiSave className="text-base" /> Save Changes
          </Button>
        </div>
      </div>

      <div className="flex flex-col gap-6 lg:flex-row">
        {/* Section nav */}
        <nav className="flex gap-2 overflow-x-auto pb-1 lg:w-56 lg:shrink-0 lg:flex-col lg:overflow-visible lg:pb-0">
          {TABS.map((t) => {
            const active = tab === t.key;
            const danger = t.key === "danger";
            return (
              <button
                key={t.key}
                onClick={() => setTab(t.key)}
                className={cx(
                  "inline-flex shrink-0 items-center gap-2.5 rounded-xl px-3.5 py-2.5 text-sm font-medium transition lg:w-full",
                  active
                    ? danger
                      ? "bg-rose-50 text-rose-600 dark:bg-rose-500/10 dark:text-rose-400"
                      : "bg-emerald-50 text-emerald-700 dark:bg-emerald-500/10 dark:text-emerald-400"
                    : "text-slate-600 hover:bg-slate-100 dark:text-slate-300 dark:hover:bg-slate-800"
                )}
              >
                <t.icon className="text-base" /> {t.label}
              </button>
            );
          })}
        </nav>

        {/* Section content */}
        <div className="min-w-0 flex-1 space-y-6">
          {/* ── GENERAL: Organization Profile + Custom Domain ── */}
          {tab === "general" && (
            <>
              <Panel title="Organization Profile" desc="Basic information about your organization">
                <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                  <Field label="Organization Name">
                    <Input variant="form" value={settings.profile.name} onChange={(e) => setProfile("name", e.target.value)} />
                  </Field>
                  <Field label="URL Slug" hint={`zoikostream.com/o/${settings.profile.slug || "…"}`}>
                    <Input variant="form" value={settings.profile.slug} onChange={(e) => setProfile("slug", e.target.value)} />
                  </Field>
                  <Field label="Website">
                    <Input variant="form" value={settings.profile.website} onChange={(e) => setProfile("website", e.target.value)} />
                  </Field>
                  <Field label="Support Email">
                    <Input variant="form" type="email" value={settings.profile.supportEmail} onChange={(e) => setProfile("supportEmail", e.target.value)} />
                  </Field>
                  <Field label="Industry">
                    <Select variant="form" value={settings.profile.industry} onChange={(e) => setProfile("industry", e.target.value)}>
                      {INDUSTRIES.map((i) => <option key={i}>{i}</option>)}
                    </Select>
                  </Field>
                  <Field label="Company Size">
                    <Select variant="form" value={settings.profile.size} onChange={(e) => setProfile("size", e.target.value)}>
                      {COMPANY_SIZES.map((s) => <option key={s}>{s}</option>)}
                    </Select>
                  </Field>
                  <Field label="Description" className="sm:col-span-2">
                    <Textarea variant="form" rows={3} value={settings.profile.description} onChange={(e) => setProfile("description", e.target.value)} />
                  </Field>
                </div>
              </Panel>

              <Panel title="Custom Domain" desc="Serve your event pages from your own domain">
                <div className="flex flex-col gap-3 sm:flex-row sm:items-end">
                  <Field label="Domain" className="flex-1">
                    <div className="relative">
                      <FiGlobe className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
                      <Input variant="form" className="pl-9" value={settings.customDomain} onChange={(e) => setDomain(e.target.value)} placeholder="events.yourcompany.com" />
                    </div>
                  </Field>
                  <Button variant="secondary" onClick={verifyDomain}>Verify</Button>
                </div>
                <div className="mt-3 flex flex-wrap items-center gap-2">
                  <Badge status={settings.domainStatus === "Verified" ? "success" : "warning"} dot>{settings.domainStatus}</Badge>
                  <span className="text-xs text-slate-400">Add a CNAME record pointing to <code className="font-mono">cname.zoikostream.com</code></span>
                </div>
              </Panel>
            </>
          )}

          {/* ── BRANDING: Logo + brand color ── */}
          {tab === "branding" && (
            <Panel title="Branding" desc="Personalize how ZoikoStream looks for your audience">
              <Field label="Logo">
                <div className="flex flex-wrap items-center gap-4">
                  <div className="grid h-16 w-16 place-items-center overflow-hidden rounded-xl border border-slate-200 bg-slate-50 dark:border-slate-700 dark:bg-slate-800">
                    <img src="/zoiko-logo.png" alt="Organization logo" className="max-h-full max-w-full object-contain" />
                  </div>
                  <label className="inline-flex cursor-pointer items-center gap-2 rounded-xl border border-dashed border-slate-300 px-4 py-2.5 text-sm font-medium text-slate-600 transition hover:border-emerald-400 hover:bg-emerald-50/40 dark:border-slate-700 dark:text-slate-300 dark:hover:border-emerald-500/50">
                    <FiUploadCloud className="text-base" /> {settings.logoName || "Upload new logo"}
                    <input type="file" accept="image/*" className="hidden" onChange={(e) => patch((s) => ({ ...s, logoName: e.target.files?.[0]?.name || "" }))} />
                  </label>
                </div>
                <p className="mt-1.5 text-xs text-slate-400">PNG or SVG, square, up to 5MB.</p>
              </Field>

              <div className="mt-6">
                <Field label="Brand Color" hint="Used across dashboards, emails, and your event pages">
                  <div className="flex flex-wrap gap-2.5">
                    {ACCENTS.map((a) => (
                      <button
                        key={a}
                        onClick={() => patch((s) => ({ ...s, accent: a }))}
                        aria-label={a}
                        aria-pressed={settings.accent === a}
                        className={cx(
                          "h-9 w-9 rounded-full ring-2 ring-offset-2 transition ring-offset-white dark:ring-offset-slate-900",
                          ACCENT[a].solid,
                          settings.accent === a ? "ring-slate-900 dark:ring-white" : "ring-transparent"
                        )}
                      />
                    ))}
                  </div>
                </Field>
                <div className="mt-4 rounded-xl border border-slate-200 p-4 dark:border-slate-700">
                  <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-400">Preview</p>
                  <span className={cx("inline-flex items-center rounded-lg px-4 py-2 text-sm font-semibold text-white", ACCENT[settings.accent].solid)}>Go Live</span>
                </div>
              </div>
            </Panel>
          )}

          {/* ── SECURITY + User Permissions ── */}
          {tab === "security" && (
            <>
              <Panel title="Security" desc="Authentication and access policies for your organization">
                <SettingRow title="Require two-factor authentication" desc="Every member must enable 2FA to sign in">
                  <Switch checked={settings.security.require2fa} onChange={(v) => setSec("require2fa", v)} />
                </SettingRow>
                <SettingRow title="Enforce SSO (SAML)" desc="Restrict sign-in to your identity provider">
                  <Switch checked={settings.security.enforceSSO} onChange={(v) => setSec("enforceSSO", v)} />
                </SettingRow>
                <SettingRow title="Minimum password length">
                  <Select variant="form" className="w-28" value={settings.security.minPasswordLength} onChange={(e) => setSec("minPasswordLength", Number(e.target.value))}>
                    {PASSWORD_LENGTHS.map((n) => <option key={n} value={n}>{n} chars</option>)}
                  </Select>
                </SettingRow>
                <SettingRow title="Session timeout" desc="Automatically sign out inactive members">
                  <Select variant="form" className="w-36" value={settings.security.sessionTimeout} onChange={(e) => setSec("sessionTimeout", e.target.value)}>
                    {SESSION_TIMEOUTS.map((t) => <option key={t}>{t}</option>)}
                  </Select>
                </SettingRow>
                <div className="pt-4">
                  <Field label="Allowed email domains" hint="Comma-separated. Only these domains can be invited.">
                    <Input variant="form" value={settings.security.allowedDomains} onChange={(e) => setSec("allowedDomains", e.target.value)} placeholder="acme.com, acme.io" />
                  </Field>
                </div>
              </Panel>

              <Panel title="User Permissions" desc="What each role can do. The Owner always has full access.">
                <div className="overflow-x-auto">
                  <table className="w-full min-w-[560px] text-sm">
                    <thead>
                      <tr className="border-b border-slate-100 dark:border-slate-800">
                        <th className="py-2 pr-3 text-left text-xs font-semibold uppercase tracking-wide text-slate-400">Permission</th>
                        {ROLES.map((r) => (
                          <th key={r} className="px-3 py-2 text-center text-xs font-semibold uppercase tracking-wide text-slate-400">{r}</th>
                        ))}
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                      {PERMISSIONS.map((p) => (
                        <tr key={p.key}>
                          <td className="py-2.5 pr-3 text-slate-700 dark:text-slate-200">{p.label}</td>
                          {ROLES.map((r) => (
                            <td key={r} className="px-3 py-2.5 text-center">
                              <Checkbox
                                checked={settings.permissions[r].includes(p.key)}
                                onChange={() => togglePerm(r, p.key)}
                                aria-label={`${r} — ${p.label}`}
                                className="cursor-pointer accent-emerald-500"
                              />
                            </td>
                          ))}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </Panel>
            </>
          )}

          {/* ── NOTIFICATIONS ── */}
          {tab === "notifications" && (
            <Panel title="Notifications" desc="Choose what your team gets notified about">
              {notificationGroups.map((g) => (
                <div key={g.title} className="mb-6 last:mb-0">
                  <h3 className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-400">{g.title}</h3>
                  {g.items.map((it) => (
                    <SettingRow key={it.key} title={it.label} desc={it.desc}>
                      <Switch checked={settings.notifs[it.key]} onChange={() => toggleNotif(it.key)} />
                    </SettingRow>
                  ))}
                </div>
              ))}
            </Panel>
          )}

          {/* ── DEVELOPER: API Keys + Integrations ── */}
          {tab === "developer" && (
            <>
              <Panel
                title="API Keys"
                desc="Authenticate requests to the ZoikoStream API"
                action={<Button variant="secondary" size="sm" onClick={generateKey}><FiPlus className="text-base" /> Generate Key</Button>}
              >
                <div className="space-y-2.5">
                  {apiKeys.map((k) => (
                    <div key={k.id} className="flex flex-wrap items-center gap-3 rounded-xl border border-slate-100 p-3 dark:border-slate-800">
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center gap-2">
                          <p className="font-medium text-slate-800 dark:text-slate-100">{k.name}</p>
                          <Badge status={k.token.startsWith("zk_live") ? "success" : "neutral"}>{k.token.startsWith("zk_live") ? "Live" : "Test"}</Badge>
                        </div>
                        <code className="mt-1 block truncate font-mono text-xs text-slate-500 dark:text-slate-400">{revealed[k.id] ? k.token : mask(k.token)}</code>
                        <p className="mt-0.5 text-[11px] text-slate-400">Created {fmtDate(k.created)} · Last used {k.lastUsed === "—" ? "—" : fmtDate(k.lastUsed)}</p>
                      </div>
                      <div className="flex items-center gap-1">
                        <IconButton icon={revealed[k.id] ? FiEyeOff : FiEye} title={revealed[k.id] ? "Hide" : "Reveal"} onClick={() => setRevealed((r) => ({ ...r, [k.id]: !r[k.id] }))} />
                        <IconButton icon={FiCopy} title="Copy" onClick={() => copyKey(k.token)} />
                        <IconButton icon={FiTrash2} title="Revoke" danger onClick={() => revokeKey(k.id)} />
                      </div>
                    </div>
                  ))}
                  {apiKeys.length === 0 && <p className="py-6 text-center text-sm text-slate-400">No API keys. Generate one to get started.</p>}
                </div>
              </Panel>

              <Panel title="Integrations" desc="Connect ZoikoStream to the tools your team already uses">
                <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                  {integrations.map((i) => {
                    const Icon = INT_ICON[i.key] || FiLink;
                    return (
                      <div key={i.key} className="flex items-start gap-3 rounded-xl border border-slate-100 p-4 dark:border-slate-800">
                        <span className="grid h-10 w-10 shrink-0 place-items-center rounded-xl bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-300"><Icon /></span>
                        <div className="min-w-0 flex-1">
                          <p className="font-medium text-slate-800 dark:text-slate-100">{i.name}</p>
                          <p className="text-xs text-slate-500 dark:text-slate-400">{i.desc}</p>
                        </div>
                        <button
                          onClick={() => toggleIntegration(i.key)}
                          className={cx(
                            "inline-flex shrink-0 items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-semibold transition",
                            i.connected
                              ? "bg-emerald-50 text-emerald-600 hover:bg-emerald-100 dark:bg-emerald-500/15 dark:text-emerald-400"
                              : "border border-slate-200 text-slate-600 hover:bg-slate-50 dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-800"
                          )}
                        >
                          {i.connected ? <><FiCheck /> Connected</> : "Connect"}
                        </button>
                      </div>
                    );
                  })}
                </div>
              </Panel>
            </>
          )}

          {/* ── DANGER ZONE ── */}
          {tab === "danger" && (
            <Panel title="Danger Zone" desc="These actions are permanent and can't be undone" className="border-rose-200 dark:border-rose-500/30">
              <div className="flex flex-col gap-4 rounded-xl border border-slate-100 p-4 sm:flex-row sm:items-center sm:justify-between dark:border-slate-800">
                <div>
                  <p className="text-sm font-medium text-slate-800 dark:text-slate-100">Transfer ownership</p>
                  <p className="text-xs text-slate-500 dark:text-slate-400">Move this organization to another admin.</p>
                </div>
                <Button variant="secondary" size="sm" onClick={() => setTransferOpen(true)}>Transfer</Button>
              </div>
              <div className="mt-3 flex flex-col gap-4 rounded-xl border border-rose-200 bg-rose-50/50 p-4 sm:flex-row sm:items-center sm:justify-between dark:border-rose-500/30 dark:bg-rose-500/5">
                <div>
                  <p className="text-sm font-medium text-rose-700 dark:text-rose-300">Delete organization</p>
                  <p className="text-xs text-rose-600/80 dark:text-rose-400/80">Permanently remove this organization and all its data.</p>
                </div>
                <Button variant="danger" size="sm" onClick={() => setDeleteOpen(true)}>Delete Organization</Button>
              </div>
            </Panel>
          )}
        </div>
      </div>

      {/* Transfer ownership modal */}
      <Modal
        open={transferOpen}
        onClose={() => setTransferOpen(false)}
        title="Transfer ownership"
        footer={
          <>
            <Button variant="secondary" size="sm" onClick={() => setTransferOpen(false)}>Cancel</Button>
            <Button size="sm" onClick={doTransfer} disabled={!newOwner.trim()}>Send invite</Button>
          </>
        }
      >
        <p className="mb-3">The new owner will get full control of <strong className="text-slate-800 dark:text-slate-100">{orgName}</strong>, including billing and deletion.</p>
        <Field label="New owner's email">
          <Input variant="form" type="email" value={newOwner} onChange={(e) => setNewOwner(e.target.value)} placeholder="admin@yourcompany.com" />
        </Field>
      </Modal>

      {/* Delete organization modal */}
      <Modal
        open={deleteOpen}
        onClose={() => { setDeleteOpen(false); setConfirmName(""); }}
        title="Delete organization?"
        footer={
          <>
            <Button variant="secondary" size="sm" onClick={() => { setDeleteOpen(false); setConfirmName(""); }}>Cancel</Button>
            <Button variant="danger" size="sm" onClick={doDelete} disabled={confirmName !== orgName}>Delete forever</Button>
          </>
        }
      >
        <p className="mb-3">This permanently deletes <strong className="text-slate-800 dark:text-slate-100">{orgName}</strong>, its events, recordings, and analytics. This cannot be undone.</p>
        <Field label={`Type "${orgName}" to confirm`}>
          <Input variant="form" value={confirmName} onChange={(e) => setConfirmName(e.target.value)} placeholder={orgName} />
        </Field>
      </Modal>
    </div>
  );
}
