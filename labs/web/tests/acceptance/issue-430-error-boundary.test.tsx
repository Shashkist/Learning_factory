/**
 * Exam for #430 criterion 1: an error boundary around the task pane of the lab screen.
 *
 * A render error in the task pane (here: a hands-on task whose checklist holds a null item, which
 * the pane cannot read) must not blank the app. Today one throw while rendering unmounts the whole
 * lab screen, terminal iframe included (that was #427). Wanted: the task pane shows ONE alert
 * (role="alert", inside the element data-testid="task-panel") and the terminal iframe stays the
 * SAME DOM node, still connected.
 *
 * Strings the implementer must add to src/i18n/en.json and src/i18n/he.json (key name is free,
 * suggested "lab.error.render"); the test compares the alert text to exactly these:
 *   en: "This task could not be displayed."
 *   he: "לא ניתן להציג את המשימה הזאת."
 *
 * The fault is reached by a move (quiz, answer, Next), so the iframe exists before the faulty
 * task renders. No recovery test: once the pane has failed, this exam does not assume any UI
 * (Previous/Next placement is left to the implementer).
 */
import { fireEvent, render, screen, within } from "@testing-library/react";

import App from "../../src/App";
import en from "../../src/i18n/en.json";
import he from "../../src/i18n/he.json";

type Reply = { status: number; body?: unknown };

const EN_ALERT = "This task could not be displayed.";
const HE_ALERT = "לא ניתן להציג את המשימה הזאת.";
const SID = "sess-430";
const API = `/api/sessions/${SID}`;
const ok = (body: unknown): Reply => ({ status: 200, body });
const QUIZ = {
  id: "01-pwd-ls", index: 1, total: 3, kind: "quiz", state: "shown",
  title: { en: "Which command lists files?", he: "איזו פקודה מציגה קבצים?" },
  question: { en: "Which command lists files?", he: "איזו פקודה מציגה קבצים?" },
  options: [{ en: "pwd", he: "pwd" }, { en: "ls", he: "ls" }],
};
// Malformed tasks, each reached by a move. Rendering them throws:
//  - a hands-on task with a null checklist item
//  - a quiz with a null option
//  - a hands-on task whose title is null
const BROKEN = {
  id: "02-broken", index: 2, total: 3, kind: "hands-on", state: "shown",
  title: { en: "Broken task", he: "משימה שבורה" },
  text: { en: "Some text", he: "טקסט" },
  checklist: [null],
};

const BROKEN_QUIZ = {
  id: "02-broken-quiz", index: 2, total: 3, kind: "quiz", state: "shown",
  title: { en: "Broken quiz", he: "חידון שבור" },
  question: { en: "Which?", he: "איזה?" },
  options: [null],
};
const BROKEN_TITLE = { ...BROKEN, id: "02-broken-title", title: null, checklist: [{ en: "x", he: "x" }] };
const FAULTS = { checklist: BROKEN, option: BROKEN_QUIZ, title: BROKEN_TITLE } as const;

function backend(next: unknown = BROKEN) {
  const all: Record<string, Reply> = {
    "GET /api/auth/me": ok({ kind: "student", name: "Dana Test" }),
    "GET /api/labs": ok([]),
    "GET /api/sessions/current": ok({
      id: SID, lab_id: "linux-1", status: "running", expires_at: new Date(Date.now() + 45 * 60_000).toISOString(),
    }),
    [`GET ${API}/tasks/current`]: ok(QUIZ),
    [`POST ${API}/heartbeat`]: { status: 204 },
    [`POST ${API}/tasks/01-pwd-ls/answer`]: ok({ correct: true }),
    [`POST ${API}/tasks/current/next`]: ok(next),
  };
  vi.spyOn(globalThis, "fetch").mockImplementation((async (url: RequestInfo | URL, init?: RequestInit) => {
    const method = (init?.method ?? "GET").toUpperCase();
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

const panel = () => screen.getByTestId("task-panel");
const iframe = (lang: "en" | "he") =>
  document.querySelector(`iframe[title="${(lang === "en" ? en : he)["lab.terminal" as keyof typeof en]}"]`);

async function faultyMove(lang: "en" | "he") {
  window.history.replaceState({}, "", "/labs/linux-1");
  window.localStorage.setItem("lf-lang", lang);
  render(<App />);
  await screen.findByTestId("task-panel");
  const before = iframe(lang);
  expect(before, "the terminal iframe is on screen before the move").not.toBeNull();
  const t = lang === "en" ? en : he;
  fireEvent.click(within(panel()).getByRole("radio", { name: "ls" }));
  fireEvent.click(within(panel()).getByRole("button", { name: t["lab.answer"] }));
  fireEvent.click(await within(panel()).findByRole("button", { name: t["lab.next"] }));
  return before;
}

beforeEach(() => {
  window.localStorage.clear();
  window.history.replaceState({}, "", "/");
  vi.spyOn(console, "error").mockImplementation(() => {}); // React logs the caught render error
});
afterEach(() => {
  vi.restoreAllMocks();
});

const lang2 = ["en", "he"] as const;
for (const [fault, task] of Object.entries(FAULTS)) {
  for (const lang of lang2) {
    test(`a render error (${fault}) in the task pane shows the ${lang === "en" ? "English" : "Hebrew"} alert in the pane and keeps the same terminal iframe`, async () => {
      backend(task);
      const before = await faultyMove(lang);
      const alert = await within(await screen.findByTestId("task-panel")).findByRole("alert");
      expect(alert.textContent).toBe(lang === "en" ? EN_ALERT : HE_ALERT);
      expect(screen.getAllByRole("alert"), "exactly one alert on screen").toHaveLength(1);
      expect(iframe(lang), "the terminal iframe is the same element").toBe(before);
      expect(before!.isConnected, "and still in the document").toBe(true);
    });
  }
}
