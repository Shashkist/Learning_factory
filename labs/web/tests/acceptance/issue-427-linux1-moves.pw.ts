import { expect, test, type Page } from "@playwright/test";

// Issue #427: in the real Linux 1 lab, moving from one task to the next must keep the lab screen
// (same terminal iframe, no navigation, no error) and a failing setup must show its own error and
// keep the task. The pytest exam labs/manager/tests/acceptance/test_issue_427_linux1_moves_docker.py
// starts the real manager and runs this file. English only. The file name has no ".spec." / ".test.":
// vitest must not pick it up.
//
// Next is only on screen once the task is passed (LabScreen.tsx): a quiz is answered correctly
// first, a hands-on task is left with Skip (same endpoint family, `.../tasks/current/<action>`).
// Every failure message names the task id and carries what the manager answered, so the CI log
// is the diagnosis.
declare const process: { env: Record<string, string | undefined> };

type TaskInfo = { id: string; kind: "quiz" | "hands-on"; title: string; correct_text: string | null };
type Reply = { status: number; body: string };

const need = (name: string): string => {
  const v = process.env[name];
  if (!v) throw new Error(`${name} is not set (the pytest exam provides it)`);
  return v;
};
const wait = { timeout: 120_000 };
const MOVE = /\/api\/sessions\/[0-9a-f-]{36}\/tasks\/current\/(next|skip)$/;

async function startLab(page: Page, labId: string, labTitle: string): Promise<string> {
  await page.addInitScript(() => window.localStorage.setItem("lf-lang", "en"));
  await page.goto("/");
  await page.getByLabel("ID number").fill(need("LF_E2E_ID"));
  await page.getByLabel("Class password").fill(need("LF_E2E_PASSWORD"));
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(page.getByRole("heading", { name: "My labs" })).toBeVisible();
  const card = page.getByTestId(`lab-card-${labId}`);
  await expect(card.getByRole("heading", { name: labTitle })).toBeVisible();
  await card.getByRole("button", { name: "Start", exact: true }).click();
  await expect(page).toHaveURL(new RegExp(`/labs/${labId}$`), wait);
  const terminal = page.locator('iframe[title="Lab terminal"]');
  await expect(terminal).toBeVisible(wait);
  const m = /^\/s\/([0-9a-f-]{36})\/term\/$/.exec((await terminal.getAttribute("src")) ?? "");
  expect(m, "terminal iframe src").not.toBeNull();
  test.info().annotations.push({ type: "lf-session", description: m![1] });
  await expect(page.frameLocator('iframe[title="Lab terminal"]').locator(".xterm")).toBeVisible(wait);
  return m![1];
}

// Tag the screen: the page, the iframe element and the iframe's own document. A reload or a
// remount of any of them loses its tag. Returns the list that collects later navigations.
async function tag(page: Page, token: string): Promise<string[]> {
  const navigations: string[] = [];
  await page.evaluate((t) => {
    (window as unknown as Record<string, string>).__lf427 = t;
    const f = document.querySelector<HTMLIFrameElement>('iframe[title="Lab terminal"]')!;
    f.dataset.lf427 = t;
    (f.contentWindow as unknown as Record<string, string>).__lf427 = t;
  }, token);
  page.on("framenavigated", (fr) => navigations.push(fr.url()));
  return navigations;
}

async function steady(page: Page, token: string, navigations: string[]): Promise<string | null> {
  const seen = await page.evaluate((t) => {
    const w = window as unknown as Record<string, string | undefined>;
    const f = document.querySelector<HTMLIFrameElement>('iframe[title="Lab terminal"]');
    const inner = f?.contentWindow as unknown as Record<string, string | undefined> | null;
    return {
      page: w.__lf427 === t,
      iframe: !!f && f.dataset.lf427 === t,
      document: !!inner && inner.__lf427 === t,
    };
  }, token);
  const lost = Object.entries(seen)
    .filter(([, ok]) => !ok)
    .map(([k]) => k);
  if (lost.length) return `lost its tag (reloaded or replaced): ${lost.join(", ")}`;
  if (navigations.length) return `navigated: ${navigations.join(", ")}`;
  return null;
}

// One move: answer a quiz and press Next, or press Skip on a hands-on task. Returns what the
// manager answered for the POST.
async function press(page: Page, from: TaskInfo): Promise<{ action: string; reply: Reply }> {
  const panel = page.getByTestId("task-panel");
  let action = "skip";
  if (from.kind === "quiz") {
    await panel.getByRole("radio", { name: from.correct_text!, exact: true }).check();
    await panel.getByRole("button", { name: "Answer", exact: true }).click();
    action = "next";
  }
  const button = panel.getByRole("button", { name: action === "next" ? "Next" : "Skip", exact: true });
  await expect(button, `${from.id}: the ${action} button`).toBeVisible({ timeout: 30_000 });
  const answered = page.waitForResponse((r) => r.request().method() === "POST" && MOVE.test(r.url()), {
    timeout: 90_000,
  });
  await button.click();
  const r = await answered;
  return { action, reply: { status: r.status(), body: (await r.text()).slice(0, 600) } };
}

