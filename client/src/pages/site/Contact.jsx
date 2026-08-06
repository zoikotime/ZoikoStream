import { useState } from "react";
import { FiSend, FiAlertTriangle, FiLifeBuoy, FiActivity, FiBookOpen, FiCopy } from "react-icons/fi";
import SitePage from "../../layouts/SitePage";
import { Button, Card, Section, Heading, Text } from "../../ui";
import { Field, Label, Select, Textarea } from "../../ui/forms";
import { notify } from "../../ui/Toast";
import { cx } from "../../ui/tokens";

// Contact.
//
// HONEST FORM, and this is the part that matters: there is no contact-submission endpoint in
// this platform, and adding one would be a backend change. So the form does not pretend to
// submit — it composes the enquiry and hands it to the visitor's own mail client via a mailto:,
// with a copy-to-clipboard fallback for anyone without one configured.
//
// The alternative — a Send button that shows "Thanks, we'll be in touch" and drops the message
// on the floor — is the single worst thing a contact page can do, so it isn't done here.
const CONTACT_EMAIL = "hello@zoikogroup.com";

const REASONS = [
  ["managed-event", "A managed Live Event"],
  ["evaluation", "Evaluating the platform"],
  ["pricing", "Pricing or procurement"],
  ["integration", "An integration question"],
  ["security", "Security or compliance review"],
  ["other", "Something else"],
];

const OTHER_ROUTES = [
  {
    icon: FiActivity,
    title: "Is something broken right now?",
    body: "Check platform status first — it is probed per request, not cached.",
    cta: "Platform status",
    href: "/status",
  },
  {
    icon: FiLifeBuoy,
    title: "Already a customer?",
    body: "Open a request from your console and it arrives with workspace context attached.",
    cta: "Support & Status",
    href: "/organization/support",
  },
  {
    icon: FiBookOpen,
    title: "Integration question?",
    body: "The lifecycle guides and API reference answer most of them, including what is deliberately not measured.",
    cta: "Read the docs",
    href: "/docs",
  },
];

