/**
 * Exam for #349 (review round 1 of #347 / #333). D108 stays: End lab is ONE click, no confirmation (#130).
 *
 * CONTRACT the implementation must honour (semantic hooks only)
 *   1. TOP BAR. The lab screen has a top bar (see lab-topbar.ts for how the exam finds it: the smallest
 *      element holding both the role="timer" countdown and the End lab button; it is not the task panel and
 *      not the whole screen, it comes before the panes, it holds no iframe). It holds: the lab title in the
 *      page language (from GET /api/labs, the entry whose id is the session's lab_id), the countdown, the
 *      session state while the lab runs (new i18n key `lab.state.running`, in en.json and he.json, words,
 *      not digits) and End lab (class "danger", ONE click = one POST .../end then /labs, no dialog, no
 *      window.confirm). End lab is NOT also in the task panel: exactly one button of that name.
 *      If GET /api/labs fails the lab screen still works (no title, End lab still in the top bar).
 *   2. FULL-PANE END STATES. "Lab complete" and "session ended" are messages that take the whole pane, each
 *      with the next action: the link "Back to My labs" (lab.to_labs) to /labs. At complete the existing
 *      contract of #157/#160/#264/#269 is kept: the one role="status" element holds lab.complete (its text is
 *      exactly that line), Previous / Skip / Restart stay in the task panel. The Playwright design exam
 *      (issue-349-design.pw.ts) checks the layout (width, terminal not shown).
 *   3. SPLITTER. role="separator" with aria-valuenow = the tasks pane width in percent: in Hebrew (rtl) the
 *      tasks pane sits on the right, so ArrowLeft and a pointer drag to the left WIDEN it (and the mirror
 *      in English).
 */
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import App from "../../src/App";
import en from "../../src/i18n/en.json";
import he from "../../src/i18n/he.json";
import { endButton, topBar } from "./lab-topbar";

type Dict = Record<string, string>;
const DICTS: Record<"en" | "he", Dict> = { en: en as Dict, he: he as Dict };
const LANGS = ["en", "he"] as const;

type Reply = { status: number; body?: unknown };
type Calls = { url: string; method: string }[];

const SID = "sess-1";
const API = `/api/sessions/${SID}`;
const T0 = Date.now();
const ok = (body: unknown): Reply => ({ status: 200, body });
const err = (status: number, detail: string): Reply => ({ status, body: { detail } });
const LAB_TITLE = { en: "Linux basics practice", he: "תרגול יסודות לינוקס" };
const LAST = {
  id: "04-last", index: 4, total: 4, kind: "hands-on", state: "shown",
  title: { en: "The last one", he: "האחרונה" },
  text: { en: "Do it.\n", he: "עשו.\n" },
  checklist: [{ en: "Done", he: "בוצע" }],
};
const PASSED_LIST = {
  lab_id: "lab-1", current: null,
  tasks: [1, 2, 3, 4].map((i) => ({
    id: i === 4 ? "04-last" : `0${i}-t`, index: i, kind: "hands-on", state: "passed", title: LAST.title,
  })),
};

