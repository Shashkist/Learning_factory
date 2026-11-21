import { expect, test } from "@playwright/test";

import { fakeBackend, SESSION_ID } from "./fake-backend";

const TEXT = {
  en: {
    dir: "ltr",
    myLabs: "My labs",
    lab: "Linux basics",
    start: "Start",
    task1: "List the files",
    task2: "Make a folder",
    check: "Check",
    next: "Next",
    end: "End lab",
  },
  he: {
    dir: "rtl",
    myLabs: "המעבדות שלי",
    lab: "יסודות לינוקס",
    start: "התחלה",
    task1: "הצגת הקבצים",
    task2: "יצירת תיקייה",
    check: "בדיקה",
    next: "הבא",
    end: "סיום המעבדה",
  },
} as const;

for (const lang of ["en", "he"] as const) {
  test(`main flow in ${lang}: login, My labs, start, task, check, next, end`, async ({ page }) => {
    const x = TEXT[lang];
    const { log, unmocked } = await fakeBackend(page);
    await page.addInitScript((l) => window.localStorage.setItem("lf-lang", l), lang);

    await page.goto("/");
    await expect(page.locator("html")).toHaveAttribute("dir", x.dir);
    await expect(page.getByTestId("app-root")).toHaveAttribute("dir", x.dir);
    await page.locator("input:not([type=password])").fill("000000018"); // synthetic ID
    await page.locator("input[type=password]").fill("class-password");
    await page.locator("form button[type=submit]").click();

    await expect(page.getByRole("heading", { name: x.myLabs })).toBeVisible();
    await expect(page.getByRole("heading", { name: x.lab })).toBeVisible();
    await page.getByRole("button", { name: x.start, exact: true }).click();

    await expect(page).toHaveURL(/\/labs\/linux-basics$/);
    const panel = page.getByTestId("task-panel");
    await expect(panel).toHaveAttribute("dir", x.dir);
    await expect(panel.getByRole("heading", { name: x.task1 })).toBeVisible();
    // The terminal area stays left-to-right in both languages.
    await expect(page.locator("iframe").locator("xpath=..")).toHaveAttribute("dir", "ltr");
    await expect(page.frameLocator("iframe").locator("#term")).toContainText("student@lab");
    await expect(page.locator("iframe")).toHaveAttribute("src", `/s/${SESSION_ID}/term/`);

    await panel.getByRole("button", { name: x.check }).click();
    await panel.getByRole("button", { name: x.next, exact: true }).click();
    await expect(panel.getByRole("heading", { name: x.task2 })).toBeVisible();

    // End lab lives in the lab screen top bar since #349 (not in the task panel).
    await expect(panel.getByRole("button", { name: x.end })).toHaveCount(0);
    await page.getByRole("button", { name: x.end }).click();
    await expect(page).toHaveURL(/\/labs$/);
    await expect(page.getByRole("heading", { name: x.myLabs })).toBeVisible();
    // Back on My labs: the ended run shows its progress and offers Start again (catalog.py).
    await expect(page.getByText("1/2")).toBeVisible();
    await expect(page.getByRole("button", { name: x.start, exact: true })).toBeVisible();

    expect(unmocked).toEqual([]);
    expect(log).toEqual(
      expect.arrayContaining([
        "POST /api/auth/login",
        "POST /api/sessions",
        `POST /api/sessions/${SESSION_ID}/tasks/t1/check`,
        `POST /api/sessions/${SESSION_ID}/tasks/current/next`,
        `POST /api/sessions/${SESSION_ID}/end`,
      ]),
    );
  });
}

