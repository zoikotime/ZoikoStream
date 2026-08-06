import { useMemo, useState } from "react";
import { NavLink, useParams } from "react-router-dom";
import { FiSearch, FiCopy, FiArrowRight, FiExternalLink } from "react-icons/fi";
import SitePage from "../../layouts/SitePage";
import NotFound from "../NotFound";
import { Button, Card, Container, Heading, Text, Badge, CodeBlock, FeatureCard, Reveal } from "../../ui";
import { STAGE_COLOR, cx, focusRing, type } from "../../ui/tokens";
import { notify } from "../../ui/Toast";
import {
  DOC_SECTIONS, DOC_LIFECYCLE, API_GROUPS, SDKS, GUIDES, ARCHITECTURE_LAYERS, API_VERSION,
} from "../../data/site";

// Documentation, API Reference, SDKs, Sandbox, Architecture and Guides — one shell, six routes.
//
// The docs are the ONE searchable surface on this site, which is why the marketing header no
// longer carries a global search button: there is a content index here and nowhere else, and the
// search below filters this section's real content rather than pretending to query a server.
//
// The API reference is transcribed from the routers in this repository, so it documents endpoints
// that exist. The sandbox is explicit that it is a shape reference rather than a live console —
// executing requests would need a backend proxy, which is out of scope here.
const METHOD_TONE = {
  GET: "bg-blue-100 text-blue-700 dark:bg-blue-500/15 dark:text-blue-400",
  POST: "bg-green-100 text-green-700 dark:bg-green-500/15 dark:text-green-400",
  PATCH: "bg-amber-100 text-amber-700 dark:bg-amber-500/15 dark:text-amber-400",
  PUT: "bg-amber-100 text-amber-700 dark:bg-amber-500/15 dark:text-amber-400",
  DELETE: "bg-rose-100 text-rose-700 dark:bg-rose-500/15 dark:text-rose-400",
};

const QUICKSTART = `# 1. Create a live input
curl -X POST https://api.zoikostream.com/streams \\
  -H "Authorization: Bearer $ZOIKO_API_KEY" \\
  -H "Content-Type: application/json" \\
  -d '{"channel_id":"<channel-uuid>","title":"Main stage"}'

# 2. Read it back to get the publish key (a list read never returns one)
curl https://api.zoikostream.com/streams/<stream-id> \\
  -H "Authorization: Bearer $ZOIKO_API_KEY"

# 3. Publish to the ingest URL with the key as the stream name
ffmpeg -re -i source.mp4 -c:v libx264 -preset veryfast -b:v 4500k \\
  -c:a aac -b:a 128k -f flv rtmps://ingest.zoikostream.com/live/<publish-key>`;

const AUTH_SAMPLE = `Authorization: Bearer zk_live_a1b2c3...

# Server-side only. A key in browser or mobile code is a published key.
# One key per environment and per integration, so a rotation is never
# all-or-nothing.`;

const ERROR_SAMPLE = `{
  "detail": "Registration is required before playback"
}

# 401  the credential is missing, malformed or revoked
# 403  authenticated, but not permitted — or a passphrase is required
# 404  no such resource, OR it belongs to another tenant (deliberately
#      indistinguishable: an existence oracle on another tenant's ids is
#      worth nothing to a legitimate caller)
# 409  a precondition about YOUR state failed — e.g. not registered yet,
#      so the client should re-check rather than prompt for a passphrase
# 422  the request body failed validation; \`detail\` is an array`;

