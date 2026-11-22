/**
 * Exam for #448 (from #154, D57 (d), D128): polish of the Solution tab of the lab screen.
 *
 * Wanted, in English and Hebrew:
 *  1. A Check pressed on the (locked) Solution tab returns to the Task tab; focus must then be on
 *     the task heading itself, not on document.body.
 *  2. A 410 session_ended answer from POST .../solution shows the ended-session state (lab.ended,
 *     role=status), not the generic load error (no role=alert).
 *  3. A 403 solution_locked answer is not cached: after a graded Check, opening the Solution tab
 *     POSTs to /solution again and shows the solution.
 *  4. A solution that already loaded is fetched once: a Check and reopening the tab do not POST again.
 */
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import App from "../../src/App";
import en from "../../src/i18n/en.json";
import he from "../../src/i18n/he.json";

type Reply = { status: number; body?: unknown };
type Dict = Record<string, string>;

const SID = "sess-448";
const API = `/api/sessions/${SID}`;
const SOLUTION_URL = `${API}/tasks/02-directory-tree/solution`;
const CHECK_URL = `${API}/tasks/02-directory-tree/check`;
const ok = (body: unknown): Reply => ({ status: 200, body });

const task = (solution_available: boolean) => ({
  id: "02-directory-tree", index: 2, total: 10, kind: "hands-on", state: "shown",
  solution_available,
  title: { en: "Build a folder tree", he: "בונים עץ תיקיות" },
  text: { en: "# Build a folder tree\n\nUse `mkdir`.\n", he: "# בונים עץ תיקיות\n\nהשתמשו ב-`mkdir`.\n" },
  checklist: [{ en: "The folder exists", he: "התיקייה קיימת" }],
});
const SOLUTION = {
  en: "## EN-solution-heading\n\nEN-solution-prose with `mkdir -p x`.\n",
  he: "## HE-solution-heading\n\nHE-solution-prose עם `mkdir -p x`.\n",
};
const checkReply = (): Reply =>
  ok({
    passed: false, attempts: 1, reason: null,
    checklist: [{ n: 1, text: { en: "The folder exists", he: "התיקייה קיימת" }, passed: false, hint: null }],
  });

let calls: string[] = [];

/** `solution` may be a function so a test can change the answer between requests. */
function backend(available: boolean, solution: Reply | (() => Reply)) {
  const fixed: Record<string, Reply> = {
    "GET /api/auth/me": ok({ kind: "student", name: "Dana Test" }),
    "GET /api/labs": ok([]),
    "GET /api/sessions/current": ok({
      id: SID, lab_id: "linux-1", status: "running", expires_at: new Date(Date.now() + 45 * 60_000).toISOString(),
    }),
    [`GET ${API}/tasks/current`]: ok(task(available)),
    [`POST ${API}/heartbeat`]: { status: 204 },
    [`POST ${CHECK_URL}`]: checkReply(),
  };
  vi.spyOn(globalThis, "fetch").mockImplementation((async (url: RequestInfo | URL, init?: RequestInit) => {
    const method = (init?.method ?? "GET").toUpperCase();
    const key = `${method} ${String(url)}`;
    calls.push(key);
    const r =
      key === `POST ${SOLUTION_URL}`
        ? typeof solution === "function" ? solution() : solution
        : fixed[key] ?? { status: 404, body: { detail: "Not Found" } };
    return {
      ok: r.status >= 200 && r.status < 300,
      status: r.status,
      headers: { get: () => null },
      json: async () => r.body ?? {},
      text: async () => JSON.stringify(r.body ?? {}),
    } as unknown as Response;
  }) as typeof fetch);
}

async function open(lang: "en" | "he") {
  window.history.replaceState({}, "", "/labs/linux-1");
  window.localStorage.setItem("lf-lang", lang);
  render(<App />);
  await screen.findByTestId("task-panel");
}
const d = (lang: "en" | "he"): Dict => (lang === "en" ? en : he) as Dict;
const panel = () => screen.getByTestId("task-panel");
const openTab = (lang: "en" | "he") =>
  fireEvent.click(within(panel()).getByRole("tab", { name: d(lang)["lab.solution.tab"] }));
