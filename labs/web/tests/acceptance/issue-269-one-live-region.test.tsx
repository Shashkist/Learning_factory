/**
 * Exam for #269 (spec 5 and 9; review of #268). One live region serves the lab screen while the lab runs.
 *
 * CONTRACT (the DOM role is the contract, no test id; extends #264).
 *  - exactly ONE role="status" on the lab screen at any time, found with getByRole("status");
 *  - it is present before any text; it holds the open-tasks count line while that view is open; when the lab
 *    is complete it holds the complete line (lab.complete) and it is the SAME element (identity) that held
 *    the empty / count text before; otherwise it is empty;
 *  - no blank block: while empty the region takes no space. jsdom does not compute stylesheet rules, so the
 *    contract is observable as an INLINE style: the empty region's inline margin-top and margin-bottom are
 *    0 (e.g. style={{ margin: 0 }}), set on the element itself.
 * Same fake backend and helpers as issue-264-live-region.test.tsx.
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

describe.each(LANGS)("one live region for the lab screen (%s)", (lang) => {
  const d = DICTS[lang];
  const title = (i: number) => (lang === "en" ? `Task number ${i}` : `משימה ${i}`);
  const text = (el: HTMLElement) => (el.textContent ?? "").trim();
  const region = () => screen.getByRole("status");
  const start = async (states: string[], extra: Record<string, Handler> = {}) => {
    backend(lastOf({ [`GET ${API}/tasks`]: list(states), ...extra }));
    await open(lang);
  };
  const toComplete = async () => {
    fireEvent.click(button(d["lab.check"]));
    fireEvent.click(await within(panel()).findByRole("button", { name: d["lab.next"] }));
    await waitFor(() => expect(text(region())).toContain(d["lab.complete"]));
  };

  test("from a running task to complete, the same region element receives the complete line", async () => {
    await start(["passed", "passed", "passed", "passed"]);
    const before = region();
    expect(text(before)).toBe("");
    await toComplete();
    expect(screen.getAllByRole("status")).toHaveLength(1);
    expect(region()).toBe(before);
    expect(text(region())).toContain(d["lab.complete"]);
  });

  /** Last task passed, Next answers no_next_task, the list shows two open tasks; the second list answer is
   * all passed. Opening task 2 from the list gives a hands-on task that passes on Check. */
  const viaOpenTasks = async (next?: Reply[]) => {
    backend(
      lastOf({
        [`GET ${API}/tasks`]: [
          list(["passed", "shown", "not_started", "passed"]),
          list(["passed", "passed", "passed", "passed"]),
        ],
        [`POST ${API}/tasks/t2/show`]: ok(HANDS),
        [`POST ${API}/tasks/02-make-file/check`]: PASS(),
        [`POST ${API}/tasks/03-third/check`]: PASS(),
        ...(next ? { [`POST ${API}/tasks/current/next`]: next } : {}),
      }),
    );
    await open(lang);
    const before = region();
    expect(text(before)).toBe("");
    fireEvent.click(button(d["lab.check"]));
    fireEvent.click(await within(panel()).findByRole("button", { name: d["lab.next"] }));
    await screen.findByRole("button", { name: title(2) });
    expect(text(region())).toContain(d["lab.open.many"]);
    return before;
  };

  /** The walk a student really takes (review of #276): Next from task 2 gives task 3, from task 3 the already
   * passed task 4, and only Next on the last task answers no_next_task (the server's rule in app/tasks.py). */
  const THIRD = { ...HANDS, id: "03-third", index: 3, title: { en: "The third one", he: "השלישית" } };

  test("from the open-tasks view to complete, still one region, the same element", async () => {
    const before = await viaOpenTasks([
      err(409, "no_next_task"),
      ok(THIRD),
      ok({ ...LAST, state: "passed" }),
      err(409, "no_next_task"),
    ]);
    expect(region()).toBe(before);
    fireEvent.click(screen.getByRole("button", { name: title(2) }));
    await within(panel()).findByRole("heading", { level: 2, name: HANDS.title[lang] });
    expect(region()).toBe(before);
    fireEvent.click(button(d["lab.check"]));
    fireEvent.click(await within(panel()).findByRole("button", { name: d["lab.next"] }));
    await within(panel()).findByRole("heading", { level: 2, name: THIRD.title[lang] });
    expect(region()).toBe(before);
    expect(text(before)).toBe("");
    fireEvent.click(button(d["lab.check"]));
    fireEvent.click(await within(panel()).findByRole("button", { name: d["lab.next"] }));
    await within(panel()).findByRole("heading", { level: 2, name: LAST.title[lang] });
    expect(region()).toBe(before);
    fireEvent.click(await within(panel()).findByRole("button", { name: d["lab.next"] }));
    await waitFor(() => expect(text(region())).toContain(d["lab.complete"]));
    expect(screen.getAllByRole("status")).toHaveLength(1);
    expect(region()).toBe(before);
    expect(text(before)).toContain(d["lab.complete"]);
  });

  test("completing from the open-tasks view leaves no open-task buttons, one status and the way back", async () => {
    await viaOpenTasks();
    // stale-list scenario (#274): the other tasks got passed elsewhere; Next from the open view completes the lab
    fireEvent.click(within(panel()).getByRole("button", { name: d["lab.next"] }));
    await waitFor(() => expect(text(region())).toContain(d["lab.complete"]));
    for (const i of [1, 2, 3, 4]) expect(screen.queryByRole("button", { name: title(i) })).toBeNull();
    expect(screen.getAllByRole("status")).toHaveLength(1);
    expect(text(region())).not.toContain(d["lab.open.many"]);
    expect(screen.getByRole("link", { name: d["lab.to_labs"] })).toBeTruthy();
  });

  test("at complete there is exactly one status element and the open-tasks line is gone", async () => {
    await start(["passed", "passed", "passed", "passed"]);
    await toComplete();
    expect(screen.getAllByRole("status")).toHaveLength(1);
    expect(text(region())).not.toContain(d["lab.open.many"]);
    expect(text(region())).not.toContain(d["lab.open.one"]);
  });

  test("an empty region takes no space: margin 0 and nothing blank above the task heading", async () => {
    await start(["passed", "shown", "not_started", "passed"]);
    const r = region();
    expect(text(r)).toBe("");
    // jsdom's own default sheet reports "0" for a <p>, so read the element's INLINE style: it must say 0.
    expect([r.style.marginTop, r.style.marginBottom].map((m) => parseFloat(m))).toEqual([0, 0]);
    // the only thing between the tab strip and the heading is that empty region: no other blank paragraph
    const h = within(panel()).getByRole("heading", { level: 2 });
    const blanks = Array.from(h.parentElement?.children ?? []).filter(
      (el) => el !== h && text(el as HTMLElement) === "" && el.tagName === "P" && el !== r,
    );
    expect(blanks.map((e) => e.outerHTML)).toEqual([]);
  });
});
