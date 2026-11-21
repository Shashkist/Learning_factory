import { defineConfig } from "@playwright/test";

// Issue #427: Linux 1 moves in the browser against the REAL manager (docker CI job). Like
// playwright.docker.config.ts (#167) there is no web server: the pytest exam
// labs/manager/tests/acceptance/test_issue_427_linux1_moves_docker.py starts the manager and runs this.
declare const process: { env: Record<string, string | undefined> };

export default defineConfig({
  testDir: ".",
  testMatch: "issue-427-linux1-moves.pw.ts",
  forbidOnly: true,
  retries: 0, // a retry would hide the failure under diagnosis
  workers: 1,
  outputDir: process.env.LF_E2E_OUTPUT_DIR,
  timeout: 420_000, // two real lab containers under runsc, nine setups
  reporter: [["list"], ["json", { outputFile: process.env.LF_E2E_REPORT ?? "e2e-report.json" }]],
  use: { baseURL: process.env.LF_E2E_BASE_URL, trace: "off" },
  projects: [{ name: "chromium", use: { browserName: "chromium" } }],
});
