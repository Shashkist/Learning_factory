/**
 * Exam for #160 (leftovers of the lab screen review, D57 amended for #160): what a student sees and what
 * the screen sends. Same fake backend and DOM contract as issue-157-lab-screen-followups.test.tsx.
 *
 * 1. COMPLETE ONLY WHEN COMPLETE. A 409 no_next_task (after Next, or Skip on a task shown as passed)
 *    makes the screen re-read GET .../tasks. Every task "passed": lab.complete. Otherwise the screen says
 *    "N tasks still open" (N = tasks not passed; English text pinned as /N tasks still open/) and never
 *    "well done". If the re-read itself fails, the screen must not claim completion either.
 * 2. 409 task_changed (another tab moved the task) on Skip/Next: the screen re-fetches GET tasks/current
 *    and shows that task, with no "try again" alert and no stale task.
 * 3. END LAB. Repeated clicks while POST /end is pending send exactly one request. A failed End shows its
 *    own alert (not lab.error.load), stays on the screen, and End can be tried again.
 * 4. CLOCK SKEW is per screen: a second screen whose answers carry no server time must not inherit the
 *    skew learned by an earlier screen.
 *
 * New wording the implementation must add (t() has no interpolation, so the screen builds the count text):
 *   open tasks: English text matching /(\d+) tasks? still open/i; Hebrew: any status/alert text containing N.
 *   lab.error.end_failed: English matching /could not be ended|ending .* failed|failed to end/i.
 */
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import App from "../../src/App";
import en from "../../src/i18n/en.json";
import he from "../../src/i18n/he.json";

type Dict = Record<string, string>;
const EN = en as Dict;
const HE = he as Dict;
const DICTS: Record<"en" | "he", Dict> = { en: EN, he: HE };
const LANGS = ["en", "he"] as const;

type Reply = { status: number; body?: unknown } | "network";
type Handler = Reply | Reply[] | (() => Reply | Promise<Reply>);
type Calls = { url: string; method: string; body?: string }[];

const SID = "sess-1";
const API = `/api/sessions/${SID}`;
const T0 = Date.UTC(2026, 10, 16, 9, 0, 0);
const HOUR = 3_600_000;
const STUDENT: Reply = { status: 200, body: { kind: "student", name: "Dana Test" } };
const expiresIn = (ms: number) => new Date(T0 + ms).toISOString();
const session = (extra: object = {}): Reply => ({
  status: 200,
  body: { id: SID, lab_id: "lab-1", status: "running", expires_at: expiresIn(55 * 60_000), ...extra },
});

const HANDS = {
  id: "02-make-file", index: 2, total: 4, kind: "hands-on", state: "shown",
  title: { en: "Make a file", he: "יוצרים קובץ" },
  text: {
    en: "# Make a file\n\nCreate the notes file in your home folder.\n",
    he: "# יוצרים קובץ\n\nצרו את קובץ ההערות בתיקיית הבית.\n",
  },
  checklist: [
    { n: 1, text: { en: "The file exists", he: "הקובץ קיים" } },
    { n: 2, text: { en: "The file has the right content", he: "לקובץ התוכן הנכון" } },
  ],
};
const LAST = { ...HANDS, id: "04-last", index: 4, total: 4, title: { en: "The last one", he: "האחרונה" } };
const QUIZ = {
  id: "03-quiz", index: 3, total: 4, kind: "quiz", state: "shown",
  title: { en: "Which command lists files?", he: "איזו פקודה מציגה קבצים?" },
  question: { en: "Which command lists files?", he: "איזו פקודה מציגה קבצים?" },
  options: [
    { en: "cd", he: "cd" },
    { en: "ls", he: "ls" },
  ],
};
const ok = (body: unknown): Reply => ({ status: 200, body });
const err = (status: number, detail: unknown): Reply => ({ status, body: { detail } });
const PASS = (): Reply =>
  ok({
    passed: true, attempts: 1, reason: null,
    checklist: HANDS.checklist.map((c) => ({ n: c.n, text: c.text, passed: true, hint: null })),
  });
const hang = () => new Promise<Reply>(() => {});

const entry = (i: number, state: string) => ({
  id: `t${i}`, index: i, kind: "hands-on", state,
  title: { en: `Task number ${i}`, he: `משימה ${i}` },
});
const list = (states: string[]): Reply =>
  ok({ lab_id: "lab-1", current: null, tasks: states.map((s, i) => entry(i + 1, s)) });

/** Routes are "METHOD path". An array is a queue (the last entry repeats). Every reply carries a `Date`
 * header showing the server clock = the (possibly faked) browser clock minus `skew` ms, unless `noTime`. */
