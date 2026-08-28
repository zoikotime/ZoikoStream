// client/e2e/global-teardown.js — deletes everything global-setup.js created, so a run never
// leaves stale test orgs/events/sessions behind in the shared dev database.
import { execFileSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";
import fs from "node:fs";
import { FIXTURES_PATH } from "./env.js";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const SERVER_DIR = path.resolve(__dirname, "../../server");
const PYTHON = path.join(SERVER_DIR, "venv", "Scripts", "python.exe");

export default function globalTeardown() {
  execFileSync(PYTHON, ["e2e_fixtures.py", "cleanup", FIXTURES_PATH], {
    cwd: SERVER_DIR,
    stdio: "inherit",
  });
  fs.rmSync(FIXTURES_PATH, { force: true });
}
