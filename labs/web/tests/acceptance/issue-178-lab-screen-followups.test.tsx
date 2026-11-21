/**
 * Exam for #178 (follow-ups of the lab screen review, spec 5 and 9, D57 amended). Same fake backend and DOM
 * contract as issue-160-lab-screen-leftovers.test.tsx. The date fix (criterion 1) is in the issue-130 and
 * issue-160 exams.
 *
 * 2. RE-READ ERROR. After a 409 no_next_task the screen re-reads GET .../tasks. When that read fails (HTTP
 *    error or network) the lab stays on screen and a role=alert says the task list could not be loaded,
 *    using the NEW i18n key `lab.error.tasks` (en + he, different from lab.error.load). The alert must not
 *    contain the lab.error.load text, the screen must not claim completion, and End still works.
 * 3. OPEN TASKS ARE LINKS. In the "N tasks still open" view each open task title is a button (accessible
 *    name = the task title in the current language). Clicking it sends POST .../tasks/{id}/show (once) and
 *    opens that task: its title is the task heading and the open-tasks view is gone. "View is gone" covers
 *    both count lines: the plural (`lab.open.many`) and the singular (`lab.open.one`, #186, #264).
 */
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import App from "../../src/App";
// Amended for #349: End lab moved to the lab screen top bar, so it is looked up there (lab-topbar.ts), not in the task panel.
import { endButton } from "./lab-topbar";
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
// Taken from the real clock when the file loads, so no case depends on the date it runs on (#178).
const T0 = Date.now();
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
    { en: "The file exists", he: "הקובץ קיים" },
    { en: "The file has the right content", he: "לקובץ התוכן הנכון" },
  ],
};
const LAST = { ...HANDS, id: "04-last", index: 4, total: 4, title: { en: "The last one", he: "האחרונה" } };
const ok = (body: unknown): Reply => ({ status: 200, body });
const err = (status: number, detail: unknown): Reply => ({ status, body: { detail } });
const PASS = (): Reply =>
  ok({
    passed: true, attempts: 1, reason: null,
    checklist: HANDS.checklist.map((_, i) => ({ n: i + 1, passed: true, hint: null })),
  });

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


const lastOf = (extra: Record<string, Handler>) =>
  base(LAST, {
    [`POST ${API}/tasks/04-last/check`]: PASS(),
    [`POST ${API}/tasks/current/next`]: err(409, "no_next_task"),
    ...extra,
  });
const reachEnd = async (lang: "en" | "he", extra: Record<string, Handler>) => {
  const d = DICTS[lang];
  const calls = backend(lastOf(extra));
  await open(lang);
  fireEvent.click(button(d["lab.check"]));
  fireEvent.click(await within(panel()).findByRole("button", { name: d["lab.next"] }));
  return calls;
};

// ------------------------------------------------------------------ 2. re-read error

describe("the new i18n key lab.error.tasks", () => {
  test("exists in English and Hebrew and says something other than lab.error.load", () => {
    for (const d of [EN, HE]) {
      expect(typeof d["lab.error.tasks"]).toBe("string");
      expect(d["lab.error.tasks"].length).toBeGreaterThan(3);
      expect(d["lab.error.tasks"]).not.toBe(d["lab.error.load"]);
    }
    expect(EN["lab.error.tasks"]).toMatch(/task list/i);
    expect(EN["lab.error.tasks"]).toMatch(/could not|couldn't|cannot|can't|unable/i);
  });
});

describe.each(LANGS)("a failed re-read of the task list after no_next_task (%s)", (lang) => {
  const d = DICTS[lang];
  const cases: [string, Reply][] = [
    ["HTTP 500", err(500, "boom")],
    ["a network failure", "network"],
  ];

  test.each(cases)("%s says the task list could not be loaded, not lab.error.load", async (_n, reply) => {
    const calls = await reachEnd(lang, { [`GET ${API}/tasks`]: reply });
    await waitFor(() => expect(count(calls, "GET", `${API}/tasks`)).toBeGreaterThanOrEqual(1));
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain(d["lab.error.tasks"]);
    expect(alert.textContent).not.toContain(d["lab.error.load"]);
    expect(body()).not.toContain(d["lab.error.load"].toLowerCase());
  });

  test("the lab stays on screen: task, End and no completion text", async () => {
    const calls = await reachEnd(lang, { [`GET ${API}/tasks`]: err(500, "boom") });
    await screen.findByRole("alert");
    expect(screen.queryByTestId("task-panel")).not.toBeNull();
    expect(window.location.pathname).toBe("/labs/lab-1");
    expect(screen.queryByText(d["lab.complete"], { exact: false })).toBeNull();
    expect((endButton(d["lab.end"]) as HTMLButtonElement).disabled).toBe(false);
    expect(count(calls, "POST", `${API}/end`)).toBe(0);
  });
});

