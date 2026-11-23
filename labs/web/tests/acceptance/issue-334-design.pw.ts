/**
 * Playwright exam for #334 (visual design, teacher pages, D105): layout and screenshots. The
 * vitest exam issue-334-teacher-pages.test.tsx has the semantics and the header comment with the
 * structural hooks. Run from labs/web with the design config (its testMatch picks up every
 * issue-<n>-design.pw.ts):
 *   npx playwright test -c tests/acceptance/playwright.design.config.ts issue-334
 *
 * What is checked here
 *   - AC2 at 390 px: no admin page (cohorts with an import result shown, class table with many lab
 *     columns, live sessions, student page) has horizontal PAGE scroll (document scrollWidth <=
 *     innerWidth), while the wide class table scrolls inside its own container: the table's
 *     nearest ancestor with overflow-x auto or scroll (not body) has scrollWidth > clientWidth.
 *   - Sticky header: on the class table and the live table, <thead> or its <th> cells compute
 *     position: sticky. (Hook: the CSS sets `position: sticky` on `thead th` or `thead`.)
 *   - AC6: screenshots of the class table, a student page, live sessions and the import form with
 *     an error, en + he, light + dark, 1280 + 390 px, saved as
 *     test-results/design-screenshots/334-<page>-<lang>-<scheme>-<width>.png.
 * The implementer needs no extra hook: pages are found by role and name, the tables by
 * data-testid="class-table" and "live-table" (both already exist).
 */
import { expect, test, type Page } from "@playwright/test";

import { DANA, fakeAdminBackend } from "../../e2e/admin-backend";
import { json } from "../../e2e/fake-backend";

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

/** The class table with many lab columns, so it is wider than a phone. */
const WIDE_LABS = Array.from({ length: 12 }, (_, i) => ({
  id: `lab-${i + 1}`,
  title: { en: `Lab number ${i + 1} title`, he: `מעבדה מספר ${i + 1} כותרת` },
}));
const WIDE_ROWS = [
  ["Dana", DANA, "Alpha"],
  ["Noa", "0b9e7a64-8c2d-4f10-b7a5-1d2e3f405162", "Beta"],
  ["Eli", "11111111-2222-4333-8444-555555555555", "Alpha"],
].map(([name, key, cohort], r) => ({
  key,
  name,
  cohort,
  last_activity: r === 1 ? null : "2026-11-20T09:00:00Z",
  cells: Object.fromEntries(
    WIDE_LABS.map((l, i) => [l.id, { status: i % 3 === 0 ? "done" : i % 3 === 1 ? "2/5" : "none", flag: r === 0 && i === 0 }]),
  ),
}));

async function open(page: Page, path: string) {
  await fakeAdminBackend(page);
  await page.route("**/api/admin/class**", (route) => json(route, { labs: WIDE_LABS, rows: WIDE_ROWS }));
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
  await page.goto(path);
  await expect(page.getByRole("banner")).toBeVisible();
  await expect(page.getByText("Signed in as admin")).toBeVisible();
}

/** The import form with an error shown: one good line and one with an invalid id, then Import. */
async function importWithError(page: Page, lang: "en" | "he") {
  await open(page, "/admin");
  await expect(page.getByTestId("cohort-1")).toBeVisible();
  await page.getByLabel(COPY[lang]["admin.import.text"]).fill("123456782,Fake Student\nnot-an-id,Fake Other");
  await page.getByRole("button", { name: COPY[lang]["admin.import.submit"], exact: true }).click();
  await expect(page.getByTestId("import-summary")).toBeVisible();
  await expect(page.getByTestId("import-row-2")).toContainText(COPY[lang]["admin.import.error.invalid_id"]);
}

const PAGES = {
  "class-table": async (page: Page) => {
    await open(page, "/admin/class");
    await expect(page.getByTestId("class-table")).toBeVisible();
    await expect(page.getByTestId("class-row-0")).toBeVisible();
  },
  student: async (page: Page) => {
    await open(page, `/admin/students/${DANA}`);
    await expect(page.getByRole("heading", { name: "Dana" })).toBeVisible();
    await expect(page.getByTestId("student-lab-linux-basics")).toBeVisible();
  },
  live: async (page: Page) => {
    await open(page, "/admin/live");
    await expect(page.getByTestId("live-table")).toBeVisible();
  },
  "import-error": importWithError,
} as const;
type PageName = keyof typeof PAGES;

