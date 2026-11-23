/**
 * Playwright exam for #332 (login and My labs design, D105): criterion 5 (the lab grid is one
 * column at 390 px and holds at least three cards in a row at 1280 px) and criterion 7 (the
 * screenshots: login and My labs with zero, one and several labs, en + he, light + dark, 1280 px
 * + 390 px, saved to test-results/design-screenshots/332-*.png for the CI artifact).
 * The vitest exam issue-332-login-mylabs.test.tsx has the rest and the contract; run from labs/web:
 *   npx playwright test -c tests/acceptance/playwright.design.config.ts issue-332
 *
 * STRUCTURAL HOOKS the implementer must provide (semantic, no class names or test ids):
 *   - every lab on My labs is an element with role `article`; the articles are laid out as a grid
 *     (cards of one module may be split in sections, but the first module's cards share one grid);
 *   - the article holds a role `progressbar` that is visible (non-zero size);
 *   - on the login page the `login.intro` text is visible.
 * The backend is the e2e fake backend; GET /api/labs is overridden in the test with page.route.
 */
import { expect, test, type Page } from "@playwright/test";

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

const bi = (en: string, he: string) => ({ en, he });
const lab = (n: number, module: number, progress: string, session = false) => ({
  id: `lab-${n}`,
  title: bi(`Lab number ${n}`, `מעבדה מספר ${n}`),
  description: bi(`What lab ${n} teaches, in a sentence or two.`, `מה מלמדת מעבדה ${n}, במשפט או שניים.`),
  module,
  lesson: n,
  tasks: 5,
  progress,
  session_id: session ? `s-${n}` : null,
});
const SEVERAL = [
  lab(1, 1, "done"),
  lab(2, 1, "3/5", true),
  lab(3, 1, "not_started"),
  lab(4, 1, "not_started"),
  lab(5, 1, "1/5"),
  lab(6, 1, "not_started"),
  lab(7, 2, "not_started"),
];
const SETS = { zero: [] as typeof SEVERAL, one: [lab(1, 1, "2/5", true)], several: SEVERAL };

async function setLang(page: Page, lang: "en" | "he") {
  await page.addInitScript((l) => window.localStorage.setItem("lf-lang", l), lang);
}

/** Sign in through the form, with the lab list overridden (later routes win over the fake's). */
async function openLabs(page: Page, labs: object[], lang: "en" | "he" = "en") {
  const { unmocked } = await fakeBackend(page);
  await page.route("**/api/labs", (route) => json(route, labs));
  await page.goto("/login");
  await page.getByLabel(COPY[lang]["login.id"]).fill("123456782");
  await page.getByLabel(COPY[lang]["login.password"]).fill("class-password");
  await page.locator("form button[type=submit]").click();
  await expect(page).toHaveURL(/\/labs$/);
  await expect(page.getByRole("heading", { name: COPY[lang]["labs.title"], level: 1 })).toBeVisible();
  await page.evaluate(() => document.fonts.ready);
  return { unmocked };
}

// ---------------------------------------------------------------- AC5: the grid
test("AC5: at 390 px the lab cards are in one column", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await openLabs(page, SEVERAL);
  const cards = page.getByRole("article");
  await expect(cards).toHaveCount(SEVERAL.length);
  const boxes = [];
  for (let i = 0; i < SEVERAL.length; i++) boxes.push((await cards.nth(i).boundingBox())!);
  for (const b of boxes) expect(b).toBeTruthy();
  for (let i = 1; i < boxes.length; i++) {
    expect(Math.abs(boxes[i].x - boxes[0].x), `card ${i} starts at the same x as card 0`).toBeLessThanOrEqual(1);
    expect(boxes[i].y, `card ${i} is below card ${i - 1}`).toBeGreaterThanOrEqual(boxes[i - 1].y + boxes[i - 1].height - 1);
  }
  expect(boxes[0].width, "a card uses most of the phone width").toBeGreaterThan(390 * 0.7);
  const m = await page.evaluate(() => ({ sw: document.documentElement.scrollWidth, iw: window.innerWidth }));
  expect(m.sw).toBeLessThanOrEqual(m.iw);
});