// ------------------------------------------------------------------ 3. open tasks are buttons

describe.each(LANGS)("the open-tasks view lets the student open a task (%s)", (lang) => {
  const d = DICTS[lang];
  const title = (i: number) => (lang === "en" ? `Task number ${i}` : `משימה ${i}`);
  const shownTask = (i: number) => ({
    ...HANDS,
    id: `t${i}`, index: i, state: "shown",
    title: { en: `Task number ${i}`, he: `משימה ${i}` },
  });
  const openView = (states: string[], extra: Record<string, Handler> = {}) =>
    reachEnd(lang, { [`GET ${API}/tasks`]: list(states), ...extra });
  // The view: the nearest element around the open-task buttons that also holds the count line (#186: the
  // live region holds the count line only, so the view is no longer the status region).
  const region = async () => {
    const line = await screen.findByText(d["lab.open.many"], { exact: false });
    let el: HTMLElement | null = (await screen.findByRole("button", { name: title(2) })).parentElement;
    while (el && !el.contains(line)) el = el.parentElement;
    return el as HTMLElement;
  };

  test("each open task title is a button, and passed tasks are not listed", async () => {
    await openView(["passed", "shown", "not_started", "passed"]);
    const view = await region();
    expect(within(view).getByRole("button", { name: title(2) })).toBeTruthy();
    expect(within(view).getByRole("button", { name: title(3) })).toBeTruthy();
    expect(within(view).queryByRole("button", { name: title(1) })).toBeNull();
    expect(within(view).queryByRole("button", { name: title(4) })).toBeNull();
  });

  test("clicking an open task POSTs .../tasks/{id}/show once and opens that task", async () => {
    const calls = await openView(["passed", "shown", "not_started", "passed"], {
      [`POST ${API}/tasks/t3/show`]: ok(shownTask(3)),
    });
    const view = await region();
    fireEvent.click(within(view).getByRole("button", { name: title(3) }));
    await waitFor(() => expect(count(calls, "POST", `${API}/tasks/t3/show`)).toBe(1));
    expect(await screen.findByRole("heading", { name: title(3) })).toBeTruthy();
    await waitFor(() => expect(screen.queryByText(d["lab.open.many"], { exact: false })).toBeNull());
    expect(screen.queryByText(d["lab.complete"], { exact: false })).toBeNull();
    expect(count(calls, "POST", `${API}/tasks/t2/show`)).toBe(0);
    expect(count(calls, "POST", `${API}/tasks/current/previous`)).toBe(0);
  });

  test("with one open task, clicking it opens it and the singular count line is gone too (#186)", async () => {
    const calls = await openView(["passed", "shown", "passed", "passed"], {
      [`POST ${API}/tasks/t2/show`]: ok(shownTask(2)),
    });
    await screen.findByText(d["lab.open.one"], { exact: false });
    fireEvent.click(await screen.findByRole("button", { name: title(2) }));
    await waitFor(() => expect(count(calls, "POST", `${API}/tasks/t2/show`)).toBe(1));
    expect(await screen.findByRole("heading", { name: title(2) })).toBeTruthy();
    await waitFor(() => expect(screen.queryByText(d["lab.open.one"], { exact: false })).toBeNull());
    expect(screen.queryByText(d["lab.open.many"], { exact: false })).toBeNull();
    expect(screen.queryByText(d["lab.complete"], { exact: false })).toBeNull();
  });

  test("a failed show keeps the open-tasks view and says so in an alert", async () => {
    await openView(["passed", "shown", "not_started", "passed"], {
      [`POST ${API}/tasks/t2/show`]: err(500, "boom"),
    });
    const view = await region();
    fireEvent.click(within(view).getByRole("button", { name: title(2) }));
    await screen.findByRole("alert");
    expect(screen.queryByText(d["lab.complete"], { exact: false })).toBeNull();
    expect(screen.queryByRole("button", { name: title(2) })).not.toBeNull();
  });
});
