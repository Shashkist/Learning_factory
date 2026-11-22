/**
 * Exam for #332 (slice 2 of the visual design epic #330, D105): the student login page and My
 * labs. This file is what jsdom can see (criteria 2, 3, 4 and the text side of 6). Layout
 * (criterion 5) and the screenshots (criterion 7) are in tests/acceptance/issue-332-design.pw.ts.
 * Criterion 1 (tokens only: no colour literal, no physical left/right property) is already
 * enforced repo-wide by issue-331-design-foundation.test.tsx (AC2, AC3) over every file in src,
 * so it is not duplicated here. Criterion 6 is the existing suites (#10, #129, #147, #236 ...)
 * staying green, plus the few state checks below.
 *
 * CONTRACT the implementation must honour (names chosen by the exam)
 *
 *   Structure (semantic only, no class names): every lab on My labs is an element with role
 *   `article` (the <article> already there) whose heading (role heading) is the lab title in the
 *   current language. Everything below is looked up INSIDE that article.
 *
 *   Progress bar (AC2): inside the article, exactly one element with role `progressbar`
 *   (`<div role="progressbar">` or a `<progress>`), with an accessible name (aria-label,
 *   aria-labelledby or a <label>; any non-empty text) and
 *     - aria-valuemax = the lab's `tasks`, aria-valuenow = tasks passed:
 *       progress "not_started" -> 0, "<n>/<m>" -> n, "done" -> tasks.
 *     (for a <progress>, its `max` and `value` properties count instead of the aria attributes;
 *      aria-valuemin, when present, is 0.)
 *   The text "<n>/<m>" stays visible on in-progress cards (existing exam #129), and the card still
 *   shows `${tasks} ${labs.tasks}`.
 *
 *   Main action (AC3): exactly ONE button in the article. Per D109 its accessible name is
 *   EXACTLY the visible action word (labs.start / labs.continue / labs.redo), unchanged, and it has
 *   aria-describedby pointing at the card heading (an element with an id holding the lab title in
 *   the current language), so its accessible description includes the lab title. Clicking it still
 *   does what #129 pins (POST /api/sessions with {lab_id}).
 *
 *   Status badge (AC4): three NEW i18n keys, in both en.json and he.json, non-empty, pairwise
 *   different within a language, Hebrew text in he:
 *     labs.status.not_started   labs.status.in_progress   labs.status.done
 *   Each article shows its status as the full text of one element (getByText exact), for
 *   not_started / "<n>/<m>" / done respectively, and never the text of the other two statuses.
 *   (labs.progress.* from #129 may keep existing; labs.status.done may equal labs.progress.done.)
 *
 *   Login (AC1/6): a NEW key `login.intro` (both files, non-empty, Hebrew in he, different from
 *   login.title): the short line about what this is, visible on the login page. The error stays a
 *   role="alert" with the existing message; while a sign-in is pending the submit button is
 *   disabled and its accessible name still contains login.submit (D105 amendment (d): busy
 *   keeps its text), and a second click sends no second request.
 *
 *   Empty state: with no published labs the page shows labs.empty and no article. A load failure
 *   keeps role="alert" with labs.error.load.
 */
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import App from "../../src/App";
import en from "../../src/i18n/en.json";
import he from "../../src/i18n/he.json";

type Dict = Record<string, string>;
const DICTS: Record<"en" | "he", Dict> = { en: en as Dict, he: he as Dict };
const LANGS = ["en", "he"] as const;

type Reply = { status: number; body?: unknown } | "network";
type Calls = { url: string; method: string; body?: string }[];

