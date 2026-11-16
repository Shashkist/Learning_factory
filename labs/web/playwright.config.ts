import { defineConfig } from "@playwright/test";

// Node runs this file; the app's tsconfig has no Node types, so declare just what is used.
declare const process: { env: Record<string, string | undefined> };

export default defineConfig({
  testDir: "e2e",
  forbidOnly: !!process.env.CI,
  retries: 0, // a retry would hide timing bugs in the flow under test
  reporter: process.env.CI ? "github" : "list",
  use: { baseURL: "http://127.0.0.1:4173", trace: "retain-on-failure" },
  projects: [{ name: "chromium", use: { browserName: "chromium" } }],
  // The built app, served as-is. The backend is faked per test with page.route.
  webServer: {
    command: "npm run build && npx vite preview --host 127.0.0.1 --port 4173 --strictPort",
    url: "http://127.0.0.1:4173",
    reuseExistingServer: false, // never test another worktree's server on the same port
    timeout: 120_000,
  },
});