function backend(routes: Record<string, Handler>, opts: { skew?: number; noTime?: boolean } = {}) {
  const skew = opts.skew ?? 0;
  const calls: Calls = [];
  const all: Record<string, Handler> = {
    "GET /api/auth/me": STUDENT,
    "GET /api/labs": ok([]),
    [`POST ${API}/heartbeat`]: { status: 204 },
    [`POST ${API}/end`]: { status: 204 },
    ...routes,
  };
  const queues = new Map<string, Reply[]>();
  vi.spyOn(globalThis, "fetch").mockImplementation((async (url: RequestInfo | URL, init?: RequestInit) => {
    const u = String(url);
    const method = (init?.method ?? "GET").toUpperCase();
    calls.push({ url: u, method, body: typeof init?.body === "string" ? init.body : undefined });
    const key = `${method} ${u}`;
    const h = all[key];
    let r: Reply = { status: 404, body: { detail: "Not Found" } };
    if (typeof h === "function") r = await h();
    else if (Array.isArray(h)) {
      if (!queues.has(key)) queues.set(key, [...h]);
      const q = queues.get(key) as Reply[];
      r = q.length > 1 ? (q.shift() as Reply) : q[0];
    } else if (h !== undefined) r = h;
    if (r === "network") throw new TypeError("offline");
    const serverNow = new Date(Date.now() - skew);
    let body = r.body;
    if (!opts.noTime && key === "GET /api/sessions/current" && r.status === 200 && body && typeof body === "object")
      body = { now: serverNow.toISOString(), ...(body as object) };
    return {
      ok: r.status >= 200 && r.status < 300,
      status: r.status,
      headers: { get: (n: string) => (!opts.noTime && n.toLowerCase() === "date" ? serverNow.toUTCString() : null) },
      json: async () => body ?? {},
      text: async () => JSON.stringify(body ?? {}),
    } as unknown as Response;
  }) as typeof fetch);
  return calls;
}

const base = (task: object, extra: Record<string, Handler> = {}) => ({
  "GET /api/sessions/current": session(),
  [`GET ${API}/tasks/current`]: ok(task),
  ...extra,
});

function at(path: string, lang?: "en" | "he") {
  window.history.replaceState({}, "", path);
  if (lang) window.localStorage.setItem("lf-lang", lang);
  return render(<App />);
}
const open = async (lang: "en" | "he") => {
  const view = at("/labs/lab-1", lang);
  await screen.findByTestId("task-panel");
  return view;
};
const panel = () => screen.getByTestId("task-panel");
const button = (name: string) => within(panel()).getByRole("button", { name });
const count = (calls: Calls, method: string, path: string) =>
  calls.filter((c) => c.method === method && c.url === path).length;
const body = () => (document.body.textContent ?? "").toLowerCase();

beforeEach(() => {
  window.localStorage.clear();
  window.history.replaceState({}, "", "/");
});
afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

async function settle() {
  for (let i = 0; i < 12; i++)
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
}
const secs = (el: HTMLElement) => {
  const m = /(\d+):(\d\d)/.exec(el.textContent ?? "");
  return m ? Number(m[1]) * 60 + Number(m[2]) : NaN;
};

// ------------------------------------------------------------------ 1. complete only when complete