const LABS = [
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
const EXPECT = {
  "cat-b": { now: 0, max: 4, status: "labs.status.not_started", action: "labs.start" },
  "cat-a": { now: 2, max: 3, status: "labs.status.in_progress", action: "labs.continue" },
  "cat-c": { now: 2, max: 2, status: "labs.status.done", action: "labs.redo" },
} as const;
const STATUS_KEYS = ["labs.status.not_started", "labs.status.in_progress", "labs.status.done"];

function backend(r: { labs?: Reply; me?: Reply; login?: Reply | (() => Promise<Reply>) }) {
  const calls: Calls = [];
  vi.spyOn(globalThis, "fetch").mockImplementation((async (url: RequestInfo | URL, init?: RequestInit) => {
    const u = String(url);
    const method = (init?.method ?? "GET").toUpperCase();
    calls.push({ url: u, method, body: typeof init?.body === "string" ? init.body : undefined });
    let reply: Reply | undefined;
    if (u === "/api/auth/me") reply = r.me ?? { status: 401, body: { detail: "not_authenticated" } };
    if (u === "/api/labs" && method === "GET") reply = r.labs ?? { status: 200, body: LABS };
    if (u === "/api/sessions" && method === "POST") reply = { status: 500, body: {} };
    if (u === "/api/auth/login" && method === "POST")
      reply = typeof r.login === "function" ? await r.login() : r.login;
    reply ??= { status: 404, body: { detail: "Not Found" } };
    if (reply === "network") throw new TypeError("offline");
    return {
      ok: reply.status >= 200 && reply.status < 300,
      status: reply.status,
      json: async () => reply.body ?? {},
      text: async () => JSON.stringify(reply.body ?? {}),
    } as Response;
  }) as typeof fetch);
  return calls;
}

const STUDENT: Reply = { status: 200, body: { kind: "student", name: "Dana Test" } };

function at(path: string, lang: "en" | "he") {
  window.history.replaceState({}, "", path);
  window.localStorage.setItem("lf-lang", lang);
  return render(<App />);
}

beforeEach(() => {
  window.localStorage.clear();
  window.history.replaceState({}, "", "/");
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const articleOf = (lab: (typeof LABS)[number], lang: "en" | "he") =>
  screen.getByRole("heading", { name: lab.title[lang] }).closest("[role=article], article") as HTMLElement;

// ------------------------------------------------------------------ i18n
test("the new keys exist in both languages, are non-empty, and the three statuses differ", () => {
  expect(Object.keys(DICTS.he).sort()).toEqual(Object.keys(DICTS.en).sort());
  for (const d of [DICTS.en, DICTS.he])
    for (const k of [...STATUS_KEYS, "login.intro"]) {
      expect(typeof d[k], k).toBe("string");
      expect(d[k].trim().length, k).toBeGreaterThan(0);
    }
  for (const d of [DICTS.en, DICTS.he]) {
    expect(new Set(STATUS_KEYS.map((k) => d[k])).size).toBe(3);
    expect(d["login.intro"]).not.toBe(d["login.title"]);
  }
  for (const k of [...STATUS_KEYS, "login.intro"]) expect(DICTS.he[k], k).toMatch(/[֐-׿]/);
  for (const k of [...STATUS_KEYS, "login.intro"]) expect(DICTS.en[k], k).not.toMatch(/[֐-׿]/);
});

// ------------------------------------------------------------------ My labs cards
describe.each(LANGS)("My labs cards in %s", (lang) => {
  const d = DICTS[lang];

  test("AC2: each card has a named progress bar with aria-valuenow / aria-valuemax", async () => {
    backend({ me: STUDENT });
    at("/labs", lang);
    await screen.findByRole("heading", { name: LABS[0].title[lang] });
    for (const lab of LABS) {
      const bars = within(articleOf(lab, lang)).getAllByRole("progressbar");
      expect(bars, `${lab.id}: exactly one progress bar`).toHaveLength(1);
      const bar = bars[0] as HTMLElement & { max?: number; value?: number };
      const named = within(articleOf(lab, lang)).getByRole("progressbar", { name: /\S/ });
      expect(named, `${lab.id}: the bar has an accessible name`).toBe(bar);
      const isNative = bar.tagName === "PROGRESS";
      const now = Number(isNative ? bar.value : bar.getAttribute("aria-valuenow"));
      const max = Number(isNative ? bar.max : bar.getAttribute("aria-valuemax"));
      const x = EXPECT[lab.id as keyof typeof EXPECT];
      expect(max, `${lab.id}: valuemax`).toBe(x.max);
      expect(now, `${lab.id}: valuenow`).toBe(x.now);
      const min = bar.getAttribute("aria-valuemin");
      if (min !== null) expect(Number(min)).toBe(0);
    }
  });

  test("AC2: the passed-out-of-total text stays visible next to the bar", async () => {
    backend({ me: STUDENT });
    at("/labs", lang);
    await screen.findByRole("heading", { name: LABS[1].title[lang] });
    const card = articleOf(LABS[1], lang);
    expect(card.textContent).toContain("2/3");
    expect(card.textContent).toContain(`3 ${d["labs.tasks"]}`);
  });

  test("AC3: the one main action is named by the action word and described by the lab title (D109)", async () => {
    backend({ me: STUDENT });
    at("/labs", lang);
    await screen.findByRole("heading", { name: LABS[0].title[lang] });
    for (const lab of LABS) {
      const article = articleOf(lab, lang);
      const card = within(article);
      const buttons = card.getAllByRole("button");
      expect(buttons, `${lab.id}: one button`).toHaveLength(1);
      const word = d[EXPECT[lab.id as keyof typeof EXPECT].action];
      expect(card.getByRole("button", { name: word }), `${lab.id}: name is exactly the action word`).toBe(
        buttons[0],
      );
      expect((buttons[0].getAttribute("aria-label") ?? word).trim(), `${lab.id}: name unchanged`).toBe(word);
      const ids = (buttons[0].getAttribute("aria-describedby") ?? "").split(/\s+/).filter(Boolean);
      expect(ids.length, `${lab.id}: button has aria-describedby`).toBeGreaterThan(0);
      const heading = card.getByRole("heading", { name: lab.title[lang] });
      expect(ids, `${lab.id}: aria-describedby points at the card heading`).toContain(heading.id);
      expect(heading.id, `${lab.id}: heading has an id`).not.toBe("");
      const description = ids
        .map((id) => document.getElementById(id)?.textContent ?? "")
        .join(" ");
      expect(description, `${lab.id}: description includes the lab title`).toContain(lab.title[lang]);
    }
  });

  test("AC3: the action still starts the lab (POST /api/sessions with the lab id)", async () => {
    const calls = backend({ me: STUDENT });
    at("/labs", lang);
    await screen.findByRole("heading", { name: LABS[0].title[lang] });
    const btn = within(articleOf(LABS[0], lang)).getByRole("button");
    await act(async () => {
      fireEvent.click(btn);
    });
    const posts = calls.filter((c) => c.url === "/api/sessions" && c.method === "POST");
    expect(posts).toHaveLength(1);
    expect(JSON.parse(posts[0].body ?? "null")).toMatchObject({ lab_id: "cat-b" });
  });

  test("AC4: each status badge has text, and only its own status text", async () => {
    backend({ me: STUDENT });
    at("/labs", lang);
    await screen.findByRole("heading", { name: LABS[0].title[lang] });
    for (const lab of LABS) {
      const card = within(articleOf(lab, lang));
      const own = EXPECT[lab.id as keyof typeof EXPECT].status;
      expect(card.getAllByText(d[own], { exact: true }).length, `${lab.id}: shows ${own}`).toBeGreaterThan(0);
      for (const other of STATUS_KEYS.filter((k) => d[k] !== d[own]))
        expect(card.queryByText(d[other], { exact: true }), `${lab.id}: not ${other}`).toBeNull();
    }
  });
});

// ------------------------------------------------------------------ My labs states
describe.each(LANGS)("My labs states in %s", (lang) => {
  const d = DICTS[lang];

  test("AC6: no published labs shows the empty text and no card", async () => {
    backend({ me: STUDENT, labs: { status: 200, body: [] } });
    at("/labs", lang);
    expect(await screen.findByText(d["labs.empty"])).toBeTruthy();
    expect(screen.queryAllByRole("article")).toHaveLength(0);
    expect(screen.queryAllByRole("progressbar")).toHaveLength(0);
  });

  test("AC6: a load failure keeps its alert text", async () => {
    backend({ me: STUDENT, labs: { status: 500, body: {} } });
    at("/labs", lang);
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain(d["labs.error.load"]);
  });
});

// ------------------------------------------------------------------ Login
describe.each(LANGS)("Login in %s", (lang) => {
  const d = DICTS[lang];

  test("AC1: the page shows the product name, the intro line, both fields and the button", async () => {
    backend({});
    at("/login", lang);
    expect(await screen.findByRole("heading", { name: d["login.title"] })).toBeTruthy();
    expect(within(screen.getByRole("banner")).getByText(d["app.title"], { exact: false })).toBeTruthy();
    expect(screen.getByText(d["login.intro"])).toBeTruthy();
    expect(screen.getByLabelText(d["login.id"])).toBeTruthy();
    expect(screen.getByLabelText(d["login.password"])).toBeTruthy();
    expect(screen.getByRole("button", { name: d["login.submit"] })).toBeTruthy();
  });

  test("AC6: a wrong password shows the existing message in an alert", async () => {
    backend({ login: { status: 401, body: { detail: "invalid_credentials" } } });
    at("/login", lang);
    fireEvent.change(await screen.findByLabelText(d["login.id"]), { target: { value: "123456782" } });
    fireEvent.change(screen.getByLabelText(d["login.password"]), { target: { value: "nope" } });
    fireEvent.click(screen.getByRole("button", { name: d["login.submit"] }));
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toBe(d["login.error.invalid_credentials"]);
  });

  test("AC6: while signing in the button is disabled and keeps its text, and a second click sends nothing", async () => {
    let release: (r: Reply) => void = () => {};
    const pending = new Promise<Reply>((res) => (release = res));
    const calls = backend({ login: () => pending });
    at("/login", lang);
    fireEvent.change(await screen.findByLabelText(d["login.id"]), { target: { value: "123456782" } });
    fireEvent.change(screen.getByLabelText(d["login.password"]), { target: { value: "pw" } });
    const btn = screen.getByRole("button", { name: d["login.submit"] });
    fireEvent.click(btn);
    await waitFor(() => expect((btn as HTMLButtonElement).disabled).toBe(true));
    expect(screen.getByRole("button", { name: (n) => n.includes(d["login.submit"]) })).toBe(btn);
    fireEvent.click(btn);
    expect(calls.filter((c) => c.url === "/api/auth/login")).toHaveLength(1);
    await act(async () => {
      release({ status: 401, body: { detail: "invalid_credentials" } });
    });
    await waitFor(() => expect((btn as HTMLButtonElement).disabled).toBe(false));
  });
});
