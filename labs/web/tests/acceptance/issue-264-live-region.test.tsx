/**
 * Exam for #264 (spec 5 and 9; review of #262/#186). The open-tasks count line is read by screen readers
 * reliably only when the live region is already in the page before its text is written.
 *
 * CONTRACT (no test id; the DOM role is the contract). While the lab screen is up and the task is not done,
 * the page holds exactly ONE element with role="status" (no other status region exists then: the done and
 * ended lines are different phases), found with getByRole("status").
 *  - before the open-tasks view opens it is present and its text is empty;
 *  - once the view opens its text is exactly the count line (`lab.open.one`, or "<n> " + `lab.open.many`),
 *    en and he, plural and singular;
 *  - when the view closes (the student opens a task) it is still present, empty again;
 *  - the task buttons are not inside it, and neither is anything else but the count line.
 * Same fake backend and rendering helpers as issue-178-lab-screen-followups.test.tsx.
 */
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";

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
    { n: 1, text: { en: "The file exists", he: "הקובץ קיים" } },
    { n: 2, text: { en: "The file has the right content", he: "לקובץ התוכן הנכון" } },
  ],
};
const LAST = { ...HANDS, id: "04-last", index: 4, total: 4, title: { en: "The last one", he: "האחרונה" } };
const ok = (body: unknown): Reply => ({ status: 200, body });
const err = (status: number, detail: unknown): Reply => ({ status, body: { detail } });
const PASS = (): Reply =>
  ok({
    passed: true, attempts: 1, reason: null,
    checklist: HANDS.checklist.map((c) => ({ n: c.n, text: c.text, passed: true, hint: null })),
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

describe.each(LANGS)("the open-tasks live region (%s)", (lang) => {
  const d = DICTS[lang];
  const title = (i: number) => (lang === "en" ? `Task number ${i}` : `משימה ${i}`);
  const shownTask = (i: number) => ({
    ...HANDS,
    id: `t${i}`, index: i, state: "shown",
    title: { en: `Task number ${i}`, he: `משימה ${i}` },
  });
  const text = (el: HTMLElement) => (el.textContent ?? "").trim();
  const region = () => screen.getByRole("status");
  const start = async (states: string[], extra: Record<string, Handler> = {}) => {
    backend(lastOf({ [`GET ${API}/tasks`]: list(states), ...extra }));
    await open(lang);
  };
  const reachView = async () => {
    fireEvent.click(button(d["lab.check"]));
    fireEvent.click(await within(panel()).findByRole("button", { name: d["lab.next"] }));
    await screen.findByRole("button", { name: title(2) });
  };

  test("before the view opens, an empty live region is already in the page", async () => {
    await start(["passed", "shown", "not_started", "passed"]);
    expect(screen.getAllByRole("status")).toHaveLength(1);
    expect(text(region())).toBe("");
  });

  test("after the view opens, the live region holds exactly the plural count line", async () => {
    await start(["passed", "shown", "not_started", "passed"]);
    const before = region();
    await reachView();
    expect(screen.getAllByRole("status")).toHaveLength(1);
    expect(text(region())).toBe(`2 ${d["lab.open.many"]}`);
    // the same element was written into, not a new one appended (that is the point of the issue)
    expect(region()).toBe(before);
  });

  test("with one open task, the live region holds exactly the singular count line", async () => {
    await start(["passed", "shown", "passed", "passed"]);
    await reachView();
    expect(text(region())).toBe(d["lab.open.one"]);
  });

  test("the task buttons are not inside the live region", async () => {
    await start(["passed", "shown", "not_started", "passed"]);
    await reachView();
    expect(within(region()).queryAllByRole("button")).toHaveLength(0);
    expect(region().contains(screen.getByRole("button", { name: title(2) }))).toBe(false);
    expect(region().contains(screen.getByRole("button", { name: title(3) }))).toBe(false);
  });

  test("after the view closes, the live region is still present and empty again", async () => {
    await start(["passed", "shown", "not_started", "passed"], {
      [`POST ${API}/tasks/t3/show`]: ok(shownTask(3)),
    });
    await reachView();
    fireEvent.click(screen.getByRole("button", { name: title(3) }));
    expect(await screen.findByRole("heading", { name: title(3) })).toBeTruthy();
    await waitFor(() => expect(text(region())).toBe(""));
    expect(screen.getAllByRole("status")).toHaveLength(1);
  });
});
