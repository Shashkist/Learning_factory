/**
 * Exam for #147 (review of #146 / #129): double clicks, a 401 while My labs is open, error vs
 * empty, the toggle label, Continue without a live session, and the conflict-dialog fallback.
 *
 * Observable contract (network shapes as in issue-129-my-labs.test.tsx)
 *   1  A card button (Start / Continue / Redo) and "End it and start this one" are `disabled`
 *      while their POST /api/sessions is pending: a double click sends ONE POST and adds ONE
 *      history entry (/labs/<id>); a slow 409 opens the dialog ONCE. After a failed start the
 *      button is enabled again.
 *   2  401 from GET /api/labs or POST /api/sessions: the login page (heading login.title), URL
 *      /login set with replaceState (never pushState), no alert.
 *   3  A failed GET /api/labs shows labs.error.load and NOT labs.empty. An empty catalog still
 *      shows labs.empty and no alert.
 *   4  The toggle label rule is pinned in issue-129-my-labs.test.tsx (changed first); here: the
 *      label contains the visible text, in both languages, and an assistive-tech name lookup
 *      by that label finds exactly one button.
 *   5  A lab at "n/m" with session_id null shows Start (not Continue); with a live session_id
 *      it keeps Continue.
 *   6  409 another_lab_open with title {} -> the dialog names the open lab by its lab_id.
 */
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { MockInstance } from "vitest";

import App from "../../src/App";
import en from "../../src/i18n/en.json";
import he from "../../src/i18n/he.json";

type Dict = Record<string, string>;
const EN = en as Dict;
const HE = he as Dict;
const DICTS: Record<"en" | "he", Dict> = { en: EN, he: HE };
const LANGS = ["en", "he"] as const;

type Plain = { status: number; body?: unknown };
type Reply = Plain | "network";
type Later = () => Promise<Plain>;
type Calls = { url: string; method: string; body?: string }[];

const STUDENT: Plain = { status: 200, body: { kind: "student", name: "Dana Test" } };

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

/** A reply that the test resolves by hand, to model a slow server. */
function slow() {
  let release!: (r: Plain) => void;
  const later: Later = () => new Promise<Plain>((res) => (release = res));
  return { later, release: (r: Plain) => release(r) };
}

