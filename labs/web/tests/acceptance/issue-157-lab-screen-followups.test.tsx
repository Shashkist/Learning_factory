/**
 * Exam for #157 (follow-ups of the lab screen #130, D57): what a student sees and what the screen sends.
 *
 * Same fake backend and DOM contract as issue-130-lab-screen.test.tsx. Additions:
 *
 * 1. CLOCK SKEW. The server tells the browser what time it is, either in the `Date` response header or
 *    as `now` (ISO string) in the GET /api/sessions/current body. The exam sends BOTH (they agree), so the
 *    implementation may use either. The browser clock is then set far ahead of the server. Pinned:
 *      - the timer shows the time left on the SERVER clock, counting down;
 *      - the lab is never ended by the browser's own timer: at 0:00 the terminal stays and heartbeats go on;
 *      - the lab ends (lab.ended, no terminal, no more heartbeats) only on the server's answer: 404 or 410
 *        from the session, task-action or heartbeat calls.
 * 2. COMPLETE. lab.complete ("Lab complete. Well done.") is shown after Next on the last task answers
 *    409 no_next_task. Previous / Skip / Restart move the task, so it must go away. Skip on a last task
 *    that was not passed answers 409 no_next_task too; the server records no completion, so the screen
 *    must not say lab.complete, and the task stays usable.
 * 3. LOADING. While the first open waits for task 1 (GET tasks/current or POST .../show hangs), the screen
 *    shows lab.preparing (existing key) instead of nothing, and the task panel appears when it arrives.
 * 4. NITS.
 *      - A failed Skip/Next (502 {detail:{code:"setup_failed"}}, 504 {detail:{code:"setup_timeout"}})
 *        shows an alert that is NOT the generic lab.error.load text; the old task and
 *        all buttons stay usable and a retry works.
 *      - While a task action hangs, End lab is still enabled and ends the lab.
 *      - Images in task Markdown load only from the page's own origin (cross-origin and protocol-relative
 *        images are not rendered as <img>); same-origin relative images still are.
 *      - The Task / Solution tabs follow the ARIA tabs pattern: ArrowRight / ArrowLeft move between them
 *        (selection follows focus, wrapping around). Asserted in English (left-to-right) only.
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

/** Routes are "METHOD path". An array is a queue (the last entry repeats). Every reply carries a `Date`
 * header showing the SERVER clock = the (possibly faked) browser clock minus `skew` ms. */
