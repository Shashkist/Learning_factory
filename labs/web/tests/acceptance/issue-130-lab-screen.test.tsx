/**
 * Exam for #130 (slice 3 of #10, D53): the student lab screen at /labs/<id>.
 *
 * BACKEND (real, see labs/manager/app/sessions.py and tasks.py, D50, D51)
 *   GET  /api/sessions/current                  -> 200 {id, lab_id, status, expires_at} | 404 no_session
 *   POST /api/sessions/<sid>/heartbeat          -> 204
 *   POST /api/sessions/<sid>/end                -> 204
 *   GET  /api/sessions/<sid>/tasks/current      -> 200 task detail | 404 no_current_task
 *   GET  /api/sessions/<sid>/tasks              -> {lab_id, current, tasks:[{id,index,kind,title,state}]}
 *   POST /api/sessions/<sid>/tasks/<id>/show    -> task detail
 *   POST /api/sessions/<sid>/tasks/current/{next,skip,previous,restart} -> task detail
 *        409 {"detail":"session_busy"|"setup_running"|"task_changed"|...}
 *   POST /api/sessions/<sid>/tasks/<id>/check   -> {passed, attempts, reason, checklist:[{n,text,passed,hint}]}
 *   POST /api/sessions/<sid>/tasks/<id>/answer  body {"option": <index>} -> {correct, attempts}
 *   Task detail: {id,index,total,kind,title:{en,he},state}, plus hands-on: text:{en,he} (markdown),
 *   checklist:[{n,text:{en,he}}]; quiz: question:{en,he}, options:[{en,he}].
 *   The server never sends a solution or the correct option, so the Solution tab is only asserted
 *   to exist and to switch (its content is not in the API yet).
 *
 * ASSUMPTIONS the exam makes (names chosen by the exam; the implementation may add more)
 *   - The screen learns its session from GET /api/sessions/current (no POST /api/sessions here).
 *     404 there -> role=alert with lab.error.no_session; no iframe and no heartbeat.
 *   - It then asks GET .../tasks/current. On 404 it asks GET .../tasks and POSTs show for the first task.
 *   - Heartbeat: POST every 15 s from when the screen is shown, stops on unmount and on End lab.
 *   - End lab: one POST .../end (no confirmation step), then the URL becomes /labs and My labs shows.
 *   - Restart and Next/Skip/Previous also have no confirmation step.
 *   - Next is offered once the task has passed (a passing check, or state "passed"); Skip always is.
 *     Previous is disabled (or absent) on task 1.
 *
 * DOM
 *   data-testid="task-panel"   the left panel; its dir equals the page direction (rtl in Hebrew)
 *   tabs                       role=tab named lab.task.tab / lab.solution.tab; aria-selected marks one
 *   progress                   role=progressbar with aria-valuenow = index, aria-valuemax = total;
 *                              the panel text has `${lab.question} N ${lab.of} M`
 *   timer                      role=timer showing the time left as m:ss (from expires_at)
 *   hands-on                   title (current language), the text (current language), a checklist of
 *                              role=listitem rows; before Check no row has a mark, after Check
 *                              passed rows have "✓", failed rows "✗" and the hint text
 *   quiz                       the question, options as role=radio named by the option text, button
 *                              lab.answer; the result shows lab.correct or lab.incorrect
 *   buttons                    lab.check, lab.answer, lab.next, lab.skip, lab.previous, lab.restart, lab.end
 *   busy                       409 session_busy / setup_running -> role=alert containing lab.busy,
 *                              the task stays, the student can retry
 *   splitter                   role=separator, focusable (tabindex 0), aria-valuenow changes with the
 *                              arrow keys
 *   terminal                   one <iframe> with src ending /s/<sid>/term/, whose nearest [dir] is "ltr"
 *
 * i18n keys required in en.json and he.json (flat, no interpolation):
 *   lab.task.tab lab.solution.tab lab.question lab.of lab.check lab.answer lab.next lab.skip
 *   lab.previous lab.restart lab.end lab.busy lab.correct lab.incorrect lab.error.no_session
 *   lab.error.load
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
type Handler = Reply | Reply[] | (() => Reply);
type Calls = { url: string; method: string; body?: string }[];

const SID = "sess-1";
const API = `/api/sessions/${SID}`;
// Taken from the real clock when the file loads, so no case depends on the date it runs on (#178).
const T0 = Date.now();
const STUDENT: Reply = { status: 200, body: { kind: "student", name: "Dana Test" } };
const session = (extra: object = {}): Reply => ({
  status: 200,
  body: { id: SID, lab_id: "lab-1", status: "running", expires_at: new Date(T0 + 55 * 60_000).toISOString(), ...extra },
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
const QUIZ = {
  id: "03-quiz", index: 3, total: 4, kind: "quiz", state: "shown",
  title: { en: "Which command lists files?", he: "איזו פקודה מציגה קבצים?" },
  question: { en: "Which command lists files?", he: "איזו פקודה מציגה קבצים?" },
  options: [
    { en: "cd", he: "cd" },
    { en: "ls", he: "ls" },
    { en: "rm", he: "rm" },
  ],
};
const FIRST = { ...HANDS, id: "01-first", index: 1, state: "shown" };
const ok = (body: unknown): Reply => ({ status: 200, body });
const busy = (code = "session_busy"): Reply => ({ status: 409, body: { detail: code } });
const CHECK_PART = {
  passed: false, attempts: 1, reason: null,
  checklist: [
    { n: 1, text: HANDS.checklist[0], passed: true, hint: null },
    { n: 2, text: HANDS.checklist[1], passed: false, hint: "Put the word hello in it" },
  ],
};
const CHECK_PASS = {
  passed: true, attempts: 2, reason: null,
  checklist: CHECK_PART.checklist.map((i) => ({ ...i, passed: true, hint: null })),
};

/** Routes are "METHOD path". An array is a queue (the last entry repeats). */
function backend(routes: Record<string, Handler>, opts: { student?: boolean } = {}) {
  const calls: Calls = [];
  const all: Record<string, Handler> = {
    "GET /api/auth/me": opts.student === false ? { status: 401, body: {} } : STUDENT,
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
    if (typeof h === "function") r = h();
    else if (Array.isArray(h)) {
      if (!queues.has(key)) queues.set(key, [...h]);
      const q = queues.get(key) as Reply[];
      r = q.length > 1 ? (q.shift() as Reply) : q[0];
    } else if (h !== undefined) r = h;
    if (r === "network") throw new TypeError("offline");
    return {
      ok: r.status >= 200 && r.status < 300,
      status: r.status,
      json: async () => r.body ?? {},
      text: async () => JSON.stringify(r.body ?? {}),
    } as Response;
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
const count = (calls: Calls, method: string, path: string) =>
  calls.filter((c) => c.method === method && c.url === path).length;
const bodyOf = (c?: Calls[number]) => JSON.parse(c?.body ?? "null");
const button = (name: string) => within(panel()).getByRole("button", { name });
const marks = (el: HTMLElement) => ({ ok: el.textContent?.includes("✓"), bad: el.textContent?.includes("✗") });

beforeEach(() => {
  window.localStorage.clear();
  window.history.replaceState({}, "", "/");
});
afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

// Fake timers must be installed before render so the screen's own interval is the fake one.
async function settle() {
  for (let i = 0; i < 12; i++)
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
}
async function openFake(lang: "en" | "he" = "en") {
  vi.useFakeTimers({ toFake: ["setInterval", "clearInterval", "setTimeout", "clearTimeout", "Date"] });
  vi.setSystemTime(T0);
  const view = at("/labs/lab-1", lang);
  await settle();
  expect(screen.queryByTestId("task-panel")).not.toBeNull();
  return view;
}
const advance = (ms: number) =>
  act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });

