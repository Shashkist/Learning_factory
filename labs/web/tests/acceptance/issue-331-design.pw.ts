/**
 * Playwright exam for #331 (design foundation, D105): criteria 6 (header mirrors in he),
 * 7 (no horizontal scroll at 360 px on every route) and 9 (screenshots saved for CI).
 * The vitest exam issue-331-design-foundation.test.tsx has the rest, and the header comment there
 * has the note for the implementer: CI must run
 *   npx playwright test -c tests/acceptance/playwright.design.config.ts
 * in the `web` job and upload labs/web/test-results/design-screenshots/ as an artifact.
 *
 * Run from labs/web. The backend is the e2e/ fake backends (student and admin).
 */
import { expect, test, type Page } from "@playwright/test";

import { DANA, fakeAdminBackend } from "../../e2e/admin-backend";
import { fakeBackend, json } from "../../e2e/fake-backend";

type Fs = {
  readFileSync: (p: string, enc: string) => string;
  existsSync: (p: string) => boolean;
  statSync: (p: string) => { size: number };
  mkdirSync: (p: string, o: object) => void;
};
// No Node types in the app's tsconfig: a computed specifier keeps tsc quiet.
const fs = (await import(/* @vite-ignore */ "node:" + "fs")) as unknown as Fs;
const nodeUrl = (await import(/* @vite-ignore */ "node:" + "url")) as unknown as { fileURLToPath: (u: string) => string };
// Resolved from this file, not from the process folder, so any cwd works.
const i18nFile = (lang: string) => nodeUrl.fileURLToPath(new URL(`../../src/i18n/${lang}.json`, import.meta.url).href);

const copy = (lang: "en" | "he") =>
  JSON.parse(fs.readFileSync(i18nFile(lang), "utf8")) as Record<string, string>;
const COPY = { en: copy("en"), he: copy("he") };
// Also resolved from this file: always labs/web/test-results/design-screenshots/, whatever the cwd.
const SHOTS = nodeUrl.fileURLToPath(new URL("../../test-results/design-screenshots", import.meta.url).href);

async function setLang(page: Page, lang: "en" | "he") {
  await page.addInitScript((l) => window.localStorage.setItem("lf-lang", l), lang);
}

/** The teacher's live page answers too (admin-backend has no /api/admin/live). */
async function withLive(page: Page) {
  await page.route("**/api/admin/live", (route) =>
    json(route, {
      sessions: [
        {
          id: "s-1",
          key: DANA,
          name: "Dana",
          cohort: "Alpha",
          lab_id: "linux-basics",
          lab_title: { en: "Linux basics", he: "יסודות לינוקס" },
          started_at: "2026-11-20T09:00:00Z",
          last_heartbeat: "2026-11-20T09:05:00Z",
        },
      ],
    }),
  );
}

async function openAdmin(page: Page, path = "/admin") {
  await fakeAdminBackend(page);
  await withLive(page);
  await page.goto(path);
  await expect(page.getByRole("banner")).toBeVisible();
  await expect(page.getByText("Signed in as admin")).toBeVisible();
}

async function openLogin(page: Page) {
  await fakeBackend(page);
  await page.goto("/login");
  await expect(page.getByRole("banner")).toBeVisible();
}

// ---------------------------------------------------------------- AC6: the header mirrors in rtl
for (const [name, open] of [
  ["login page", openLogin],
  ["admin page", (p: Page) => openAdmin(p)],
] as const) {
  test(`AC6: on the ${name} the product name and the language switch swap sides in he`, async ({ page }) => {
    const centres: Record<string, { name: number; sw: number }> = {};
    for (const lang of ["en", "he"] as const) {
      await page.context().clearCookies();
      await setLang(page, lang);
      await page.setViewportSize({ width: 1280, height: 800 });
      await open(page);
      await expect(page.locator("html")).toHaveAttribute("dir", lang === "he" ? "rtl" : "ltr");
      const header = page.getByRole("banner");
      const product = header.getByText(COPY[lang]["app.title"], { exact: true }).first();
      const sw = header.getByRole("button", { name: COPY[lang]["lang.switch.label"] });
      await expect(product).toBeVisible();
      await expect(sw).toBeVisible();
      const [p, s] = [await product.boundingBox(), await sw.boundingBox()];
      expect(p && s).toBeTruthy();
      centres[lang] = { name: p!.x + p!.width / 2, sw: s!.x + s!.width / 2 };
      // The two never overlap.
      expect(p!.x + p!.width <= s!.x || s!.x + s!.width <= p!.x).toBe(true);
    }
    expect(centres.en.name, "en: the product name is left of the switch").toBeLessThan(centres.en.sw);
    expect(centres.he.name, "he: the product name is right of the switch").toBeGreaterThan(centres.he.sw);
    // The header's far ends hold different items: the switch moved from the right end to the left end.
    expect(centres.he.sw).toBeLessThan(centres.en.sw);
  });
}

