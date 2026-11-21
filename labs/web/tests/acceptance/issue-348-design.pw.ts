/**
 * Playwright exam for #348 (follow-up of the #334 design exam, D110): two behaviours the first
 * exam checked only by style. Runs in the design config (tests/acceptance/playwright.design.config.ts),
 * from labs/web:
 *   npx playwright test -c tests/acceptance/playwright.design.config.ts issue-348
 *
 * CONTRACT
 *   - AC3: a long class table (60 students) scrolled inside its own container keeps its header row
 *     visible: after scrolling the container well past the first rows, the column header cells sit
 *     at the top edge of that container (within 2 px) and are inside the viewport, and the element at
 *     the centre of a header cell is that header cell (no body row paints over it). The first body
 *     row has really moved out of view (the scroll did something). If the table is not the thing
 *     that scrolls, the page scrolls and the header must sit at the top of the viewport instead.
 *   - AC4: the import "too many lines" (more than 2,000 lines) error is inside the same fieldset as
 *     the textarea, is visible, and is painted below the textarea's box inside the fieldset's box.
 *   - The fake backend sees no unmocked request (tracked right after it is installed).
 * No hook is needed: the table is data-testid="class-table", the field is found by its label.
 */
import { expect, test, type Page } from "@playwright/test";

import { fakeAdminBackend } from "../../e2e/admin-backend";
import { json } from "../../e2e/fake-backend";

type Fs = { readFileSync: (p: string, enc: string) => string };
// No Node types in the app's tsconfig: a computed specifier keeps tsc quiet.
const fs = (await import(/* @vite-ignore */ "node:" + "fs")) as unknown as Fs;
const copy = (lang: "en" | "he") =>
  JSON.parse(fs.readFileSync(`src/i18n/${lang}.json`, "utf8")) as Record<string, string>;
const COPY = { en: copy("en"), he: copy("he") };
type Lang = "en" | "he";

let seen: string[] | null = null;
test.afterEach(() => {
  expect(seen, "the fake backend saw an unmocked request").toEqual([]);
  seen = null;
});

const LABS = Array.from({ length: 4 }, (_, i) => ({
  id: `lab-${i + 1}`,
  title: { en: `Lab number ${i + 1}`, he: `מעבדה מספר ${i + 1}` },
}));
const uuid = (n: number) => `00000000-0000-4000-8000-${String(n).padStart(12, "0")}`;
const ROWS = Array.from({ length: 60 }, (_, r) => ({
  key: uuid(r + 1),
  name: `Fake Student ${r + 1}`,
  cohort: "Alpha",
  last_activity: "2026-11-20T09:00:00Z",
  cells: Object.fromEntries(LABS.map((l, i) => [l.id, { status: (r + i) % 3 === 0 ? "done" : "none", flag: false }])),
}));

async function open(page: Page, lang: Lang, path: string) {
  await page.addInitScript((l) => window.localStorage.setItem("lf-lang", l), lang);
  await page.setViewportSize({ width: 1280, height: 800 });
  const { unmocked } = await fakeAdminBackend(page);
  seen = unmocked; // tracked from the moment the fake is installed, so a failed step still checks it
  await page.route("**/api/admin/class**", (route) => json(route, { labs: LABS, rows: ROWS }));
  await page.goto(path);
  await expect(page.getByRole("banner")).toBeVisible();
}

