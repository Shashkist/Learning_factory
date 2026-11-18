import { expect, test, type Page } from "@playwright/test";

import { fakeAdminBackend } from "./admin-backend";

const TEXT = {
  en: { dir: "ltr", title: "Cohorts", name: "Cohort name", password: "Class password", access: "Access until", create: "Create cohort" },
  he: { dir: "rtl", title: "קבוצות", name: "שם הקבוצה", password: "סיסמת הכיתה", access: "גישה עד", create: "יצירת קבוצה" },
} as const;

async function open(page: Page, path: string, lang: "en" | "he" = "en") {
  const backend = await fakeAdminBackend(page);
  await page.addInitScript((l) => window.localStorage.setItem("lf-lang", l), lang);
  await page.goto(path);
  await expect(page.getByText("Signed in as admin")).toBeVisible();
  return backend;
}

for (const lang of ["en", "he"] as const) {
  test(`cohort create and paste import in ${lang}`, async ({ page }) => {
    const x = TEXT[lang];
    const { log, unmocked, bodies } = await open(page, "/admin", lang);
    await expect(page.locator("html")).toHaveAttribute("dir", x.dir);
    await expect(page.getByRole("heading", { name: x.title })).toBeVisible();
    await expect(page.getByTestId("cohort-1")).toContainText("Alpha");

    await page.getByLabel(x.name, { exact: true }).fill("Gamma");
    await page.getByLabel(x.password, { exact: true }).fill("class-password");
    await page.getByLabel(x.access, { exact: true }).fill("2027-06-30");
    await page.getByRole("button", { name: x.create, exact: true }).click();
    await expect(page.getByTestId("cohort-3")).toContainText("Gamma");
    await expect(page.getByTestId("cohort-3")).toContainText("2027-06-30");
    expect(bodies["POST /api/admin/cohorts"]).toEqual([
      { name: "Gamma", password: "class-password", access_until: "2027-06-30" },
    ]);

    // One bad ID (line 2): reported; the two good lines are still imported.
    await page.locator("select").selectOption("3");
    await page.locator("textarea").fill("000000018, Avi\n12, Bad\n000000026, Lior");
    await page.locator("form:has(textarea) button[type=submit]").click();
    await expect(page.getByTestId("import-summary")).toContainText("2");
    await expect(page.getByTestId("import-row-2")).not.toContainText(/Added|נוסף/);
    await expect(page.getByTestId("import-row-1")).toContainText(/Added|נוסף/);
    await expect(page.getByTestId("import-row-3")).toContainText(/Added|נוסף/);
    await expect(page.getByTestId("cohort-3")).toContainText("2");
    expect(bodies["POST /api/admin/cohorts/3/import"]).toHaveLength(1);

    expect(unmocked).toEqual([]);
    expect(log).toContain("POST /api/admin/cohorts/3/import");
  });
}

test("import row error names the invalid ID line", async ({ page }) => {
  const { unmocked } = await open(page, "/admin");
  await page.locator("textarea").fill("12, Bad");
  await page.locator("form:has(textarea) button[type=submit]").click();
  await expect(page.getByTestId("import-row-1")).toContainText("The ID is not valid.");
  await expect(page.getByTestId("import-summary")).toContainText("0");
  expect(unmocked).toEqual([]);
});

test("class table: cohort filter and a flagged row", async ({ page }) => {
  const { unmocked, log } = await open(page, "/admin");
  await page.getByRole("link", { name: "Class table" }).click();
  await expect(page).toHaveURL(/\/admin\/class$/);
  await expect(page.getByTestId("class-table")).toContainText("Dana");
  await expect(page.getByTestId("class-table")).toContainText("Noa");
  await expect(page.getByRole("img", { name: "Finished unusually fast" })).toHaveCount(1);
  await expect(page.getByTestId("class-cell-0-linux-basics")).toContainText("✓");

  await page.locator("select").selectOption({ label: "Beta" });
  await expect(page.getByTestId("class-table")).not.toContainText("Dana");
  await expect(page.getByTestId("class-table")).toContainText("Noa");
  await page.locator("select").selectOption({ label: "All cohorts" });
  await expect(page.getByTestId("class-table")).toContainText("Noa");

  await page.getByLabel("Flagged only").check();
  await expect(page.getByTestId("class-table")).not.toContainText("Noa");
  await expect(page.getByTestId("class-row-0")).toContainText("Dana");
  expect(log).toEqual(expect.arrayContaining(["GET /api/admin/class"]));
  expect(unmocked).toEqual([]);
});

test("student page: extend access, disable, note, dismiss a flag", async ({ page }) => {
  const { unmocked, bodies } = await open(page, "/admin/class");
  await page.getByRole("link", { name: "Dana" }).click();
  await expect(page).toHaveURL(/\/admin\/students\/k-dana$/);
  const sp = page.getByTestId("student-page");
  await expect(sp.getByRole("heading", { name: "Dana" })).toBeVisible();
  await expect(page.getByTestId("student-flag-f1")).toContainText("task t1 in 3 s");

  const patch = "PATCH /api/admin/students/k-dana";
  await sp.getByLabel("Personal access until").fill("2027-03-01");
  await sp.getByRole("button", { name: "Set access date" }).click();
  await expect.poll(() => bodies[patch]?.length).toBe(1);
  expect(bodies[patch][0]).toEqual({ access_until: "2027-03-01" });

  await sp.getByRole("button", { name: "Disable student" }).click();
  await expect(sp.getByText("This student is disabled and cannot sign in.")).toBeVisible();
  await expect(sp.getByRole("button", { name: "Enable student" })).toBeVisible();
  expect(bodies[patch][1]).toEqual({ disabled: true });

  await sp.getByLabel("Teacher note").fill("Needs a quiet seat");
  await sp.getByRole("button", { name: "Save note" }).click();
  await expect.poll(() => bodies[patch]?.length).toBe(3);
  expect(bodies[patch][2]).toEqual({ note: "Needs a quiet seat" });
  await expect(sp.getByLabel("Teacher note")).toHaveValue("Needs a quiet seat");

  await sp.getByRole("button", { name: "Dismiss flag" }).click();
  await expect(page.getByTestId("student-flag-f1")).toContainText("Dismissed");
  await expect(sp.getByRole("button", { name: "Dismiss flag" })).toHaveCount(0);

  // Back on the class table the dismissed flag is gone and the data survived the round trip.
  await sp.getByRole("link", { name: "Back to the class table" }).click();
  await expect(page.getByRole("img", { name: "Finished unusually fast" })).toHaveCount(0);
  expect(unmocked).toEqual([]);
});
