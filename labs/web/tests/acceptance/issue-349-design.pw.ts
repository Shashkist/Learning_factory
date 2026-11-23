/**
 * Playwright exam for #349 (lab screen top bar and full-pane end states; review of #347). The vitest exam
 * issue-349-lab-topbar.test.tsx has the DOM contract. Runs in the design config
 * (tests/acceptance/playwright.design.config.ts), from labs/web.
 *
 * CONTRACT (layout; all semantic, no test ids beyond the existing task-panel)
 *   - The top bar is a strip above the panes: End lab and the countdown (role=timer) are both fully above
 *     the tasks pane and the terminal, and visible without switching tabs. At 390 px End lab and the timer
 *     stay visible on the Terminal tab too (the bar is not part of a pane).
 *   - Lab complete: the message takes the whole pane area (at 1280 px its box is at least 60 % of the
 *     viewport wide), the terminal is not shown, "Back to My labs" is visible and works. The terminal
 *     iframe stays mounted (Previous brings the task back without reloading ttyd).
 *   - Session ended (the server answers 410 for the session): the panes are gone (no task panel, no
 *     terminal), the message and "Back to My labs" are visible and the link goes to /labs.
 *   - The fake backend sees no unmocked request.
 */
import { expect, test, type Page } from "@playwright/test";

import { fakeBackend, json } from "../../e2e/fake-backend";

type Fs = { readFileSync: (p: string, enc: string) => string };
// No Node types in the app's tsconfig: a computed specifier keeps tsc quiet.
const fs = (await import(/* @vite-ignore */ "node:" + "fs")) as unknown as Fs;
const nodeUrl = (await import(/* @vite-ignore */ "node:" + "url")) as unknown as { fileURLToPath: (u: string) => string };
// Resolved from this file, not from the process folder, so any cwd works.
const i18nFile = (lang: string) => nodeUrl.fileURLToPath(new URL(`../../src/i18n/${lang}.json`, import.meta.url).href);
const copy = (lang: "en" | "he") =>
  JSON.parse(fs.readFileSync(i18nFile(lang), "utf8")) as Record<string, string>;
const COPY = { en: copy("en"), he: copy("he") };
type Lang = "en" | "he";

let seen: string[] | null = null;
test.afterEach(() => {
  expect(seen, "the fake backend saw an unmocked request").toEqual([]);
  seen = null;
});

/** Sign in, start the lab, open the lab screen. `ended` makes the server answer 410 for the session. */
async function openLab(page: Page, lang: Lang, width: number, height: number, ended = false) {
  await page.addInitScript((l) => window.localStorage.setItem("lf-lang", l), lang);
  await page.setViewportSize({ width, height });
  const { unmocked } = await fakeBackend(page);
  seen = unmocked;
  await page.route("**/api/sessions/s-e2e/tasks/*/check", (route) =>
    json(route, { passed: true, attempts: 1, reason: null, checklist: [{ n: 1, passed: true, hint: null }] }),
  );
  await page.route("**/api/sessions/s-e2e/tasks", (route) =>
    json(route, {
      lab_id: "linux-basics",
      current: null,
      tasks: [
        { id: "t1", index: 1, kind: "hands-on", state: "passed", title: { en: "List the files", he: "הצגת הקבצים" } },
        { id: "t2", index: 2, kind: "hands-on", state: "passed", title: { en: "Make a folder", he: "יצירת תיקייה" } },
      ],
    }),
  );
  await page.goto("/login");
  await page.locator("input:not([type=password])").fill("123456782");
  await page.locator("input[type=password]").fill("class-password");
  await page.locator("form button[type=submit]").click();
  await expect(page).toHaveURL(/\/labs$/);
  await page.evaluate(() =>
    fetch("/api/sessions", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ lab_id: "linux-basics" }) }),
  );
  if (ended)
    await page.route("**/api/sessions/s-e2e/tasks/current", (route) => json(route, { detail: "session_ended" }, 410));
  await page.goto("/labs/linux-basics");
  if (!ended) await expect(page.getByTestId("task-panel")).toBeVisible();
  await page.evaluate(() => document.fonts.ready);
}

