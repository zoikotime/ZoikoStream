import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

/** Static guards on the Trust Center, reporter portal and preference centre.
 *
 *  ZST-EC-001 TRU-001 -> TRU-003 and MKT-000. Structural, in the same spirit as
 *  Contact.static.test.js and Status.static.test.js: these lock out whole classes of defect
 *  rather than the one instance a rendering test happens to drive. The separation these
 *  pages implement is a backend guarantee, and the browser must not undo it.
 */
const read = (name) => readFileSync(resolve(process.cwd(), `src/pages/${name}`), "utf8");

// Strip comments first: every one of these files explains in prose what it must NOT do, and
// a bare substring search would flag the explanation rather than a defect.
const strip = (source) => source
  .replace(/\/\*[\s\S]*?\*\//g, "")
  .replace(/\{\/\*[\s\S]*?\*\/\}/g, "")
  .replace(/^\s*\/\/.*$/gm, "");

const TRUST = strip(read("Trust.jsx"));
// JSX wraps prose across source lines, so assertions on sentences run against a
// whitespace-collapsed copy rather than the raw file.
const TRUST_PROSE = TRUST.replace(/\s+/g, " ");
// The ADVISORY renderer alone: the report form legitimately collects the researcher's own
// name and address, so scoping the identity assertions to what the page DISPLAYS is the
// real requirement - a submission field is not a disclosure.
const ADVISORY = TRUST.slice(TRUST.indexOf("function Advisory"),
                             TRUST.indexOf("function Maintenance") > -1
                               ? TRUST.indexOf("function Maintenance")
                               : TRUST.indexOf("function EvidenceRequest"));
const REPORT = strip(read("SecurityReport.jsx"));
const PREFS = strip(read("EmailPreferences.jsx"));
const APP = readFileSync(resolve(process.cwd(), "src/App.jsx"), "utf8");

describe("routes the emails actually link to", () => {
  it("every emailed CTA path is a real route", () => {
    // email.py links advisories at /trust, the reporter portal at /security/report/:ref,
    // and every marketing footer at /preferences and /unsubscribe. A missing route here is
    // a dead link in a security advisory, so it is asserted rather than assumed.
    for (const path of ['path="/trust"', 'path="/security/report/:reference"',
                        'path="/preferences"', 'path="/unsubscribe"']) {
      expect(APP).toContain(path);
    }
  });

  it("none of them is behind authentication", () => {
    // They sit in the same public block as /contact and /status. A researcher has no
    // account, and requiring a login to unsubscribe is what makes people report spam.
    const publicBlock = APP.slice(APP.indexOf('path="/contact"'),
                                  APP.indexOf('path="/login"'));
    for (const path of ['path="/trust"', 'path="/security/report/:reference"',
                        'path="/preferences"', 'path="/unsubscribe"']) {
      expect(publicBlock).toContain(path);
    }
  });
});

describe("Trust Center page", () => {
  it("reads published trust data only", () => {
    expect(TRUST).toContain('api.get("/trust")');
    for (const forbidden of ["/admin/", "platform_ops", "internal_incident_id",
                             "vulnerability_report_id", "storage_reference"]) {
      expect(TRUST).not.toContain(forbidden);
    }
  });

  it("renders no reporter identity and no internal analysis", () => {
    // Asserted against the advisory renderer: an advisory must never expose the researcher
    // whose report produced it, nor any internal analysis.
    for (const field of ["reporter_email", "reporter_name", "reporter", "reproduction",
                         "evidence_reference", "commander", "root_cause",
                         "internal_incident"]) {
      expect(ADVISORY).not.toContain(field);
    }
  });

  it("renders the full append-only advisory history", () => {
    // A customer who acted on version 1 must be able to read what version 1 said.
    expect(TRUST).toContain("advisory.history");
    expect(TRUST).toContain("change_summary");
    expect(TRUST).toContain("Later updated");
  });

  it("never derives a severity or invents a score", () => {
    // The label comes from the API. Nothing here computes one.
    expect(TRUST).toContain("severity_label");
    expect(TRUST).not.toMatch(/cvss_score/);
    expect(TRUST).not.toMatch(/severity\s*[<>]/);
  });

  it("gates imperative wording on the recorded policy flag", () => {
    // Severity alone never licenses "upgrade immediately".
    expect(TRUST).toContain("action_mandatory");
    expect(TRUST.toLowerCase()).not.toContain("upgrade immediately");
    expect(TRUST.toLowerCase()).not.toContain("patch now");
  });

  it("states the absence of a deadline rather than inventing one", () => {
    expect(TRUST).toContain("No deadline has been set");
    for (const invented of ["within 30 days", "within 14 days", "72 hours"]) {
      expect(TRUST.toLowerCase()).not.toContain(invented);
    }
  });

  it("does not claim customers have patched when an advisory closes", () => {
    expect(TRUST_PROSE).toContain("does not confirm the update has been applied");
    for (const overclaim of ["all customers have", "everyone has remediated",
                             "your organization is now secure"]) {
      expect(TRUST_PROSE.toLowerCase()).not.toContain(overclaim);
    }
  });

  it("offers no download link for a confidential document", () => {
    // The catalogue is metadata. Access arrives by email, bound and expiring.
    expect(TRUST).toContain("requires_approval");
    expect(TRUST).toContain("Request required");
    expect(TRUST).not.toMatch(/href=\{[^}]*document[^}]*\}/);
    expect(TRUST).not.toContain("download");
  });

  it("binds the request form's purpose and scope to the chosen document", () => {
    expect(TRUST).toContain("selected.allowed_purposes");
    expect(TRUST).toContain("selected.allowed_scopes");
  });

  it("states the disclosure programme as fact, and asks for no credentials", () => {
    expect(TRUST).toContain("policy?.bounty");
    expect(TRUST).toContain("policy?.public_credit");
    expect(TRUST_PROSE).toContain("Please do not send credentials");
    // The form has no FIELD that could carry a secret. Checked on the field names the form
    // actually posts - the page's warning text names those words on purpose, telling
    // researchers not to send them, and a bare search would flag the warning itself.
    const fields = [...TRUST.matchAll(/(?:value|onChange)=\{[^}]*form\.(\w+)/g)]
      .map((match) => match[1]);
    expect(fields.length).toBeGreaterThan(4);
    for (const field of fields) {
      for (const secret of ["password", "api_key", "apikey", "private_key", "secret",
                            "token", "credential"]) {
        expect(field.toLowerCase()).not.toContain(secret);
      }
    }
  });
});

