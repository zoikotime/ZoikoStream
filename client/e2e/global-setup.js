// client/e2e/global-setup.js — creates the real org/host/contributor/event fixtures this
// whole suite runs against, by invoking server/e2e_fixtures.py directly (real DB writes,
// same models/functions the app itself uses — see that file's own docstring for why).
import { execFileSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";
import fs from "node:fs";
import { FIXTURES_PATH } from "./env.js";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const SERVER_DIR = path.resolve(__dirname, "../../server");
const PYTHON = path.join(SERVER_DIR, "venv", "Scripts", "python.exe");

export default function globalSetup() {
  fs.mkdirSync(path.dirname(FIXTURES_PATH), { recursive: true });
  execFileSync(PYTHON, ["e2e_fixtures.py", "create", FIXTURES_PATH], {
    cwd: SERVER_DIR,
    stdio: "inherit", // prints only ids (see e2e_fixtures.py) — never tokens
  });
}
