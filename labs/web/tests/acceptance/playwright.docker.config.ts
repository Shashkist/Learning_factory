import { defineConfig } from "@playwright/test";

// Issue #167: the one browser flow against the REAL manager (docker CI job). There is no
// web server here: the manager serves the built app, and the pytest exam
// labs/manager/tests/acceptance/test_issue_167_browser_e2e_docker.py starts it and runs this config.
// Node runs this file; the app's tsconfig has no Node types, so declare just what is used.
declare const process: { env: Record<string, string | undefined> };

export default defineConfig({
  testDir: ".",
  testMatch: "issue-167-docker-e2e.pw.ts",
  forbidOnly: true,
  retries: 0, // a retry would hide timing bugs in the flow under test
  workers: 1,
  timeout: 240_000, // a real lab container under runsc takes a while to start
  reporter: [["list"], ["json", { outputFile: process.env.LF_E2E_REPORT ?? "e2e-report.json" }]],
  use: { baseURL: process.env.LF_E2E_BASE_URL, trace: "off" },
  projects: [{ name: "chromium", use: { browserName: "chromium" } }],
});