describe.each(LANGS)("lab complete only when every task is passed (%s)", (lang) => {
  const d = DICTS[lang];
  /** On the last task: Check (passes) then Next. */
  const nextOnPassedLast = async (extra: Record<string, Handler>) => {
    const calls = backend(
      base(LAST, {
        [`POST ${API}/tasks/04-last/check`]: PASS(),
        [`POST ${API}/tasks/current/next`]: err(409, "no_next_task"),
        ...extra,
      }),
    );
    await open(lang);
    fireEvent.click(button(d["lab.check"]));
    fireEvent.click(await within(panel()).findByRole("button", { name: d["lab.next"] }));
    return calls;
  };
  /** Check (passes) then Skip. */
  const skipOnPassedLast = async (extra: Record<string, Handler>) => {
    const calls = backend(
      base(LAST, {
        [`POST ${API}/tasks/04-last/check`]: PASS(),
        [`POST ${API}/tasks/current/skip`]: err(409, "no_next_task"),
        ...extra,
      }),
    );
    await open(lang);
    fireEvent.click(button(d["lab.check"]));
    await within(panel()).findByRole("button", { name: d["lab.next"] });
    fireEvent.click(button(d["lab.skip"]));
    return calls;
  };
  const notComplete = async (calls: Calls, open: number) => {
    await waitFor(() => expect(count(calls, "GET", `${API}/tasks`)).toBeGreaterThanOrEqual(1));
    if (lang === "en")
      expect(await screen.findByText(new RegExp(`\\b${open} tasks? still open`, "i"), { exact: false })).toBeTruthy();
    else
      await waitFor(() =>
        expect(
          screen.queryAllByRole("status").concat(screen.queryAllByRole("alert")).some((e) => (e.textContent ?? "").includes(String(open))),
        ).toBe(true),
      );
    expect(screen.queryByText(d["lab.complete"], { exact: false })).toBeNull();
    expect(body()).not.toContain("well done");
    expect(body()).not.toContain(d["lab.complete"].toLowerCase());
  };

  test("Next on the last task with every task passed re-reads the task list and says lab complete", async () => {
    const calls = await nextOnPassedLast({ [`GET ${API}/tasks`]: list(["passed", "passed", "passed", "passed"]) });
    expect(await screen.findByText(d["lab.complete"], { exact: false })).toBeTruthy();
    expect(count(calls, "GET", `${API}/tasks`)).toBeGreaterThanOrEqual(1);
  });

  test("Next on the last task while 3 tasks are not passed says 3 tasks still open and not well done", async () => {
    const calls = await nextOnPassedLast({
      [`GET ${API}/tasks`]: list(["passed", "shown", "not_started", "not_started"]),
    });
    await notComplete(calls, 3);
  });

  test("Next on the last task with exactly 1 task open says 1 task still open", async () => {
    const calls = await nextOnPassedLast({
      [`GET ${API}/tasks`]: list(["not_started", "passed", "passed", "passed"]),
    });
    await notComplete(calls, 1);
  });

  test("the open-task answer shows the open tasks (title) so the student can go back to them", async () => {
    await nextOnPassedLast({ [`GET ${API}/tasks`]: list(["passed", "shown", "passed", "passed"]) });
    expect(await screen.findByText(new RegExp(lang === "en" ? "Task number 2" : "משימה 2"))).toBeTruthy();
    expect(screen.queryByText(new RegExp(lang === "en" ? "Task number 1" : "משימה 1"))).toBeNull();
  });

  test("Skip on a passed last task with every task passed says lab complete", async () => {
    await skipOnPassedLast({ [`GET ${API}/tasks`]: list(["passed", "passed", "passed", "passed"]) });
    expect(await screen.findByText(d["lab.complete"], { exact: false })).toBeTruthy();
  });

  test("Skip on a passed last task while 2 tasks are not passed says 2 tasks still open and not well done", async () => {
    const calls = await skipOnPassedLast({
      [`GET ${API}/tasks`]: list(["passed", "not_started", "shown", "passed"]),
    });
    await notComplete(calls, 2);
  });

  test("when the task list cannot be re-read the screen does not claim the lab is complete", async () => {
    const calls = await nextOnPassedLast({ [`GET ${API}/tasks`]: err(500, "boom") });
    await waitFor(() => expect(count(calls, "GET", `${API}/tasks`)).toBeGreaterThanOrEqual(1));
    await act(async () => {});
    expect(screen.queryByText(d["lab.complete"], { exact: false })).toBeNull();
    expect(body()).not.toContain("well done");
  });
});

// ------------------------------------------------------------------ 2. task_changed

