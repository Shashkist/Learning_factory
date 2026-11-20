import { expect, test } from "@playwright/test";

// Issue #167, criterion 1: log in as the synthetic smoke student, My labs shows the lab, Start,
// the lab screen shows the terminal iframe, End lab. The pytest exam that runs this file then
// checks that the lab-<uuid> container is gone. English only, one browser, one flow.
// The file name has no ".spec." / ".test.": vitest must not pick it up.
declare const process: { env: Record<string, string | undefined> };

const need = (name: string): string => {
  const v = process.env[name];
  if (!v) throw new Error(`${name} is not set (the pytest exam provides it)`);
  return v;
};

test("smoke student: login, My labs, Start, terminal iframe, End lab", async ({ page }) => {
  const id = need("LF_E2E_ID");
  const password = need("LF_E2E_PASSWORD");
  const labId = need("LF_E2E_LAB_ID");
  const labTitle = need("LF_E2E_LAB_TITLE");
  const wait = { timeout: 120_000 };

  await page.addInitScript(() => window.localStorage.setItem("lf-lang", "en"));
  await page.goto("/");
  await page.getByLabel("ID number").fill(id);
  await page.getByLabel("Class password").fill(password);
  await page.getByRole("button", { name: "Sign in", exact: true }).click();

  await expect(page.getByRole("heading", { name: "My labs" })).toBeVisible();
  const card = page.getByTestId(`lab-card-${labId}`);
  await expect(card.getByRole("heading", { name: labTitle })).toBeVisible();
  await card.getByRole("button", { name: "Start", exact: true }).click();

  await expect(page).toHaveURL(new RegExp(`/labs/${labId}$`), wait);
  const terminal = page.locator('iframe[title="Lab terminal"]');
  await expect(terminal).toBeVisible(wait);
  const src = (await terminal.getAttribute("src")) ?? "";
  const m = /^\/s\/([0-9a-f-]{36})\/term\/$/.exec(src);
  expect(m, `terminal iframe src was ${src}`).not.toBeNull();
  // The pytest exam reads the session id from the JSON report (no Node modules here: the
  // app's tsconfig has no Node types, and `tsc --noEmit` checks this file too).
  test.info().annotations.push({ type: "lf-session", description: m![1] });

  // The proxy reaches ttyd: the terminal itself renders inside the iframe.
  await expect(
    page.frameLocator('iframe[title="Lab terminal"]').locator(".xterm"),
  ).toBeVisible(wait);

  await page.getByRole("button", { name: "End lab" }).click();
  await expect(page).toHaveURL(/\/labs$/, wait);
  await expect(page.getByRole("heading", { name: "My labs" })).toBeVisible();
});