test("AC5: at 1280 px at least three cards sit in one row", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  await openLabs(page, SEVERAL);
  const cards = page.getByRole("article");
  await expect(cards).toHaveCount(SEVERAL.length);
  const boxes = [];
  for (let i = 0; i < 3; i++) boxes.push((await cards.nth(i).boundingBox())!);
  for (const b of boxes) expect(b).toBeTruthy();
  for (let i = 1; i < 3; i++)
    expect(Math.abs(boxes[i].y - boxes[0].y), `card ${i} is in the same row as card 0`).toBeLessThanOrEqual(2);
  const xs = boxes.map((b) => Math.round(b.x));
  expect(new Set(xs).size, "the three cards are side by side").toBe(3);
  // They do not overlap.
  const sorted = [...boxes].sort((a, b) => a.x - b.x);
  for (let i = 1; i < 3; i++) expect(sorted[i].x).toBeGreaterThanOrEqual(sorted[i - 1].x + sorted[i - 1].width - 1);
});

test("AC5: the same row holds at 1280 px in Hebrew (the grid mirrors)", async ({ page }) => {
  await setLang(page, "he");
  await page.setViewportSize({ width: 1280, height: 800 });
  await openLabs(page, SEVERAL, "he");
  const cards = page.getByRole("article");
  await expect(cards).toHaveCount(SEVERAL.length);
  const [a, b, c] = [await cards.nth(0).boundingBox(), await cards.nth(1).boundingBox(), await cards.nth(2).boundingBox()];
  expect(Math.abs(b!.y - a!.y)).toBeLessThanOrEqual(2);
  expect(Math.abs(c!.y - a!.y)).toBeLessThanOrEqual(2);
  // rtl: the first card is at the right-hand end of the row.
  expect(a!.x).toBeGreaterThan(b!.x);
  expect(b!.x).toBeGreaterThan(c!.x);
});

test("AC2: a card's progress bar is visible in the page", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  await openLabs(page, SEVERAL);
  const bar = page.getByRole("article").nth(1).getByRole("progressbar");
  await expect(bar).toBeVisible();
  const box = (await bar.boundingBox())!;
  expect(box.width).toBeGreaterThan(20);
  expect(box.height).toBeGreaterThan(2);
  await expect(bar).toHaveAttribute("aria-valuenow", "3");
  await expect(bar).toHaveAttribute("aria-valuemax", "5");
});

// ---------------------------------------------------------------- AC7: screenshots
const MATRIX = (["login", "labs-zero", "labs-one", "labs-several"] as const).flatMap((pg) =>
  (["en", "he"] as const).flatMap((lang) =>
    (["light", "dark"] as const).flatMap((scheme) => [1280, 390].map((width) => ({ pg, lang, scheme, width }))),
  ),
);

for (const s of MATRIX) {
  const file = `${SHOTS}/332-${s.pg}-${s.lang}-${s.scheme}-${s.width}.png`;
  test(`AC7: screenshot of ${s.pg}, ${s.lang}, ${s.scheme}, ${s.width} px is saved to ${file}`, async ({ page }) => {
    fs.mkdirSync(SHOTS, { recursive: true });
    await setLang(page, s.lang);
    await page.emulateMedia({ colorScheme: s.scheme });
    await page.setViewportSize({ width: s.width, height: s.width === 1280 ? 800 : 844 });
    if (s.pg === "login") {
      await fakeBackend(page);
      await page.goto("/login");
      const intro = COPY[s.lang]["login.intro"];
      expect(intro, "the i18n key login.intro exists").toBeTruthy();
      await expect(page.getByText(intro)).toBeVisible();
    } else {
      const set = SETS[s.pg.slice("labs-".length) as keyof typeof SETS];
      await openLabs(page, set, s.lang);
      if (set.length === 0) await expect(page.getByText(COPY[s.lang]["labs.empty"])).toBeVisible();
      else {
        await expect(page.getByRole("article")).toHaveCount(set.length);
        await expect(page.getByRole("progressbar").first()).toBeVisible();
      }
    }
    await page.evaluate(() => document.fonts.ready);
    await page.screenshot({ path: file, fullPage: true });
    expect(fs.existsSync(file)).toBe(true);
    expect(fs.statSync(file).size).toBeGreaterThan(1000);
  });
}