for (const lang of ["en", "he"] as const) {
  test(`AC3: scrolling a long class table keeps the header row visible at the top of the scroll area (${lang})`, async ({ page }) => {
    await open(page, lang, "/admin/class");
    const table = page.getByTestId("class-table");
    await expect(table).toBeVisible();
    await expect(page.getByTestId("class-row-59")).toBeAttached();
    await page.evaluate(() => document.fonts.ready);

    const first = await table.locator("tbody tr").first().boundingBox();
    expect(first, "first body row has a box").not.toBeNull();

    // Scroll whatever scrolls vertically: the nearest ancestor with overflow-y, else the page.
    const scroller = await table.evaluate((t) => {
      let el: HTMLElement | null = t.parentElement;
      while (el && el !== document.documentElement) {
        const oy = getComputedStyle(el).overflowY;
        if ((oy === "auto" || oy === "scroll") && el.scrollHeight > el.clientHeight) {
          el.setAttribute("data-scroller-348", "1");
          el.scrollTop = Math.floor((el.scrollHeight - el.clientHeight) / 2);
          return "container";
        }
        el = el.parentElement;
      }
      window.scrollTo(0, document.documentElement.scrollHeight / 2);
      return "page";
    });
    await page.waitForTimeout(100);

    const afterFirst = await table.locator("tbody tr").first().boundingBox();
    expect(afterFirst, "first body row still has a box").not.toBeNull();
    const top = scroller === "container" ? (await page.locator('[data-scroller-348="1"]').boundingBox())!.y : 0;
    expect(afterFirst!.y + afterFirst!.height, "the scroll moved the first body row out of view").toBeLessThanOrEqual(top + 1);

    const heads = table.locator("thead th");
    const count = await heads.count();
    expect(count).toBeGreaterThanOrEqual(LABS.length + 2);
    for (let i = 0; i < count; i++) {
      const th = heads.nth(i);
      const b = (await th.boundingBox())!;
      expect(b, `header cell ${i} has a box`).not.toBeNull();
      expect(b.y, `header cell ${i} top ${b.y} is at the scroll area's top edge ${top}`).toBeGreaterThanOrEqual(top - 1);
      expect(b.y, `header cell ${i} top ${b.y} stays within 2 px of the top edge ${top}`).toBeLessThanOrEqual(top + 2);
      expect(b.y + b.height, "header cell is inside the viewport").toBeLessThanOrEqual(800);
      const onTop = await th.evaluate((el) => {
        const r = el.getBoundingClientRect();
        const hit = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
        return !!hit && (el === hit || el.contains(hit));
      });
      expect(onTop, `header cell ${i} is painted on top, not covered by a body row`).toBe(true);
    }
  });

  test(`AC4: the import "too many lines" error is inside the textarea's fieldset, below the textarea (${lang})`, async ({ page }) => {
    const d = COPY[lang];
    await open(page, lang, "/admin");
    await expect(page.getByTestId("cohort-1")).toBeVisible();
    const field = page.getByLabel(d["admin.import.text"]);
    await field.fill(Array.from({ length: 2001 }, (_, i) => `${i + 1},Fake ${i}`).join("\n"));
    await page.getByRole("button", { name: d["admin.import.submit"], exact: true }).click();

    const alert = page.getByRole("alert").filter({ hasText: d["admin.import.too_many"] });
    await expect(alert).toBeVisible();
    const inside = await field.evaluate((ta, text) => {
      const fieldset = ta.closest("fieldset");
      if (!fieldset) return { fieldset: false, contains: false };
      const hit = Array.from(fieldset.querySelectorAll('[role="alert"]')).some((e) => (e.textContent ?? "").includes(text));
      return { fieldset: true, contains: hit };
    }, d["admin.import.too_many"]);
    expect(inside.fieldset, "the textarea is in a fieldset").toBe(true);
    expect(inside.contains, "the error element is inside that fieldset").toBe(true);

    const a = (await alert.boundingBox())!;
    const t = (await field.boundingBox())!;
    const f = (await field.locator("xpath=ancestor::fieldset[1]").boundingBox())!;
    expect(a.y, "the error is painted below the textarea").toBeGreaterThanOrEqual(t.y + t.height - 1);
    expect(a.y + a.height, "the error is inside the fieldset's box").toBeLessThanOrEqual(f.y + f.height + 1);
    await expect(field).toHaveAttribute("aria-invalid", "true");
  });
}