// Section-scoped search. Each section contributes plain text so one input can filter whatever
// is on screen without a per-section implementation.
function useFiltered(query, items, toText) {
  const q = query.trim().toLowerCase();
  return useMemo(() => {
    if (!q) return items;
    return items.filter((item) => toText(item).toLowerCase().includes(q));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [q, items]);
}

function DocsNav({ current }) {
  return (
    <nav aria-label="Documentation sections" className="lg:sticky lg:top-24">
      <ul className="flex gap-1 overflow-x-auto pb-2 lg:flex-col lg:overflow-visible lg:pb-0">
        {DOC_SECTIONS.map(({ slug, label, icon: Icon }) => {
          const to = slug ? `/docs/${slug}` : "/docs";
          const active = slug === current;
          return (
            <li key={label} className="shrink-0">
              <NavLink
                to={to}
                end
                className={cx(
                  "flex items-center gap-2.5 rounded-xl px-3 py-2 text-sm font-medium transition-colors duration-150 motion-reduce:transition-none",
                  focusRing,
                  active
                    ? "bg-emerald-50 text-emerald-700 dark:bg-emerald-500/10 dark:text-emerald-400"
                    : "text-slate-600 hover:bg-slate-100 hover:text-slate-900 dark:text-slate-400 dark:hover:bg-slate-800 dark:hover:text-white"
                )}
              >
                <Icon className="shrink-0 text-base" aria-hidden="true" />
                {label}
              </NavLink>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}

// ── Sections ─────────────────────────────────────────────────────────────────

function Overview({ query }) {
  const stages = useFiltered(query, DOC_LIFECYCLE, ([, label, body]) => `${label} ${body}`);

  return (
    <div className="space-y-12">
      <section>
        <Heading level={2} size="h3">
          Quickstart
        </Heading>
        <Text className="mt-2 text-sm">
          Three requests from nothing to a live stream. Every plan has the same API surface, so this
          works identically on any tier.
        </Text>
        <div className="mt-5">
          <CodeBlock filename="quickstart.sh" code={QUICKSTART} />
        </div>
      </section>

      <section>
        <Heading level={2} size="h3">
          Authentication
        </Heading>
        <Text className="mt-2 text-sm">
          A bearer API key on every request. Keys are stored hashed — the console can only ever show
          you a prefix, and no endpoint can return a stored secret.
        </Text>
        <div className="mt-5">
          <CodeBlock filename="auth" code={AUTH_SAMPLE} />
        </div>
      </section>

      <section>
        <Heading level={2} size="h3">
          Errors
        </Heading>
        <Text className="mt-2 text-sm">
          The distinction between 403, 404 and 409 is deliberate and worth reading once — it is what
          lets a client know whether to prompt, re-check, or give up.
        </Text>
        <div className="mt-5">
          <CodeBlock filename="errors" code={ERROR_SAMPLE} />
        </div>
      </section>

      <section>
        <div className="flex flex-wrap items-baseline justify-between gap-3">
          <Heading level={2} size="h3">
            By lifecycle stage
          </Heading>
          <Badge status="info">API version {API_VERSION}</Badge>
        </div>
        <Text className="mt-2 text-sm">
          The platform is organised around eight stages a broadcast travels. The console rails use
          the same eight, so a docs page and a dashboard describe the same thing.
        </Text>

        {stages.length === 0 ? (
          <p className="mt-8 rounded-xl border border-dashed border-slate-300 px-5 py-10 text-center text-sm text-slate-500 dark:border-slate-700 dark:text-slate-400">
            No stage matches “{query}”.
          </p>
        ) : (
          <ul className="mt-6 divide-y divide-slate-200 dark:divide-slate-800">
            {stages.map(([key, label, body]) => (
              <li key={key} className="flex gap-4 py-4">
                <span
                  className="mt-1.5 h-2.5 w-2.5 shrink-0 rounded-full"
                  style={{ backgroundColor: STAGE_COLOR[key] }}
                  aria-hidden="true"
                />
                <div className="min-w-0">
                  <p className="text-sm font-semibold text-slate-900 dark:text-white">{label}</p>
                  <p className="mt-0.5 text-sm text-slate-600 dark:text-slate-400">{body}</p>
                </div>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}

function ApiReference({ query }) {
  const groups = useFiltered(query, API_GROUPS, (g) =>
    `${g.title} ${g.base} ${g.body} ${g.endpoints.map((e) => e.join(" ")).join(" ")}`
  );

  const copyPath = async (path) => {
    try {
      await navigator.clipboard.writeText(path);
      notify.success("Path copied");
    } catch {
      notify.error("Clipboard is unavailable in this browser");
    }
  };

  return (
    <div className="space-y-10">
      <Card padding="lg" variant="subtle">
        <Text className="text-sm leading-relaxed">
          Base URL <code className={cx(type.mono, "text-[13px] text-slate-800 dark:text-slate-200")}>https://api.zoikostream.com</code>{" "}
          · version{" "}
          <code className={cx(type.mono, "text-[13px] text-slate-800 dark:text-slate-200")}>{API_VERSION}</code>{" "}
          · every request carries a bearer API key. Additive changes ship continuously; anything
          that could break a working integration gets a new dated version.
        </Text>
      </Card>

      {groups.length === 0 ? (
        <p className="rounded-xl border border-dashed border-slate-300 px-5 py-10 text-center text-sm text-slate-500 dark:border-slate-700 dark:text-slate-400">
          No endpoint matches “{query}”.
        </p>
      ) : (
        groups.map((g) => (
          <section key={g.id} id={g.id} className="scroll-mt-28">
            <div className="flex flex-wrap items-baseline gap-3">
              <Heading level={2} size="h3">
                {g.title}
              </Heading>
              <code className={cx(type.mono, "text-sm text-slate-500 dark:text-slate-400")}>{g.base}</code>
            </div>
            <Text className="mt-2 max-w-3xl text-sm">{g.body}</Text>

            <div className="mt-5 overflow-x-auto">
              <table className="w-full min-w-[38rem] border-collapse">
                <thead>
                  <tr className="border-b border-slate-200 dark:border-slate-800">
                    <th className="w-20 py-2 text-left text-[11px] font-semibold uppercase tracking-wider text-slate-400">
                      Method
                    </th>
                    <th className="py-2 text-left text-[11px] font-semibold uppercase tracking-wider text-slate-400">
                      Path
                    </th>
                    <th className="py-2 text-left text-[11px] font-semibold uppercase tracking-wider text-slate-400">
                      What it does
                    </th>
                    <th className="w-10 py-2" />
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100 dark:divide-slate-800/70">
                  {g.endpoints.map(([method, path, desc]) => (
                    <tr key={`${method} ${path}`} className="group">
                      <td className="py-2.5 pr-3 align-top">
                        <span
                          className={cx(
                            "inline-block rounded px-1.5 py-0.5 text-[10px] font-bold",
                            type.mono,
                            METHOD_TONE[method]
                          )}
                        >
                          {method}
                        </span>
                      </td>
                      <td className={cx("py-2.5 pr-4 align-top text-[13px]", type.mono, "text-slate-800 dark:text-slate-200")}>
                        {path}
                      </td>
                      <td className="py-2.5 pr-3 align-top text-sm text-slate-600 dark:text-slate-400">{desc}</td>
                      <td className="py-2.5 align-top">
                        <button
                          type="button"
                          onClick={() => copyPath(path)}
                          aria-label={`Copy ${path}`}
                          className={cx(
                            "rounded p-1 text-slate-300 opacity-0 transition-opacity focus-visible:opacity-100 group-hover:opacity-100 hover:text-slate-600 dark:text-slate-600 dark:hover:text-slate-300",
                            focusRing
                          )}
                        >
                          <FiCopy className="text-sm" />
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        ))
      )}
    </div>
  );
}

function Sdks({ query }) {
  const sdks = useFiltered(query, SDKS, (s) => `${s.name} ${s.body} ${s.install}`);

  return (
    <div className="space-y-8">
      {sdks.length === 0 ? (
        <p className="rounded-xl border border-dashed border-slate-300 px-5 py-10 text-center text-sm text-slate-500 dark:border-slate-700 dark:text-slate-400">
          No SDK matches “{query}”.
        </p>
      ) : (
        <div className="grid gap-5 sm:grid-cols-2">
          {sdks.map((s, i) => (
            <Reveal key={s.id} delay={i * 60}>
              <Card padding="xl" className="flex h-full flex-col">
                <div className="flex items-center justify-between gap-3">
                  <Heading level={2} size="h4">
                    {s.name}
                  </Heading>
                  <Badge status={s.status.includes("deprecated") ? "warning" : "success"}>
                    {s.status}
                  </Badge>
                </div>
                <Text className="mt-2 flex-1 text-sm leading-relaxed">{s.body}</Text>
                <div className="mt-5">
                  <CodeBlock filename="install" code={s.install} />
                </div>
              </Card>
            </Reveal>
          ))}
        </div>
      )}

      <Card padding="lg" variant="subtle">
        <Text className="text-sm leading-relaxed">
          <span className="font-semibold text-slate-800 dark:text-slate-200">Version support.</span>{" "}
          The current major is supported indefinitely; the previous major keeps working until the
          announced end-of-support date. Deprecations are published on the{" "}
          <a href="/changelog" className="font-semibold text-emerald-700 underline decoration-emerald-500/40 dark:text-emerald-400">
            changelog
          </a>{" "}
          before support ends, never removed quietly.
        </Text>
      </Card>
    </div>
  );
}

function Sandbox() {
  return (
    <div className="space-y-10">
      {/* Stated up front: this is a shape reference, not a live request runner. */}
      <Card padding="lg" variant="subtle">
        <Text className="text-sm leading-relaxed">
          <span className="font-semibold text-slate-800 dark:text-slate-200">
            This page does not send requests.
          </span>{" "}
          Executing them from a public page would mean proxying your API key through our web tier,
          which is exactly what the handling rules tell you not to do. Copy these into your own
          terminal, or use the CLI. Signed-in organisations get sandbox keys from their console.
        </Text>
      </Card>

      <section>
        <Heading level={2} size="h3">
          Get sandbox credentials
        </Heading>
        <Text className="mt-2 text-sm">
          Sandbox keys are issued per organisation. Sign up, then request one from Credentials in
          your console — key creation is a platform-admin operation, so it is a request rather than
          a self-service button.
        </Text>
        <div className="mt-5 flex flex-wrap gap-3">
          <Button href="/signup" variant="primary">
            Create an organisation
          </Button>
          <Button href="/organization/credentials" variant="secondary">
            Credentials in the console <FiExternalLink className="ml-1" />
          </Button>
        </div>
      </section>

      <section>
        <Heading level={2} size="h3">
          Try the lifecycle end to end
        </Heading>
        <Text className="mt-2 text-sm">
          The quickstart takes you from no input to a live stream in three requests.
        </Text>
        <div className="mt-5">
          <CodeBlock filename="quickstart.sh" code={QUICKSTART} />
        </div>
      </section>

      <section>
        <Heading level={2} size="h3">
          Test mode
        </Heading>
        <Text className="mt-2 text-sm leading-relaxed">
          A test organisation&apos;s sessions are marked test and are excluded from readiness,
          badge and attention counts across the platform consoles — so rehearsing does not pollute
          the operational picture your colleagues are reading. That exclusion is enforced
          server-side, not by a filter you have to remember to tick.
        </Text>
      </section>
    </div>
  );
}

function Architecture({ query }) {
  const layers = useFiltered(query, ARCHITECTURE_LAYERS, (l) => `${l.title} ${l.body}`);

  return (
    <div className="space-y-10">
      <Text className="max-w-3xl text-sm leading-relaxed">
        A broadcast moves through six layers. Each one owns a decision the next one trusts, which is
        why an access check lives at the media boundary rather than in the page that renders the
        player.
      </Text>

      {layers.length === 0 ? (
        <p className="rounded-xl border border-dashed border-slate-300 px-5 py-10 text-center text-sm text-slate-500 dark:border-slate-700 dark:text-slate-400">
          No layer matches “{query}”.
        </p>
      ) : (
        <div className="grid gap-5 sm:grid-cols-2">
          {layers.map((l, i) => (
            <Reveal key={l.title} delay={i * 60}>
              <FeatureCard icon={l.icon} accent={l.accent} title={l.title} body={l.body} />
            </Reveal>
          ))}
        </div>
      )}

      <Card padding="xl" variant="subtle">
        <Heading level={2} size="h4">
          Two invariants worth knowing before you integrate
        </Heading>
        <ul className="mt-4 space-y-3">
          <li className="text-sm leading-relaxed text-slate-600 dark:text-slate-400">
            <span className="font-semibold text-slate-800 dark:text-slate-200">
              Access is checked on the media.
            </span>{" "}
            A landing page stays reachable to someone who has not registered, because that is where
            the register button is. Registration, passphrase and watch window are enforced when
            playback starts — which is why a 409 means &ldquo;re-check your state&rdquo; and a 403
            means &ldquo;prompt for the passphrase&rdquo;.
          </li>
          <li className="text-sm leading-relaxed text-slate-600 dark:text-slate-400">
            <span className="font-semibold text-slate-800 dark:text-slate-200">
              A credential is returned once, for one thing.
            </span>{" "}
            Publish keys come from a single-input read, never a list. Access-link tokens are shown
            at issue or rotation and hashed thereafter. Nothing can return a stored secret, so no
            integration should be built expecting to fetch one later.
          </li>
        </ul>
        <Button href="/docs/api" variant="secondary" className="mt-6">
          API reference <FiArrowRight className="ml-1" />
        </Button>
      </Card>
    </div>
  );
}

function Guides({ query }) {
  const guides = useFiltered(query, GUIDES, ([, title, body]) => `${title} ${body}`);

  return guides.length === 0 ? (
    <p className="rounded-xl border border-dashed border-slate-300 px-5 py-10 text-center text-sm text-slate-500 dark:border-slate-700 dark:text-slate-400">
      No guide matches “{query}”.
    </p>
  ) : (
    <ul className="divide-y divide-slate-200 dark:divide-slate-800">
      {guides.map(([stage, title, body]) => (
        <li key={title} className="flex gap-4 py-5">
          <span
            className="mt-1.5 h-2.5 w-2.5 shrink-0 rounded-full"
            style={{ backgroundColor: STAGE_COLOR[stage] }}
            aria-hidden="true"
          />
          <div className="min-w-0">
            <h2 className="text-base font-bold text-slate-900 dark:text-white">{title}</h2>
            <p className="mt-1 text-sm leading-relaxed text-slate-600 dark:text-slate-400">{body}</p>
            <p className="mt-1.5 text-xs font-semibold uppercase tracking-wider text-slate-400">
              {stage}
            </p>
          </div>
        </li>
      ))}
    </ul>
  );
}

const SECTION_META = {
  "": { title: "Documentation", lead: "Everything needed to integrate ZoikoStream — organised the way the platform is." },
  api: { title: "API Reference", lead: "Endpoints as they exist in the platform today, grouped the way the API is actually organised." },
  sdks: { title: "SDKs", lead: "Official clients, their current versions, and what is being deprecated." },
  sandbox: { title: "Sandbox", lead: "How to get credentials and try the lifecycle safely, without proxying your key through a web page." },
  architecture: { title: "Architecture", lead: "The six layers a broadcast moves through, and the two invariants that shape every integration." },
  guides: { title: "Guides", lead: "Task-shaped walkthroughs, indexed by the lifecycle stage they belong to." },
};

const PLACEHOLDER = {
  "": "Search the stages…",
  api: "Search endpoints — “access-links”, “transcript”, “register”…",
  sdks: "Search SDKs…",
  sandbox: "",
  architecture: "Search layers…",
  guides: "Search guides — “webhook”, “replay”, “SRT”…",
};

export default function Docs() {
  const { section = "" } = useParams();
  const [query, setQuery] = useState("");
  const meta = SECTION_META[section];

  // An unknown /docs/* path is a 404 rather than an empty docs shell.
  if (!meta) return <NotFound />;

  const searchable = section !== "sandbox";

  return (
    <SitePage
      eyebrow="Developers"
      title={meta.title}
      lead={meta.lead}
      crumbs={section ? [["Docs", "/docs"], [meta.title]] : [["Docs"]]}
      actions={
        <>
          <Button href="/signup" variant="primary" size="lg">
            Get sandbox keys
          </Button>
          <Button href="/support" variant="secondary" size="lg">
            Get help
          </Button>
        </>
      }
    >
      <Container className="py-12 sm:py-16">
        <div className="grid gap-10 lg:grid-cols-[220px_minmax(0,1fr)] lg:gap-14">
          <DocsNav current={section} />

          <div className="min-w-0">
            {searchable && (
              <div className="relative mb-10 max-w-md">
                <FiSearch
                  className="pointer-events-none absolute left-4 top-1/2 -translate-y-1/2 text-slate-400"
                  aria-hidden="true"
                />
                <input
                  type="search"
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  placeholder={PLACEHOLDER[section]}
                  aria-label={`Search ${meta.title}`}
                  className="w-full rounded-xl border border-slate-200 bg-white py-3 pl-11 pr-4 text-slate-800 outline-none transition focus:border-emerald-500 focus:ring-2 focus:ring-emerald-100 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100 dark:placeholder:text-slate-500 dark:focus:border-emerald-500 dark:focus:ring-emerald-500/20"
                />
              </div>
            )}

            {section === "" && <Overview query={query} />}
            {section === "api" && <ApiReference query={query} />}
            {section === "sdks" && <Sdks query={query} />}
            {section === "sandbox" && <Sandbox />}
            {section === "architecture" && <Architecture query={query} />}
            {section === "guides" && <Guides query={query} />}
          </div>
        </div>
      </Container>
    </SitePage>
  );
}
