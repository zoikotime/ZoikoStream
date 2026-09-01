// client/e2e/env.js — shared constants for playwright.config.js and every spec/helper under
// e2e/. One place so the frontend/backend URLs can't drift between the config and the tests.
import { fileURLToPath } from "node:url";

export const FRONTEND_URL = process.env.E2E_FRONTEND_URL || "http://127.0.0.1:5173";
export const BACKEND_URL = process.env.E2E_BACKEND_URL || "http://127.0.0.1:8000";
export const FIXTURES_PATH = fileURLToPath(new URL("./.auth/fixtures.json", import.meta.url));