const taskTab = (lang: "en" | "he") =>
  fireEvent.click(within(panel()).getByRole("tab", { name: d(lang)["lab.task.tab"] }));
const checkButton = (lang: "en" | "he") => screen.getByRole("button", { name: d(lang)["lab.check"] });
/** The graded result of a Check: the failed checklist row carries the cross mark (as in the #130 exam). */
const gradedMark = () =>
  waitFor(() => expect(within(panel()).getAllByRole("listitem").some((r) => r.textContent?.includes("✗"))).toBe(true));
const solutionPosts = () => calls.filter((c) => c === `POST ${SOLUTION_URL}`);

beforeEach(() => {
  window.localStorage.clear();
  window.history.replaceState({}, "", "/");
  calls = [];
});
afterEach(() => {
  vi.restoreAllMocks();
});

for (const lang of ["en", "he"] as const) {
  const name = lang === "en" ? "English" : "Hebrew";

  test(`${name}: focus lands on the task heading after a Check pressed on the Solution tab, not on body`, async () => {
    backend(false, ok({ solution: SOLUTION }));
    await open(lang);
    openTab(lang);
    const button = checkButton(lang);
    button.focus();
    expect(document.activeElement).toBe(button);
    fireEvent.click(button);
    await waitFor(() => expect(calls).toContain(`POST ${CHECK_URL}`));
    // back on the Task tab: the checklist row text is shown
    await waitFor(() => expect(panel().textContent).not.toContain(d(lang)["lab.solution.hint"]));
    const heading = await within(panel()).findByRole("heading", { name: task(false).title[lang] });
    await waitFor(() => expect(document.activeElement).toBe(heading));
  });

  test(`${name}: a 410 session_ended from the solution fetch shows the ended-session state`, async () => {
    backend(true, { status: 410, body: { detail: "session_ended" } });
    await open(lang);
    openTab(lang);
    await waitFor(() => expect(solutionPosts()).toHaveLength(1));
    expect(await screen.findByText(d(lang)["lab.ended"])).toBeTruthy();
    expect(screen.queryAllByRole("alert"), "not the generic load error").toHaveLength(0);
    expect(screen.queryByText(d(lang)["lab.error.load"])).toBeNull();
  });

  test(`${name}: a 403 solution_locked answer is not cached, a graded Check makes the next open fetch again`, async () => {
    let locked = true;
    backend(true, () => (locked ? { status: 403, body: { detail: "solution_locked" } } : ok({ solution: SOLUTION })));
    await open(lang);
    openTab(lang);
    await waitFor(() => expect(solutionPosts()).toHaveLength(1));
    await waitFor(() => expect(panel().textContent).toContain(d(lang)["lab.solution.none"]));
    // the server now has a graded attempt
    locked = false;
    taskTab(lang);
    fireEvent.click(checkButton(lang));
    await waitFor(() => expect(calls).toContain(`POST ${CHECK_URL}`));
    await gradedMark();
    openTab(lang);
    await waitFor(() => expect(panel().textContent).toContain(`${lang.toUpperCase()}-solution-prose`));
    expect(solutionPosts(), "fetched again after the Check").toHaveLength(2);
  });

  test(`${name}: a loaded solution is fetched once, a Check and reopening the tab do not fetch it again`, async () => {
    backend(true, ok({ solution: SOLUTION }));
    await open(lang);
    openTab(lang);
    await waitFor(() => expect(panel().textContent).toContain(`${lang.toUpperCase()}-solution-prose`));
    expect(solutionPosts()).toHaveLength(1);
    taskTab(lang);
    fireEvent.click(checkButton(lang));
    await waitFor(() => expect(calls).toContain(`POST ${CHECK_URL}`));
    await gradedMark();
    openTab(lang);
    await waitFor(() => expect(panel().textContent).toContain(`${lang.toUpperCase()}-solution-prose`));
    expect(solutionPosts(), "still one fetch after the Check").toHaveLength(1);
  });
}
