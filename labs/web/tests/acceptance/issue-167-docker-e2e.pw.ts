import { expect, test, type Page } from "@playwright/test";

// Issue #167, criterion 1: log in as the synthetic smoke student, My labs shows the lab, Start,
// the lab screen shows the terminal iframe, End lab. The pytest exam that runs this file then
// checks that the lab-<uuid> container is gone. English only, one browser, one flow.
// The file name has no ".spec." / ".test.": vitest must not pick it up.
//
// Issue #319 (with the #349 scope addition, D112): the flow also TYPES a command into the terminal and
// requires proof that it ran in the lab container, i.e. that keystrokes went browser -> manager
// WebSocket relay -> ttyd -> shell. The `.xterm` check below only proves the HTTP path (ttyd draws
// .xterm before it connects). The proof is a SIDE EFFECT read back from outside the browser:
// the typed command is `echo <token> > ~/<file>` and the exam reads the file with
// `docker exec lab-<session> cat /home/student/<file>`. It does not depend on how ttyd renders
// (DOM, canvas or WebGL) nor on any ttyd global such as window.term, and the file can only hold the
// token if the shell ran the command (the token is also in the typed text, so the echoed screen
// proves nothing; the file does). The same proof runs again in a 390 px viewport after tapping the
// Terminal tab. Everything happens in ONE test and one lab session: the pytest harness expects
// exactly one passing test and one lf-session annotation.
declare const process: {
  env: Record<string, string | undefined>;
  getBuiltinModule?: (id: string) => unknown;
};

type ExecFile = (
  file: string,
  args: string[],
  opts: { timeout: number },
  cb: (err: unknown, stdout: string) => void,
) => void;
// No Node types in the app's tsconfig: a computed specifier / getBuiltinModule keeps tsc quiet.
const execFile = (
  (process.getBuiltinModule?.("node:" + "child_process") ?? {}) as { execFile?: ExecFile }
).execFile;

/** Contents of a file in the student's home inside the lab container; "" while it is not there. */
const readInLab = (session: string, file: string): Promise<string> =>
  new Promise((resolve) => {
    if (!execFile) throw new Error("Node 22 process.getBuiltinModule is needed to run docker exec");
    execFile(
      "docker",
      ["exec", `lab-${session}`, "cat", `/home/student/${file}`],
      { timeout: 10_000 },
      (err, stdout) => resolve(err ? "" : String(stdout).trim()),
    );
  });

/** Type `echo <token> > ~/<file>` into the lab terminal and wait (bounded) until the shell wrote it. */
async function typeAndProve(page: Page, session: string, label: string) {
  const token = `lf-ok-${Math.random().toString(36).slice(2, 10)}`;
  const file = `${token}.txt`;
  const term = page.frameLocator('iframe[title="Lab terminal"]').locator(".xterm");
  let seen = "";
  // A key typed before ttyd's WebSocket is open is dropped, so type again (a fresh, idempotent
  // write) a few times; each attempt waits 8 s. A broken relay fails after ~32 s, not at the 240 s test limit.
  for (let attempt = 0; attempt < 4 && seen !== token; attempt++) {
    await term.click();
    await page.keyboard.type(`echo ${token} > ~/${file}`, { delay: 15 });
    await page.keyboard.press("Enter");
    const deadline = Date.now() + 8_000;
    while (seen !== token && Date.now() < deadline) {
      seen = await readInLab(session, file);
      if (seen !== token) await page.waitForTimeout(500);
    }
  }
  expect(seen, `${label}: the typed command did not run in lab-${session} (WebSocket relay broken?)`).toBe(token);
}

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

  // Desktop width: the typed command goes through the relay and runs in the lab container.
  await typeAndProve(page, m![1], "desktop");

  // Phone width (#349 / D112): reload the lab screen at 390 px. The terminal pane starts hidden, and
  // focus must stay in the Tasks pane after load (ttyd must not steal it). Then tap Terminal and type.
  await page.setViewportSize({ width: 390, height: 844 });
  await page.reload();
  await expect(page).toHaveURL(new RegExp(`/labs/${labId}$`), wait);
  const phoneTerminal = page.locator('iframe[title="Lab terminal"]');
  await expect(phoneTerminal).toBeAttached(wait);
  const tasksTab = page.getByRole("tab", { name: "Tasks", exact: true });
  const terminalTab = page.getByRole("tab", { name: "Terminal", exact: true });
  await expect(tasksTab).toBeVisible(wait);
  await expect(tasksTab).toHaveAttribute("aria-selected", "true");
  await expect(phoneTerminal, "the terminal pane is hidden on the Tasks tab").toBeHidden();
  // ttyd focuses its textarea once it has loaded; give it time to try, then check where focus is.
  await page.waitForTimeout(3_000);
  const focus = await page.evaluate(() => {
    const a = document.activeElement;
    return {
      iframe: a instanceof HTMLIFrameElement,
      inTasks: !!a?.closest("#lab-panel-tasks, [data-testid=task-panel]"),
      desc: a ? `${a.tagName}#${a.id}` : "none",
    };
  });
  expect(focus.iframe, `focus went into the terminal on load (${focus.desc})`).toBe(false);
  expect(focus.inTasks, `focus is not in the Tasks pane after load (${focus.desc})`).toBe(true);
  await terminalTab.click();
  await expect(phoneTerminal).toBeVisible(wait);
  await expect(
    page.frameLocator('iframe[title="Lab terminal"]').locator(".xterm"),
  ).toBeVisible(wait);
  await typeAndProve(page, m![1], "390 px");

  await page.getByRole("button", { name: "End lab" }).click();
  await expect(page).toHaveURL(/\/labs$/, wait);
  await expect(page.getByRole("heading", { name: "My labs" })).toBeVisible();
});
