/**
 * Exam for #466: a Check pressed while the first solution fetch is still pending must not cause a
 * second POST .../solution.
 *
 * Wanted, in English and Hebrew: the student opens the (unlocked) Solution tab, goes back to the
 * Task tab and presses Check before the solution POST answers. Once it has answered, reopening the
 * Solution tab shows the solution and the whole run sent exactly one /solution POST.
 */
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import App from "../../src/App";
import en from "../../src/i18n/en.json";
import he from "../../src/i18n/he.json";

type Reply = { status: number; body?: unknown };
type Dict = Record<string, string>;

const SID = "sess-466";
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
function backend(available: boolean, solution: Reply | (() => Reply), gate?: Promise<void>) {
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
    if (gate && key === `POST ${SOLUTION_URL}`) await gate;
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

  test(`${name}: a Check pressed while the first solution fetch is pending leads to exactly one solution POST`, async () => {
    let release!: () => void;
    const gate = new Promise<void>((res) => { release = res; });
    backend(true, ok({ solution: SOLUTION }), gate);
    await open(lang);
    openTab(lang);
    await waitFor(() => expect(solutionPosts()).toHaveLength(1));
    // the first POST is still pending: back to the Task tab and Check
    taskTab(lang);
    fireEvent.click(checkButton(lang));
    await waitFor(() => expect(calls).toContain(`POST ${CHECK_URL}`));
    await gradedMark();
    // now the solution answers
    release();
    await new Promise((r) => setTimeout(r, 50));
    openTab(lang);
    await waitFor(() => expect(panel().textContent).toContain(`${lang.toUpperCase()}-solution-prose`));
    await new Promise((r) => setTimeout(r, 50));
    expect(solutionPosts(), "exactly one solution POST").toHaveLength(1);
  });
}