// ---------------------------------------------------------------- AC7: no horizontal scroll at 360 px
const noHScroll = async (page: Page, label: string) => {
  await expect(page.getByRole("banner")).toBeVisible();
  await page.evaluate(() => document.fonts.ready);
  const m = await page.evaluate(() => ({ sw: document.documentElement.scrollWidth, iw: window.innerWidth }));
  expect(m.sw, `${label}: scrollWidth ${m.sw} must not exceed the 360 px viewport`).toBeLessThanOrEqual(m.iw);
};

for (const lang of ["en", "he"] as const) {
  test(`AC7: student routes have no horizontal scroll at 360 px (${lang})`, async ({ page }) => {
    await setLang(page, lang);
    await page.setViewportSize({ width: 360, height: 800 });
    const { unmocked } = await fakeBackend(page);
    await page.goto("/login");
    await noHScroll(page, "student login");

    await page.locator("input:not([type=password])").fill("123456782");
    await page.locator("input[type=password]").fill("class-password");
    await page.locator("form button[type=submit]").click();
    await expect(page).toHaveURL(/\/labs$/);
    await noHScroll(page, "My labs");
    await expect(page.getByText(/Linux basics|יסודות לינוקס/).first()).toBeVisible();

    // A live session so the lab screen shows its tasks and the terminal.
    await page.evaluate(() => fetch("/api/sessions", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ lab_id: "linux-basics" }) }));
    await page.goto("/labs/linux-basics");
    await expect(page.getByText(/List the files|הצגת הקבצים/).first()).toBeVisible();
    await noHScroll(page, "lab screen");

    await page.goto("/no/such/page");
    await noHScroll(page, "unknown path");
    expect(unmocked).toEqual([]);
  });

  test(`AC7: admin routes have no horizontal scroll at 360 px (${lang})`, async ({ page }) => {
    await setLang(page, lang);
    await page.setViewportSize({ width: 360, height: 800 });
    for (const path of ["/admin", "/admin/class", "/admin/live", `/admin/students/${DANA}`]) {
      await page.unrouteAll({ behavior: "ignoreErrors" });
      await openAdmin(page, path);
      // The page's own content is on screen (the cohort list shows Alpha; the others show Dana).
      await expect((path === "/admin" ? page.getByTestId("cohort-1") : page.getByText(/Dana/).first())).toBeVisible();
      await noHScroll(page, path);
    }
  });
}

// ---------------------------------------------------------------- AC9: screenshots for the PR
const SHOTS_MATRIX = [
  ...["login", "admin"].flatMap((pg) =>
    (["en", "he"] as const).flatMap((lang) =>
      (["light", "dark"] as const).flatMap((scheme) =>
        [1280, 390].map((width) => ({ pg, lang, scheme, width })),
      ),
    ),
  ),
];

for (const s of SHOTS_MATRIX) {
  const shot = `${s.pg}-${s.lang}-${s.scheme}-${s.width}.png`;
  const file = `${SHOTS}/${shot}`;
  test(`AC9: screenshot of the ${s.pg} page, ${s.lang}, ${s.scheme}, ${s.width} px is saved to test-results/design-screenshots/${shot}`, async ({ page }) => {
    fs.mkdirSync(SHOTS, { recursive: true });
    await setLang(page, s.lang);
    await page.emulateMedia({ colorScheme: s.scheme });
    await page.setViewportSize({ width: s.width, height: s.width === 1280 ? 800 : 844 });
    if (s.pg === "login") await openLogin(page);
    else await openAdmin(page);
    await page.evaluate(() => document.fonts.ready);
    // The theme tokens exist and the page paints with them (not the browser default white).
    const bg = await page.evaluate(() => getComputedStyle(document.documentElement).getPropertyValue("--color-bg").trim());
    expect(bg, "--color-bg is defined by the token file").not.toEqual("");
    await page.screenshot({ path: file, fullPage: true });
    expect(fs.existsSync(file)).toBe(true);
    expect(fs.statSync(file).size).toBeGreaterThan(1000);
  });
}

// The dark theme really changes the page: the screenshots would otherwise show the same thing twice.
test("AC9: dark and light themes paint a different page background", async ({ page }) => {
  await fakeBackend(page);
  const colour = async (scheme: "light" | "dark") => {
    await page.emulateMedia({ colorScheme: scheme });
    await page.goto("/login");
    await expect(page.getByRole("banner")).toBeVisible();
    return page.evaluate(() => {
      const clear = "rgba(0, 0, 0, 0)";
      const body = getComputedStyle(document.body).backgroundColor;
      return body !== clear ? body : getComputedStyle(document.documentElement).backgroundColor;
    });
  };
  const [light, dark] = [await colour("light"), await colour("dark")];
  expect(light).not.toEqual("rgba(0, 0, 0, 0)");
  expect(dark).not.toEqual("rgba(0, 0, 0, 0)");
  expect(dark).not.toEqual(light);
});
