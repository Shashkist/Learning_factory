/**
 * Exam for #129 (slice 2 of #10, D53) plus the slice-1 follow-ups of #136 that touch the same
 * web files: the My labs page and the shell around it.
 *
 * CONTRACT the implementation must honour (names chosen by the exam)
 *
 * Network. After GET /api/auth/me answers a student, the app calls GET /api/labs. Reply: a JSON
 * array (see labs/manager/tests/acceptance/test_issue_129_catalog.py), items
 *   { id, title:{en,he}, description:{en,he}, module:int, lesson:int, tasks:int,
 *     progress: "done" | "<n>/<m>" | "not_started", session_id: string | null }
 * Starting: POST /api/sessions, Content-Type application/json, body {"lab_id": "<id>"} (a `force`
 * key is absent or false). 201 {id, lab_id, status} -> the app goes to /labs/<id> (pushState).
 * 409 {"detail": {"code": "another_lab_open", "session_id", "lab_id", "title": {en, he}}} -> dialog.
 *
 * i18n keys (flat, no interpolation, in both files)
 *   app.title                   document.title (differs between the two languages)
 *   lang.switch.label           aria-label of the EN / עב toggle (the accessible name)
 *   labs.module                 word before the module number: heading text `${labs.module} ${n}`
 *   labs.tasks                  word after the task count on a card: `${n} ${labs.tasks}`
 *   labs.progress.not_started   shown on a card that was never opened
 *   labs.progress.done          shown next to the check mark "✓" on a completed card
 *   labs.start / labs.continue / labs.redo   the card buttons
 *   labs.open_conflict.title    heading of the "Another lab is open" dialog
 *   labs.open_conflict.body     sentence in the dialog (the open lab's title follows it)
 *   labs.open_conflict.force    "End it and start this one"
 *   labs.open_conflict.cancel   "Cancel"
 *   labs.error.load             role=alert when GET /api/labs fails (any non-2xx or network)
 *   labs.error.start            role=alert when POST /api/sessions fails with anything but 409
 *   (existing keys stay: labs.title, labs.empty, nav.logout, login.error.unavailable, ...)
 *
 * DOM
 *   module group    data-testid="module-<n>", contains a heading named `${labs.module} ${n}`;
 *                   groups appear in ascending module order, cards in the API's order
 *   lab card        data-testid="lab-card-<id>": heading with the title of the current language,
 *                   the description, `${tasks} ${labs.tasks}`, the progress and ONE button:
 *                   not_started -> labs.start, "<n>/<m>" -> labs.continue, done -> labs.redo
 *                   progress text: done -> "✓" and labs.progress.done; n/m -> "n/m" text
 *   dialog          role="dialog" with heading labs.open_conflict.title, the open lab's title in the
 *                   current language, and buttons labs.open_conflict.force / .cancel
 *   student name    rendered inside a <bdi> element
 *   toggle          <button aria-label={lang.switch.label}> WITHOUT a lang attribute (#147: the label is
 *                   spoken in the page's language and contains the visible text, WCAG 2.5.3); the
 *                   visible text sits in <span lang="he"> while the page is English, lang="en" while Hebrew
 * Shell (#136): /me answering anything but 200 or 401 (or failing) shows role=alert with
 *   login.error.unavailable and neither the login form nor My labs; 401 is signed out.
 *   Login and logout redirects use replaceState (never pushState). A signed-in user on /login
 *   sees My labs and the URL becomes /labs. <html lang/dir> and document.title follow the language.
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
type Calls = { url: string; method: string; body?: string }[];

const NAME = "Dana Test";
const STUDENT: Reply = { status: 200, body: { kind: "student", name: NAME } };

const CATALOG = [
  {
    id: "cat-b", title: { en: "Shell basics", he: "יסודות המעטפת" },
    description: { en: "First steps in the shell.", he: "צעדים ראשונים במעטפת." },
    module: 1, lesson: 1, tasks: 4, progress: "not_started", session_id: null,
  },
  {
    id: "cat-a", title: { en: "Files and folders", he: "קבצים ותיקיות" },
    description: { en: "Move around the tree.", he: "מסתובבים בעץ." },
    module: 1, lesson: 2, tasks: 3, progress: "2/3", session_id: "sess-a",
  },
  {
    id: "cat-c", title: { en: "Permissions", he: "הרשאות" },
    description: { en: "Who may do what.", he: "מי רשאי מה." },
    module: 2, lesson: 1, tasks: 2, progress: "done", session_id: null,
  },
];

function backend(replies: {
  me?: Reply; labs?: Reply; sessions?: Reply[]; login?: Reply; logout?: Reply;
}) {
  const calls: Calls = [];
  const sessions = [...(replies.sessions ?? [])];
  vi.spyOn(globalThis, "fetch").mockImplementation((async (url: RequestInfo | URL, init?: RequestInit) => {
    const u = String(url);
    const method = (init?.method ?? "GET").toUpperCase();
    calls.push({ url: u, method, body: typeof init?.body === "string" ? init.body : undefined });
    let r: Reply | undefined;
    if (u === "/api/auth/me") r = replies.me ?? { status: 401, body: { detail: "not_authenticated" } };
    if (u === "/api/labs" && method === "GET") r = replies.labs ?? { status: 200, body: CATALOG };
    if (u === "/api/sessions" && method === "POST") r = sessions.shift() ?? { status: 500, body: {} };
    if (u === "/api/auth/login" && method === "POST") r = replies.login;
    if (u === "/api/auth/logout" && method === "POST") r = replies.logout ?? { status: 204 };
    if (r === undefined) r = { status: 404, body: { detail: "Not Found" } };
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

function at(path: string, lang?: "en" | "he") {
  window.history.replaceState({}, "", path);
  if (lang) window.localStorage.setItem("lf-lang", lang);
  return render(<App />);
}

const card = (id: string) => screen.getByTestId(`lab-card-${id}`);
const sessionPosts = (calls: Calls) => calls.filter((c) => c.url === "/api/sessions" && c.method === "POST");
const bodyOf = (c: Calls[number]) => JSON.parse(c.body ?? "null");
const conflict = (lab: string, title: { en: string; he: string }) => ({
  status: 409,
  body: { detail: { code: "another_lab_open", session_id: "sess-x", lab_id: lab, title } },
});
const OPEN = { en: "Network basics", he: "יסודות רשת" };

beforeEach(() => {
  window.localStorage.clear();
  window.history.replaceState({}, "", "/");
});
afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

// ------------------------------------------------------------------ i18n files

const KEYS = [
  "app.title", "lang.switch.label", "labs.module", "labs.tasks", "labs.progress.not_started",
  "labs.progress.done", "labs.start", "labs.continue", "labs.redo", "labs.open_conflict.title",
  "labs.open_conflict.body", "labs.open_conflict.force", "labs.open_conflict.cancel",
  "labs.error.load", "labs.error.start",
];

test("both language files have the new keys, non-empty, and the same key set", () => {
  expect(Object.keys(HE).sort()).toEqual(Object.keys(EN).sort());
  for (const d of [EN, HE])
    for (const k of KEYS) {
      expect(typeof d[k], k).toBe("string");
      expect(d[k].trim().length, k).toBeGreaterThan(0);
    }
});

test("the Hebrew texts are Hebrew and the three buttons differ from each other", () => {
  for (const k of ["labs.start", "labs.continue", "labs.redo", "labs.open_conflict.force"])
    expect(HE[k], k).toMatch(/[֐-׿]/);
  for (const d of [EN, HE])
    expect(new Set([d["labs.start"], d["labs.continue"], d["labs.redo"]]).size).toBe(3);
  expect(EN["app.title"]).not.toBe(HE["app.title"]);
  expect(EN["labs.open_conflict.force"]).toBe("End it and start this one");
  expect(EN["labs.open_conflict.cancel"]).toBe("Cancel");
});

// ------------------------------------------------------------------ the page

describe.each(LANGS)("My labs in %s", (lang) => {
  const d = DICTS[lang];

  test("asks GET /api/labs once signed in and groups the cards by module", async () => {
    const calls = backend({ me: STUDENT });
    at("/labs", lang);
    const m1 = await screen.findByTestId("module-1");
    const m2 = screen.getByTestId("module-2");
    expect(calls.some((c) => c.url === "/api/labs" && c.method === "GET")).toBe(true);
    expect(within(m1).getByRole("heading", { name: `${d["labs.module"]} 1` })).toBeTruthy();
    expect(within(m2).getByRole("heading", { name: `${d["labs.module"]} 2` })).toBeTruthy();
    expect(within(m1).getByTestId("lab-card-cat-a")).toBeTruthy();
    expect(within(m1).getByTestId("lab-card-cat-b")).toBeTruthy();
    expect(within(m2).getByTestId("lab-card-cat-c")).toBeTruthy();
    expect(within(m1).queryByTestId("lab-card-cat-c")).toBeNull();
    const order = screen.getAllByTestId(/^lab-card-/).map((e) => e.getAttribute("data-testid"));
    expect(order).toEqual(["lab-card-cat-b", "lab-card-cat-a", "lab-card-cat-c"]);
    expect(screen.queryByText(d["labs.empty"])).toBeNull();
  });

  test("a card shows the title and description of the current language and the task count", async () => {
    backend({ me: STUDENT });
    at("/labs", lang);
    await screen.findByTestId("module-1");
    const c = within(card("cat-a"));
    expect(c.getByRole("heading", { name: CATALOG[1].title[lang] })).toBeTruthy();
    expect(c.getByText(CATALOG[1].description[lang])).toBeTruthy();
    expect(card("cat-a").textContent).toContain(`3 ${d["labs.tasks"]}`);
    const other = lang === "en" ? "he" : "en";
    expect(card("cat-a").textContent).not.toContain(CATALOG[1].title[other]);
  });

  test("progress: not started, n/m and a check mark for done", async () => {
    backend({ me: STUDENT });
    at("/labs", lang);
    await screen.findByTestId("module-1");
    expect(card("cat-b").textContent).toContain(d["labs.progress.not_started"]);
    expect(card("cat-a").textContent).toContain("2/3");
    expect(card("cat-a").textContent).not.toContain("✓");
    expect(card("cat-c").textContent).toContain("✓");
    expect(card("cat-c").textContent).toContain(d["labs.progress.done"]);
    expect(card("cat-c").textContent).not.toContain(d["labs.progress.not_started"]);
  });

  test("each card has the one right button: Start, Continue or Redo", async () => {
    backend({ me: STUDENT });
    at("/labs", lang);
    await screen.findByTestId("module-1");
    const names = (id: string) =>
      within(card(id)).getAllByRole("button").map((b) => b.textContent?.trim());
    expect(names("cat-b")).toEqual([d["labs.start"]]);
    expect(names("cat-a")).toEqual([d["labs.continue"]]);
    expect(names("cat-c")).toEqual([d["labs.redo"]]);
  });

  test.each([
    ["cat-b", "labs.start"],
    ["cat-a", "labs.continue"],
    ["cat-c", "labs.redo"],
  ])("%s: the %s button POSTs /api/sessions and opens the lab", async (id, key) => {
    const calls = backend({ me: STUDENT, sessions: [{ status: 201, body: { id: "new-1", lab_id: id, status: "starting" } }] });
    at("/labs", lang);
    await screen.findByTestId("module-1");
    fireEvent.click(within(card(id)).getByRole("button", { name: d[key] }));
    await waitFor(() => expect(window.location.pathname).toBe(`/labs/${id}`));
    const posts = sessionPosts(calls);
    expect(posts).toHaveLength(1);
    expect(bodyOf(posts[0]).lab_id).toBe(id);
    expect(bodyOf(posts[0]).force).not.toBe(true);
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  test("409 another_lab_open opens the dialog naming the open lab, nothing else is posted yet", async () => {
    const calls = backend({ me: STUDENT, sessions: [conflict("net-1", OPEN)] });
    at("/labs", lang);
    await screen.findByTestId("module-1");
    fireEvent.click(within(card("cat-b")).getByRole("button", { name: d["labs.start"] }));
    const dialog = await screen.findByRole("dialog");
    const dlg = within(dialog);
    expect(dlg.getByRole("heading", { name: d["labs.open_conflict.title"] })).toBeTruthy();
    expect(dialog.textContent).toContain(d["labs.open_conflict.body"]);
    expect(dialog.textContent).toContain(OPEN[lang]);
    expect(dlg.getByRole("button", { name: d["labs.open_conflict.force"] })).toBeTruthy();
    expect(dlg.getByRole("button", { name: d["labs.open_conflict.cancel"] })).toBeTruthy();
    expect(sessionPosts(calls)).toHaveLength(1);
    expect(window.location.pathname).toBe("/labs");
  });

  test("End it and start this one re-POSTs with force: true and opens the lab", async () => {
    const calls = backend({
      me: STUDENT,
      sessions: [conflict("net-1", OPEN), { status: 201, body: { id: "new-2", lab_id: "cat-b", status: "starting" } }],
    });
    at("/labs", lang);
    await screen.findByTestId("module-1");
    fireEvent.click(within(card("cat-b")).getByRole("button", { name: d["labs.start"] }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.click(within(dialog).getByRole("button", { name: d["labs.open_conflict.force"] }));
    await waitFor(() => expect(window.location.pathname).toBe("/labs/cat-b"));
    const posts = sessionPosts(calls);
    expect(posts).toHaveLength(2);
    expect(bodyOf(posts[1])).toEqual({ lab_id: "cat-b", force: true });
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  test("Cancel closes the dialog without a second POST and stays on My labs", async () => {
    const calls = backend({ me: STUDENT, sessions: [conflict("net-1", OPEN)] });
    at("/labs", lang);
    await screen.findByTestId("module-1");
    fireEvent.click(within(card("cat-b")).getByRole("button", { name: d["labs.start"] }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.click(within(dialog).getByRole("button", { name: d["labs.open_conflict.cancel"] }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(sessionPosts(calls)).toHaveLength(1);
    expect(window.location.pathname).toBe("/labs");
    expect(screen.getByRole("heading", { name: d["labs.title"] })).toBeTruthy();
  });

  test("a start that fails for another reason shows labs.error.start, no dialog, no navigation", async () => {
    backend({ me: STUDENT, sessions: [{ status: 404, body: { detail: "unknown_lab" } }] });
    at("/labs", lang);
    await screen.findByTestId("module-1");
    fireEvent.click(within(card("cat-b")).getByRole("button", { name: d["labs.start"] }));
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain(d["labs.error.start"]);
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(window.location.pathname).toBe("/labs");
  });

  test("a network failure on start shows labs.error.start", async () => {
    backend({ me: STUDENT, sessions: ["network"] });
    at("/labs", lang);
    await screen.findByTestId("module-1");
    fireEvent.click(within(card("cat-b")).getByRole("button", { name: d["labs.start"] }));
    expect((await screen.findByRole("alert")).textContent).toContain(d["labs.error.start"]);
  });

  test.each([
    [{ status: 503, body: { detail: "database unavailable" } } as Reply],
    [{ status: 500, body: {} } as Reply],
    ["network" as Reply],
  ])("a failing GET /api/labs (%j) shows labs.error.load", async (reply) => {
    backend({ me: STUDENT, labs: reply });
    at("/labs", lang);
    expect((await screen.findByRole("alert")).textContent).toContain(d["labs.error.load"]);
    expect(screen.queryByTestId("module-1")).toBeNull();
    expect(screen.queryByRole("heading", { name: d["login.title"] })).toBeNull();
  });

  test("an empty catalog shows labs.empty and no module groups", async () => {
    backend({ me: STUDENT, labs: { status: 200, body: [] } });
    at("/labs", lang);
    expect(await screen.findByText(d["labs.empty"])).toBeTruthy();
    expect(screen.queryByTestId("module-1")).toBeNull();
  });
});

test("the dialog and cards change language with the toggle", async () => {
  backend({ me: STUDENT, sessions: [conflict("net-1", OPEN)] });
  at("/labs", "en");
  await screen.findByTestId("module-1");
  fireEvent.click(within(card("cat-b")).getByRole("button", { name: EN["labs.start"] }));
  await screen.findByRole("dialog");
  fireEvent.click(screen.getByRole("button", { name: EN["lang.switch.label"] }));
  const dialog = await screen.findByRole("dialog");
  await waitFor(() => expect(dialog.textContent).toContain(OPEN.he));
  expect(within(dialog).getByRole("button", { name: HE["labs.open_conflict.force"] })).toBeTruthy();
  expect(card("cat-b").textContent).toContain(CATALOG[0].title.he);
});

test("no Latin words leak into the Hebrew My labs page or its dialog", async () => {
  backend({ me: STUDENT, sessions: [conflict("net-1", { en: "Network basics", he: "יסודות רשת" })] });
  const view = at("/labs", "he");
  await screen.findByTestId("module-1");
  const scrub = (s: string) =>
    s.replace(NAME, "").replace(/\b(cat-[abc]|net-1)\b/g, "").replace(/\d+\/\d+/g, "");
  expect(scrub(view.container.textContent ?? "")).not.toMatch(/[A-Za-z]{4,}/);
  fireEvent.click(within(card("cat-b")).getByRole("button", { name: HE["labs.start"] }));
  await screen.findByRole("dialog");
  expect(scrub(view.container.textContent ?? "")).not.toMatch(/[A-Za-z]{4,}/);
});

// ------------------------------------------------------------------ #136: shell follow-ups

describe("/me failures are not 'signed out' (#136)", () => {
  test.each([
    [{ status: 503, body: { detail: "database unavailable" } } as Reply],
    [{ status: 500, body: {} } as Reply],
    ["network" as Reply],
  ])("/me answering %j shows login.error.unavailable, not the login form", async (reply) => {
    backend({ me: reply });
    at("/labs");
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain(EN["login.error.unavailable"]);
    expect(screen.queryByRole("heading", { name: EN["login.title"] })).toBeNull();
    expect(screen.queryByLabelText(EN["login.id"])).toBeNull();
    expect(screen.queryByRole("heading", { name: EN["labs.title"] })).toBeNull();
  });

  test("the unavailable message is Hebrew on a Hebrew page", async () => {
    backend({ me: { status: 503, body: {} } });
    at("/labs", "he");
    expect((await screen.findByRole("alert")).textContent).toContain(HE["login.error.unavailable"]);
  });

  test("401 still means signed out: the login page", async () => {
    backend({ me: { status: 401, body: { detail: "not_authenticated" } } });
    at("/labs");
    expect(await screen.findByRole("heading", { name: EN["login.title"] })).toBeTruthy();
    expect(screen.queryByRole("alert")).toBeNull();
  });
});

test("login navigates with replaceState, never pushState", async () => {
  const calls = backend({ login: STUDENT });
  const push = vi.spyOn(window.history, "pushState");
  const replace = vi.spyOn(window.history, "replaceState");
  at("/login");
  replace.mockClear();
  fireEvent.change(await screen.findByLabelText(EN["login.id"]), { target: { value: "1234567" } });
  fireEvent.change(screen.getByLabelText(EN["login.password"]), { target: { value: "x" } });
  fireEvent.click(screen.getByRole("button", { name: EN["login.submit"] }));
  await screen.findByRole("heading", { name: EN["labs.title"] });
  expect(window.location.pathname).toBe("/labs");
  expect(push).not.toHaveBeenCalled();
  expect(replace.mock.calls.some((c) => c[2] === "/labs")).toBe(true);
  expect(calls.some((c) => c.url === "/api/auth/login")).toBe(true);
});

test("logout navigates with replaceState, never pushState", async () => {
  backend({ me: STUDENT });
  const push = vi.spyOn(window.history, "pushState");
  const replace = vi.spyOn(window.history, "replaceState");
  at("/labs");
  await screen.findByTestId("module-1");
  replace.mockClear();
  fireEvent.click(screen.getByRole("button", { name: EN["nav.logout"] }));
  await screen.findByRole("heading", { name: EN["login.title"] });
  expect(window.location.pathname).toBe("/login");
  expect(push).not.toHaveBeenCalled();
  expect(replace.mock.calls.some((c) => c[2] === "/login")).toBe(true);
});

test("a signed-in student on /login sees My labs and the URL becomes /labs", async () => {
  backend({ me: STUDENT });
  at("/login");
  expect(await screen.findByRole("heading", { name: EN["labs.title"] })).toBeTruthy();
  await waitFor(() => expect(window.location.pathname).toBe("/labs"));
});

test("the toggle label is in the page language and contains the visible text; the visible text carries the other language", async () => {
  backend({});
  at("/login");
  await screen.findByRole("heading", { name: EN["login.title"] });
  const toHebrew = screen.getByRole("button", { name: EN["lang.switch.label"] });
  expect(toHebrew.hasAttribute("lang")).toBe(false);
  expect(toHebrew.getAttribute("aria-label")).toBe(EN["lang.switch.label"]);
  expect(EN["lang.switch.label"]).toContain(EN["lang.switch"]);
  const span1 = within(toHebrew).getByText(EN["lang.switch"]);
  expect(span1.tagName).toBe("SPAN");
  expect(span1.getAttribute("lang")).toBe("he");
  fireEvent.click(toHebrew);
  await screen.findByRole("heading", { name: HE["login.title"] });
  const toEnglish = screen.getByRole("button", { name: HE["lang.switch.label"] });
  expect(toEnglish.hasAttribute("lang")).toBe(false);
  expect(toEnglish.getAttribute("aria-label")).toBe(HE["lang.switch.label"]);
  expect(HE["lang.switch.label"]).toContain(HE["lang.switch"]);
  const span2 = within(toEnglish).getByText(HE["lang.switch"]);
  expect(span2.tagName).toBe("SPAN");
  expect(span2.getAttribute("lang")).toBe("en");
});

test("<html lang>, <html dir> and the title follow the language", async () => {
  backend({});
  at("/login");
  await screen.findByRole("heading", { name: EN["login.title"] });
  expect(document.documentElement.getAttribute("lang")).toBe("en");
  expect(document.documentElement.getAttribute("dir")).toBe("ltr");
  expect(document.title).toBe(EN["app.title"]);
  fireEvent.click(screen.getByRole("button", { name: EN["lang.switch.label"] }));
  await screen.findByRole("heading", { name: HE["login.title"] });
  expect(document.documentElement.getAttribute("lang")).toBe("he");
  expect(document.documentElement.getAttribute("dir")).toBe("rtl");
  expect(document.title).toBe(HE["app.title"]);
});

test("a stored Hebrew choice sets <html> and the title on load", async () => {
  backend({ me: STUDENT });
  at("/labs", "he");
  await screen.findByTestId("module-1");
  expect(document.documentElement.getAttribute("lang")).toBe("he");
  expect(document.documentElement.getAttribute("dir")).toBe("rtl");
  expect(document.title).toBe(HE["app.title"]);
});

test("the student name is rendered inside a <bdi>", async () => {
  backend({ me: STUDENT });
  at("/labs", "he");
  await screen.findByTestId("module-1");
  const el = screen.getByText(NAME);
  expect((el.closest("bdi") ?? el).tagName).toBe("BDI");
});

test("an act-wrapped popstate to a lab page still works with the catalog loaded", async () => {
  backend({ me: STUDENT });
  at("/labs");
  await screen.findByTestId("module-1");
  window.history.pushState({}, "", "/labs/cat-a");
  act(() => {
    window.dispatchEvent(new PopStateEvent("popstate"));
  });
  // Since #130 the lab route is the lab screen; this fake has no live session (404), so it
  // shows the no-session message instead of the slice-2 placeholder.
  expect(await screen.findByText(EN["lab.error.no_session"], { exact: false })).toBeTruthy();
  expect(screen.queryByTestId("module-1")).toBeNull();
});