// ------------------------------------------------------------------ i18n files

const KEYS = [
  "lab.task.tab", "lab.solution.tab", "lab.question", "lab.of", "lab.check", "lab.answer", "lab.next",
  "lab.skip", "lab.previous", "lab.restart", "lab.end", "lab.busy", "lab.correct", "lab.incorrect",
  "lab.error.no_session", "lab.error.load",
];

test("both language files have the lab screen keys, non-empty, with the same key set", () => {
  expect(Object.keys(HE).sort()).toEqual(Object.keys(EN).sort());
  for (const d of [EN, HE])
    for (const k of KEYS) {
      expect(typeof d[k], k).toBe("string");
      expect(d[k].trim().length, k).toBeGreaterThan(0);
    }
});

test("the Hebrew lab texts are Hebrew and the navigation buttons differ from each other", () => {
  for (const k of KEYS) expect(HE[k], k).toMatch(/[֐-׿]/);
  for (const d of [EN, HE])
    expect(new Set(["lab.next", "lab.skip", "lab.previous", "lab.restart", "lab.check", "lab.end"].map((k) => d[k])).size).toBe(6);
});

// ------------------------------------------------------------------ the screen, per language

describe.each(LANGS)("lab screen in %s", (lang) => {
  const d = DICTS[lang];
  const other = lang === "en" ? "he" : "en";

  test("a hands-on task shows its title, text, progress N of M, tabs and the checklist", async () => {
    backend(base(HANDS));
    await open(lang);
    const p = within(panel());
    expect(p.getByRole("heading", { name: HANDS.title[lang] })).toBeTruthy();
    expect(panel().textContent).toContain(lang === "en" ? "Create the notes file in your home folder." : "צרו את קובץ ההערות בתיקיית הבית.");
    expect(panel().textContent).not.toContain(HANDS.title[other]);
    expect(panel().textContent).toContain(`${d["lab.question"]} 2 ${d["lab.of"]} 4`);
    const bar = p.getByRole("progressbar");
    expect(bar.getAttribute("aria-valuenow")).toBe("2");
    expect(bar.getAttribute("aria-valuemax")).toBe("4");
    expect(p.getByRole("tab", { name: d["lab.task.tab"] }).getAttribute("aria-selected")).toBe("true");
    expect(p.getByRole("tab", { name: d["lab.solution.tab"] }).getAttribute("aria-selected")).toBe("false");
    const rows = p.getAllByRole("listitem");
    expect(rows.map((r) => r.textContent)).toEqual([
      expect.stringContaining(HANDS.checklist[0][lang]),
      expect.stringContaining(HANDS.checklist[1][lang]),
    ]);
    for (const r of rows) expect(marks(r)).toEqual({ ok: false, bad: false });
    expect(p.getByRole("button", { name: d["lab.check"] })).toBeTruthy();
    expect(p.queryByRole("radio")).toBeNull();
  });

  test("the Solution tab can be selected and the Task tab brings the task back", async () => {
    backend(base(HANDS));
    await open(lang);
    const p = within(panel());
    fireEvent.click(p.getByRole("tab", { name: d["lab.solution.tab"] }));
    expect(p.getByRole("tab", { name: d["lab.solution.tab"] }).getAttribute("aria-selected")).toBe("true");
    expect(p.getByRole("tab", { name: d["lab.task.tab"] }).getAttribute("aria-selected")).toBe("false");
    fireEvent.click(p.getByRole("tab", { name: d["lab.task.tab"] }));
    expect(p.getByRole("tab", { name: d["lab.task.tab"] }).getAttribute("aria-selected")).toBe("true");
    expect(panel().textContent).toContain(HANDS.checklist[0][lang]);
  });

  test("Check POSTs /<task>/check and marks passed rows with a check and failed rows with a cross and the hint", async () => {
    const calls = backend(base(HANDS, { [`POST ${API}/tasks/02-make-file/check`]: ok(CHECK_PART) }));
    await open(lang);
    fireEvent.click(button(d["lab.check"]));
    await waitFor(() => expect(marks(within(panel()).getAllByRole("listitem")[1]).bad).toBe(true));
    expect(count(calls, "POST", `${API}/tasks/02-make-file/check`)).toBe(1);
    const [a, b] = within(panel()).getAllByRole("listitem");
    expect(marks(a)).toEqual({ ok: true, bad: false });
    expect(marks(b)).toEqual({ ok: false, bad: true });
    expect(b.textContent).toContain("Put the word hello in it");
    expect(a.textContent).not.toContain("hello");
  });

  test("after a failed check the next Check can pass; a pass shows Next, which POSTs current/next", async () => {
    const calls = backend(
      base(HANDS, {
        [`POST ${API}/tasks/02-make-file/check`]: [ok(CHECK_PART), ok(CHECK_PASS)],
        [`POST ${API}/tasks/current/next`]: ok(QUIZ),
      }),
    );
    await open(lang);
    fireEvent.click(button(d["lab.check"]));
    await waitFor(() => expect(marks(within(panel()).getAllByRole("listitem")[1]).bad).toBe(true));
    fireEvent.click(button(d["lab.check"]));
    await waitFor(() => expect(within(panel()).getAllByRole("listitem").every((r) => marks(r).ok)).toBe(true));
    expect(panel().textContent).not.toContain("hello");
    fireEvent.click(button(d["lab.next"]));
    await waitFor(() => expect(count(calls, "POST", `${API}/tasks/current/next`)).toBe(1));
    expect(await within(panel()).findByRole("radio", { name: "ls" })).toBeTruthy();
    expect(panel().textContent).toContain(`${d["lab.question"]} 3 ${d["lab.of"]} 4`);
  });

  test("a check that answers 409 session_busy shows the busy text, keeps the checklist and can be retried", async () => {
    const calls = backend(
      base(HANDS, { [`POST ${API}/tasks/02-make-file/check`]: [busy("session_busy"), ok(CHECK_PART)] }),
    );
    await open(lang);
    fireEvent.click(button(d["lab.check"]));
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain(d["lab.busy"]);
    for (const r of within(panel()).getAllByRole("listitem")) expect(marks(r)).toEqual({ ok: false, bad: false });
    expect(within(panel()).getByRole("heading", { name: HANDS.title[lang] })).toBeTruthy();
    fireEvent.click(button(d["lab.check"]));
    await waitFor(() => expect(marks(within(panel()).getAllByRole("listitem")[1]).bad).toBe(true));
    expect(count(calls, "POST", `${API}/tasks/02-make-file/check`)).toBe(2);
    expect(screen.queryByText(d["lab.busy"], { exact: false })).toBeNull();
  });

  test("Restart task POSTs current/restart and shows the task again", async () => {
    const calls = backend(base(HANDS, { [`POST ${API}/tasks/current/restart`]: ok(HANDS) }));
    await open(lang);
    fireEvent.click(button(d["lab.restart"]));
    await waitFor(() => expect(count(calls, "POST", `${API}/tasks/current/restart`)).toBe(1));
    expect(await within(panel()).findByRole("heading", { name: HANDS.title[lang] })).toBeTruthy();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  test.each(["session_busy", "setup_running"])("Restart answering 409 %s shows the busy text and keeps the task", async (code) => {
    backend(base(HANDS, { [`POST ${API}/tasks/current/restart`]: busy(code) }));
    await open(lang);
    fireEvent.click(button(d["lab.restart"]));
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain(d["lab.busy"]);
    expect(within(panel()).getByRole("heading", { name: HANDS.title[lang] })).toBeTruthy();
    expect(button(d["lab.check"])).toBeTruthy();
  });

  test("Skip POSTs current/skip and Previous POSTs current/previous, each showing the returned task", async () => {
    const calls = backend(
      base(HANDS, {
        [`POST ${API}/tasks/current/skip`]: ok(QUIZ),
        [`POST ${API}/tasks/current/previous`]: ok(HANDS),
      }),
    );
    await open(lang);
    fireEvent.click(button(d["lab.skip"]));
    expect(await within(panel()).findByRole("radio", { name: "rm" })).toBeTruthy();
    expect(count(calls, "POST", `${API}/tasks/current/skip`)).toBe(1);
    fireEvent.click(button(d["lab.previous"]));
    expect(await within(panel()).findByRole("heading", { name: HANDS.title[lang] })).toBeTruthy();
    expect(count(calls, "POST", `${API}/tasks/current/previous`)).toBe(1);
  });

  test("a navigation that answers 409 setup_running shows the busy text and stays on the task", async () => {
    backend(base(HANDS, { [`POST ${API}/tasks/current/skip`]: busy("setup_running") }));
    await open(lang);
    fireEvent.click(button(d["lab.skip"]));
    expect((await screen.findByRole("alert")).textContent).toContain(d["lab.busy"]);
    expect(panel().textContent).toContain(`${d["lab.question"]} 2 ${d["lab.of"]} 4`);
  });

  test("on the first task Previous is disabled or absent", async () => {
    backend(base(FIRST));
    await open(lang);
    const prev = within(panel()).queryByRole("button", { name: d["lab.previous"] }) as HTMLButtonElement | null;
    if (prev) expect(prev.disabled).toBe(true);
    expect(panel().textContent).toContain(`${d["lab.question"]} 1 ${d["lab.of"]} 4`);
  });

  test("a quiz shows the question and options of the language; Answer POSTs the chosen index", async () => {
    const calls = backend(base(QUIZ, { [`POST ${API}/tasks/03-quiz/answer`]: ok({ correct: true, attempts: 1 }) }));
    await open(lang);
    const p = within(panel());
    expect(p.getAllByText(QUIZ.question[lang], { exact: false }).length).toBeGreaterThan(0);
    expect(p.getAllByRole("radio").map((r) => r.getAttribute("aria-label") ?? r.closest("label")?.textContent ?? "")).toEqual(
      expect.arrayContaining(["cd", "ls", "rm"]),
    );
    expect(p.queryByRole("button", { name: d["lab.check"] })).toBeNull();
    expect(p.queryAllByRole("listitem").some((r) => r.textContent?.includes("✓"))).toBe(false);
    fireEvent.click(p.getByRole("radio", { name: "ls" }));
    fireEvent.click(p.getByRole("button", { name: d["lab.answer"] }));
    await waitFor(() => expect(count(calls, "POST", `${API}/tasks/03-quiz/answer`)).toBe(1));
    expect(bodyOf(calls.find((c) => c.url.endsWith("/answer")))).toEqual({ option: 1 });
    expect(await p.findByText(d["lab.correct"], { exact: false })).toBeTruthy();
    expect(p.queryByText(d["lab.incorrect"], { exact: false })).toBeNull();
    expect(p.getByRole("button", { name: d["lab.next"] })).toBeTruthy();
  });

  test("a wrong quiz answer shows the incorrect text, no Next, and the student can answer again", async () => {
    const calls = backend(
      base(QUIZ, {
        [`POST ${API}/tasks/03-quiz/answer`]: [ok({ correct: false, attempts: 1 }), ok({ correct: true, attempts: 2 })],
      }),
    );
    await open(lang);
    const p = within(panel());
    fireEvent.click(p.getByRole("radio", { name: "cd" }));
    fireEvent.click(p.getByRole("button", { name: d["lab.answer"] }));
    expect(await p.findByText(d["lab.incorrect"], { exact: false })).toBeTruthy();
    expect(bodyOf(calls.find((c) => c.url.endsWith("/answer")))).toEqual({ option: 0 });
    expect(p.queryByRole("button", { name: d["lab.next"] })).toBeNull();
    fireEvent.click(p.getByRole("radio", { name: "ls" }));
    fireEvent.click(p.getByRole("button", { name: d["lab.answer"] }));
    expect(await p.findByText(d["lab.correct"], { exact: false })).toBeTruthy();
    expect(calls.filter((c) => c.url.endsWith("/answer")).map(bodyOf)).toEqual([{ option: 0 }, { option: 1 }]);
  });

  test("a quiz answer that answers 409 session_busy shows the busy text and no verdict", async () => {
    backend(base(QUIZ, { [`POST ${API}/tasks/03-quiz/answer`]: busy() }));
    await open(lang);
    fireEvent.click(within(panel()).getByRole("radio", { name: "ls" }));
    fireEvent.click(within(panel()).getByRole("button", { name: d["lab.answer"] }));
    expect((await screen.findByRole("alert")).textContent).toContain(d["lab.busy"]);
    expect(panel().textContent).not.toContain(d["lab.correct"]);
    expect(panel().textContent).not.toContain(d["lab.incorrect"]);
  });

  test("the terminal iframe points at /s/<session>/term/ and is always left-to-right", async () => {
    const view = await (async () => {
      backend(base(HANDS));
      return open(lang);
    })();
    const frames = view.container.querySelectorAll("iframe");
    expect(frames).toHaveLength(1);
    expect((frames[0].getAttribute("src") ?? "").endsWith(`/s/${SID}/term/`)).toBe(true);
    expect(frames[0].closest("[dir]")?.getAttribute("dir")).toBe("ltr");
    expect(panel().querySelector("iframe")).toBeNull();
  });

  test("the splitter is a focusable separator that the arrow keys move", async () => {
    backend(base(HANDS));
    await open(lang);
    const sep = screen.getByRole("separator");
    expect(sep.getAttribute("tabindex")).toBe("0");
    const start = sep.getAttribute("aria-valuenow");
    expect(start).not.toBeNull();
    fireEvent.keyDown(sep, { key: "ArrowRight" });
    const afterRight = sep.getAttribute("aria-valuenow");
    fireEvent.keyDown(sep, { key: "ArrowLeft" });
    fireEvent.keyDown(sep, { key: "ArrowLeft" });
    const afterLeft = sep.getAttribute("aria-valuenow");
    expect(new Set([start, afterRight, afterLeft]).size).toBeGreaterThan(1);
    expect(afterRight).not.toBe(afterLeft);
  });

  test("End lab POSTs /end once and returns to My labs", async () => {
    const calls = backend(base(HANDS, { "GET /api/labs": ok([]) }));
    await open(lang);
    fireEvent.click(screen.getByRole("button", { name: d["lab.end"] }));
    await waitFor(() => expect(window.location.pathname).toBe("/labs"));
    expect(count(calls, "POST", `${API}/end`)).toBe(1);
    expect(await screen.findByRole("heading", { name: d["labs.title"] })).toBeTruthy();
    expect(screen.queryByTestId("task-panel")).toBeNull();
  });

  test("when no task is current the screen lists the tasks and shows the first one", async () => {
    const calls = backend({
      "GET /api/sessions/current": session(),
      [`GET ${API}/tasks/current`]: { status: 404, body: { detail: "no_current_task" } },
      [`GET ${API}/tasks`]: ok({
        lab_id: "lab-1", current: null,
        tasks: [{ id: "01-first", index: 1, kind: "hands-on", title: FIRST.title, state: "not_started" }],
      }),
      [`POST ${API}/tasks/01-first/show`]: ok(FIRST),
    });
    await open(lang);
    expect(count(calls, "POST", `${API}/tasks/01-first/show`)).toBe(1);
    expect(panel().textContent).toContain(`${d["lab.question"]} 1 ${d["lab.of"]} 4`);
  });

  test("without a running session the screen says so: alert, no terminal, no heartbeat", async () => {
    const calls = backend({ "GET /api/sessions/current": { status: 404, body: { detail: "no_session" } } });
    const view = at("/labs/lab-1", lang);
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain(d["lab.error.no_session"]);
    expect(view.container.querySelector("iframe")).toBeNull();
    expect(screen.queryByTestId("task-panel")).toBeNull();
    expect(calls.some((c) => c.url.endsWith("/heartbeat"))).toBe(false);
  });

  test("a task list that cannot be loaded shows lab.error.load", async () => {
    backend({ "GET /api/sessions/current": session(), [`GET ${API}/tasks/current`]: { status: 500, body: {} } });
    at("/labs/lab-1", lang);
    expect((await screen.findByRole("alert")).textContent).toContain(d["lab.error.load"]);
  });
});

// ------------------------------------------------------------------ RTL

test("the Hebrew panel is right-to-left, the English panel left-to-right, the terminal left-to-right in both", async () => {
  backend(base(HANDS));
  const he_ = await open("he");
  expect(panel().getAttribute("dir") ?? panel().closest("[dir]")?.getAttribute("dir")).toBe("rtl");
  expect(he_.container.querySelector("iframe")?.closest("[dir]")?.getAttribute("dir")).toBe("ltr");
  he_.unmount();
  vi.restoreAllMocks();
  window.localStorage.clear();
  backend(base(HANDS));
  await open("en");
  expect(panel().getAttribute("dir") ?? panel().closest("[dir]")?.getAttribute("dir")).toBe("ltr");
});

test("switching language on the lab screen translates the panel without ending or reloading the session", async () => {
  const calls = backend(base(HANDS));
  await open("en");
  fireEvent.click(screen.getByRole("button", { name: EN["lang.switch.label"] }));
  await waitFor(() => expect(panel().textContent).toContain(HANDS.title.he));
  expect(within(panel()).getByRole("button", { name: HE["lab.check"] })).toBeTruthy();
  expect(panel().closest("[dir]")?.getAttribute("dir")).toBe("rtl");
  expect(count(calls, "POST", `${API}/end`)).toBe(0);
  expect(count(calls, "GET", "/api/sessions/current")).toBe(1);
});

// ------------------------------------------------------------------ timer and heartbeat

describe("timer", () => {
  const secs = (el: HTMLElement) => {
    const m = /(\d+):(\d\d)/.exec(el.textContent ?? "");
    return m ? Number(m[1]) * 60 + Number(m[2]) : NaN;
  };

  test("shows the time left until expires_at and counts down", async () => {
    backend(base(HANDS));
    await openFake();
    const timer = screen.getByRole("timer");
    const first = secs(timer);
    expect(first).toBeGreaterThanOrEqual(54 * 60 + 58);
    expect(first).toBeLessThanOrEqual(55 * 60);
    await advance(60_000);
    const later = secs(screen.getByRole("timer"));
    expect(first - later).toBeGreaterThanOrEqual(59);
    expect(first - later).toBeLessThanOrEqual(61);
  });

  test("shows 0:00 (never a negative time) once the time is over", async () => {
    backend(base(HANDS, { "GET /api/sessions/current": session({ expires_at: new Date(T0 + 30_000).toISOString() }) }));
    await openFake();
    await advance(90_000);
    expect(secs(screen.getByRole("timer"))).toBe(0);
    expect(screen.getByRole("timer").textContent).not.toContain("-");
  });
});

describe("heartbeat", () => {
  const beats = (calls: Calls) => count(calls, "POST", `${API}/heartbeat`);

  test("POSTs /heartbeat every 15 s while the screen is open", async () => {
    const calls = backend(base(HANDS));
    await openFake();
    const before = beats(calls);
    expect(before).toBeLessThanOrEqual(1);
    await advance(14_000);
    expect(beats(calls)).toBe(before);
    await advance(46_000); // 60 s after open
    expect(beats(calls) - before).toBeGreaterThanOrEqual(3);
    expect(beats(calls) - before).toBeLessThanOrEqual(4);
  });

  test("stops when the screen is left (unmount)", async () => {
    const calls = backend(base(HANDS));
    const view = await openFake();
    await advance(31_000);
    view.unmount();
    const frozen = beats(calls);
    expect(frozen).toBeGreaterThanOrEqual(2);
    await advance(120_000);
    expect(beats(calls)).toBe(frozen);
  });

  test("stops after End lab", async () => {
    const calls = backend(base(HANDS));
    await openFake();
    await advance(16_000);
    fireEvent.click(screen.getByRole("button", { name: EN["lab.end"] }));
    await settle();
    expect(count(calls, "POST", `${API}/end`)).toBe(1);
    const frozen = beats(calls);
    await advance(120_000);
    expect(beats(calls)).toBe(frozen);
  });

  test("a failing heartbeat does not break the screen", async () => {
    const calls = backend(base(HANDS, { [`POST ${API}/heartbeat`]: { status: 500, body: {} } }));
    await openFake();
    await advance(50_000);
    expect(beats(calls)).toBeGreaterThanOrEqual(3);
    expect(screen.queryByTestId("task-panel")).not.toBeNull();
    expect(screen.queryByRole("alert")).toBeNull();
  });
});