function backend(routes: Record<string, Reply> = {}) {
  const calls: Calls = [];
  const all: Record<string, Reply> = {
    "GET /api/auth/me": ok({ kind: "student", name: "Dana Test" }),
    "GET /api/labs": ok([{
      id: "lab-1", title: LAB_TITLE, description: { en: "d", he: "d" }, module: 1, lesson: 1, tasks: 4,
      progress: "0/4", session_id: SID,
    }]),
    "GET /api/sessions/current": ok({
      id: SID, lab_id: "lab-1", status: "running", expires_at: new Date(T0 + 55 * 60_000).toISOString(),
    }),
    [`GET ${API}/tasks/current`]: ok(LAST),
    [`POST ${API}/heartbeat`]: { status: 204 },
    [`POST ${API}/end`]: { status: 204 },
    ...routes,
  };
  vi.spyOn(globalThis, "fetch").mockImplementation((async (url: RequestInfo | URL, init?: RequestInit) => {
    const method = (init?.method ?? "GET").toUpperCase();
    calls.push({ url: String(url), method });
    const r: Reply = all[`${method} ${String(url)}`] ?? { status: 404, body: { detail: "Not Found" } };
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

function at(lang: "en" | "he") {
  window.history.replaceState({}, "", "/labs/lab-1");
  window.localStorage.setItem("lf-lang", lang);
  render(<App />);
}
async function open(lang: "en" | "he") {
  at(lang);
  await screen.findByTestId("task-panel");
}
const panel = () => screen.getByTestId("task-panel");

beforeEach(() => {
  window.localStorage.clear();
  window.history.replaceState({}, "", "/");
});
afterEach(() => {
  vi.restoreAllMocks();
});

describe.each(LANGS)("AC1: the lab screen top bar (%s)", (lang) => {
  const d = DICTS[lang];

  test("AC1: the top bar holds the lab title, the countdown, the session state and End lab", async () => {
    expect(DICTS.en["lab.state.running"], "en.json has lab.state.running").toBeTruthy();
    expect(DICTS.he["lab.state.running"], "he.json has lab.state.running").toBeTruthy();
    expect(d["lab.state.running"]).not.toMatch(/^[\d:\s]*$/);
    backend();
    await open(lang);
    const bar = topBar(d["lab.end"]);
    expect(within(bar).getByRole("timer")).toBe(screen.getByRole("timer"));
    expect(bar.textContent).toContain(LAB_TITLE[lang]);
    expect(bar.textContent).toContain(d["lab.state.running"]);
    expect(within(bar).getByRole("button", { name: d["lab.end"] })).toBeTruthy();
  });

  test("AC1: End lab is in the top bar only, not also in the task panel (one button of that name)", async () => {
    backend();
    await open(lang);
    expect(within(panel()).queryByRole("button", { name: d["lab.end"] })).toBeNull();
    expect(screen.getAllByRole("button", { name: d["lab.end"] })).toHaveLength(1);
    expect(topBar(d["lab.end"]).contains(panel())).toBe(false);
  });

  test("AC1: End lab has the danger style", async () => {
    backend();
    await open(lang);
    expect(endButton(d["lab.end"]).classList.contains("danger")).toBe(true);
  });

  test("AC1: End lab is one click: one POST .../end, then My labs, no dialog and no confirm()", async () => {
    const calls = backend();
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    await open(lang);
    fireEvent.click(endButton(d["lab.end"]));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(screen.queryByRole("alertdialog")).toBeNull();
    await waitFor(() => expect(window.location.pathname).toBe("/labs"));
    expect(calls.filter((c) => c.method === "POST" && c.url === `${API}/end`)).toHaveLength(1);
    expect(confirm).not.toHaveBeenCalled();
  });

  test("AC1: the top bar stays and End lab still works when the lab list cannot be read", async () => {
    const calls = backend({ "GET /api/labs": err(500, "boom") });
    await open(lang);
    expect(topBar(d["lab.end"]).querySelector('[role="timer"]')).not.toBeNull();
    fireEvent.click(endButton(d["lab.end"]));
    await waitFor(() => expect(window.location.pathname).toBe("/labs"));
    expect(calls.filter((c) => c.method === "POST" && c.url === `${API}/end`)).toHaveLength(1);
  });
});

describe.each(LANGS)("AC2: full-pane end states (%s)", (lang) => {
  const d = DICTS[lang];

  test("AC2: lab complete is a message with the next action (Back to My labs), End lab stays in the top bar", async () => {
    const calls = backend({
      [`POST ${API}/tasks/04-last/check`]: ok({ passed: true, attempts: 1, reason: null, checklist: [{ n: 1, passed: true, hint: null }] }),
      [`POST ${API}/tasks/current/next`]: err(409, "no_next_task"),
      [`GET ${API}/tasks`]: ok(PASSED_LIST),
    });
    await open(lang);
    fireEvent.click(within(panel()).getByRole("button", { name: d["lab.check"] }));
    fireEvent.click(await within(panel()).findByRole("button", { name: d["lab.next"] }));
    await waitFor(() => expect(screen.getByRole("status").textContent).toBe(d["lab.complete"]));
    expect(screen.getAllByRole("status")).toHaveLength(1);
    // The task itself is gone from the pane: only the message and what follows from it.
    expect(within(panel()).queryByRole("heading", { level: 2, name: LAST.title[lang] })).toBeNull();
    expect(within(panel()).queryByRole("button", { name: d["lab.check"] })).toBeNull();
    // The way out is a link, and following it needs no extra POST.
    const link = screen.getByRole("link", { name: d["lab.to_labs"] });
    expect(topBar(d["lab.end"]).contains(link), "the next action belongs to the message, not to the top bar").toBe(false);
    expect(endButton(d["lab.end"])).toBeTruthy();
    fireEvent.click(link);
    await waitFor(() => expect(window.location.pathname).toBe("/labs"));
    expect(calls.some((c) => c.method === "POST" && c.url === `${API}/end`)).toBe(false);
  });

  test("AC2: session ended replaces the panes and offers Back to My labs", async () => {
    backend({ [`GET ${API}/tasks/current`]: err(410, "session_ended") });
    at(lang);
    expect(await screen.findByText(d["lab.ended"])).toBeTruthy();
    expect(screen.queryByTestId("task-panel")).toBeNull();
    expect(document.querySelector("iframe"), "no terminal on an ended session").toBeNull();
    for (const k of ["lab.check", "lab.skip", "lab.restart", "lab.previous"])
      expect(screen.queryByRole("button", { name: d[k] }), k).toBeNull();
    fireEvent.click(screen.getByRole("link", { name: d["lab.to_labs"] }));
    await waitFor(() => expect(window.location.pathname).toBe("/labs"));
  });
});

describe.each(LANGS)("AC3: the splitter direction (%s)", (lang) => {
  const WIDE = { left: 0, right: 1000, width: 1000, top: 0, bottom: 600, height: 600, x: 0, y: 0, toJSON: () => ({}) };
  const now = (el: HTMLElement) => Number(el.getAttribute("aria-valuenow"));
  const rtl = lang === "he";

  test(
    rtl
      ? "AC3: in Hebrew ArrowLeft widens the tasks pane and ArrowRight narrows it"
      : "AC3: in English ArrowRight widens the tasks pane and ArrowLeft narrows it",
    async () => {
      backend();
      await open(lang);
      const sep = screen.getByRole("separator");
      const start = now(sep);
      expect(start).toBeGreaterThanOrEqual(20);
      expect(start).toBeLessThanOrEqual(80);
      const [widen, narrow] = rtl ? ["ArrowLeft", "ArrowRight"] : ["ArrowRight", "ArrowLeft"];
      fireEvent.keyDown(sep, { key: widen });
      const wider = now(sep);
      expect(wider, `${widen} widens the tasks pane`).toBeGreaterThan(start);
      fireEvent.keyDown(sep, { key: narrow });
      fireEvent.keyDown(sep, { key: narrow });
      expect(now(sep), `${narrow} narrows it`).toBeLessThan(start);
    },
  );

  test(
    rtl
      ? "AC3: in Hebrew a pointer drag to the left widens the tasks pane and a drag to the right narrows it"
      : "AC3: in English a pointer drag to the right widens the tasks pane and a drag to the left narrows it",
    async () => {
      backend();
      await open(lang);
      vi.spyOn(Element.prototype, "getBoundingClientRect").mockReturnValue(WIDE as DOMRect);
      const sep = screen.getByRole("separator");
      const start = now(sep);
      // The grab point is the pane edge: tasks pane on the right in rtl (x counts from the right), left in ltr.
      const edge = rtl ? 1000 - start * 10 : start * 10;
      const widen = rtl ? edge - 150 : edge + 150;
      const narrow = rtl ? edge + 150 : edge - 150;
      fireEvent.pointerDown(sep, { clientX: edge, pointerId: 1 });
      fireEvent.pointerMove(sep, { clientX: widen, pointerId: 1 });
      expect(now(sep), "dragging toward the terminal widens the tasks pane").toBeGreaterThan(start);
      fireEvent.pointerMove(sep, { clientX: narrow, pointerId: 1 });
      expect(now(sep), "dragging the other way narrows it").toBeLessThan(start);
      fireEvent.pointerUp(sep, { clientX: narrow, pointerId: 1 });
    },
  );
});
