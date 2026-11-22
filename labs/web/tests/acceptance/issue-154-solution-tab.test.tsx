/**
 * Exam for #154 (D128): the Solution tab of the lab screen.
 *
 * The manager sends `solution_available: bool` in every task detail (false until the first graded
 * attempt) and answers `POST /api/sessions/<sid>/tasks/<task_id>/solution` with
 * `200 {solution: {en, he}}` (Markdown) or `403 {detail: "solution_locked"}`.
 *
 * Wanted, in English and Hebrew:
 *  - while `solution_available` is false the Solution tab shows the "not available yet" text
 *    (i18n key lab.solution.none, unchanged) AND a hint to run Check first. The hint is a NEW
 *    key `lab.solution.hint` in src/i18n/en.json and he.json (any wording, but it must name the
 *    Check button, i.e. contain the text of lab.check). No request to /solution is made.
 *  - when it is true, opening the tab sends ONE POST to /solution (switching tabs back and forth
 *    does not send another) and renders the text of the screen's language with the task's Markdown
 *    renderer (a heading is a heading, code is dir="ltr", also in the Hebrew panel; raw text of
 *    the other language is not shown).
 *  - a 403 solution_locked answer (the screen thought it was available, the server disagrees)
 *    shows the "not available yet" text, not an error card (no role="alert").
 */
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import App from "../../src/App";
import en from "../../src/i18n/en.json";
import he from "../../src/i18n/he.json";

type Reply = { status: number; body?: unknown };
type Dict = Record<string, string>;

const SID = "sess-154";
const API = `/api/sessions/${SID}`;
const SOLUTION_URL = `${API}/tasks/02-directory-tree/solution`;
const ok = (body: unknown): Reply => ({ status: 200, body });

// Exactly the manager's hands-on detail shape, plus the new boolean.
const task = (solution_available: boolean) => ({
  id: "02-directory-tree", index: 2, total: 10, kind: "hands-on", state: "shown",
  solution_available,
  title: { en: "Build a folder tree", he: "בונים עץ תיקיות" },
  text: { en: "# Build a folder tree\n\nUse `mkdir`.\n", he: "# בונים עץ תיקיות\n\nהשתמשו ב-`mkdir`.\n" },
  checklist: [{ en: "The folder exists", he: "התיקייה קיימת" }],
});
const SOLUTION = {
  en: "## EN-solution-heading\n\nEN-solution-prose with `mkdir -p inlinecmd` inside.\n\n```text\nmkdir -p blockcmd\n```\n",
  he: "## HE-solution-heading\n\nHE-solution-prose עם `mkdir -p inlinecmd` בפנים.\n\n```text\nmkdir -p blockcmd\n```\n",
};

let calls: string[] = [];

function backend(available: boolean, solutionReply: Reply = ok({ solution: SOLUTION })) {
  const all: Record<string, Reply> = {
    "GET /api/auth/me": ok({ kind: "student", name: "Dana Test" }),
    "GET /api/labs": ok([]),
    "GET /api/sessions/current": ok({
      id: SID, lab_id: "linux-1", status: "running", expires_at: new Date(Date.now() + 45 * 60_000).toISOString(),
    }),
    [`GET ${API}/tasks/current`]: ok(task(available)),
    [`POST ${API}/heartbeat`]: { status: 204 },
    [`POST ${SOLUTION_URL}`]: solutionReply,
  };
  vi.spyOn(globalThis, "fetch").mockImplementation((async (url: RequestInfo | URL, init?: RequestInit) => {
    const method = (init?.method ?? "GET").toUpperCase();
    calls.push(`${method} ${String(url)}`);
    const r = all[`${method} ${String(url)}`] ?? { status: 404, body: { detail: "Not Found" } };
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

  test(`${name}: while the solution is not available the tab says so and hints to run Check first`, async () => {
    backend(false);
    await open(lang);
    openTab(lang);
    const hint = d(lang)["lab.solution.hint"];
    expect(hint, "i18n key lab.solution.hint exists").toBeTruthy();
    expect(hint).toContain(d(lang)["lab.check"]);
    expect(hint).not.toBe(d(lang)["lab.solution.none"]);
    expect(panel().textContent).toContain(d(lang)["lab.solution.none"]);
    expect(panel().textContent).toContain(hint);
    expect(solutionPosts(), "no request to /solution while locked").toHaveLength(0);
  });

  test(`${name}: an available solution is loaded on opening the tab, once, in the screen's language`, async () => {
    backend(true);
    await open(lang);
    expect(solutionPosts(), "nothing is fetched before the tab is opened").toHaveLength(0);
    openTab(lang);
    const other = lang === "en" ? "HE" : "EN";
    await waitFor(() => expect(panel().textContent).toContain(`${lang.toUpperCase()}-solution-prose`));
    expect(panel().textContent).not.toContain(`${other}-solution-prose`);
    expect(panel().textContent, "the locked text is gone").not.toContain(d(lang)["lab.solution.none"]);
    // switch away and back: still one request
    fireEvent.click(within(panel()).getByRole("tab", { name: d(lang)["lab.task.tab"] }));
    openTab(lang);
    await waitFor(() => expect(panel().textContent).toContain(`${lang.toUpperCase()}-solution-prose`));
    expect(solutionPosts()).toHaveLength(1);
  });

  test(`${name}: the solution is rendered as Markdown with code always left-to-right`, async () => {
    backend(true);
    await open(lang);
    openTab(lang);
    const heading = await screen.findByText(`${lang.toUpperCase()}-solution-heading`);
    expect(heading.tagName, "a Markdown heading, not the raw ## text").toMatch(/^H[1-6]$/);
    expect(panel().textContent).not.toContain("##");
    expect(panel().textContent).not.toContain("```");
    const inline = screen.getByText("mkdir -p inlinecmd");
    const block = screen.getByText("mkdir -p blockcmd");
    for (const el of [inline, block]) {
      expect(el.closest("[dir]")?.getAttribute("dir"), "code is dir=ltr").toBe("ltr");
    }
    expect(block.closest("pre"), "the fenced block is a <pre>").not.toBeNull();
  });

  test(`${name}: a 403 solution_locked answer shows the not-available text, not an error card`, async () => {
    backend(true, { status: 403, body: { detail: "solution_locked" } });
    await open(lang);
    openTab(lang);
    await waitFor(() => expect(solutionPosts()).toHaveLength(1));
    await waitFor(() => expect(panel().textContent).toContain(d(lang)["lab.solution.none"]));
    expect(screen.queryAllByRole("alert"), "no error card").toHaveLength(0);
  });
}