function backend(routes: Record<string, Handler>, opts: { skew?: number } = {}) {
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
    if (key === "GET /api/sessions/current" && r.status === 200 && body && typeof body === "object")
      body = { now: serverNow.toISOString(), ...(body as object) };
    return {
      ok: r.status >= 200 && r.status < 300,
      status: r.status,
      headers: { get: (n: string) => (n.toLowerCase() === "date" ? serverNow.toUTCString() : null) },
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
/** Fake timers before render. `browserAhead` moves the browser clock ahead of the server clock (T0). */
async function openFake(browserAhead = 0) {
  vi.useFakeTimers({ toFake: ["setInterval", "clearInterval", "setTimeout", "clearTimeout", "Date"] });
  vi.setSystemTime(T0 + browserAhead);
  const view = at("/labs/lab-1", "en");
  await settle();
  return view;
}
const advance = (ms: number) =>
  act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
const secs = (el: HTMLElement) => {
  const m = /(\d+):(\d\d)/.exec(el.textContent ?? "");
  return m ? Number(m[1]) * 60 + Number(m[2]) : NaN;
};
const beats = (calls: Calls) => count(calls, "POST", `${API}/heartbeat`);
const frames = () => document.querySelectorAll("iframe").length;

// ------------------------------------------------------------------ 1. clock skew

describe("a wrong PC clock must not end a lab", () => {
  test("a browser clock 2 hours ahead of the server still shows the lab, the terminal and about 55 minutes left", async () => {
    backend(base(HANDS), { skew: 2 * HOUR });
    await openFake(2 * HOUR);
    expect(screen.queryByTestId("task-panel")).not.toBeNull();
    expect(screen.queryByText(EN["lab.ended"])).toBeNull();
    expect(frames()).toBe(1);
    const first = secs(screen.getByRole("timer"));
    expect(first).toBeGreaterThanOrEqual(54 * 60);
    expect(first).toBeLessThanOrEqual(55 * 60);
  });

  test("with a browser clock 2 hours ahead the timer counts down and the lab stays open", async () => {
    backend(base(HANDS), { skew: 2 * HOUR });
    await openFake(2 * HOUR);
    const first = secs(screen.getByRole("timer"));
    await advance(5 * 60_000);
    expect(screen.queryByText(EN["lab.ended"])).toBeNull();
    expect(frames()).toBe(1);
    const later = secs(screen.getByRole("timer"));
    expect(first - later).toBeGreaterThanOrEqual(299);
    expect(first - later).toBeLessThanOrEqual(301);
  });

  test("with a browser clock 2 hours ahead the heartbeat keeps going every 15 seconds", async () => {
    const calls = backend(base(HANDS), { skew: 2 * HOUR });
    await openFake(2 * HOUR);
    const before = beats(calls);
    await advance(60_000);
    expect(beats(calls) - before).toBeGreaterThanOrEqual(3);
    expect(screen.queryByText(EN["lab.ended"])).toBeNull();
  });

  test("a browser clock far ahead and a session that really has 30 s left: the screen does not end it by itself", async () => {
    const calls = backend(
      base(HANDS, { "GET /api/sessions/current": session({ expires_at: expiresIn(30_000) }) }),
      { skew: 2 * HOUR },
    );
    await openFake(2 * HOUR);
    expect(screen.queryByText(EN["lab.ended"])).toBeNull();
    expect(secs(screen.getByRole("timer"))).toBeGreaterThanOrEqual(28);
    await advance(20_000);
    expect(screen.queryByText(EN["lab.ended"])).toBeNull();
    expect(frames()).toBe(1);
    expect(beats(calls)).toBeGreaterThanOrEqual(1);
  });

  test("at 0:00 on an honest clock the terminal and the heartbeat stay until the server answers", async () => {
    const calls = backend(
      base(HANDS, { "GET /api/sessions/current": session({ expires_at: expiresIn(30_000) }) }),
    );
    await openFake();
    await advance(90_000);
    expect(secs(screen.getByRole("timer"))).toBe(0);
    expect(screen.queryByText(EN["lab.ended"])).toBeNull();
    expect(frames()).toBe(1);
    expect(screen.queryByTestId("task-panel")).not.toBeNull();
    const frozen = beats(calls);
    await advance(30_000);
    expect(beats(calls)).toBeGreaterThan(frozen);
  });

  test.each([404, 410])("a heartbeat answering %i ends the lab: message, no terminal, no more heartbeats", async (status) => {
    const calls = backend(
      base(HANDS, { [`POST ${API}/heartbeat`]: [{ status: 204 }, err(status, "session_ended")] }),
    );
    await openFake();
    await advance(45_000);
    expect(screen.queryByText(EN["lab.ended"])).not.toBeNull();
    expect(frames()).toBe(0);
    const frozen = beats(calls);
    await advance(120_000);
    expect(beats(calls)).toBe(frozen);
  });

  test.each([404, 410])("a heartbeat answering %i with no detail text still ends the lab", async (status) => {
    backend(base(HANDS, { [`POST ${API}/heartbeat`]: { status, body: {} } }));
    await openFake();
    await advance(20_000);
    expect(screen.queryByText(EN["lab.ended"])).not.toBeNull();
    expect(frames()).toBe(0);
  });

  test("a heartbeat answering 500 or a network error does not end the lab", async () => {
    const calls = backend(base(HANDS, { [`POST ${API}/heartbeat`]: [err(500, "x"), "network", err(502, "x")] }));
    await openFake();
    await advance(50_000);
    expect(screen.queryByText(EN["lab.ended"])).toBeNull();
    expect(frames()).toBe(1);
    expect(beats(calls)).toBeGreaterThanOrEqual(3);
  });

  test.each([404, 410])("a task action answering %i ends the lab", async (status) => {
    backend(base(HANDS, { [`POST ${API}/tasks/current/skip`]: err(status, "session_ended") }));
    await openFake();
    fireEvent.click(button(EN["lab.skip"]));
    await settle();
    expect(screen.queryByText(EN["lab.ended"])).not.toBeNull();
    expect(frames()).toBe(0);
  });

  test("the task call on load answering 410 ends the lab", async () => {
    backend(base(HANDS, { [`GET ${API}/tasks/current`]: err(410, "session_ended") }));
    at("/labs/lab-1", "en");
    expect(await screen.findByText(EN["lab.ended"])).toBeTruthy();
    expect(frames()).toBe(0);
  });

  test("the session call on load answering 410 ends the lab and starts no heartbeat", async () => {
    const calls = backend({ "GET /api/sessions/current": err(410, "session_ended") });
    at("/labs/lab-1", "en");
    await waitFor(() => expect(screen.queryByRole("alert") ?? screen.queryByText(EN["lab.ended"])).not.toBeNull());
    expect(frames()).toBe(0);
    expect(calls.some((c) => c.url.endsWith("/heartbeat"))).toBe(false);
  });
});

// ------------------------------------------------------------------ 2. stale "complete"

describe.each(LANGS)("lab complete in %s", (lang) => {
  const d = DICTS[lang];
  const completed = async (extra: Record<string, Handler>) => {
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
    expect(await screen.findByText(d["lab.complete"], { exact: false })).toBeTruthy();
    return calls;
  };

  test("Next on a passed last task shows lab complete (unchanged)", async () => {
    await completed({});
  });

  test("Previous after lab complete brings the task back and removes lab complete", async () => {
    await completed({ [`POST ${API}/tasks/current/previous`]: ok(HANDS) });
    fireEvent.click(button(d["lab.previous"]));
    expect(await within(panel()).findByRole("heading", { name: HANDS.title[lang] })).toBeTruthy();
    expect(screen.queryByText(d["lab.complete"], { exact: false })).toBeNull();
  });

  test("Skip after lab complete brings the task back and removes lab complete", async () => {
    await completed({ [`POST ${API}/tasks/current/skip`]: ok(QUIZ) });
    fireEvent.click(button(d["lab.skip"]));
    expect(await within(panel()).findByRole("radio", { name: "ls" })).toBeTruthy();
    expect(screen.queryByText(d["lab.complete"], { exact: false })).toBeNull();
  });

  test("Restart after lab complete shows the task again and removes lab complete", async () => {
    await completed({ [`POST ${API}/tasks/current/restart`]: ok(LAST) });
    fireEvent.click(button(d["lab.restart"]));
    expect(await within(panel()).findByRole("heading", { name: LAST.title[lang] })).toBeTruthy();
    expect(screen.queryByText(d["lab.complete"], { exact: false })).toBeNull();
    expect(within(panel()).getByRole("button", { name: d["lab.check"] })).toBeTruthy();
  });

  test("Skip on an unpassed last task does not say lab complete or well done, and the task stays usable", async () => {
    const calls = backend(base(LAST, { [`POST ${API}/tasks/current/skip`]: err(409, "no_next_task") }));
    await open(lang);
    fireEvent.click(button(d["lab.skip"]));
    await waitFor(() => expect(count(calls, "POST", `${API}/tasks/current/skip`)).toBe(1));
    await act(async () => {});
    expect(screen.queryByText(d["lab.complete"], { exact: false })).toBeNull();
    expect(document.body.textContent?.toLowerCase()).not.toContain("well done");
    expect(within(panel()).getByRole("heading", { name: LAST.title[lang] })).toBeTruthy();
    expect(button(d["lab.check"])).toBeTruthy();
    expect((button(d["lab.skip"]) as HTMLButtonElement).disabled).toBe(false);
  });
});

// ------------------------------------------------------------------ 3. loading state

describe.each(LANGS)("loading state in %s", (lang) => {
  const d = DICTS[lang];

  test("while GET tasks/current is pending the screen says it is preparing the lab, then shows task 1", async () => {
    let release: (r: Reply) => void = () => {};
    const gate = new Promise<Reply>((r) => (release = r));
    backend({ "GET /api/sessions/current": session(), [`GET ${API}/tasks/current`]: () => gate });
    at("/labs/lab-1", lang);
    expect(await screen.findByText(d["lab.preparing"], { exact: false })).toBeTruthy();
    expect(screen.queryByTestId("task-panel")).toBeNull();
    expect(screen.queryByRole("alert")).toBeNull();
    release(ok(HANDS));
    expect(await screen.findByTestId("task-panel")).toBeTruthy();
    expect(screen.queryByText(d["lab.preparing"], { exact: false })).toBeNull();
  });

  test("while POST show of the first task is pending the screen says it is preparing the lab", async () => {
    let release: (r: Reply) => void = () => {};
    const gate = new Promise<Reply>((r) => (release = r));
    backend({
      "GET /api/sessions/current": session(),
      [`GET ${API}/tasks/current`]: err(404, "no_current_task"),
      [`GET ${API}/tasks`]: ok({
        lab_id: "lab-1", current: null,
        tasks: [{ id: "01-first", index: 1, kind: "hands-on", title: HANDS.title, state: "not_started" }],
      }),
      [`POST ${API}/tasks/01-first/show`]: () => gate,
    });
    at("/labs/lab-1", lang);
    expect(await screen.findByText(d["lab.preparing"], { exact: false })).toBeTruthy();
    expect(screen.queryByTestId("task-panel")).toBeNull();
    release(ok({ ...HANDS, id: "01-first", index: 1 }));
    expect(await screen.findByTestId("task-panel")).toBeTruthy();
    expect(screen.queryByText(d["lab.preparing"], { exact: false })).toBeNull();
  });

  test("the preparing state shows no terminal and no task buttons yet", async () => {
    backend({ "GET /api/sessions/current": session(), [`GET ${API}/tasks/current`]: hang });
    const view = at("/labs/lab-1", lang);
    expect(await screen.findByText(d["lab.preparing"], { exact: false })).toBeTruthy();
    expect(view.container.querySelector("iframe")).toBeNull();
    expect(screen.queryByRole("button", { name: d["lab.check"] })).toBeNull();
  });
});

// ------------------------------------------------------------------ 4. nits

describe.each(LANGS)("failed Next or Skip in %s", (lang) => {
  const d = DICTS[lang];
  const failures: [string, Reply][] = [
    ["502 setup_failed", { status: 502, body: { detail: { code: "setup_failed", message: "The task could not be set up." } } }],
    ["504 setup_timeout", { status: 504, body: { detail: { code: "setup_timeout", message: "Setting up the task took too long." } } }],
    // 409 task_changed is no longer here: since #160 (D57) the screen re-fetches tasks/current and shows
    // that task instead of keeping the old one (see issue-160-lab-screen-leftovers.test.tsx).
  ];

  test.each(failures)("Skip answering %s says what failed (not 'could not be loaded'), keeps the old task and can be retried", async (_n, reply) => {
    const calls = backend(base(HANDS, { [`POST ${API}/tasks/current/skip`]: [reply, ok(QUIZ)] }));
    await open(lang);
    fireEvent.click(button(d["lab.skip"]));
    const alert = await screen.findByRole("alert");
    expect((alert.textContent ?? "").trim().length).toBeGreaterThan(0);
    expect(alert.textContent).not.toContain(d["lab.error.load"]);
    expect(within(panel()).getByRole("heading", { name: HANDS.title[lang] })).toBeTruthy();
    for (const k of ["lab.check", "lab.skip", "lab.restart", "lab.end"])
      expect((button(d[k]) as HTMLButtonElement).disabled, k).toBe(false);
    fireEvent.click(button(d["lab.skip"]));
    expect(await within(panel()).findByRole("radio", { name: "ls" })).toBeTruthy();
    expect(count(calls, "POST", `${API}/tasks/current/skip`)).toBe(2);
    expect(screen.queryByRole("alert")).toBeNull();
  });

  test.each(failures)("Next answering %s says what failed, keeps the passed task and can be retried", async (_n, reply) => {
    const calls = backend(
      base(HANDS, {
        [`POST ${API}/tasks/02-make-file/check`]: PASS(),
        [`POST ${API}/tasks/current/next`]: [reply, ok(QUIZ)],
      }),
    );
    await open(lang);
    fireEvent.click(button(d["lab.check"]));
    fireEvent.click(await within(panel()).findByRole("button", { name: d["lab.next"] }));
    const alert = await screen.findByRole("alert");
    expect((alert.textContent ?? "").trim().length).toBeGreaterThan(0);
    expect(alert.textContent).not.toContain(d["lab.error.load"]);
    expect(within(panel()).getByRole("heading", { name: HANDS.title[lang] })).toBeTruthy();
    expect((button(d["lab.next"]) as HTMLButtonElement).disabled).toBe(false);
    fireEvent.click(button(d["lab.next"]));
    expect(await within(panel()).findByRole("radio", { name: "ls" })).toBeTruthy();
    expect(count(calls, "POST", `${API}/tasks/current/next`)).toBe(2);
  });
});

test("End lab stays usable while a task action hangs, and ends the lab", async () => {
  const calls = backend(base(HANDS, { [`POST ${API}/tasks/current/skip`]: hang }));
  await open("en");
  fireEvent.click(button(EN["lab.skip"]));
  await waitFor(() => expect(count(calls, "POST", `${API}/tasks/current/skip`)).toBe(1));
  const end = button(EN["lab.end"]) as HTMLButtonElement;
  expect(end.disabled).toBe(false);
  fireEvent.click(end);
  await waitFor(() => expect(count(calls, "POST", `${API}/end`)).toBe(1));
  await waitFor(() => expect(window.location.pathname).toBe("/labs"));
  expect(screen.queryByTestId("task-panel")).toBeNull();
});

test("images in task Markdown load only from the page's own origin", async () => {
  const md = [
    "# Make a file",
    "",
    "![remote](https://evil.example/pixel.png)",
    "",
    "![scheme-less](//evil.example/pixel2.png)",
    "",
    "![local](/content/lab-1/diagram.png)",
    "",
  ].join("\n");
  backend(base({ ...HANDS, text: { en: md, he: md } }));
  const view = await open("en");
  const imgs = Array.from(view.container.querySelectorAll("img"));
  for (const img of imgs) {
    const src = img.getAttribute("src") ?? "";
    expect(new URL(src, window.location.href).origin, src).toBe(window.location.origin);
  }
  expect(imgs.map((i) => i.getAttribute("src"))).toContain("/content/lab-1/diagram.png");
  expect(view.container.innerHTML).not.toContain("evil.example/pixel");
});

describe("Task and Solution tabs follow the arrow keys", () => {
  const tab = (k: "lab.task.tab" | "lab.solution.tab") => within(panel()).getByRole("tab", { name: EN[k] });

  test("ArrowRight on the Task tab selects and focuses the Solution tab; ArrowLeft goes back", async () => {
    backend(base(HANDS));
    await open("en");
    tab("lab.task.tab").focus();
    fireEvent.keyDown(tab("lab.task.tab"), { key: "ArrowRight" });
    expect(tab("lab.solution.tab").getAttribute("aria-selected")).toBe("true");
    expect(tab("lab.task.tab").getAttribute("aria-selected")).toBe("false");
    expect(document.activeElement).toBe(tab("lab.solution.tab"));
    expect(panel().textContent).toContain(EN["lab.solution.none"]);
    fireEvent.keyDown(tab("lab.solution.tab"), { key: "ArrowLeft" });
    expect(tab("lab.task.tab").getAttribute("aria-selected")).toBe("true");
    expect(document.activeElement).toBe(tab("lab.task.tab"));
    expect(panel().textContent).toContain(HANDS.checklist[0].text.en);
  });

  test("the arrow keys wrap around: ArrowLeft on Task selects Solution, ArrowRight on Solution selects Task", async () => {
    backend(base(HANDS));
    await open("en");
    tab("lab.task.tab").focus();
    fireEvent.keyDown(tab("lab.task.tab"), { key: "ArrowLeft" });
    expect(tab("lab.solution.tab").getAttribute("aria-selected")).toBe("true");
    fireEvent.keyDown(tab("lab.solution.tab"), { key: "ArrowRight" });
    expect(tab("lab.task.tab").getAttribute("aria-selected")).toBe("true");
  });
});