export default function Contact() {
  const [form, setForm] = useState({
    name: "",
    email: "",
    organization: "",
    reason: REASONS[0][0],
    message: "",
  });

  const set = (key) => (e) => setForm((f) => ({ ...f, [key]: e.target.value }));
  const reasonLabel = REASONS.find(([v]) => v === form.reason)?.[1] || "Enquiry";
  const valid = form.name.trim() && form.email.trim() && form.message.trim();

  const body = [
    `Name: ${form.name}`,
    `Email: ${form.email}`,
    form.organization && `Organization: ${form.organization}`,
    `Reason: ${reasonLabel}`,
    "",
    form.message,
  ]
    .filter(Boolean)
    .join("\n");

  const subject = `ZoikoStream — ${reasonLabel}`;

  const openMail = () => {
    if (!valid) return;
    window.location.href = `mailto:${CONTACT_EMAIL}?subject=${encodeURIComponent(subject)}&body=${encodeURIComponent(body)}`;
  };

  const copyEnquiry = async () => {
    try {
      await navigator.clipboard.writeText(`To: ${CONTACT_EMAIL}\nSubject: ${subject}\n\n${body}`);
      notify.success("Enquiry copied — paste it into any mail client");
    } catch {
      notify.error("Clipboard is unavailable — select the text and copy it manually");
    }
  };

  return (
    <SitePage
      eyebrow="Contact"
      title="Talk to an expert"
      lead="Managed events, evaluations, procurement and security reviews all start with a conversation. Tell us what you are trying to broadcast and who needs to reach it."
      crumbs={[["Contact"]]}
    >
      <Section tone="base">
        <div className="grid gap-10 lg:grid-cols-[1.15fr_1fr] lg:gap-16">
          <Card padding="xl" as="form" onSubmit={(e) => { e.preventDefault(); openMail(); }}>
            <Heading level={2} size="h3">
              Send us the details
            </Heading>
            <Text className="mt-2 text-sm">
              This opens the message in your own mail client, addressed to{" "}
              <span className="font-semibold text-slate-800 dark:text-slate-200">{CONTACT_EMAIL}</span>{" "}
              — nothing is submitted to a server from this page.
            </Text>

            <div className="mt-6 space-y-4">
              <Field
                label="Your name"
                variant="form"
                value={form.name}
                onChange={set("name")}
                autoComplete="name"
                required
              />
              <Field
                label="Work email"
                type="email"
                variant="form"
                value={form.email}
                onChange={set("email")}
                autoComplete="email"
                required
              />
              <Field
                label="Organization"
                variant="form"
                value={form.organization}
                onChange={set("organization")}
                autoComplete="organization"
                hint="Optional, but it helps us route the reply."
              />

              <div>
                <Label htmlFor="contact-reason">What is this about?</Label>
                <Select id="contact-reason" value={form.reason} onChange={set("reason")}>
                  {REASONS.map(([value, label]) => (
                    <option key={value} value={value}>
                      {label}
                    </option>
                  ))}
                </Select>
              </div>

              <div>
                <Label htmlFor="contact-message">Message</Label>
                <Textarea
                  id="contact-message"
                  rows={5}
                  value={form.message}
                  onChange={set("message")}
                  placeholder="What are you broadcasting, to whom, and when? If there is a date, tell us the date."
                  required
                />
              </div>
            </div>

            <div className="mt-6 flex flex-col gap-3 sm:flex-row">
              <Button type="submit" variant="primary" size="lg" className="flex-1" disabled={!valid}>
                <FiSend /> Open in mail client
              </Button>
              <Button variant="secondary" size="lg" onClick={copyEnquiry} disabled={!valid}>
                <FiCopy /> Copy
              </Button>
            </div>
            <p className="mt-3 text-xs text-slate-500 dark:text-slate-400">
              No mail client configured? Copy the enquiry and send it however you like.
            </p>
          </Card>

          <div className="space-y-6">
            {/* A live broadcast failing is not a contact-form situation, and saying so here is
                more useful than a form that will be read tomorrow. */}
            <Card padding="lg" className="border-amber-300 bg-amber-50 dark:border-amber-500/30 dark:bg-amber-500/10">
              <div className="flex gap-3">
                <FiAlertTriangle className="mt-0.5 shrink-0 text-lg text-amber-600 dark:text-amber-400" aria-hidden="true" />
                <div>
                  <h3 className="text-sm font-bold text-amber-900 dark:text-amber-200">
                    Is a broadcast failing right now?
                  </h3>
                  <p className="mt-1 text-sm text-amber-800 dark:text-amber-300">
                    Do not use this form. Use the escalation path in your agreement — a Sev 1 during
                    a live event needs a person on a phone, not a queue.
                  </p>
                </div>
              </div>
            </Card>

            {OTHER_ROUTES.map(({ icon: Icon, title, body: text, cta, href }) => (
              <Card key={title} padding="lg" hover>
                <div className="flex gap-3">
                  <span
                    className={cx(
                      "grid h-10 w-10 shrink-0 place-items-center rounded-xl",
                      "bg-slate-100 text-slate-500 dark:bg-slate-800 dark:text-slate-400"
                    )}
                    aria-hidden="true"
                  >
                    <Icon className="text-lg" />
                  </span>
                  <div className="min-w-0">
                    <h3 className="text-sm font-bold text-slate-900 dark:text-white">{title}</h3>
                    <p className="mt-1 text-sm text-slate-600 dark:text-slate-400">{text}</p>
                    <Button href={href} variant="ghost" size="sm" className="mt-2 -ml-3">
                      {cta} →
                    </Button>
                  </div>
                </div>
              </Card>
            ))}
          </div>
        </div>
      </Section>
    </SitePage>
  );
}