// ---------------------------------------------------------------- AC2: no page scroll at 390 px
const noHScroll = async (page: Page, label: string) => {
  await page.evaluate(() => document.fonts.ready);
  const m = await page.evaluate(() => ({ sw: document.documentElement.scrollWidth, iw: window.innerWidth }));
  expect(m.sw, `${label}: scrollWidth ${m.sw} must not exceed the 390 px viewport`).toBeLessThanOrEqual(m.iw);
};

for (const lang of ["en", "he"] as const) {
  for (const name of Object.keys(PAGES) as PageName[]) {
    test(`AC2: ${name} has no horizontal page scroll at 390 px (${lang})`, async ({ page }) => {
      await setLang(page, lang);
      await page.setViewportSize({ width: 390, height: 844 });
      await PAGES[name](page, lang);
      await noHScroll(page, name);
    });
  }

  test(`AC2: the wide class table scrolls inside its own container at 390 px (${lang})`, async ({ page }) => {
    await setLang(page, lang);
    await page.setViewportSize({ width: 390, height: 844 });
    await PAGES["class-table"](page);
    const probe = await page.getByTestId("class-table").evaluate((table) => {
      let el = table.parentElement;
      while (el && el !== document.documentElement) {
        const ox = getComputedStyle(el).overflowX;
        if (ox === "auto" || ox === "scroll") {
          return { found: true, scrollWidth: el.scrollWidth, clientWidth: el.clientWidth, isBody: el === document.body };
        }
        el = el.parentElement;
      }
      return { found: false, scrollWidth: 0, clientWidth: 0, isBody: false };
    });
    expect(probe.found, "the table has a scrollable ancestor (overflow-x auto or scroll)").toBe(true);
    expect(probe.isBody, "the scroll container is not <body>").toBe(false);
    expect(probe.scrollWidth, "the table is wider than its container").toBeGreaterThan(probe.clientWidth);
    expect(probe.clientWidth, "the container fits the viewport").toBeLessThanOrEqual(390);
    await noHScroll(page, "class table page");
  });
}

// ---------------------------------------------------------------- sticky header
for (const [name, path, id] of [
  ["class table", "/admin/class", "class-table"],
  ["live sessions table", "/admin/live", "live-table"],
] as const) {
  test(`the ${name} header is sticky`, async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 800 });
    await open(page, path);
    const pos = await page.getByTestId(id).evaluate((table) => {
      const head = table.querySelector("thead");
      const th = table.querySelector("thead th");
      return { thead: head ? getComputedStyle(head).position : "", th: th ? getComputedStyle(th).position : "" };
    });
    expect(pos.thead === "sticky" || pos.th === "sticky", `thead ${pos.thead} / th ${pos.th}`).toBe(true);
  });
}

// ---------------------------------------------------------------- AC6: screenshots for the PR
for (const pg of Object.keys(PAGES) as PageName[]) {
  for (const lang of ["en", "he"] as const) {
    for (const scheme of ["light", "dark"] as const) {
      for (const width of [1280, 390]) {
        const file = `${SHOTS}/334-${pg}-${lang}-${scheme}-${width}.png`;
        test(`AC6: screenshot of ${pg}, ${lang}, ${scheme}, ${width} px is saved to ${file}`, async ({ page }) => {
          fs.mkdirSync(SHOTS, { recursive: true });
          await setLang(page, lang);
          await page.emulateMedia({ colorScheme: scheme });
          await page.setViewportSize({ width, height: width === 1280 ? 800 : 844 });
          await PAGES[pg](page, lang);
          await page.evaluate(() => document.fonts.ready);
          await page.screenshot({ path: file, fullPage: true });
          expect(fs.existsSync(file)).toBe(true);
          expect(fs.statSync(file).size).toBeGreaterThan(1000);
        });
      }
    }
  }
}
