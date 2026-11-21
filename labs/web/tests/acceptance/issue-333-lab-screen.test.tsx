/**
 * Exam for #333 (slice 3 of the visual design epic #330, D105): the lab screen. This file covers what
 * jsdom can see (criteria 2 (the tab semantics and the iframe identity), 3, 4, 5 (the label)); the layout
 * criteria (1, 2 stacked, 5 tabular numbers, mirroring, 7 screenshots) are in
 * tests/acceptance/issue-333-design.pw.ts. Criterion 6 is "the existing lab screen exams stay green":
 * issue-130/157/160/178/224/264/269 and LabScreen.test.tsx are NOT changed, so, among other things,
 * there is still exactly one role="status" on the lab screen (#269) and End lab still has no extra
 * confirmation step (#130 says: one click, one POST .../end; the issue's "existing confirmation kept" means
 * that behaviour stays exactly as it is).
 *
 * CONTRACT the implementation must honour (hooks the exam relies on; all semantic)
 *   - Pane switch: a `role="tablist"` (in addition to the existing Task / Solution tablist, which stays) with
 *     exactly two `role="tab"` buttons whose accessible names are the new i18n keys `lab.pane.tasks` and
 *     `lab.pane.terminal` (both in en.json and he.json, different from `lab.task.tab` / `lab.solution.tab`).
 *     It is ALWAYS in the DOM (jsdom has no media queries and no CSS); CSS hides it on wide screens.
 *     Exactly one of the two has aria-selected="true"; the selected one has tabIndex 0, the other -1.
 *     Tasks is selected first. Switching panes hides one with CSS (display:none), it never unmounts:
 *     the terminal <iframe title=lab.terminal> is the same DOM node before and after, with the same src.
 *   - The countdown stays `role="timer"` and gets an accessible name (aria-label) made of words in the page
 *     language (not just the clock digits).
 *   - Task code (pre, code) stays dir="ltr". (The "task id in <bdi dir=ltr>" loop was removed in #349: the page
 *     never shows the id, so it asserted nothing.)
 *   - The check result line keeps living in a pre-existing `aria-live` element (not role=status: #269).
 */
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import App from "../../src/App";
import en from "../../src/i18n/en.json";
import he from "../../src/i18n/he.json";

type Dict = Record<string, string>;
const DICTS: Record<"en" | "he", Dict> = { en: en as Dict, he: he as Dict };
const LANGS = ["en", "he"] as const;

type Reply = { status: number; body?: unknown };
type Handler = Reply | (() => Reply);
type Calls = { url: string; method: string }[];

const SID = "sess-1";
const API = `/api/sessions/${SID}`;
const T0 = Date.now();
const ok = (body: unknown): Reply => ({ status: 200, body });
const TASK_ID = "02-make-file";
const HANDS = {
  id: TASK_ID, index: 2, total: 4, kind: "hands-on", state: "shown",
  title: { en: "Make a file", he: "יוצרים קובץ" },
  text: {
    en: "Create the notes file.\n\n```\ntouch notes.txt\n```\n\nThen run `ls`.\n",
    he: "צרו את קובץ ההערות.\n\n```\ntouch notes.txt\n```\n\nואז הריצו `ls`.\n",
  },
  checklist: [{ en: "The file exists", he: "הקובץ קיים" }],
};

