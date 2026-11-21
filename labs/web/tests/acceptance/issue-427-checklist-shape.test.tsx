/**
 * Exam for #427 (jsdom part): the lab screen renders a hands-on task in the shape the REAL manager
 * sends. `GET/POST .../tasks/...` answers a hands-on task with `checklist: [{en, he}, ...]` (plain
 * bilingual texts, pinned by the manager exam test_issue_9_task_api.py), while `POST .../check`
 * answers `checklist: [{n, passed, hint}]` with n counted from 1 in checklist order (spec section 7,
 * `PASS <n>`). The browser exam (issue-427-linux1-moves.pw.ts, CI docker job) showed the move from
 * Linux 1 task 1 (quiz) to task 2 (hands-on) dropping the whole lab screen, terminal iframe included.
 *
 * Criteria covered here: 1 (Next from a quiz to a hands-on task shows the hands-on task, no error)
 * and 2 (the terminal iframe is the same element after the move), with the real shape; plus the
 * check result is matched to the items by their position (n = 1..N).
 */
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import App from "../../src/App";
import en from "../../src/i18n/en.json";

type Reply = { status: number; body?: unknown };
type Handler = Reply | (() => Reply);

const SID = "sess-427";
const API = `/api/sessions/${SID}`;
const ok = (body: unknown): Reply => ({ status: 200, body });
const QUIZ = {
  id: "01-pwd-ls", index: 1, total: 10, kind: "quiz", state: "shown",
  title: { en: "Which command lists files?", he: "איזו פקודה מציגה קבצים?" },
  question: { en: "Which command lists files?", he: "איזו פקודה מציגה קבצים?" },
  options: [{ en: "pwd", he: "pwd" }, { en: "ls", he: "ls" }, { en: "cd", he: "cd" }],
};
// Exactly the manager's shape: `checklist` items are {en, he}, no `n`, no `text`.
const HANDS = {
  id: "02-directory-tree", index: 2, total: 10, kind: "hands-on", state: "shown",
  title: { en: "Build a folder tree", he: "בונים עץ תיקיות" },
  text: {
    en: "# Build a folder tree\n\nMake this structure:\n\n```text\nprojects/\n  website/\n```\n\nUse `mkdir`.\n",
    he: "# בונים עץ תיקיות\n\nצרו את המבנה:\n\n```text\nprojects/\n  website/\n```\n\nהשתמשו ב-`mkdir`.\n",
  },
  checklist: [
    { en: "The folder ~/projects/website exists", he: "התיקייה ‎~/projects/website‎ קיימת" },
    { en: "The folder ~/projects/website/css exists", he: "התיקייה ‎~/projects/website/css‎ קיימת" },
    { en: "The folder ~/projects/website/images exists", he: "התיקייה ‎~/projects/website/images‎ קיימת" },
  ],
};

function backend(first: unknown, routes: Record<string, Handler> = {}) {
  const all: Record<string, Handler> = {
    "GET /api/auth/me": ok({ kind: "student", name: "Dana Test" }),
    "GET /api/labs": ok([]),
    "GET /api/sessions/current": ok({
      id: SID, lab_id: "linux-1", status: "running", expires_at: new Date(Date.now() + 45 * 60_000).toISOString(),
    }),
    [`GET ${API}/tasks/current`]: ok(first),
    [`POST ${API}/heartbeat`]: { status: 204 },
    ...routes,
  };
  vi.spyOn(globalThis, "fetch").mockImplementation((async (url: RequestInfo | URL, init?: RequestInit) => {
    const method = (init?.method ?? "GET").toUpperCase();
    const h = all[`${method} ${String(url)}`];
    const r: Reply = h === undefined ? { status: 404, body: { detail: "Not Found" } } : typeof h === "function" ? h() : h;
    return {
      ok: r.status >= 200 && r.status < 300,
      status: r.status,
      headers: { get: () => null },
      json: async () => r.body ?? {},
      text: async () => JSON.stringify(r.body ?? {}),
    } as unknown as Response;
  }) as typeof fetch);
}

async function open() {
  window.history.replaceState({}, "", "/labs/linux-1");
  window.localStorage.setItem("lf-lang", "en");
  render(<App />);
  await screen.findByTestId("task-panel");
}
const panel = () => screen.getByTestId("task-panel");
const iframe = () => document.querySelector('iframe[title="' + (en as Record<string, string>)["lab.terminal"] + '"]');

beforeEach(() => {
  window.localStorage.clear();
  window.history.replaceState({}, "", "/");
});
afterEach(() => {
  vi.restoreAllMocks();
});

test("a hands-on task in the manager's shape shows its title, text and every checklist item", async () => {
  backend(HANDS);
  await open();
  expect(within(panel()).getByRole("heading", { level: 2 }).textContent).toBe("Build a folder tree");
  for (const item of HANDS.checklist) expect(within(panel()).getByText(item.en)).toBeTruthy();
  expect(within(panel()).getByText("mkdir")).toBeTruthy();
  expect(iframe(), "the terminal iframe is on screen").not.toBeNull();
});

test("Next from a quiz to a hands-on task in the manager's shape keeps the screen and the same terminal iframe", async () => {
  backend(QUIZ, {
    [`POST ${API}/tasks/01-pwd-ls/answer`]: ok({ correct: true }),
    [`POST ${API}/tasks/current/next`]: ok(HANDS),
  });
  await open();
  const before = iframe();
  expect(before, "the terminal iframe is on screen before the move").not.toBeNull();
  fireEvent.click(within(panel()).getByRole("radio", { name: "ls" }));
  fireEvent.click(within(panel()).getByRole("button", { name: en["lab.answer"] }));
  fireEvent.click(await within(panel()).findByRole("button", { name: en["lab.next"] }));
  await waitFor(() =>
    expect(within(panel()).getByRole("heading", { level: 2 }).textContent).toBe("Build a folder tree"),
  );
  expect(screen.queryByRole("alert"), "no error after the move").toBeNull();
  for (const item of HANDS.checklist) expect(within(panel()).getByText(item.en)).toBeTruthy();
  expect(iframe(), "the terminal iframe is the same element after the move").toBe(before);
});

test("a check result is matched to the manager-shaped items by position (n = 1..N)", async () => {
  backend(HANDS, {
    [`POST ${API}/tasks/02-directory-tree/check`]: ok({
      passed: false,
      attempts: 1,
      reason: null,
      checklist: [
        { n: 1, passed: true, hint: null },
        { n: 2, passed: false, hint: "css is missing" },
        { n: 3, passed: true, hint: null },
      ],
    }),
  });
  await open();
  fireEvent.click(within(panel()).getByRole("button", { name: en["lab.check"] }));
  await within(panel()).findByText("css is missing");
  const items = within(panel()).getAllByRole("listitem").filter((li) => li.closest(".lab-checklist"));
  expect(items).toHaveLength(3);
  expect(items[0].textContent).toContain("✓");
  expect(items[1].textContent).toContain("✗");
  expect(items[1].textContent).toContain("css is missing");
  expect(items[2].textContent).toContain("✓");
});