function backend(replies: { labs?: Reply; sessions?: (Reply | Later)[] }) {
  const calls: Calls = [];
  const sessions = [...(replies.sessions ?? [])];
  vi.spyOn(globalThis, "fetch").mockImplementation((async (url: RequestInfo | URL, init?: RequestInit) => {
    const u = String(url);
    const method = (init?.method ?? "GET").toUpperCase();
    calls.push({ url: u, method, body: typeof init?.body === "string" ? init.body : undefined });
    let r: Reply | Later | undefined;
    if (u === "/api/auth/me") r = STUDENT;
    if (u === "/api/labs" && method === "GET") r = replies.labs ?? { status: 200, body: CATALOG };
    if (u === "/api/sessions" && method === "POST") r = sessions.shift() ?? { status: 500, body: {} };
    if (r === undefined) r = { status: 404, body: { detail: "Not Found" } };
    if (typeof r === "function") r = await r();
    if (r === "network") throw new TypeError("offline");
    const p = r;
    return {
      ok: p.status >= 200 && p.status < 300,
      status: p.status,
      json: async () => p.body ?? {},
      text: async () => JSON.stringify(p.body ?? {}),
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
const created = (lab: string) => ({ status: 201, body: { id: "new-1", lab_id: lab, status: "starting" } });
const conflict = (lab: string, title: unknown) => ({
  status: 409,
  body: { detail: { code: "another_lab_open", session_id: "sess-x", lab_id: lab, title } },
});
const OPEN = { en: "Network basics", he: "יסודות רשת" };
const tick = () => new Promise((r) => setTimeout(r, 20));

beforeEach(() => {
  window.localStorage.clear();
  window.history.replaceState({}, "", "/");
});
afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

// ------------------------------------------------------------------ 1 double clicks

describe.each(LANGS)("double clicks in %s", (lang) => {
  const d = DICTS[lang];

  test.each([
    ["cat-b", "labs.start"],
    ["cat-a", "labs.continue"],
    ["cat-c", "labs.redo"],
  ])("1 double clicks: %s %s is disabled while pending, one POST, one history entry", async (id, key) => {
    const s = slow();
    const calls = backend({ sessions: [s.later] });
    at("/labs", lang);
    await screen.findByTestId("module-1");
    const before = window.history.length;
    const btn = within(card(id)).getByRole("button", { name: d[key] }) as HTMLButtonElement;
    fireEvent.click(btn);
    await waitFor(() => expect(btn.disabled).toBe(true));
    fireEvent.click(btn);
    fireEvent.click(btn);
    await tick();
    expect(sessionPosts(calls)).toHaveLength(1);
    s.release(created(id));
    await waitFor(() => expect(window.location.pathname).toBe(`/labs/${id}`));
    await tick();
    expect(sessionPosts(calls)).toHaveLength(1);
    expect(window.history.length - before).toBe(1);
  });

  test("1 double clicks: a slow 409 opens the dialog once and sends one POST", async () => {
    const s = slow();
    const calls = backend({ sessions: [s.later] });
    at("/labs", lang);
    await screen.findByTestId("module-1");
    const btn = within(card("cat-b")).getByRole("button", { name: d["labs.start"] }) as HTMLButtonElement;
    fireEvent.click(btn);
    await waitFor(() => expect(btn.disabled).toBe(true));
    fireEvent.click(btn);
    s.release(conflict("net-1", OPEN));
    await screen.findByRole("dialog");
    await tick();
    expect(screen.getAllByRole("dialog")).toHaveLength(1);
    expect(sessionPosts(calls)).toHaveLength(1);
    expect(window.location.pathname).toBe("/labs");
  });

  test("1 double clicks: End it and start this one is disabled while pending, one forced POST, one history entry", async () => {
    const s = slow();
    const calls = backend({ sessions: [conflict("net-1", OPEN), s.later] });
    at("/labs", lang);
    await screen.findByTestId("module-1");
    fireEvent.click(within(card("cat-b")).getByRole("button", { name: d["labs.start"] }));
    const dialog = await screen.findByRole("dialog");
    const before = window.history.length;
    const force = within(dialog).getByRole("button", { name: d["labs.open_conflict.force"] }) as HTMLButtonElement;
    fireEvent.click(force);
    await waitFor(() => expect(force.disabled).toBe(true));
    fireEvent.click(force);
    fireEvent.click(force);
    await tick();
    expect(sessionPosts(calls)).toHaveLength(2);
    s.release(created("cat-b"));
    await waitFor(() => expect(window.location.pathname).toBe("/labs/cat-b"));
    await tick();
    expect(sessionPosts(calls)).toHaveLength(2);
    expect(JSON.parse(sessionPosts(calls)[1].body ?? "null")).toEqual({ lab_id: "cat-b", force: true });
    expect(window.history.length - before).toBe(1);
  });

  test("1 double clicks: after a failed start the button works again", async () => {
    const calls = backend({ sessions: [{ status: 500, body: {} }, created("cat-b")] });
    at("/labs", lang);
    await screen.findByTestId("module-1");
    const btn = () => within(card("cat-b")).getByRole("button", { name: d["labs.start"] }) as HTMLButtonElement;
    fireEvent.click(btn());
    await screen.findByRole("alert");
    await waitFor(() => expect(btn().disabled).toBe(false));
    fireEvent.click(btn());
    await waitFor(() => expect(window.location.pathname).toBe("/labs/cat-b"));
    expect(sessionPosts(calls)).toHaveLength(2);
  });
});

// ------------------------------------------------------------------ 2 signed out while open

describe("2 signed out while on My labs", () => {
  async function expectLoginPage(push: MockInstance, replace: MockInstance) {
    expect(await screen.findByRole("heading", { name: EN["login.title"] })).toBeTruthy();
    expect(window.location.pathname).toBe("/login");
    expect(push).not.toHaveBeenCalled();
    expect(replace.mock.calls.some((c: unknown[]) => c[2] === "/login")).toBe(true);
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.queryByTestId("module-1")).toBeNull();
  }

  test("2 a 401 from GET /api/labs returns to the login page via replaceState", async () => {
    backend({ labs: { status: 401, body: { detail: "not_authenticated" } } });
    const push = vi.spyOn(window.history, "pushState");
    const replace = vi.spyOn(window.history, "replaceState");
    at("/labs");
    replace.mockClear();
    await expectLoginPage(push, replace);
  });

  test("2 a 401 from POST /api/sessions returns to the login page via replaceState", async () => {
    backend({ sessions: [{ status: 401, body: { detail: "not_authenticated" } }] });
    const push = vi.spyOn(window.history, "pushState");
    const replace = vi.spyOn(window.history, "replaceState");
    at("/labs");
    await screen.findByTestId("module-1");
    replace.mockClear();
    fireEvent.click(within(card("cat-b")).getByRole("button", { name: EN["labs.start"] }));
    await expectLoginPage(push, replace);
  });

  test("2 a 401 on the forced POST (from the dialog) returns to the login page too", async () => {
    backend({ sessions: [conflict("net-1", OPEN), { status: 401, body: { detail: "not_authenticated" } }] });
    const push = vi.spyOn(window.history, "pushState");
    const replace = vi.spyOn(window.history, "replaceState");
    at("/labs");
    await screen.findByTestId("module-1");
    fireEvent.click(within(card("cat-b")).getByRole("button", { name: EN["labs.start"] }));
    const dialog = await screen.findByRole("dialog");
    replace.mockClear();
    fireEvent.click(within(dialog).getByRole("button", { name: EN["labs.open_conflict.force"] }));
    await expectLoginPage(push, replace);
    expect(screen.queryByRole("dialog")).toBeNull();
  });
});

// ------------------------------------------------------------------ 3 error or empty

describe.each(LANGS)("error or empty, not both, in %s", (lang) => {
  const d = DICTS[lang];

  test.each([
    [{ status: 503, body: { detail: "database unavailable" } } as Reply],
    [{ status: 500, body: {} } as Reply],
    ["network" as Reply],
  ])("3 a failed GET /api/labs (%j) shows labs.error.load and not labs.empty", async (reply) => {
    backend({ labs: reply });
    at("/labs", lang);
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain(d["labs.error.load"]);
    await tick();
    expect(screen.queryByText(d["labs.empty"])).toBeNull();
    expect(screen.queryByTestId("module-1")).toBeNull();
  });

  test("3 an empty catalog still shows labs.empty and no alert", async () => {
    backend({ labs: { status: 200, body: [] } });
    at("/labs", lang);
    expect(await screen.findByText(d["labs.empty"])).toBeTruthy();
    expect(screen.queryByRole("alert")).toBeNull();
  });
});

// ------------------------------------------------------------------ 4 toggle accessibility

test("4 the toggle label contains the visible text in both languages and names one button", async () => {
  backend({});
  window.history.replaceState({}, "", "/labs");
  render(<App />);
  await screen.findByTestId("module-1");
  for (const lang of ["en", "he"] as const) {
    const d = DICTS[lang];
    expect(d["lang.switch.label"]).toContain(d["lang.switch"]);
    const buttons = screen.getAllByRole("button", { name: d["lang.switch.label"] });
    expect(buttons).toHaveLength(1);
    expect(buttons[0].hasAttribute("lang")).toBe(false);
    expect(buttons[0].textContent).toContain(d["lang.switch"]);
    if (lang === "en") fireEvent.click(buttons[0]);
  }
});

// ------------------------------------------------------------------ 5 Continue needs a live session

describe.each(LANGS)("5 Continue on an ended session in %s", (lang) => {
  const d = DICTS[lang];
  const ENDED = [
    { ...CATALOG[1], id: "cat-e", progress: "1/4", tasks: 4, session_id: null },
    CATALOG[1],
  ];

  test("5 a lab at n/m with no live session shows Start, not Continue", async () => {
    backend({ labs: { status: 200, body: ENDED } });
    at("/labs", lang);
    await screen.findByTestId("module-1");
    const names = within(card("cat-e")).getAllByRole("button").map((b) => b.textContent?.trim());
    expect(names).toEqual([d["labs.start"]]);
    expect(card("cat-e").textContent).toContain("1/4");
  });

  test("5 a lab at n/m with a live session keeps Continue", async () => {
    backend({ labs: { status: 200, body: ENDED } });
    at("/labs", lang);
    await screen.findByTestId("module-1");
    const names = within(card("cat-a")).getAllByRole("button").map((b) => b.textContent?.trim());
    expect(names).toEqual([d["labs.continue"]]);
  });

  test("5 Start on that card POSTs {lab_id} with no force", async () => {
    const calls = backend({ labs: { status: 200, body: ENDED }, sessions: [created("cat-e")] });
    at("/labs", lang);
    await screen.findByTestId("module-1");
    fireEvent.click(within(card("cat-e")).getByRole("button", { name: d["labs.start"] }));
    await waitFor(() => expect(window.location.pathname).toBe("/labs/cat-e"));
    const body = JSON.parse(sessionPosts(calls)[0].body ?? "null");
    expect(body.lab_id).toBe("cat-e");
    expect(body.force).not.toBe(true);
  });
});

// ------------------------------------------------------------------ 6 nit: dialog falls back to lab_id

describe.each(LANGS)("6 conflict dialog with an empty title in %s", (lang) => {
  const d = DICTS[lang];

  test("6 the dialog names the open lab by its lab_id when title is {}", async () => {
    backend({ sessions: [conflict("net-1", {})] });
    at("/labs", lang);
    await screen.findByTestId("module-1");
    fireEvent.click(within(card("cat-b")).getByRole("button", { name: d["labs.start"] }));
    const dialog = await screen.findByRole("dialog");
    expect(dialog.textContent).toContain(d["labs.open_conflict.body"]);
    expect(dialog.textContent).toContain("net-1");
    expect(dialog.textContent).not.toMatch(/undefined|null|\[object/);
    expect(within(dialog).getByRole("button", { name: d["labs.open_conflict.force"] })).toBeTruthy();
  });
});