const box = async (loc: ReturnType<Page["locator"]>) => {
  const b = await loc.boundingBox();
  expect(b, "element has a box").not.toBeNull();
  return b!;
};

for (const lang of ["en", "he"] as const) {
  const d = COPY[lang];
  const end = (page: Page) => page.getByRole("button", { name: d["lab.end"], exact: true });
  const frame = (page: Page) => page.getByTitle(d["lab.terminal"]);

  test(`AC1: at 1280 px End lab and the countdown sit in a strip above both panes (${lang})`, async ({ page }) => {
    await openLab(page, lang, 1280, 800);
    await expect(end(page)).toBeVisible();
    await expect(page.getByTestId("task-panel").getByRole("button", { name: d["lab.end"], exact: true })).toHaveCount(0);
    const e = await box(end(page));
    const t = await box(page.getByRole("timer"));
    const tasks = await box(page.getByTestId("task-panel"));
    const term = await box(frame(page));
    for (const [name, b] of [["End lab", e], ["countdown", t]] as const) {
      expect(b.y + b.height, `${name} ends at ${b.y + b.height}, the panes start at ${tasks.y} / ${term.y}`).toBeLessThanOrEqual(Math.min(tasks.y, term.y) + 1);
    }
  });

  test(`AC1: at 390 px End lab and the countdown stay visible on the Tasks tab and on the Terminal tab (${lang})`, async ({ page }) => {
    await openLab(page, lang, 390, 844);
    await expect(end(page)).toBeVisible();
    await expect(page.getByRole("timer")).toBeVisible();
    await page.getByRole("tab", { name: d["lab.pane.terminal"], exact: true }).click();
    await expect(frame(page)).toBeVisible();
    await expect(end(page), "End lab is reachable from the terminal view").toBeVisible();
    await expect(page.getByRole("timer")).toBeVisible();
    const e = await box(end(page));
    expect(e.x).toBeGreaterThanOrEqual(0);
    expect(e.x + e.width, "End lab is inside the 390 px window").toBeLessThanOrEqual(390);
  });

  test(`AC1: one click on End lab ends the lab and shows My labs (${lang})`, async ({ page }) => {
    await openLab(page, lang, 1280, 800);
    await end(page).click();
    await expect(page).toHaveURL(/\/labs$/);
  });

  test(`AC2: lab complete is a full-pane message: wide, no terminal shown, Back to My labs works (${lang})`, async ({ page }) => {
    await openLab(page, lang, 1280, 800);
    const check = page.getByRole("button", { name: d["lab.check"], exact: true });
    const next = page.getByRole("button", { name: d["lab.next"], exact: true });
    await check.click();
    await next.click();
    await expect(page.getByRole("heading", { name: lang === "en" ? "Make a folder" : "יצירת תיקייה" })).toBeVisible();
    await check.click();
    await next.click();
    const msg = page.getByText(d["lab.complete"]);
    await expect(msg).toBeVisible();
    const m = await box(msg);
    expect(m.width, `the message is ${m.width} px wide in a 1280 px window`).toBeGreaterThanOrEqual(1280 * 0.6);
    await expect(frame(page), "the terminal is not shown next to the message").toBeHidden();
    await expect(frame(page)).toHaveCount(1); // hidden, not unmounted
    await expect(end(page)).toBeVisible();
    const back = page.getByRole("link", { name: d["lab.to_labs"] });
    await expect(back).toBeVisible();
    await back.click();
    await expect(page).toHaveURL(/\/labs$/);
  });

  test(`AC2: session ended replaces the panes with a message and Back to My labs (${lang})`, async ({ page }) => {
    await openLab(page, lang, 1280, 800, true);
    await expect(page.getByText(d["lab.ended"])).toBeVisible();
    await expect(page.getByTestId("task-panel")).toHaveCount(0);
    await expect(frame(page)).toHaveCount(0);
    const back = page.getByRole("link", { name: d["lab.to_labs"] });
    await expect(back).toBeVisible();
    await back.click();
    await expect(page).toHaveURL(/\/labs$/);
  });
}
