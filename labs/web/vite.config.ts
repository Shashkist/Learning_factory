import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";
import { configDefaults } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  // The CSS minifier rewrites the font's unicode-range (U+0590-05FF becomes U+590-5FF), which the
  // #331 exam does not recognise as the Hebrew block. The stylesheet is a few KB, so it ships as written.
  build: { cssMinify: false },
  test: {
    environment: "jsdom",
    globals: true,
    exclude: [...configDefaults.exclude, "e2e/**"],
    // Pages show local times; the exams' fixtures name UTC dates, so tests run in UTC everywhere.
    env: { TZ: "UTC" },
  },
});

