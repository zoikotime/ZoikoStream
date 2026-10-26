import { Link } from "react-router-dom";
import {
  FiActivity, FiAlertTriangle, FiCalendar, FiChevronRight, FiCode, FiCreditCard, FiHome, FiImage,
  FiKey, FiLock, FiSettings, FiShield, FiUsers,
} from "react-icons/fi";
import api from "../../api";
import useApi from "../../hooks/useApi";
import OrganizationPageHeader from "../../components/organization/OrganizationPageHeader";
import { useOrgScope } from "../../components/organization/orgScope";
import {
  Banner, IconChip, KpiRow, KpiTile, Panel, PanelLink, Tag,
} from "../../components/organization/console/ConsoleUI";
import { CARD } from "../../components/organization/console/consoleStyles";
import { roleLabel } from "../../components/organization/profile/derive";
import { ConsoleButton } from "../../ui/Button";
import UserAvatar from "../../ui/UserAvatar";
import { CONSOLE, cx, focusRing } from "../../ui/tokens";

// Organization (ZST-UX-NAV-001 §4, ORG-06): the home of everything that is about the
// organization rather than an event — team and roles, branding, security & audit, plan and
// billing, settings, and integrations. Each card states one real fact from its own source and
// links to the page that owns it; a source that fails says so and the rest still render.
// Governance priorities are derived from those same sources — nothing is listed that the API
// did not report.

function AreaCard({ to, icon, tone, title, description, fact, wide = false }) {
  return (
    <Link
      to={to}
      className={cx(
        "group flex rounded-xl border p-3.5 transition-colors duration-150 hover:border-violet-300 hover:bg-violet-50/30 motion-reduce:transition-none dark:hover:border-violet-400/40 dark:hover:bg-white/[0.03]",
        CONSOLE.divider,
        wide ? "items-center gap-4" : "flex-col gap-3",
        focusRing
      )}
    >
      <div className={cx("flex items-start justify-between gap-3", wide && "shrink-0")}>
        <IconChip icon={icon} tone={tone} size="sm" />
        {!wide && <FiChevronRight aria-hidden="true" className={cx("transition-transform group-hover:translate-x-0.5", CONSOLE.faint)} />}
      </div>
      <div className="min-w-0 flex-1">
        <h3 className={cx("text-[13.5px] font-semibold", CONSOLE.heading)}>{title}</h3>
        <p className={cx("mt-0.5 text-[12.5px]", CONSOLE.muted)}>{description}</p>
        <p className={cx("mt-2 text-[12.5px] font-medium", CONSOLE.body)}>{fact}</p>
      </div>
      {wide && <FiChevronRight aria-hidden="true" className={cx("shrink-0 transition-transform group-hover:translate-x-0.5", CONSOLE.faint)} />}
    </Link>
  );
}

