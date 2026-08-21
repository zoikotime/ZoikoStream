import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

/** Static guard on the Contact page's submit path.
 *
 *  Locks out the whole class of defect rather than one instance: the form must never hand the
 *  browser to an external handler, in any spelling. Checked against the source because a
 *  rendering test can only prove the paths it happens to drive, while this proves the sink is
 *  absent outright. Mirrors the backend's code_only() structural assertions.
 */
const SOURCE = readFileSync(resolve(process.cwd(), "src/pages/Contact.jsx"), "utf8");

// Strip comments first, so the prose explaining the old bug cannot trip its own guard.
const CODE = SOURCE.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");

describe("Contact page submit path", () => {
  it("contains no mail-protocol link", () => {
    expect(CODE.toLowerCase()).not.toContain("mailto");
  });

  it("performs no imperative browser navigation", () => {
    for (const sink of ["window.location", "location.assign", "location.replace",
                        "location.href", "window.open"]) {
      expect(CODE).not.toContain(sink);
    }
  });

  it("references no externally-hosted mail domain", () => {
    for (const domain of ["secureserver", "godaddy", "go-daddy", "outlook.office",
                          "webmail"]) {
      expect(CODE.toLowerCase()).not.toContain(domain);
    }
  });

  it("submits through the API helper", () => {
    expect(CODE).toContain('api.post("/contact"');
  });

  it("hardcodes no destination inbox", () => {
    // The recipient lives in backend configuration (CONTACT_EMAIL). A literal address here
    // would mean the browser knows — and could be made to influence — where enquiries go.
    expect(CODE).not.toContain("info@zoikostream.com");
    expect(CODE).not.toContain("support@zoikostream.com");
  });
});
