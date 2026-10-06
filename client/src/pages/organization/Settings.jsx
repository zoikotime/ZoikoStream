// client/src/pages/organization/Settings.jsx
// Organization Settings — profile, branding, security, permissions, notifications,
// developer (API keys + integrations), and danger zone. Route: /organization/settings.
// Rendered inside OrganizationLayout. Each editable panel saves ONLY its own section, and only
// the fields that changed (see SECTIONS); immediate actions (keys, integrations) act on their own.
import { useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import {
  FiUser, FiImage, FiShield, FiBell, FiCode, FiAlertTriangle,
  FiUploadCloud, FiCheck, FiSave, FiLock,
  FiVideo, FiCloud,
} from "react-icons/fi";
import api, { errMsg } from "../../api";
import useApi from "../../hooks/useApi";
import { cx, ACCENT } from "../../ui/tokens";
import Card from "../../ui/Card";
import Button from "../../ui/Button";
import { Input, Textarea, Select, Switch } from "../../ui/forms";
import { notify } from "../../ui/Toast";
import OrganizationErrorState from "../../components/organization/OrganizationErrorState";
import ApiCredentials from "../../components/organization/ApiCredentials";
import ChangePasswordForm from "../../components/organization/profile/ChangePasswordForm";
import BrandingPreview from "../../components/organization/BrandingPreview";
import CustomDomainPanel from "../../components/organization/CustomDomainPanel";
import {
  INDUSTRIES, COMPANY_SIZES, ACCENTS,
  SESSION_TIMEOUTS, PASSWORD_LENGTHS,
  ROLES, PERMISSIONS, permissionDefaults,
} from "../../data/orgSettings";

const TABS = [
  { key: "general", label: "General", icon: FiUser },
  { key: "branding", label: "Branding", icon: FiImage },
  { key: "security", label: "Security", icon: FiShield },
  { key: "notifications", label: "Notifications", icon: FiBell },
  { key: "developer", label: "Developer", icon: FiCode },
  { key: "danger", label: "Danger Zone", icon: FiAlertTriangle },
];
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
function Field({ label, hint, error, className, children }) {
  return (
    <div className={className}>
      <label className="mb-1.5 block text-sm font-medium text-slate-700 dark:text-slate-300">{label}</label>
      {children}
      {error ? (
        <p className="mt-1 text-xs font-medium text-rose-600 dark:text-rose-400">{error}</p>
      ) : (
        hint && <p className="mt-1 text-xs text-slate-400">{hint}</p>
      )}
    </div>
  );
}
// Says which channel a control affects and how far it reaches, so a reader can tell an
// organization-wide routing change from a personal one.
function scopeNote(item) {
  const scope = item.scope === "organization" ? "Applies organization-wide" : "Applies to you";
  return `${scope} · ${item.channel === "email" ? "Email" : item.channel}.`;
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

// The save control every editable panel ends with. The error stays IN the panel it belongs
// to - it outlives the toast, and it never lands on a panel that saved fine.
function SectionFooter({ id, label, dirty, saving, error, onSave }) {
  return (
    <div className="mt-5 flex flex-col gap-3 border-t border-slate-100 pt-4 sm:flex-row sm:items-center sm:justify-between dark:border-slate-800">
      <div className="min-w-0 text-xs" data-testid={`section-status-${id}`}>
        {error ? (
          <p role="alert" className="font-medium text-rose-600 dark:text-rose-400">
            {label} not saved: {error}
          </p>
        ) : dirty ? (
          <p className="font-medium text-amber-600 dark:text-amber-400">Unsaved changes</p>
        ) : null}
      </div>
      <Button size="sm" onClick={onSave} disabled={!dirty || saving} aria-label={`Save ${label.toLowerCase()}`}>
        <FiSave className="text-base" /> {saving ? "Saving…" : "Save"}
      </Button>
    </div>
  );
}

// ── API ⇄ form-state mapping ──────────────────────────────────────────────────
// The form keeps its camelCase shape; these translate at the network boundary so the
// JSX below is untouched. Only profile / branding-color / security / notifications have a
// backend here — permissions, API keys, integrations and the danger zone stay local (no
// endpoint yet) and are marked at their call sites. The custom domain is its own component
// (CustomDomainPanel) with its own lifecycle actions; this page only hands it the first GET.
// GET /organization/security is the one call on this page that requires org_admin; the other
// five are readable by any member. Under a plain Promise.all a host's 403 rejected the whole
// batch, so General, Branding, Notifications and Domain — all of which had returned 200 — were
// thrown away and the page rendered one page-wide red "org_admin access required". 322 hosts
// and 183 viewers on this deployment saw nothing else.
//
// Same `soft` idea as Profile.jsx, with one addition: WHY it failed is kept. A 403 is a
// permission fact and the panel says so; anything else is a real fault and must not be
// disguised as one. The backend gate is untouched.
const softSecurity = (p) =>
  p.then((r) => ({ data: r.data, access: "ok" })).catch((e) => ({
    data: null,
    access: e?.response?.status === 403 ? "forbidden" : "error",
    error: e,
  }));

const loadSettings = async () => {
  const [profile, security, notifs, domainData, branding, notifCatalog] = await Promise.all([
    api.get("/organization/profile").then((r) => r.data),
    softSecurity(api.get("/organization/security")),
    api.get("/organization/notifications").then((r) => r.data),
    api.get("/organization/domain").then((r) => r.data),
    api.get("/organization/branding").then((r) => r.data),
    // The notification catalog is the server's description of what each preference actually
    // controls — its message class, whether it is mandatory, and whether a send path for it
    // exists at all. Rendering the panel from this instead of a hardcoded list is what stops
    // the page offering a switch for an email Zoiko Steam never sends. Tolerant: if it fails,
    // the rest of Settings still loads and the Notifications panel says it could not load
    // (it used to fall back to a hardcoded list with working-looking switches for billing,
    // security and emails that do not exist).
    api.get("/organization/notifications/catalog").then((r) => r.data).catch(() => null),
  ]);
  return {
    profile,
    security: security.data,
    securityAccess: security.access,
    notifs,
    domain: domainData,
    branding,
    notifCatalog,
  };
};

// Slugs are constrained server-side (^[a-z0-9][a-z0-9-]*$, 3-140). Normalising as the user
// types is why the field can't produce a 422 — it can only produce a valid slug or "".
const slugify = (v) =>
  v.toLowerCase().replace(/[^a-z0-9-]+/g, "-").replace(/-{2,}/g, "-").replace(/^-+/, "");

const isEmail = (v) => /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(v.trim());

// Client-side mirror of the constraints the schemas enforce, so a bad value is caught at the
// field instead of coming back as an opaque 422 for the whole section.
const validate = (s) => {
  const e = {};
  if (!s.profile.name.trim()) e.name = "Organization name is required.";
  const slug = s.profile.slug.trim();
  if (slug && slug.length < 3) e.slug = "Use at least 3 characters.";
  if (s.profile.supportEmail.trim() && !isEmail(s.profile.supportEmail)) {
    e.supportEmail = "Enter a valid email address.";
  }
  return e;
};

// FastAPI 422 bodies are [{loc:["body","support_email"], msg}, …]. Map the API field name
// back to the form key so the error lands on the control the reader has to fix.
const API_TO_FORM = { support_email: "supportEmail", company_size: "size", min_password_length: "minPasswordLength" };
const fieldErrors = (err) => {
  const detail = err?.response?.data?.detail;
  if (!Array.isArray(detail)) return {};
  const pairs = detail
    .map((d) => {
      const key = String(d?.loc?.[d.loc.length - 1] ?? "");
      return [API_TO_FORM[key] || key, d?.msg];
    })
    .filter(([k, v]) => k && v);
  return Object.fromEntries(pairs);
};

// Per-section mappers. `from` turns a server payload (the GET at load, or the PATCH response
// after a save) into that section's slice of form state; `payload` turns form state back into
// the section's request body. One pair per section is what lets a save replace exactly its
// own slice and leave every other panel's unsaved edits alone.
//
// Security: `s0` is null when the caller may not read the policy (see softSecurity). The form
// still needs a shape, so the fields fall back to empty values - never rendered, never sent.
const profileFrom = (p) => ({
  profile: {
    name: p.name ?? "", slug: p.slug ?? "", website: p.website ?? "",
    supportEmail: p.support_email ?? "", industry: p.industry ?? INDUSTRIES[0],
    size: p.company_size ?? COMPANY_SIZES[0], description: p.description ?? "",
  },
});
// primary_color stores an ACCENT key; anything else (a hex from another surface) falls back
// rather than indexing ACCENT[undefined] and crashing the picker.
const brandingFrom = (b) => ({
  accent: ACCENTS.includes(b.primary_color) ? b.primary_color : "violet",
  logoUrl: b.logo_url ?? "",
});
const securityFrom = (s0) => ({
  security: {
    require2fa: s0?.require_2fa ?? false,
    enforceSSO: s0?.enforce_sso ?? false,
    // null, not 8: an unreadable policy is unknown, and ChangePasswordForm renders its
    // generic hint for a non-number rather than asserting a minimum that may be wrong.
    minPasswordLength: s0?.min_password_length ?? null,
    sessionTimeout: s0?.session_timeout ?? null,
    allowedDomains: s0?.allowed_domains ?? "",
  },
});
// Keyed by the API's own preference names ("event_scheduled"), because the panel indexes it
// with the key on each catalog row. It used to be re-keyed to camelCase ("eventScheduled"),
// so every switch read `undefined` and drew itself OFF whatever was stored, and a toggle wrote
// a key the save payload never read: Save stayed dead and nothing reached the database.
const notifsFrom = (n) => ({
  notifs: Object.fromEntries(
    Object.entries(n || {}).filter(([, v]) => typeof v === "boolean")
  ),
});

const fromApi = ({ profile, security, notifs, branding }) => ({
  ...profileFrom(profile), ...brandingFrom(branding),
  ...securityFrom(security), ...notifsFrom(notifs),
});

// One entry per independently saved section. Saving used to PATCH all five on every click,
// so toggling one notification re-sent Branding and the custom domain too - and a failure in
// ANY of the five requests surfaced as "Couldn't save Branding, Custom domain - ..." against
// panels nobody had touched. Now a panel's Save sends one request, to its own endpoint, with
// only the fields that differ from what the server last confirmed. The server treats every
// PATCH as partial (exclude_unset), so an omitted field is preserved, not cleared.
// EmailStr rejects "" -> empty optionals are sent as null (an explicit clear).
const SECTIONS = {
  profile: {
    label: "Organization profile", url: "/organization/profile", from: profileFrom,
    payload: (f) => ({
      name: f.profile.name, slug: f.profile.slug || null, website: f.profile.website || null,
      description: f.profile.description || null, industry: f.profile.industry || null,
      company_size: f.profile.size || null, support_email: f.profile.supportEmail || null,
    }),
  },
  branding: {
    label: "Branding", url: "/organization/branding", from: brandingFrom,
    payload: (f) => ({ primary_color: f.accent, logo_url: f.logoUrl.trim() || null }),
  },
  security: {
    label: "Security policy", url: "/organization/security", from: securityFrom,
    payload: (f) => ({
      require_2fa: f.security.require2fa, enforce_sso: f.security.enforceSSO,
      min_password_length: Number(f.security.minPasswordLength),
      session_timeout: f.security.sessionTimeout, allowed_domains: f.security.allowedDomains,
    }),
  },
  // The same keys the server sent. Only a switch can change a value, and switches exist only
  // for the catalog's configurable rows, so a PATCH only ever carries the configurable
  // preferences that actually moved (the server refuses unknown keys and pins the rest).
  notifs: {
    label: "Notifications", url: "/organization/notifications", from: notifsFrom,
    payload: (f) => ({ ...f.notifs }),
    saved: "Notification preferences updated successfully.",
  },
};

// The fields of one section whose value differs from the server-confirmed baseline.
const changedFields = (key, form, base) => {
  const now = SECTIONS[key].payload(form);
  const was = SECTIONS[key].payload(base);
  return Object.fromEntries(
    Object.entries(now).filter(([k, v]) => JSON.stringify(v) !== JSON.stringify(was[k]))
  );
};

export default function OrganizationSettings() {
  const { data, loading, error, reload } = useApi(loadSettings);
  // Server-described notification controls. null on an older API build, which the panel
  // falls back to handling.
  const catalog = data?.notifCatalog ?? null;
  // "ok" only when GET /organization/security actually returned a policy. "forbidden" is a
  // host or viewer; "error" is a real fault on an optional call. Derived from the response the
  // backend already gave, not from a stored role.
  const securityAccess = data?.securityAccess ?? "ok";
  const canManageSecurity = securityAccess === "ok";
  // ?tab= IS the tab state — not a seed for it.
  //
  // It used to initialise a useState, which meant the URL was read exactly once, at mount.
  // Every ?tab=security link (the rail's "Security & Governance", the profile's security
  // panel, Support & Status) therefore did nothing whenever the reader was ALREADY on this
  // page: the URL changed, React kept the old state, and the General panel stayed put — which
  // is why the rail looked like it pointed at the same page twice.
  //
  // Deriving from the URL instead makes every deep link land where it says, keeps back/forward
  // working across panels, and makes the address bar shareable. Unknown values fall back to
  // General rather than rendering an empty page.
  const [params, setParams] = useSearchParams();
  const requested = params.get("tab");
  const tab = TABS.some((t) => t.key === requested) ? requested : "general";
  // replace: switching panels is a filter, not a destination — pushing 6 entries onto the
  // history stack would make Back mean "previous tab" instead of "previous page".
  const setTab = (key) =>
    setParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        next.set("tab", key);
        return next;
      },
      { replace: true }
    );
  // `settings` is what the form shows; `baseline` is what the server last confirmed. A section
  // is dirty exactly when the two differ for it, so dirtiness is derived, never tracked by hand.
  const [settings, setSettings] = useState(null);
  const [baseline, setBaseline] = useState(null);
  const [seededData, setSeededData] = useState(null);
  const [saving, setSaving] = useState({});
  const [errors, setErrors] = useState({});
  const [sectionErrors, setSectionErrors] = useState({});
  // Synchronous guard: two clicks inside one render would both see saving=false.
  const inFlight = useRef(new Set());
  // Branding preview: open state, and the trigger to hand focus back to on close.
  const [previewOpen, setPreviewOpen] = useState(false);
  const previewTrigger = useRef(null);

  // Seed the editable form the first render fetched data arrives (and again after a
  // retry, which yields a fresh object). React's "adjust state during render" pattern,
  // guarded so it runs once per data object — no effect, no cascading render.
  if (data && data !== seededData) {
    const seeded = fromApi(data);
    setSeededData(data);
    setSettings(seeded);
    setBaseline(seeded);
    setErrors({});
    setSectionErrors({});
  }

  if (error) {
    return (
      <div className="space-y-6">
        <OrganizationErrorState error={error} onRetry={reload} title="Couldn't load settings" />
      </div>
    );
  }
  if (loading || !settings) {
    return (
      <div className="space-y-6">
        <div className="zk-skeleton h-8 w-56 rounded-lg bg-slate-200 dark:bg-slate-800" />
        <div className="zk-skeleton h-72 w-full rounded-2xl bg-slate-200 dark:bg-slate-800" />
      </div>
    );
  }

  // Editing a section clears that section's save error: the reader is fixing it.
  const patch = (key, updater) => {
    setSettings(updater);
    setSectionErrors((e) => (e[key] ? { ...e, [key]: undefined } : e));
  };
  // Editing a field clears its error — a stale red border under a corrected value is noise.
  const setProfile = (k, v) => {
    setErrors((e) => (e[k] ? { ...e, [k]: undefined } : e));
    patch("profile", (s) => ({ ...s, profile: { ...s.profile, [k]: v } }));
  };
  const setSec = (k, v) => patch("security", (s) => ({ ...s, security: { ...s.security, [k]: v } }));
  const toggleNotif = (k) => patch("notifs", (s) => ({ ...s, notifs: { ...s.notifs, [k]: !s.notifs[k] } }));

  const isDirty = (key) => Object.keys(changedFields(key, settings, baseline)).length > 0;
  const editable = Object.keys(SECTIONS).filter((k) => k !== "security" || canManageSecurity);
  const unsaved = editable.filter(isDirty);

  // One section, one request, only the changed fields. A failure stays in its own panel: the
  // other sections are neither re-sent nor blamed, and nothing typed is thrown away.
  const saveSection = async (key) => {
    if (inFlight.current.has(key)) return;
    const section = SECTIONS[key];
    if (key === "profile") {
      const found = validate(settings);
      if (Object.keys(found).length) {
        setErrors((e) => ({ ...e, ...found }));
        setSectionErrors((e) => ({ ...e, profile: "check the highlighted fields." }));
        return;
      }
    }
    const body = changedFields(key, settings, baseline);
    if (!Object.keys(body).length) return;

    inFlight.current.add(key);
    setSaving((x) => ({ ...x, [key]: true }));
    try {
      const { data: saved } = await api.patch(section.url, body);
      // The server's answer becomes the new baseline for THIS section only - a normalised
      // slug, say - and every other panel keeps its edits.
      const fresh = section.from(saved);
      setBaseline((b) => ({ ...b, ...fresh }));
      setSettings((f) => ({ ...f, ...fresh }));
      setSectionErrors((e) => ({ ...e, [key]: undefined }));
      notify.success(section.saved || `${section.label} saved`);
    } catch (err) {
      const fields = fieldErrors(err);
      setErrors((e) => ({ ...e, ...fields }));
      const message = Object.keys(fields).length ? "check the highlighted fields." : errMsg(err);
      setSectionErrors((e) => ({ ...e, [key]: message }));
      notify.error(`${section.label} not saved: ${message}`);
    } finally {
      inFlight.current.delete(key);
      setSaving((x) => ({ ...x, [key]: false }));
    }
  };
  const footer = (key) => (
    <SectionFooter
      id={key}
      label={SECTIONS[key].label}
      dirty={isDirty(key)}
      saving={Boolean(saving[key])}
      error={sectionErrors[key]}
      onSave={() => saveSection(key)}
    />
  );



  const orgName = settings.profile.name;
  const hasErrors = Object.values(errors).some(Boolean);
  // null (not []) means the key list request failed — an empty list would claim "no keys".

  return (
    <div className="space-y-6">
      {/* Header + Save */}
      {/* Sticky so Save is reachable from the bottom of a long panel — the reason a change
          could be made, scrolled past, and lost. */}
      <div className="sticky top-0 z-20 -mx-4 flex flex-col gap-4 border-b border-transparent bg-slate-50/95 px-4 py-3 backdrop-blur sm:flex-row sm:items-start sm:justify-between dark:bg-slate-950/95">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-slate-900 dark:text-white">Settings</h1>
          <p className="text-sm text-slate-500 dark:text-slate-400">Manage your organization's profile, security, and integrations</p>
        </div>
        <div className="flex items-center gap-3">
          {/* Each panel has its own Save. This line only says where unsaved edits are, so a
              change made on one tab is not forgotten on another. */}
          {hasErrors ? (
            <span className="text-xs font-medium text-rose-600 dark:text-rose-400">Check the highlighted fields</span>
          ) : (
            unsaved.length > 0 && (
              <span className="text-xs font-medium text-amber-600 dark:text-amber-400" data-testid="unsaved-summary">
                Unsaved changes: {unsaved.map((k) => SECTIONS[k].label).join(", ")}
              </span>
            )
          )}
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
                  <Field label="Organization Name" error={errors.name}>
                    <Input variant="form" error={errors.name} maxLength={120} value={settings.profile.name} onChange={(e) => setProfile("name", e.target.value)} />
                  </Field>
                  <Field
                    label="URL Slug"
                    error={errors.slug}
                    hint={`zoikostream.com/o/${settings.profile.slug || "…"} · lowercase, numbers and dashes`}
                  >
                    <Input
                      variant="form"
                      error={errors.slug}
                      maxLength={140}
                      value={settings.profile.slug}
                      onChange={(e) => setProfile("slug", slugify(e.target.value))}
                    />
                  </Field>
                  <Field label="Website" error={errors.website}>
                    <Input variant="form" error={errors.website} maxLength={255} placeholder="https://yourcompany.com" value={settings.profile.website} onChange={(e) => setProfile("website", e.target.value)} />
                  </Field>
                  <Field label="Support Email" error={errors.supportEmail}>
                    <Input variant="form" type="email" error={errors.supportEmail} maxLength={255} placeholder="support@yourcompany.com" value={settings.profile.supportEmail} onChange={(e) => setProfile("supportEmail", e.target.value)} />
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
                  <Field
                    label="Description"
                    className="sm:col-span-2"
                    error={errors.description}
                    hint={`${settings.profile.description.length}/2000`}
                  >
                    <Textarea variant="form" rows={3} maxLength={2000} error={errors.description} value={settings.profile.description} onChange={(e) => setProfile("description", e.target.value)} />
                  </Field>
                </div>
                {footer("profile")}
              </Panel>

              <CustomDomainPanel initial={data.domain} />
            </>
          )}

          {/* ── BRANDING: Logo + brand color ── */}
          {tab === "branding" && (
            <Panel title="Branding" desc="Personalize how ZoikoStream looks for your audience">
              {/* Was a file picker that only remembered the filename — there is no upload
                  endpoint, so nothing left the browser. logo_url IS a real stored field, so
                  the honest control is the URL, and it saves with everything else. */}
              <Field
                label="Logo URL"
                error={errors.logo_url}
                hint="Direct link to a square PNG or SVG. Stored on your organization — nothing displays it yet; event pages and emails still show the ZoikoStream mark."
              >
                <div className="flex flex-wrap items-center gap-4">
                  <div className="grid h-16 w-16 shrink-0 place-items-center overflow-hidden rounded-xl border border-slate-200 bg-slate-50 dark:border-slate-700 dark:bg-slate-800">
                    <img
                      src={settings.logoUrl.trim() || "/zoiko-logo.png"}
                      alt="Organization logo"
                      className="max-h-full max-w-full object-contain"
                      // A bad URL falls back to the platform mark instead of a broken image.
                      onError={(e) => { e.currentTarget.src = "/zoiko-logo.png"; }}
                    />
                  </div>
                  <div className="min-w-[240px] flex-1">
                    <div className="relative">
                      <FiUploadCloud className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" aria-hidden="true" />
                      <Input
                        variant="form"
                        className="pl-9"
                        maxLength={500}
                        error={errors.logo_url}
                        placeholder="https://cdn.yourcompany.com/logo.png"
                        value={settings.logoUrl}
                        onChange={(e) => { setErrors((x) => (x.logo_url ? { ...x, logo_url: undefined } : x)); patch("branding", (s) => ({ ...s, logoUrl: e.target.value })); }}
                      />
                    </div>
                  </div>
                </div>
              </Field>

              <div className="mt-6">
                <Field
                  label="Brand Color"
                  hint="Stored on your organization. Nothing renders from it yet — the console, emails and event pages still use the ZoikoStream palette."
                >
                  <div className="flex flex-wrap gap-2.5">
                    {ACCENTS.map((a) => {
                      const selected = settings.accent === a;
                      return (
                        <button
                          key={a}
                          type="button"
                          onClick={() => patch("branding", (s) => ({ ...s, accent: a }))}
                          aria-label={a}
                          aria-pressed={selected}
                          title={a}
                          className={cx(
                            "grid h-9 w-9 place-items-center rounded-full ring-2 ring-offset-2 transition duration-150 ring-offset-white hover:scale-110 motion-reduce:transition-none motion-reduce:hover:scale-100 dark:ring-offset-slate-900",
                            ACCENT[a].solid,
                            selected ? "ring-slate-900 dark:ring-white" : "ring-transparent"
                          )}
                        >
                          {/* A tick, not only a ring — two adjacent purples are hard to tell apart
                              by outline alone. */}
                          <FiCheck
                            className={cx("text-base text-white transition-opacity", selected ? "opacity-100" : "opacity-0")}
                            aria-hidden="true"
                          />
                        </button>
                      );
                    })}
                  </div>
                </Field>
                <div className="mt-4 rounded-xl border border-slate-200 p-4 dark:border-slate-700">
                  <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-400">
                    Preview · <span className="capitalize">{settings.accent}</span>
                  </p>
                  {/* Was a <span> styled as a button: it looked like an action and did nothing.
                      It now opens the preview, with the current (unsaved) values. */}
                  <div className="flex flex-wrap items-center gap-3">
                    <button
                      ref={previewTrigger}
                      type="button"
                      onClick={() => setPreviewOpen(true)}
                      aria-haspopup="dialog"
                      aria-label="Go Live — open branding preview"
                      title="Open a preview. Nothing is started."
                      className={cx(
                        "inline-flex items-center rounded-lg px-4 py-2 text-sm font-semibold text-white transition hover:brightness-110",
                        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-slate-900 focus-visible:ring-offset-2 dark:focus-visible:ring-white dark:focus-visible:ring-offset-slate-900",
                        ACCENT[settings.accent].solid
                      )}
                    >
                      Go Live
                    </button>
                    <span className="text-xs text-slate-500 dark:text-slate-400">
                      Opens a preview — nothing is started.
                    </span>
                  </div>
                </div>
                <BrandingPreview
                  open={previewOpen}
                  onClose={() => {
                    setPreviewOpen(false);
                    // Back to where the reader was, not the top of the page.
                    requestAnimationFrame(() => previewTrigger.current?.focus());
                  }}
                  accent={settings.accent}
                  logoUrl={settings.logoUrl}
                  orgName={settings.profile.name}
                />
              </div>
              {footer("branding")}
            </Panel>
          )}

          {/* ── SECURITY + User Permissions ── */}
          {tab === "security" && (
            <>
              {/* The password change itself, in place. It used to be a button that sent a
                  signed-IN admin to /forgot-password — the signed-out recovery flow — to
                  re-prove an identity the session had already proved. The minimum is passed
                  as a hint only; PATCH /api/auth/password re-validates against the same
                  org_policy the control below writes to. */}
              <Panel title="Account Security" desc="Change the password you sign in with">
                <ChangePasswordForm minLength={settings.security.minPasswordLength} />
              </Panel>

              {/* Org-wide policy, admin-only at GET and PATCH alike. A host or viewer never
                  had it; what changed is that they now keep the rest of the page. The panel
                  stays in place and says why it is empty rather than vanishing, so the
                  absence is legible instead of looking like a missing feature. Account
                  Security above is deliberately NOT gated — changing your own password is
                  every member's business and runs through PATCH /api/auth/password, which
                  has no org_admin gate. */}
              {canManageSecurity && (
                <Panel title="Security Configuration" desc="Authentication and access policies for your organization">
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
                  {footer("security")}
                </Panel>
              )}
              {securityAccess === "forbidden" && (
                <Panel
                  title="Security Configuration"
                  desc="Authentication and access policies for your organization"
                >
                  <div className="flex items-start gap-3 py-1">
                    <span
                      className="mt-0.5 grid h-7 w-7 shrink-0 place-items-center rounded-lg bg-slate-100 text-slate-500 dark:bg-white/[0.07] dark:text-neutral-400"
                      aria-hidden="true"
                    >
                      <FiLock className="text-sm" />
                    </span>
                    <div className="min-w-0">
                      <p className="text-sm font-medium text-slate-800 dark:text-slate-100">
                        Organization admin access required.
                      </p>
                      <p className="mt-0.5 text-xs text-slate-500 dark:text-slate-400">
                        Two-factor, SSO, password policy, session timeout and allowed email
                        domains are managed by an organization admin. Contact one if you need
                        these changed.
                      </p>
                    </div>
                  </div>
                </Panel>
              )}
              {securityAccess === "error" && (
                <Panel
                  title="Security Configuration"
                  desc="Authentication and access policies for your organization"
                >
                  {/* NOT a permission problem — say so, and offer the retry. Showing the
                      "admin access required" copy here would blame the reader for a fault. */}
                  <div className="flex flex-wrap items-center justify-between gap-3 py-1">
                    <p className="text-sm text-slate-600 dark:text-slate-300">
                      Couldn&apos;t load the security policy.
                    </p>
                    <Button variant="secondary" size="sm" onClick={reload}>Retry</Button>
                  </div>
                </Panel>
              )}

              {/* Read-only on purpose. These capabilities are enforced in the API (role gates
                  like require_org_admin) and in the router's RoleRoute allow-lists — there is
                  no per-org permission store behind them. Editable checkboxes here saved
                  nothing, which made a security screen say the opposite of the truth. */}
              <Panel
                title="Role capabilities"
                desc="What each role can do today. Enforced by the platform — the Owner always has full access."
              >
                <div className="overflow-x-auto">
                  <table className="w-full min-w-[560px] text-sm">
                    <caption className="sr-only">Capabilities granted to each organization role</caption>
                    <thead>
                      <tr className="border-b border-slate-100 dark:border-slate-800">
                        <th scope="col" className="py-2 pr-3 text-left text-xs font-semibold uppercase tracking-wide text-slate-400">Capability</th>
                        {ROLES.map((r) => (
                          <th key={r} scope="col" className="px-3 py-2 text-center text-xs font-semibold uppercase tracking-wide text-slate-400">{r}</th>
                        ))}
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                      {PERMISSIONS.map((p) => (
                        <tr key={p.key}>
                          <th scope="row" className="py-2.5 pr-3 text-left font-normal text-slate-700 dark:text-slate-200">{p.label}</th>
                          {ROLES.map((r) => {
                            const granted = permissionDefaults[r].includes(p.key);
                            return (
                              <td key={r} className="px-3 py-2.5 text-center">
                                <span className="sr-only">{granted ? "Granted" : "Not granted"}</span>
                                {granted ? (
                                  <FiCheck className="mx-auto text-base text-emerald-600 dark:text-emerald-400" aria-hidden="true" />
                                ) : (
                                  <span className="mx-auto block h-px w-3 bg-slate-300 dark:bg-slate-600" aria-hidden="true" />
                                )}
                              </td>
                            );
                          })}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <p className="mt-4 text-xs text-slate-500 dark:text-slate-400">
                  Need a different split? Change a member's role under{" "}
                  <Link to="/organization/users" className="font-medium text-emerald-700 hover:underline dark:text-emerald-400">
                    Members &amp; Access
                  </Link>
                  . Custom per-role permissions aren't available yet.
                </p>
              </Panel>
            </>
          )}

          {/* ── NOTIFICATIONS ── */}
          {tab === "notifications" && (
            <Panel
              title="Notifications"
              desc="Choose what your team gets notified about"
            >
              {catalog ? (
                <>
                  {/* Three groups, decided by the server rather than by this file:
                      configurable, mandatory (always on) and not-yet-available. A control
                      that cannot change anything is never rendered as a working switch. */}
                  {[
                    { title: "Operational notifications",
                      rows: catalog.filter((c) => c.configurable) },
                    { title: "Always on",
                      rows: catalog.filter((c) => c.mandatory) },
                    { title: "Not available yet",
                      rows: catalog.filter((c) => !c.configurable && !c.mandatory) },
                  ].filter((g) => g.rows.length > 0).map((g) => (
                    <div key={g.title} className="mb-6 last:mb-0">
                      <h3 className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-400">
                        {g.title}
                      </h3>
                      {g.rows.map((it) => (
                        <SettingRow
                          key={it.key}
                          title={it.label}
                          desc={`${it.description} ${scopeNote(it)}`}
                        >
                          {it.mandatory ? (
                            <span className="whitespace-nowrap rounded-full bg-emerald-50 px-2.5 py-1 text-xs font-semibold text-emerald-700 dark:bg-emerald-500/10 dark:text-emerald-300">
                              Always on
                            </span>
                          ) : it.configurable ? (
                            <Switch
                              checked={settings.notifs[it.key]}
                              onChange={() => toggleNotif(it.key)}
                            />
                          ) : (
                            <span className="whitespace-nowrap rounded-full bg-slate-100 px-2.5 py-1 text-xs font-semibold text-slate-500 dark:bg-slate-800 dark:text-slate-400">
                              Unavailable
                            </span>
                          )}
                        </SettingRow>
                      ))}
                    </div>
                  ))}
                  <p className="mt-4 text-xs text-slate-500 dark:text-slate-400">
                    Mandatory security, legal, access, and contract communications cannot be
                    disabled.
                  </p>
                </>
              ) : (
                // No catalog, no switches: without the server's description of what each
                // preference controls, a switch could claim to govern an email that is
                // mandatory or that is never sent.
                <p role="alert" className="rounded-xl bg-amber-50 px-3 py-2.5 text-sm text-amber-800 dark:bg-amber-500/10 dark:text-amber-300">
                  Notification settings couldn&apos;t be loaded. Refresh the page to try again.
                </p>
              )}
              {catalog && footer("notifs")}
            </Panel>
          )}

          {/* ── DEVELOPER: API Keys + Integrations ── */}
          {tab === "developer" && (
            <>
              {/* The FULL credential UI, not a second copy of it.
                  This panel used to be a lite list/create/revoke talking to its own route
                  pair (/organization/api-keys) against the same `org.api_keys` column the
                  Credentials screen writes through /organization/developer/api-keys. Two
                  code paths over one store is how two screens quietly start disagreeing —
                  and this one could not show expiry, lifecycle state or inventory at all.
                  Both now render the same component. */}
              <Panel title="API Credentials" desc="Keys this organization authenticates API requests with">
                <ApiCredentials embedded />
              </Panel>

              {/* Was a grid of connect/disconnect toggles for Slack, Zoom, Salesforce… none of
                  which had an endpoint, so every "Connected" badge was decoration. These are
                  the integration surfaces that do exist. */}
              <Panel title="Integrations" desc="Connect ZoikoStream to the tools your team already uses">
                <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                  {[
                    [FiCode, "API reference", "Endpoints, versioning and platform status", "/organization/developers"],
                    [FiVideo, "Live inputs", "RTMP / WHIP ingest for OBS, vMix and hardware encoders", "/organization/live-inputs"],
                    [FiCloud, "Playback & access", "Where and how your streams can be watched", "/organization/playback"],
                  ].map(([Icon, name, desc, to]) => (
                    <Link
                      key={to}
                      to={to}
                      className="group flex items-start gap-3 rounded-xl border border-slate-100 p-4 transition hover:border-emerald-300 hover:bg-emerald-50/40 dark:border-slate-800 dark:hover:border-emerald-500/40 dark:hover:bg-emerald-500/5"
                    >
                      <span className="grid h-10 w-10 shrink-0 place-items-center rounded-xl bg-slate-100 text-slate-600 transition group-hover:bg-emerald-100 group-hover:text-emerald-700 dark:bg-slate-800 dark:text-slate-300 dark:group-hover:bg-emerald-500/15 dark:group-hover:text-emerald-400">
                        <Icon />
                      </span>
                      <div className="min-w-0 flex-1">
                        <p className="font-medium text-slate-800 dark:text-slate-100">{name}</p>
                        <p className="text-xs text-slate-500 dark:text-slate-400">{desc}</p>
                      </div>
                    </Link>
                  ))}
                </div>
                <p className="mt-4 text-xs text-slate-500 dark:text-slate-400">
                  Slack, Zoom, Salesforce and Zapier connectors aren't available yet —{" "}
                  <Link to="/contact" className="font-medium text-emerald-700 hover:underline dark:text-emerald-400">
                    tell us which you need
                  </Link>
                  .
                </p>
              </Panel>
            </>
          )}

          {/* ── DANGER ZONE ── */}
          {tab === "danger" && (
            // Both actions used to open a confirm dialog and toast "invite sent" / "scheduled
            // for deletion" while doing nothing at all. There is no endpoint for either, and
            // the API deliberately refuses self-deletion of your own org (admin.py: "Cannot
            // delete your own organization"), so the honest surface is the support route.
            <Panel title="Danger Zone" desc="Handled by platform support, not self-service" className="border-rose-200 dark:border-rose-500/30">
              <div className="flex flex-col gap-4 rounded-xl border border-slate-100 p-4 sm:flex-row sm:items-center sm:justify-between dark:border-slate-800">
                <div className="min-w-0">
                  <p className="text-sm font-medium text-slate-800 dark:text-slate-100">Transfer ownership</p>
                  <p className="text-xs text-slate-500 dark:text-slate-400">
                    Moving <strong className="font-semibold">{orgName}</strong> to another owner is done by support so
                    billing and access move with it. Members can be promoted to admin any time under{" "}
                    <Link to="/organization/users" className="font-medium text-emerald-700 hover:underline dark:text-emerald-400">
                      Members &amp; Access
                    </Link>
                    .
                  </p>
                </div>
                <Button variant="secondary" size="sm" href="/contact">Contact support</Button>
              </div>
              <div className="mt-3 flex flex-col gap-4 rounded-xl border border-rose-200 bg-rose-50/50 p-4 sm:flex-row sm:items-center sm:justify-between dark:border-rose-500/30 dark:bg-rose-500/5">
                <div className="min-w-0">
                  <p className="text-sm font-medium text-rose-700 dark:text-rose-300">Delete organization</p>
                  <p className="text-xs text-rose-600/80 dark:text-rose-400/80">
                    Permanently removes this organization, its events, recordings and analytics.
                    Support confirms ownership before anything is deleted.
                  </p>
                </div>
                <Button variant="danger" size="sm" href="/contact">Request deletion</Button>
              </div>
            </Panel>
          )}
        </div>
      </div>


    </div>
  );
}