describe("reporter portal", () => {
  it("serves the safe projection only", () => {
    expect(REPORT).toContain("/trust/security/reports/");
    for (const field of ["reporter_email", "reporter_name", "description",
                         "reproduction", "evidence"]) {
      expect(REPORT).not.toContain(field);
    }
  });

  it("reads the coordination state rather than assuming one", () => {
    expect(REPORT).toContain("coordinated_disclosure_recorded");
    expect(REPORT).toContain("No disclosure timeline has been set");
    for (const invented of ["embargo until", "90 days", "we will credit", "bounty"]) {
      expect(REPORT.toLowerCase()).not.toContain(invented);
    }
  });

  it("stores no token in the browser", () => {
    for (const sink of ["localStorage", "sessionStorage", "document.cookie"]) {
      expect(REPORT).not.toContain(sink);
    }
  });
});

describe("preference centre and unsubscribe", () => {
  it("applies a one-click unsubscribe on arrival", () => {
    // An unsubscribe that needs a second click is not one-click.
    expect(PREFS).toContain("/trust/marketing/unsubscribe");
    expect(PREFS).toContain('mode === "unsubscribe"');
    expect(PREFS).toContain("takes effect immediately");
  });

  it("keeps the manage and unsubscribe handles distinct", () => {
    // Different endpoints, different purposes. The unsubscribe handle cannot read
    // preferences, and neither grants account access.
    expect(PREFS).toContain("/trust/marketing/preferences");
    expect(PREFS).toContain("/trust/marketing/unsubscribe");
  });

  it("strips the credential from the URL after using it", () => {
    expect(PREFS).toContain('next.delete("confirm")');
    expect(PREFS).toContain('next.delete("t")');
  });

  it("says plainly what unsubscribing does NOT switch off", () => {
    expect(PREFS).toContain("What is still active");
    expect(PREFS).toContain("cannot be switched off");
    // Each mandatory domain is named, because "unsubscribe from everything" is what people
    // assume they just did.
    for (const domain of ["security", "Billing", "privacy", "Support", "status page"]) {
      expect(PREFS).toContain(domain);
    }
  });

  it("treats every topic as an independent choice", () => {
    expect(PREFS).toContain("Choosing one does not sign you up for the others");
    // No "select all" that would widen consent in one click.
    expect(PREFS).not.toContain("Select all");
    expect(PREFS).toContain("Turn everything off");
  });

  it("stores no token in the browser", () => {
    for (const sink of ["localStorage", "sessionStorage", "document.cookie"]) {
      expect(PREFS).not.toContain(sink);
    }
  });
});
