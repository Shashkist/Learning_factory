/**
 * Playwright exam for #333 (the lab screen, D105): layout criteria 1, 2, 5 and the mirroring in he, and
 * the screenshots of criterion 7. The vitest exam issue-333-lab-screen.test.tsx has the DOM contract.
 * Runs in the design config (tests/acceptance/playwright.design.config.ts), from labs/web.
 *
 * CONTRACT / structural hooks the implementer must provide (all semantic, no new test ids)
 *   - the page's <main> (role "main") is full width on the lab screen: at least 95 % of the viewport
 *     (the shell's centred 72rem column must not apply there). The terminal iframe (title = lab.terminal)
 *     is at least 55 % of main's width and of the viewport, and ends within 48 px of the window's bottom.
 *   - the existing data-testid="task-panel" element is the tasks pane (its box is the pane).
 *   - at 1280 x 800 nothing scrolls the page: the iframe fills the height below the header and the top
 *     bar (at least 75 % of the viewport height), document.scrollHeight <= innerHeight.
 *   - the pane switch tablist (tabs named by i18n keys lab.pane.tasks / lab.pane.terminal) is visible at
 *     390 px and NOT visible at 1280 px. At 390 px the Tasks pane is shown first (iframe hidden), the
 *     Terminal tab shows the iframe full width and hides the tasks pane; panes are hidden with CSS
 *     (display:none), never unmounted, so the iframe is the same element and is never reloaded.
 *   - the role=timer element has font-variant-numeric including tabular-nums.
 *   - in he the tasks pane is on the right and the terminal on the left (en: the other way round).
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
type Lang = "en" | "he";

const HINT = "demo is still missing";

/** Sign in, start the lab, open the lab screen. `mode` drives the answer to Check. */
async function openLab(page: Page, lang: Lang, width: number, height: number) {
  await page.addInitScript((l) => window.localStorage.setItem("lf-lang", l), lang);
  await page.setViewportSize({ width, height });
  const { unmocked } = await fakeBackend(page);
  const state = { fail: false };
  // Registered after the fake, so these win (Playwright tries the last route first).
  await page.route("**/api/sessions/s-e2e/tasks/*/check", (route) =>
    json(
      route,
      state.fail
        ? { passed: false, attempts: 1, reason: null, checklist: [{ n: 1, passed: false, hint: HINT }] }
        : { passed: true, attempts: 1, reason: null, checklist: [{ n: 1, passed: true, hint: null }] },
    ),
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
  await page.goto("/labs/linux-basics");
  await expect(page.getByTestId("task-panel")).toBeVisible();
  await page.evaluate(() => document.fonts.ready);
  seen = unmocked;
  return { state, unmocked };
}

// #349: every test also asserts the fake backend saw no request it does not know (the e2e convention).
let seen: string[] | null = null;
test.afterEach(() => {
  expect(seen, "the fake backend saw an unmocked request").toEqual([]);
  seen = null;
});

const frameOf = (page: Page, lang: Lang) => page.getByTitle(COPY[lang]["lab.terminal"]);
const paneTab = (page: Page, lang: Lang, which: "tasks" | "terminal") => {
  const name = COPY[lang][`lab.pane.${which}`];
  expect(name, `${lang}.json has lab.pane.${which}`).toBeTruthy();
  return page.getByRole("tab", { name, exact: true });
};
const box = async (loc: ReturnType<Page["locator"]>) => {
  const b = await loc.boundingBox();
  expect(b, "element has a box").not.toBeNull();
  return b!;
};

for (const lang of ["en", "he"] as const) {
  // ------------------------------------------------------------ AC1
  test(`AC1: at 1280 px the terminal is at least 55 % of the content width and fills the height, no page scroll (${lang})`, async ({ page }) => {
    await openLab(page, lang, 1280, 800);
    const iframe = frameOf(page, lang);
    await expect(iframe).toBeVisible();
    const main = await box(page.getByRole("main"));
    expect(main.width, `main is ${main.width} px wide: the lab screen is full width`).toBeGreaterThanOrEqual(1280 * 0.95);
    const f = await box(iframe);
    expect(f.width / main.width, `iframe ${f.width} of main ${main.width}`).toBeGreaterThanOrEqual(0.55);
    expect(f.width, `iframe is ${f.width} px wide`).toBeGreaterThanOrEqual(1280 * 0.55);
    expect(f.y + f.height, "the iframe reaches the bottom of the window").toBeGreaterThanOrEqual(800 - 48);
    expect(f.height, `iframe is ${f.height} px high`).toBeGreaterThanOrEqual(800 * 0.75);
    expect(f.y + f.height).toBeLessThanOrEqual(800);
    const m = await page.evaluate(() => ({ sh: document.documentElement.scrollHeight, ih: window.innerHeight }));
    expect(m.sh, `page scrollHeight ${m.sh} must not exceed ${m.ih}`).toBeLessThanOrEqual(m.ih);
    // Both panes and no pane switch on a wide screen.
    await expect(page.getByTestId("task-panel")).toBeVisible();
    await expect(paneTab(page, lang, "tasks")).toBeHidden();
    await expect(paneTab(page, lang, "terminal")).toBeHidden();
  });

  // ------------------------------------------------------------ mirroring
  test(`AC1: the tasks pane and the terminal swap sides in he (${lang})`, async ({ page }) => {
    await openLab(page, lang, 1280, 800);
    const t = await box(page.getByTestId("task-panel"));
    const f = await box(frameOf(page, lang));
    expect(t.x + t.width <= f.x + 1 || f.x + f.width <= t.x + 1, "panes do not overlap").toBe(true);
    if (lang === "en") expect(t.x, "en: tasks pane left of the terminal").toBeLessThan(f.x);
    else expect(t.x, "he: tasks pane right of the terminal").toBeGreaterThan(f.x);
  });

  // ------------------------------------------------------------ AC2
  test(`AC2: at 390 px the panes are stacked behind Tasks / Terminal tabs and the same iframe survives switching (${lang})`, async ({ page }) => {
    const requests: string[] = [];
    page.on("request", (r) => {
      if (new URL(r.url()).pathname === "/s/s-e2e/term/") requests.push(r.url());
    });
    await openLab(page, lang, 390, 844);
    const tasks = paneTab(page, lang, "tasks");
    const terminal = paneTab(page, lang, "terminal");
    await expect(tasks.locator("xpath=ancestor::*[@role='tablist'][1]")).toBeVisible();
    await expect(tasks).toHaveAttribute("aria-selected", "true");
    const iframe = frameOf(page, lang);
    const handle = await iframe.elementHandle();
    await handle!.evaluate((el) => ((el as unknown as { __lfSame: string }).__lfSame = "kept"));

    // Tasks first: the task is readable, the terminal is behind the other tab.
    await expect(page.getByRole("heading", { name: COPY[lang] === COPY.en ? "List the files" : "הצגת הקבצים" })).toBeVisible();
    await expect(iframe).toBeHidden();

    await terminal.click();
    await expect(terminal).toHaveAttribute("aria-selected", "true");
    await expect(iframe).toBeVisible();
    const f = await box(iframe);
    expect(f.width, "stacked: the terminal takes the full width").toBeGreaterThanOrEqual(390 * 0.9);
    await expect(page.getByTestId("task-panel")).toBeHidden();
    const m = await page.evaluate(() => ({ sh: document.documentElement.scrollHeight, ih: window.innerHeight, sw: document.documentElement.scrollWidth, iw: window.innerWidth }));
    expect(m.sh, "no page scroll on the terminal pane").toBeLessThanOrEqual(m.ih);
    expect(m.sw).toBeLessThanOrEqual(m.iw);

    await tasks.click();
    await expect(page.getByTestId("task-panel")).toBeVisible();
    await expect(iframe).toBeHidden();
    const t = await box(page.getByTestId("task-panel"));
    expect(t.width, "stacked: the tasks pane takes the full width").toBeGreaterThanOrEqual(390 * 0.9);
    await terminal.click();
    await expect(iframe).toBeVisible();

    const same = await page.evaluate(() => {
      const el = document.querySelector("iframe") as unknown as { __lfSame?: string; isConnected: boolean };
      return { tag: el.__lfSame, connected: el.isConnected, count: document.querySelectorAll("iframe").length };
    });
    expect(same, "the iframe element is the one tagged before switching").toEqual({ tag: "kept", connected: true, count: 1 });
    expect(requests, "the terminal page was requested exactly once (never reloaded)").toHaveLength(1);
  });

  // ------------------------------------------------------------ AC5
  test(`AC5: the countdown uses tabular numbers and keeps its width as it ticks (${lang})`, async ({ page }) => {
    await openLab(page, lang, 1280, 800);
    const timer = page.getByRole("timer");
    await expect(timer).toBeVisible();
    const fvn = await timer.evaluate((el) => getComputedStyle(el).fontVariantNumeric);
    expect(fvn, "font-variant-numeric").toContain("tabular-nums");
    const w1 = (await box(timer)).width;
    const t1 = await timer.textContent();
    await expect.poll(async () => timer.textContent(), { timeout: 5000 }).not.toBe(t1);
    expect((await box(timer)).width).toBeCloseTo(w1, 1);
  });
}

// ---------------------------------------------------------------- AC7: screenshots for the PR
type Shot = "task-open" | "check-failed" | "check-passed" | "lab-complete";
const SHOT_STATES: Shot[] = ["task-open", "check-failed", "check-passed", "lab-complete"];

for (const state of SHOT_STATES)
  for (const lang of ["en", "he"] as const)
    for (const scheme of ["light", "dark"] as const)
      for (const width of [1280, 390]) {
        const file = `${SHOTS}/333-lab-${state}-${lang}-${scheme}-${width}.png`;
        test(`AC7: screenshot of the lab screen (${state}), ${lang}, ${scheme}, ${width} px is saved to ${file}`, async ({ page }) => {
          fs.mkdirSync(SHOTS, { recursive: true });
          await page.emulateMedia({ colorScheme: scheme });
          const { state: mode } = await openLab(page, lang, width, width === 1280 ? 800 : 844);
          const d = COPY[lang];
          const check = page.getByRole("button", { name: d["lab.check"], exact: true });
          const next = page.getByRole("button", { name: d["lab.next"], exact: true });
          if (state === "check-failed") {
            mode.fail = true;
            await check.click();
            await expect(page.getByText(HINT)).toBeVisible();
            await expect(next).toHaveCount(0);
          } else if (state === "check-passed") {
            await check.click();
            await expect(next).toBeVisible();
          } else if (state === "lab-complete") {
            await check.click();
            await next.click();
            await expect(page.getByRole("heading", { name: lang === "en" ? "Make a folder" : "יצירת תיקייה" })).toBeVisible();
            await check.click();
            await next.click();
            await expect(page.getByText(d["lab.complete"])).toBeVisible();
          }
          await page.evaluate(() => document.fonts.ready);
          const bg = await page.evaluate(() => getComputedStyle(document.documentElement).getPropertyValue("--color-bg").trim());
          expect(bg, "--color-bg is defined by the token file").not.toEqual("");
          await page.screenshot({ path: file, fullPage: true });
          expect(fs.existsSync(file)).toBe(true);
          expect(fs.statSync(file).size).toBeGreaterThan(1000);
        });
      }