function backend(routes: Record<string, Handler> = {}) {
  const calls: Calls = [];
  const all: Record<string, Handler> = {
    "GET /api/auth/me": ok({ kind: "student", name: "Dana Test" }),
    "GET /api/labs": ok([]),
    "GET /api/sessions/current": ok({
      id: SID, lab_id: "lab-1", status: "running", expires_at: new Date(T0 + 55 * 60_000).toISOString(),
    }),
    [`GET ${API}/tasks/current`]: ok(HANDS),
    [`POST ${API}/heartbeat`]: { status: 204 },
    [`POST ${API}/end`]: { status: 204 },
    ...routes,
  };
  vi.spyOn(globalThis, "fetch").mockImplementation((async (url: RequestInfo | URL, init?: RequestInit) => {
    const method = (init?.method ?? "GET").toUpperCase();
    calls.push({ url: String(url), method });
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
  return calls;
}

async function open(lang: "en" | "he") {
  window.history.replaceState({}, "", "/labs/lab-1");
  window.localStorage.setItem("lf-lang", lang);
  render(<App />);
  await screen.findByTestId("task-panel");
}
const panel = () => screen.getByTestId("task-panel");
const paneTabs = (lang: "en" | "he") => {
  const d = DICTS[lang];
  expect(d["lab.pane.tasks"], `${lang}.json has lab.pane.tasks`).toBeTruthy();
  expect(d["lab.pane.terminal"], `${lang}.json has lab.pane.terminal`).toBeTruthy();
  return {
    tasks: screen.getByRole("tab", { name: d["lab.pane.tasks"] }),
    terminal: screen.getByRole("tab", { name: d["lab.pane.terminal"] }),
  };
};

beforeEach(() => {
  window.localStorage.clear();
  window.history.replaceState({}, "", "/");
});
afterEach(() => {
  vi.restoreAllMocks();
});

describe.each(LANGS)("lab screen design contract (%s)", (lang) => {
  const d = DICTS[lang];

  test("AC2: the pane switch is a tablist with exactly two tabs named by lab.pane.tasks and lab.pane.terminal", async () => {
    expect(DICTS.en["lab.pane.tasks"], "en.json has lab.pane.tasks").toBeTruthy();
    expect(DICTS.en["lab.pane.terminal"], "en.json has lab.pane.terminal").toBeTruthy();
    expect(DICTS.he["lab.pane.tasks"], "he.json has lab.pane.tasks").toBeTruthy();
    expect(DICTS.he["lab.pane.terminal"], "he.json has lab.pane.terminal").toBeTruthy();
    for (const k of ["lab.pane.tasks", "lab.pane.terminal"])
      expect([d["lab.task.tab"], d["lab.solution.tab"]]).not.toContain(d[k]);
    backend();
    await open(lang);
    const { tasks, terminal } = paneTabs(lang);
    const list = tasks.closest('[role="tablist"]');
    expect(list, "the two pane tabs sit in one tablist").not.toBeNull();
    expect(terminal.closest('[role="tablist"]')).toBe(list);
    expect(within(list as HTMLElement).getAllByRole("tab")).toHaveLength(2);
    // The existing Task / Solution tabs are not this switch.
    expect(list).not.toBe(screen.getByRole("tab", { name: d["lab.task.tab"] }).closest('[role="tablist"]'));
  });

  test("AC2: Tasks is selected first, one tab is selected at a time, roving tabindex", async () => {
    backend();
    await open(lang);
    const { tasks, terminal } = paneTabs(lang);
    expect(tasks.getAttribute("aria-selected")).toBe("true");
    expect(terminal.getAttribute("aria-selected")).toBe("false");
    expect([tasks.tabIndex, terminal.tabIndex]).toEqual([0, -1]);
    fireEvent.click(terminal);
    expect(terminal.getAttribute("aria-selected")).toBe("true");
    expect(tasks.getAttribute("aria-selected")).toBe("false");
    expect([tasks.tabIndex, terminal.tabIndex]).toEqual([-1, 0]);
    fireEvent.click(tasks);
    expect(tasks.getAttribute("aria-selected")).toBe("true");
  });

  test("AC2: switching panes keeps the same terminal iframe node and src", async () => {
    backend();
    await open(lang);
    const frame = screen.getByTitle(d["lab.terminal"]) as HTMLIFrameElement;
    const src = frame.getAttribute("src");
    expect(src).toBe(`/s/${SID}/term/`);
    const { tasks, terminal } = paneTabs(lang);
    fireEvent.click(terminal);
    fireEvent.click(tasks);
    fireEvent.click(terminal);
    const after = screen.getByTitle(d["lab.terminal"]);
    expect(after).toBe(frame);
    expect(frame.isConnected).toBe(true);
    expect(frame.getAttribute("src")).toBe(src);
  });

  test("AC3: task code (pre and code) stays left to right", async () => {
    backend();
    await open(lang);
    const pre = panel().querySelector("pre");
    expect(pre?.getAttribute("dir")).toBe("ltr");
    const codes = [...panel().querySelectorAll("code")];
    expect(codes.length).toBeGreaterThan(0);
    for (const c of codes) expect(c.getAttribute("dir")).toBe("ltr");
  });

  test("AC4: the check result is announced by an aria-live element that was in the page before the check", async () => {
    backend({
      [`POST ${API}/tasks/${TASK_ID}/check`]: ok({ passed: false, reason: "timeout", checklist: [] }),
    });
    await open(lang);
    const before = [...document.querySelectorAll("[aria-live]")];
    expect(before.length).toBeGreaterThan(0);
    expect(screen.getAllByRole("status"), "#269: exactly one role=status").toHaveLength(1);
    fireEvent.click(within(panel()).getByRole("button", { name: d["lab.check"] }));
    const line = await screen.findByText(d["lab.check_failed"]);
    const live = line.closest("[aria-live]");
    expect(live, "the result line is inside an aria-live element").not.toBeNull();
    expect(before).toContain(live);
    expect(screen.getAllByRole("status")).toHaveLength(1);
    expect(screen.getAllByText(d["lab.check_failed"])).toHaveLength(1);
  });

  test("AC4: a failed checklist item shows its hint on screen", async () => {
    backend({
      [`POST ${API}/tasks/${TASK_ID}/check`]: ok({
        passed: false, attempts: 1, reason: null,
        checklist: [{ n: 1, passed: false, hint: "Run touch first" }],
      }),
    });
    await open(lang);
    fireEvent.click(within(panel()).getByRole("button", { name: d["lab.check"] }));
    expect(await screen.findByText("Run touch first")).toBeTruthy();
    expect(within(panel()).queryByRole("button", { name: d["lab.next"] })).toBeNull();
  });

  test("AC5: the countdown is the one role=timer and has an accessible name in words", async () => {
    backend();
    await open(lang);
    const timer = screen.getByRole("timer");
    expect(screen.getAllByRole("timer")).toHaveLength(1);
    const named = screen.getByRole("timer", { name: /[^\d:\s]/ });
    expect(named).toBe(timer);
    const name = timer.getAttribute("aria-label") ?? "";
    expect(name).toMatch(lang === "he" ? /[\u0590-\u05FF]/ : /[A-Za-z]/);
    expect(timer.textContent).toMatch(/^\d+:\d{2}$/);
  });

  test("End lab: one click is one POST .../end and then My labs (the existing behaviour is kept)", async () => {
    const calls = backend();
    await open(lang);
    fireEvent.click(screen.getByRole("button", { name: d["lab.end"] }));
    await waitFor(() => expect(window.location.pathname).toBe("/labs"));
    expect(calls.filter((c) => c.method === "POST" && c.url === `${API}/end`)).toHaveLength(1);
  });
});
