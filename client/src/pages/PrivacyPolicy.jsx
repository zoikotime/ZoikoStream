import { Link } from "react-router-dom";
import { Logo } from "../ui";

// /privacy — the public privacy policy.
//
// WHY THIS PAGE EXISTS: Google Play requires a privacy policy that is publicly reachable
// without a login for every app that ships in the store (RELEASE.md §5), and this app
// ships with sign-in, camera and microphone. Until now the platform had a complete
// privacy-request ENGINE (server/app/services/privacy_comms.py) and no public surface at
// all — a researcher, a store reviewer or a prospect had nowhere to read what is
// collected.
//
// WHAT IT MAY AND MAY NOT CLAIM: every statement below is grounded in the codebase —
// what AuthContext stores, what the API logs, which subprocessors the platform actually
// integrates (services/privacy_comms.KNOWN_SUBPROCESSORS). Nothing here names a
// jurisdiction, a statutory deadline or a retention period, because the platform
// configures none (DEADLINE_POLICIES is empty on purpose) and a policy that invents a
// "30 days" it cannot honour is worse than one that describes what actually happens.

const SECTIONS = [
  {
    id: "data-we-collect",
    title: "What we collect",
    body: (
      <>
        <p>
          When you create an account we store your name, email address and password
          (hashed — never readable by us or by anyone with database access). If you join
          an organization, we record your role in it.
        </p>
        <p>
          When you attend or host an event, we process the live audio and video of that
          event to deliver it to its audience. Live media is carried by our streaming
          provider for the duration of the event; it is not stored by this app on your
          device. Recordings exist only where an event's organizer explicitly creates
          one, under the retention rules that organizer configures.
        </p>
        <p>
          When you contact support or report a security issue, we keep the message you
          sent us so we can respond to it.
        </p>
      </>
    ),
  },
  {
    id: "how-we-use-it",
    title: "How we use it",
    body: (
      <>
        <p>
          Your account data is used to sign you in, to deliver the events you are
          invited to, and to send you operational email about those events — invitations,
          assignment notifications, verification links and security notices. These are
          not marketing messages, and unsubscribing from marketing mail does not stop
          them.
        </p>
        <p>
          We keep audit and security logs of significant actions (sign-ins, event
          operations, administrative changes) to detect and investigate abuse. These logs
          are not public and are not sold or shared for advertising — we run no
          advertising of any kind.
        </p>
      </>
    ),
  },
  {
    id: "who-processes-it",
    title: "Who processes it",
    body: (
      <p>
        We use a small number of providers to operate the service: transactional email,
        live streaming and media ingest, payment processing, object storage and managed
        Redis. The authoritative, versioned list of processors is published in the{" "}
        <Link to="/organization/privacy">Privacy Center</Link> and updated before a
        change takes effect where it requires notice.
      </p>
    ),
  },
  {
    id: "your-rights",
    title: "Your rights",
    body: (
      <>
        <p>
          You can request a copy of your personal data, ask us to correct it, restrict
          its processing, object to it, or ask us to delete it. Requests are made through
          the <Link to="/organization/privacy">Privacy Center</Link>; every request gets
          a reference you can quote, and you can check its status there at any time.
        </p>
        <p>
          For export and deletion we verify that the request really comes from you — by
          confirming control of the email address — before we act. That verification is
          required even when you are signed in, because these are the two requests with
          disclosive or irreversible consequences.
        </p>
        <p>
          Deletion is described honestly: where records must be retained to meet a legal
          obligation (for example billing records, or a recording under legal hold), the
          completion message tells you exactly which categories of record survived and
          why. We do not claim total erasure when residue lawfully remains.
        </p>
      </>
    ),
  },
  {
    id: "security",
    title: "How we protect it",
    body: (
      <p>
        Traffic is encrypted in transit (TLS only — the app refuses cleartext
        connections). Access tokens live in your browser's local storage and are never
        backed up off the device by the Android app. Data exports are delivered as
        single-use links that expire, not as email attachments. If you believe you have
        found a security issue, please report it through the{" "}
        <Link to="/trust">Trust Center</Link>.
      </p>
    ),
  },
  {
    id: "children",
    title: "Children",
    body: (
      <p>
        ZoikoStream is a business product and is not directed at children. We do not
        knowingly create accounts for anyone under the age required by their jurisdiction
        for consent to data processing, and we will delete an account we learn was
        created by a child.
      </p>
    ),
  },
  {
    id: "changes",
    title: "Changes to this policy",
    body: (
      <p>
        Material changes are published through the notice lifecycle in the{" "}
        <Link to="/organization/privacy">Privacy Center</Link>, which keeps every
        published version readable — including the ones that have been superseded — and
        tells you when a change needs your consent rather than pretending your silence
        gave it.
      </p>
    ),
  },
  {
    id: "contact",
    title: "Contacting us",
    body: (
      <p>
        Privacy requests go through the <Link to="/organization/privacy">Privacy
        Center</Link> so they are logged with a reference. Security matters go through
        the <Link to="/trust">Trust Center</Link>. Everything else:{" "}
        <Link to="/contact">contact us</Link>.
      </p>
    ),
  },
];

export default function PrivacyPolicy() {
  return (
    <main className="min-h-screen bg-slate-50">
      <section className="bg-[#050a14] text-white">
        <div className="mx-auto flex max-w-3xl items-center justify-between px-6 py-6 lg:px-8">
          <Link to="/" aria-label="ZoikoStream home"><Logo height="h-7" /></Link>
          <Link
            to="/organization/privacy"
            className="rounded-xl border border-white/15 bg-white/[0.04] px-3.5 py-2 text-[13px] font-medium text-white/85 transition hover:border-white/30 hover:bg-white/[0.08]"
          >
            Manage your data
          </Link>
        </div>
        <div className="mx-auto max-w-3xl px-6 pb-12 pt-2 lg:px-8">
          <p className="text-[11px] font-semibold uppercase tracking-[0.2em] text-teal-300">
            Privacy policy
          </p>
          <h1 className="mt-3 text-[1.9rem] font-bold leading-tight tracking-tight sm:text-[2.4rem]">
            What we collect, and what we do with it.
          </h1>
          <p className="mt-3 text-[14px] leading-relaxed text-white/60">
            Plain statements, grounded in what the product actually does. Where a detail
            is governed by a lifecycle (the processor list, the notice text itself), this
            page links to the thing that governs it rather than copying a version of it
            that could drift.
          </p>
        </div>
      </section>

      <div className="mx-auto max-w-3xl space-y-8 px-6 py-10 lg:px-8">
        {SECTIONS.map((section) => (
          <section key={section.id} id={section.id} aria-labelledby={section.id}>
            <h2 className="text-[17px] font-semibold text-slate-900">{section.title}</h2>
            <div className="mt-2 space-y-3 text-[14px] leading-relaxed text-slate-700">
              {section.body}
            </div>
          </section>
        ))}

        <p className="rounded-2xl border border-slate-200 bg-white px-5 py-4 text-[13px] leading-relaxed text-slate-500">
          This policy describes the ZoikoStream platform and the ZoikoStream Android app.
          The app adds no collection of its own: it runs the same client, talks to the
          same API, and stores nothing on the device beyond the session this policy
          already describes.
        </p>
      </div>
    </main>
  );
}