const plural = (n, one, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

// A member's event-scoped access, in words: "2 events · host, speaker" or none. "—" when the
// grants could not be read, so a failed request never reads as "no access".
function scopeOf(access, userId) {
  if (access.error) return "—";
  if (!access.data) return "…";
  const m = access.data.members?.[userId];
  if (!m) return "No event assignments";
  return `${plural(m.events, "event")} · ${Object.keys(m.roles).sort().join(", ")}`;
}

export default function OrganizationHub() {
  const { state } = useOrgScope();
  const members = useApi(() => api.get("/organization/users", { params: { page_size: 5 } }).then((r) => r.data));
  const invites = useApi(() =>
    api.get("/organization/invitations", { params: { status: "pending", page_size: 1 } }).then((r) => r.data)
  );
  const events = useApi(() => api.get("/events", { params: { page_size: 1 } }).then((r) => r.data));
  // Event-scoped role grants (host / speaker on one event each) and every member's scope.
  const access = useApi(() => api.get("/organization/team/access").then((r) => r.data));
  const branding = useApi(() => api.get("/organization/branding").then((r) => r.data));
  const audit = useApi(() => api.get("/organization/activity", { params: { page_size: 1 } }).then((r) => r.data));

  const fact = (q, ok) => (q.error ? "Unavailable right now" : !q.data ? "…" : ok(q.data));
  // A KPI figure: null (rendered "—") until it loads or when its source failed.
  const count = (q, pick) => (q.data && !q.error ? pick(q.data) ?? null : null);
  const caption = (q, text) => (q.error ? "Unavailable right now" : text);
  const org = state?.organization;
  const viewer = state?.user;

  const pending = count(invites, (d) => d.total);
  const refused = count(audit, (d) => d.summary?.refused);
  const highImpact = count(audit, (d) => d.summary?.high_impact);
  const priorities = [
    pending > 0 && {
      key: "invites", dot: "bg-amber-500", title: `Follow up ${plural(pending, "pending invitation")}`,
      detail: "Invited staff who have not accepted yet", to: "/organization/users", link: "Team",
    },
    refused > 0 && {
      key: "refused", dot: "bg-violet-500", title: `Review ${plural(refused, "refused action")}`,
      detail: "Requests the server declined, with their reasons", to: "/organization/security?outcome=refused", link: "Security",
    },
    highImpact > 0 && {
      key: "impact", dot: "bg-rose-500", title: `${plural(highImpact, "high-impact action")} recorded`,
      detail: "Lifecycle, access and recording changes worth a second look", to: "/organization/security", link: "Security",
    },
    branding.data && !branding.data.logo_url && {
      key: "logo", dot: "bg-sky-500", title: "Add your organization's logo",
      detail: "Viewers see the ZoikoStream mark until you do", to: "/organization/settings?tab=branding", link: "Branding",
    },
  ].filter(Boolean);
  const reviewKnown = !invites.loading && !audit.loading && !branding.loading;

  const team = members.data?.items || [];
  const colours = [branding.data?.primary_color, branding.data?.secondary_color].filter(Boolean);

  return (
    <div className="space-y-6">
      <OrganizationPageHeader
        breadcrumbs={[["Organization", "/organization/manage"], ["Overview"]]}
        eyebrow="Organization console / Workspace governance"
        title="Organization"
        subtitle="Manage your people, security, identity and account configuration in one place."
        actions={
          <>
            {viewer?.role_label && (
              <span className={cx("inline-flex h-9 items-center gap-1.5 rounded-lg border px-3 text-[12.5px] font-medium", CONSOLE.control)}>
                <FiShield aria-hidden="true" /> {viewer.role_label} view
              </span>
            )}
            <ConsoleButton variant="secondary" size="sm" href="/organization/security" leftIcon={FiActivity}>
              View activity
            </ConsoleButton>
          </>
        }
      />

      <section aria-label="Organization identity" className={cx(CARD, "flex flex-col gap-3 p-4 sm:flex-row sm:items-center")}>
        <span aria-hidden="true" className="grid h-12 w-12 shrink-0 place-items-center rounded-xl bg-gradient-to-br from-violet-600 to-fuchsia-600 text-[20px] text-white">
          <FiHome />
        </span>
        <div className="min-w-0 flex-1">
          <p className={cx("truncate text-[16px] font-semibold", CONSOLE.heading)}>{org?.name || "Your organization"}</p>
          <p className={cx("text-[12.5px]", CONSOLE.faint)}>
            Organization scope{org?.plan ? ` · ${org.plan} plan` : ""}{org?.status ? ` · Status: ${org.status}` : ""}
          </p>
        </div>
        <Tag tone="slate"><FiLock aria-hidden="true" /> Settings and actions resolved by permissions</Tag>
      </section>

      <KpiRow>
        <KpiTile
          icon={FiUsers}
          label="Team members"
          value={count(members, (d) => d.total)}
          caption={caption(members, "Staff accounts in this organization")}
          to="/organization/users"
        />
        <KpiTile
          icon={FiKey}
          tone="sky"
          label="Assigned access grants"
          value={count(access, (d) => d.grants)}
          caption={caption(access, "Event roles are scope-specific")}
        />
        <KpiTile
          icon={FiAlertTriangle}
          tone="amber"
          label="Items to review"
          value={reviewKnown ? priorities.length : null}
          caption="From the governance checks below"
        />
        <KpiTile
          icon={FiCalendar}
          tone="green"
          label="Events in scope"
          value={count(events, (d) => d.total)}
          caption={caption(events, "Events this organization holds")}
          to="/organization/events"
        />
      </KpiRow>

      <Banner title="Internal team members are not your event audience.">
        Staff accounts, event crew assignments, invited viewers and playback sessions are separate records. Permissions and
        entitlements are enforced by the server, not by this page.
      </Banner>

      <div className="grid items-start gap-6 xl:grid-cols-[minmax(0,1fr)_22rem] 2xl:grid-cols-[minmax(0,1.55fr)_minmax(0,1fr)]">
        <div className="min-w-0 space-y-6">
          <Panel title="Manage your organization" subtitle="Administration destinations" tag="6 core areas" tagTone="slate">
            <div className="grid gap-3 sm:grid-cols-2">
              <AreaCard
                to="/organization/users"
                icon={FiUsers}
                title="Team & roles"
                description="Members of your organization, their roles and invitations."
                fact={fact(members, (d) => `${d.total} member${d.total === 1 ? "" : "s"}`)}
              />
              <AreaCard
                to="/organization/security"
                icon={FiShield}
                tone="green"
                title="Security & audit"
                description="Every recorded action and refusal, with request references."
                fact={fact(audit, (d) =>
                  `${d.summary.total} recorded action${d.summary.total === 1 ? "" : "s"} · ${d.summary.refused} refused`
                )}
              />
              <AreaCard
                to="/organization/settings?tab=branding"
                icon={FiImage}
                tone="pink"
                title="Branding"
                description="Logo and colours viewers see on your event pages."
                fact={fact(branding, (d) => (d.logo_url ? "Logo set" : "No logo yet — the ZoikoStream mark is shown"))}
              />
              <AreaCard
                to="/organization/billing"
                icon={FiCreditCard}
                tone="sky"
                title="Plan, usage & billing"
                description="Your plan, entitlements, usage and invoices."
                fact={org?.plan ? `${org.plan} plan` : "No plan on record"}
              />
            </div>
            <div className="mt-3 grid gap-3">
              <AreaCard
                wide
                to="/organization/settings"
                icon={FiSettings}
                tone="slate"
                title="Organization settings"
                description="Profile, security policy, notifications and domain."
                fact={org?.status ? `Status: ${org.status}` : "—"}
              />
              <AreaCard
                wide
                to="/organization/developers"
                icon={FiCode}
                tone="slate"
                title="Integrations"
                description="API credentials, webhooks and the developer platform."
                fact={state?.features?.developers ? "Developer access in use" : "Not set up"}
              />
            </div>
          </Panel>

          <Panel
            title="Team & access"
            subtitle="Staff accounts and their roles — not audience registrations"
            action={<PanelLink to="/organization/users">Open Team & roles</PanelLink>}
            flush
          >
            {members.error ? (
              <p className="border-t border-slate-100 px-5 py-8 text-center text-[13px] text-rose-600 dark:border-white/[0.08] dark:text-rose-300">
                Couldn&apos;t load your team right now.
              </p>
            ) : !members.data ? (
              <p className={cx("border-t px-5 py-8 text-center text-[13px]", CONSOLE.divider, CONSOLE.faint)}>Loading team…</p>
            ) : team.length === 0 ? (
              <p className={cx("border-t px-5 py-8 text-center text-[13px]", CONSOLE.divider, CONSOLE.faint)}>No team members yet.</p>
            ) : (
              <div className="overflow-x-auto border-t border-slate-100 dark:border-white/[0.08]">
                <table className="w-full min-w-[560px] text-left">
                  <thead className={cx("bg-slate-50/70 text-[11px] uppercase tracking-wider dark:bg-white/[0.03]", CONSOLE.muted)}>
                    <tr>
                      <th scope="col" className="py-2.5 pl-5 pr-3 font-semibold">Team member</th>
                      <th scope="col" className="px-3 py-2.5 font-semibold">Primary role</th>
                      <th scope="col" className="px-3 py-2.5 font-semibold">Access scope</th>
                      <th scope="col" className="px-3 py-2.5 font-semibold">Type</th>
                      <th scope="col" className="px-3 py-2.5 font-semibold">Status</th>
                    </tr>
                  </thead>
                  <tbody className={cx("divide-y", CONSOLE.divideY)}>
                    {team.map((u) => (
                      <tr key={u.id}>
                        <td className="py-3 pl-5 pr-3">
                          <div className="flex min-w-0 items-center gap-3">
                            <UserAvatar
                              name={u.full_name}
                              src={u.avatar_url}
                              className="h-8 w-8 shrink-0 rounded-lg bg-gradient-to-br from-violet-600 to-fuchsia-600 text-[11px] font-bold text-white"
                            />
                            <div className="min-w-0">
                              <p className={cx("truncate text-[13px] font-semibold", CONSOLE.heading)}>{u.full_name}</p>
                              <p className={cx("truncate text-[12px]", CONSOLE.faint)}>{u.email}</p>
                            </div>
                          </div>
                        </td>
                        <td className={cx("whitespace-nowrap px-3 py-3 text-[13px]", CONSOLE.body)}>{roleLabel(u.role)}</td>
                        <td className={cx("whitespace-nowrap px-3 py-3 text-[13px]", CONSOLE.body)}>
                          {u.role === "org_admin" ? "Organization-wide" : scopeOf(access, u.id)}
                        </td>
                        <td className="px-3 py-3">
                          <Tag plain tone={u.role === "org_admin" ? "violet" : "slate"}>{u.role === "org_admin" ? "Owner" : "Scoped"}</Tag>
                        </td>
                        <td className="px-3 py-3">
                          <Tag plain tone={u.is_active ? "green" : "slate"}>{u.is_active ? "Active" : "Inactive"}</Tag>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            <div className={cx("flex flex-wrap items-center justify-between gap-2 border-t px-5 py-2.5 text-[12px]", CONSOLE.divider, CONSOLE.faint)}>
              <span className="inline-flex items-center gap-1.5"><FiLock aria-hidden="true" /> Visible to organization admins only.</span>
              {members.data?.total > team.length && (
                <PanelLink to="/organization/users">View all {members.data.total} members</PanelLink>
              )}
            </div>
          </Panel>
        </div>

        <aside className="min-w-0 space-y-6" aria-label="Governance">
          <Panel
            title="Governance priorities"
            subtitle="Items for the right owner, from live records"
            tag={reviewKnown && priorities.length > 0 ? `${priorities.length} to review` : undefined}
            tagTone="amber"
          >
            {!reviewKnown ? (
              <p className={cx("py-4 text-center text-[12.5px]", CONSOLE.faint)}>Checking…</p>
            ) : priorities.length === 0 ? (
              <p className={cx("rounded-xl border border-dashed px-4 py-5 text-center text-[12.5px]", CONSOLE.divider, CONSOLE.faint)}>
                Nothing needs review right now.
              </p>
            ) : (
              <ul className={cx("divide-y", CONSOLE.divideY)}>
                {priorities.map((p) => (
                  <li key={p.key} className="flex items-center gap-3 py-2.5">
                    <span aria-hidden="true" className={cx("h-2.5 w-2.5 shrink-0 rounded-full", p.dot)} />
                    <div className="min-w-0 flex-1">
                      <p className={cx("text-[13px] font-semibold", CONSOLE.heading)}>{p.title}</p>
                      <p className={cx("text-[12px]", CONSOLE.faint)}>{p.detail}</p>
                    </div>
                    <PanelLink to={p.to}>{p.link}</PanelLink>
                  </li>
                ))}
              </ul>
            )}
          </Panel>

          <Panel title="Plan, usage & billing" subtitle="Commercial details for billing administrators" tag="Admin only" tagTone="slate">
            <div className="flex items-start gap-3">
              <IconChip icon={FiCreditCard} tone="violet" size="sm" />
              <div className="min-w-0">
                <p className={cx("text-[13px] font-semibold", CONSOLE.heading)}>{org?.plan ? `${org.plan} plan` : "No plan on record"}</p>
                <p className={cx("text-[12px]", CONSOLE.faint)}>Pricing, quotas and renewal come from your entitlements, not from this page.</p>
              </div>
            </div>
            <div className="mt-3">
              <PanelLink to="/organization/billing">Open billing overview</PanelLink>
            </div>
          </Panel>

          <Panel
            title="Brand identity"
            subtitle="What viewers see on your event pages"
            action={<PanelLink to="/organization/settings?tab=branding">Open branding</PanelLink>}
          >
            <div className="flex items-center gap-3">
              {branding.data?.logo_url ? (
                <img src={branding.data.logo_url} alt="" className="h-11 w-11 shrink-0 rounded-xl border border-slate-200 bg-white object-contain p-1 dark:border-white/10" />
              ) : (
                <span aria-hidden="true" className="grid h-11 w-11 shrink-0 place-items-center rounded-xl bg-[#1b1f4d] text-white"><FiHome /></span>
              )}
              <div className="min-w-0 flex-1">
                <p className={cx("truncate text-[13px] font-semibold", CONSOLE.heading)}>{org?.name || "Your organization"}</p>
                <p className={cx("text-[12px]", CONSOLE.faint)}>
                  {branding.error
                    ? "Branding unavailable right now"
                    : !branding.data
                    ? "…"
                    : branding.data.logo_url
                    ? "Logo set"
                    : "No logo yet — the ZoikoStream mark is shown"}
                </p>
              </div>
              {colours.length > 0 && (
                <span className="flex shrink-0 gap-1" aria-label={`Brand colours ${colours.join(", ")}`} role="img">
                  {colours.map((c) => (
                    <span key={c} className="h-4 w-4 rounded border border-black/10" style={{ background: c }} title={c} />
                  ))}
                </span>
              )}
            </div>
          </Panel>
        </aside>
      </div>
    </div>
  );
}