test("criteria 1-3: every move of Linux 1 keeps the lab screen and shows the next task, no error", async ({
  page,
}) => {
  const tasks = JSON.parse(need("LF_E2E_TASKS")) as TaskInfo[];
  const sid = await startLab(page, "linux-1", need("LF_E2E_LAB_TITLE"));
  const token = `lf427-${Math.random().toString(36).slice(2)}`;
  const calls: string[] = [];
  page.on("response", (r) => {
    if (r.request().method() === "POST" && r.url().includes("/tasks/")) {
      calls.push(`POST ${new URL(r.url()).pathname.replace(sid, "<sid>")} -> ${r.status()}`);
    }
  });
  const panel = page.getByTestId("task-panel");
  await expect(panel.getByRole("heading", { level: 2 })).toHaveText(tasks[0].title, wait);
  const navigations = await tag(page, token);

  for (let i = 0; i + 1 < tasks.length; i++) {
    const from = tasks[i];
    const to = tasks[i + 1];
    const label = `Linux 1, move from task ${from.id} (${i + 1}) to task ${to.id} (${i + 2})`;
    const problems: string[] = [];
    let answer = "(no answer captured)";
    try {
      const { action, reply } = await press(page, from);
      answer = `POST /api/sessions/<sid>/tasks/current/${action} -> HTTP ${reply.status} ${reply.body}`;
      console.log(`LF427 ${label}: ${answer}`);
      if (reply.status !== 200) problems.push("the manager did not answer 200");
    } catch (e) {
      problems.push(`pressing the button failed: ${String(e).slice(0, 400)}`);
    }
    const alerts = await page.getByRole("alert").allTextContents();
    if (alerts.length) problems.push(`an error is shown: ${JSON.stringify(alerts)}`);
    try {
      await expect(panel.getByRole("heading", { level: 2 })).toHaveText(to.title, { timeout: 15_000 });
      await expect(page.getByText(`Question ${i + 2} of ${tasks.length}`)).toBeVisible({ timeout: 5_000 });
    } catch {
      problems.push(`task ${to.id}'s title ("${to.title}") is not on screen`);
    }
    const lost = await steady(page, token, navigations).catch(
      (e) => `the terminal iframe is gone: ${String(e).slice(0, 200)}`,
    );
    if (lost) problems.push(`the lab screen ${lost}`);
    if (problems.length) {
      const probe: string[] = [];
      for (const t of tasks.filter((x) => x.kind === "hands-on")) {
        const r = await page.request.post(`/api/sessions/${sid}/tasks/${t.id}/show`).catch(() => null);
        probe.push(`show ${t.id}: ${r ? `HTTP ${r.status()} ${(await r.text()).slice(0, 200)}` : "no answer"}`);
      }
      throw new Error(
        `${label} FAILED (task id that failed: ${from.id} -> ${to.id})\n` +
          problems.map((p) => `  - ${p}`).join("\n") +
          `\nmanager answered: ${answer}\nall task POSTs so far:\n  ${calls.join("\n  ")}\n` +
          `direct show of every hands-on task (diagnosis only):\n  ${probe.join("\n  ")}`,
      );
    }
  }
  await page.getByRole("button", { name: "End lab" }).click();
  await expect(page).toHaveURL(/\/labs$/, wait);
});

test("criterion 4: a failing setup shows the setup error, keeps the task and allows a retry, no reload", async ({
  page,
}) => {
  const tasks = JSON.parse(need("LF_E2E_TASKS")) as TaskInfo[];
  const errorText = need("LF_E2E_SETUP_ERROR");
  const sid = await startLab(page, need("LF_E2E_BROKEN_LAB_ID"), need("LF_E2E_BROKEN_LAB_TITLE"));
  const token = `lf427-${Math.random().toString(36).slice(2)}`;
  const panel = page.getByTestId("task-panel");
  await expect(panel.getByRole("heading", { level: 2 })).toHaveText(tasks[0].title, wait);
  const navigations = await tag(page, token);

  // Task 2's setup.sh in this copy of the lab exits 1.
  for (const attempt of [1, 2]) {
    let reply: Reply;
    if (attempt === 1) {
      ({ reply } = await press(page, tasks[0]));
    } else {
      const next = panel.getByRole("button", { name: "Next", exact: true });
      await expect(next, "retry: the Next button is enabled").toBeEnabled();
      const answered = page.waitForResponse((r) => r.request().method() === "POST" && MOVE.test(r.url()));
      await next.click();
      const r = await answered;
      reply = { status: r.status(), body: (await r.text()).slice(0, 600) };
    }
    const said = `attempt ${attempt}: POST .../tasks/current/next -> HTTP ${reply.status} ${reply.body}`;
    console.log(`LF427 broken lab ${said}`);
    expect(reply.status, `the manager must answer 502 setup_failed (${said})`).toBe(502);
    expect(reply.body, said).toContain("setup_failed");
    await expect(page.getByRole("alert"), said).toHaveText(errorText, { timeout: 15_000 });
    await expect(panel.getByRole("heading", { level: 2 }), "the current task stays").toHaveText(tasks[0].title);
    await expect(page.getByText(`Question 1 of ${tasks.length}`)).toBeVisible();
    expect(await steady(page, token, navigations), `${said}: the lab screen reloaded`).toBeNull();
    expect(
      (await page.request.get(`/api/sessions/${sid}/tasks/current`)).status(),
      "the session is still alive",
    ).toBe(200);
  }
  await page.getByRole("button", { name: "End lab" }).click();
  await expect(page).toHaveURL(/\/labs$/, wait);
});