describe.each(LANGS)("409 task_changed re-fetches the current task (%s)", (lang) => {
  const d = DICTS[lang];

  test("Skip answering 409 task_changed shows the task now current on the server, not the old one", async () => {
    const calls = backend(
      base(HANDS, {
        [`GET ${API}/tasks/current`]: [ok(HANDS), ok(QUIZ)],
        [`POST ${API}/tasks/current/skip`]: err(409, "task_changed"),
      }),
    );
    await open(lang);
    fireEvent.click(button(d["lab.skip"]));
    expect(await within(panel()).findByRole("radio", { name: "ls" })).toBeTruthy();
    expect(within(panel()).queryByRole("heading", { name: HANDS.title[lang] })).toBeNull();
    expect(count(calls, "GET", `${API}/tasks/current`)).toBe(2);
    expect(count(calls, "POST", `${API}/tasks/current/skip`)).toBe(1); // no blind retry
    expect(screen.queryByText(d["lab.error.task_changed"])).toBeNull();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  test("Next answering 409 task_changed shows the task now current on the server and no retry invitation", async () => {
    const calls = backend(
      base(HANDS, {
        [`GET ${API}/tasks/current`]: [ok(HANDS), ok(QUIZ)],
        [`POST ${API}/tasks/02-make-file/check`]: PASS(),
        [`POST ${API}/tasks/current/next`]: err(409, "task_changed"),
      }),
    );
    await open(lang);
    fireEvent.click(button(d["lab.check"]));
    fireEvent.click(await within(panel()).findByRole("button", { name: d["lab.next"] }));
    expect(await within(panel()).findByRole("radio", { name: "ls" })).toBeTruthy();
    expect(within(panel()).queryByRole("heading", { name: HANDS.title[lang] })).toBeNull();
    expect(count(calls, "GET", `${API}/tasks/current`)).toBe(2);
    expect(count(calls, "POST", `${API}/tasks/current/next`)).toBe(1);
    expect(screen.queryByText(d["lab.error.task_changed"])).toBeNull();
  });

  test("the re-fetched task is a fresh task: the old passed state and Next button are gone", async () => {
    backend(
      base(HANDS, {
        [`GET ${API}/tasks/current`]: [ok(HANDS), ok({ ...LAST, state: "shown" })],
        [`POST ${API}/tasks/02-make-file/check`]: PASS(),
        [`POST ${API}/tasks/current/next`]: err(409, "task_changed"),
      }),
    );
    await open(lang);
    fireEvent.click(button(d["lab.check"]));
    fireEvent.click(await within(panel()).findByRole("button", { name: d["lab.next"] }));
    expect(await within(panel()).findByRole("heading", { name: LAST.title[lang] })).toBeTruthy();
    expect(within(panel()).queryByRole("button", { name: d["lab.next"] })).toBeNull();
    expect(within(panel()).getByRole("button", { name: d["lab.check"] })).toBeTruthy();
  });
});

// ------------------------------------------------------------------ 3. End lab

describe.each(LANGS)("End lab (%s)", (lang) => {
  const d = DICTS[lang];

  test("clicking End lab three times while POST /end is pending sends exactly one request", async () => {
    const calls = backend(base(HANDS, { [`POST ${API}/end`]: hang }));
    await open(lang);
    const end = button(d["lab.end"]);
    fireEvent.click(end);
    fireEvent.click(end);
    fireEvent.click(end);
    await act(async () => {});
    await waitFor(() => expect(count(calls, "POST", `${API}/end`)).toBe(1));
    fireEvent.click(end);
    await act(async () => {});
    expect(count(calls, "POST", `${API}/end`)).toBe(1);
  });

  test("a failed End (500) shows its own message, not 'could not be loaded', and stays on the lab", async () => {
    backend(base(HANDS, { [`POST ${API}/end`]: err(500, "boom") }));
    await open(lang);
    fireEvent.click(button(d["lab.end"]));
    const alert = await screen.findByRole("alert");
    const text = (alert.textContent ?? "").trim();
    expect(text.length).toBeGreaterThan(0);
    expect(text).not.toContain(d["lab.error.load"]);
    expect(text).not.toContain(d["lab.error.task_changed"]);
    if (lang === "en") expect(text).toMatch(/could not be ended|ending .* failed|failed to end/i);
    expect(window.location.pathname).toBe("/labs/lab-1");
    expect(screen.queryByTestId("task-panel")).not.toBeNull();
  });

  test("a failed End (network error) shows its own message too", async () => {
    backend(base(HANDS, { [`POST ${API}/end`]: "network" }));
    await open(lang);
    fireEvent.click(button(d["lab.end"]));
    const alert = await screen.findByRole("alert");
    expect((alert.textContent ?? "").trim().length).toBeGreaterThan(0);
    expect(alert.textContent).not.toContain(d["lab.error.load"]);
    expect(window.location.pathname).toBe("/labs/lab-1");
  });

  test("after a failed End the button works again and the second try ends the lab", async () => {
    const calls = backend(base(HANDS, { [`POST ${API}/end`]: [err(500, "boom"), { status: 204 }] }));
    await open(lang);
    fireEvent.click(button(d["lab.end"]));
    await screen.findByRole("alert");
    expect((button(d["lab.end"]) as HTMLButtonElement).disabled).toBe(false);
    fireEvent.click(button(d["lab.end"]));
    await waitFor(() => expect(count(calls, "POST", `${API}/end`)).toBe(2));
    await waitFor(() => expect(window.location.pathname).toBe("/labs"));
  });
});

// ------------------------------------------------------------------ 4. skew per screen

describe("the clock skew belongs to one screen", () => {
  test("a second lab screen whose answers carry no server time does not inherit the skew of the first", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval", "setTimeout", "clearTimeout", "Date"] });
    // screen one: the browser clock is 2 hours ahead of the server; it learns a skew of about -2 h
    vi.setSystemTime(T0 + 2 * HOUR);
    backend(base(HANDS), { skew: 2 * HOUR });
    const first = at("/labs/lab-1", "en");
    await settle();
    expect(screen.queryByTestId("task-panel")).not.toBeNull();
    first.unmount();
    vi.restoreAllMocks();

    // screen two: honest browser clock, the server sends neither a Date header nor `now`
    vi.setSystemTime(T0);
    backend(base(HANDS), { noTime: true });
    at("/labs/lab-1", "en");
    await settle();
    expect(screen.queryByTestId("task-panel")).not.toBeNull();
    const left = secs(screen.getByRole("timer"));
    expect(left).toBeGreaterThanOrEqual(54 * 60); // 55 min on the honest clock, not 55 min + 2 h
    expect(left).toBeLessThanOrEqual(55 * 60);
  });
});
