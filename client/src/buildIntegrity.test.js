// Things that break the PRODUCTION BUILD but not a dev server, caught here instead of in CI.
//
// The build that failed in deployment reported two unresolved assets and then exited 1. The
// assets were a red herring — Vite says of both "it will remain unchanged to be resolved at
// runtime", and the build still exits 0 with them present. The terminating error was further
// down the log: an unresolved merge conflict left in a source file by a `git stash pop`.
//
//     [builtin:vite-transform] Unexpected token
//       src/components/admin/DataTable.jsx:476:1
//       476 │ <<<<<<< Updated upstream
//
// `git status` showed no unmerged entries, so nothing flagged it. Only the bundler did, and
// by then it was inside a Docker build on a remote VM. Both checks below are cheap and run
// with the ordinary test suite.
import { readFileSync, readdirSync, statSync, existsSync } from "node:fs";
import { join, relative, resolve } from "node:path";
import { describe, expect, it } from "vitest";

const CLIENT = process.cwd();
const SRC = resolve(CLIENT, "src");
const PUBLIC = resolve(CLIENT, "public");

const walk = (dir, out = []) => {
  for (const entry of readdirSync(dir)) {
    if (entry === "node_modules" || entry.startsWith(".")) continue;
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) walk(full, out);
    else out.push(full);
  }
  return out;
};

const TEXT = /\.(jsx?|tsx?|css|html|json)$/;
const sourceFiles = walk(SRC).filter((f) => TEXT.test(f));

// Conflict markers are looked for in EVERY source file — one in a test breaks the test run
// even though tests are never bundled. Asset references are looked for only in files that
// reach the production bundle: a URL written inside a test (including this file's own
// example above) is not an asset the build has to resolve.
const bundledFiles = sourceFiles.filter((f) => !/\.test\.[jt]sx?$/.test(f));

describe("no unresolved merge conflict reaches the bundler", () => {
  // Written as three separate markers rather than one alternation so a failure names which
  // half of the conflict was left behind.
  it.each([
    ["start", /^<{7}( |$)/m],
    ["separator-with-branch", /^>{7} /m],
    ["common-ancestor", /^\|{7}( |$)/m],
  ])("no %s marker in any source file", (_name, pattern) => {
    const hits = sourceFiles
      .filter((f) => pattern.test(readFileSync(f, "utf8")))
      .map((f) => relative(CLIENT, f));
    expect(hits).toEqual([]);
  });

  it("finds the markers when they are there — the check is not vacuous", () => {
    // A guard that cannot fail is not a guard. This is the exact line the build choked on.
    expect(/^<{7}( |$)/m.test("<<<<<<< Updated upstream\n")).toBe(true);
    expect(/^<{7}( |$)/m.test("const a = 1;\n")).toBe(false);
  });

  it("actually scanned the tree", () => {
    expect(sourceFiles.length).toBeGreaterThan(100);
  });
});

// Root-relative image URLs written into CSS-in-JS (`bg-[url('/x.jpg')]`) or markup. Vite
// cannot resolve these at build time unless the file is in public/, and it only WARNS when it
// cannot — so a typo in one is silent until someone looks at the page.
const IMAGE_URL = /url\(['"]?(\/[^'")]+\.(?:jpe?g|png|svg|webp|avif|gif))['"]?\)/g;

// Referenced on purpose, absent on purpose. Each is an OPTIONAL photograph layered over a
// CSS-drawn scene that renders fully without it, and each is documented as such at its use
// site. They are background-image layers, never <img>, so a missing file paints nothing —
// there is no broken-image icon to show. Dropping a real photo into public/ makes it appear
// with no code change, which is the point.
//
// This list is the difference between "absent by design" and "someone mistyped a path": a new
// unresolved asset is a failure, these two are not.
const INTENTIONALLY_ABSENT = new Set(["/auth-stage.jpg", "/live-events-hero.jpg"]);

const referencedImages = () => {
  const found = new Map();
  for (const file of bundledFiles) {
    const text = readFileSync(file, "utf8");
    for (const [, url] of text.matchAll(IMAGE_URL)) {
      if (!found.has(url)) found.set(url, []);
      found.get(url).push(relative(CLIENT, file));
    }
  }
  return found;
};

describe("every root-relative image reference is accounted for", () => {
  it("resolves to a file in public/, or is a declared optional asset", () => {
    const unaccounted = [];
    for (const [url, files] of referencedImages()) {
      if (INTENTIONALLY_ABSENT.has(url)) continue;
      if (!existsSync(join(PUBLIC, url.replace(/^\//, "")))) {
        unaccounted.push(`${url} (referenced in ${files.join(", ")})`);
      }
    }
    expect(unaccounted).toEqual([]);
  });

  it("keeps the optional ones honest — each is still referenced somewhere", () => {
    // If a reference is deleted, the allowlist entry should go with it rather than linger.
    const urls = new Set(referencedImages().keys());
    for (const url of INTENTIONALLY_ABSENT) {
      expect(urls.has(url), `${url} is allowlisted but no longer referenced`).toBe(true);
    }
  });

  it("the optional ones are genuinely absent, not quietly added", () => {
    // If someone drops a real photograph in, this fails and the allowlist entry should be
    // removed — at which point the first test starts enforcing the file's existence.
    for (const url of INTENTIONALLY_ABSENT) {
      expect(existsSync(join(PUBLIC, url.replace(/^\//, "")))).toBe(false);
    }
  });
});
