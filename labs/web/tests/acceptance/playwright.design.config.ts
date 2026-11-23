import { defineConfig } from "@playwright/test";

// Issue #331: the design Playwright exam (issue-331-design.pw.ts). Modelled on
// labs/web/playwright.config.ts: it builds the app and serves it with vite preview, the backend
// is faked per test with the e2e/ fake backends. Run it from labs/web:
//   npx playwright test -c tests/acceptance/playwright.design.config.ts
// Screenshots (criterion 9) are written to labs/web/test-results/design-screenshots/ (resolved from
// each spec file, so any working directory gives the same folder); CI uploads that folder as an artifact. outputDir is a sibling
// folder so Playwright's own clean-up of its output never deletes the screenshots.
// Node runs this file; the app's tsconfig has no Node types, so declare just what is used.
declare const process: { env: Record<string, string | undefined> };

const PORT = 4331; // its own port: never collide with the e2e preview on 4173

export default defineConfig({
  testDir: ".",
  testMatch: /issue-\d+-design\.pw\.ts$/,
  forbidOnly: !!process.env.CI,
  retries: 0,
  reporter: process.env.CI ? "github" : "list",
  outputDir: "../../test-results/design-output",
  use: { baseURL: `http://127.0.0.1:${PORT}`, trace: "retain-on-failure" },
  projects: [{ name: "chromium", use: { browserName: "chromium" } }],
  webServer: {
    command: `npm run build && npx vite preview --host 127.0.0.1 --port ${PORT} --strictPort`,
    cwd: "../..", // labs/web, from this file's folder
    url: `http://127.0.0.1:${PORT}`,
    reuseExistingServer: false, // never test another worktree's server on the same port
    timeout: 180_000,
  },
});
