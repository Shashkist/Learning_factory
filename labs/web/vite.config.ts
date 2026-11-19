import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";
import { configDefaults } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    globals: true,
    exclude: [...configDefaults.exclude, "e2e/**"],
    // Pages show local times; the exams' fixtures name UTC dates, so tests run in UTC everywhere.
    env: { TZ: "UTC" },
  },
});

