/**
 * Exam for #224 item 2: a Check that runs out of time is worded for Check, not for setting up.
 *
 * The manager answers a Check or Answer with no time left as 504 {detail:{code:"check_timeout"}}.
 * The lab screen must show an alert that is specific to Check (key `lab.error.check_timeout`, in en and
 * he): not the setup text, not the generic "could not be loaded" text. The task stays usable and a
 * retry works. A 504 `setup_timeout` on Skip keeps the setup wording.
 */
import { fireEvent, render, screen, within } from "@testing-library/react";

import App from "../../src/App";
import en from "../../src/i18n/en.json";
import he from "../../src/i18n/he.json";

type Dict = Record<string, string>;
const DICTS: Record<"en" | "he", Dict> = { en: en as Dict, he: he as Dict };
const LANGS = ["en", "he"] as const;

type Reply = { status: number; body?: unknown };

const SID = "sess-1";
const API = `/api/sessions/${SID}`;
const ok = (body: unknown): Reply => ({ status: 200, body });
const err = (status: number, detail: unknown): Reply => ({ status, body: { detail } });

const HANDS = {
  id: "02-make-file", index: 2, total: 4, kind: "hands-on", state: "shown",
  title: { en: "Make a file", he: "יוצרים קובץ" },
  text: { en: "# Make a file\n\nCreate it.\n", he: "# יוצרים קובץ\n\nצרו אותו.\n" },
  checklist: [{ en: "The file exists", he: "הקובץ קיים" }],
};
const QUIZ = {
  id: "03-quiz", index: 3, total: 4, kind: "quiz", state: "shown",
  title: { en: "Which command lists files?", he: "איזו פקודה מציגה קבצים?" },
  question: { en: "Which command lists files?", he: "איזו פקודה מציגה קבצים?" },
  options: [{ en: "cd", he: "cd" }, { en: "ls", he: "ls" }],
};
const PASS = ok({
  passed: true, attempts: 1, reason: null,
  checklist: [{ n: 1, passed: true, hint: null }],
});
const CHECK_TIMEOUT = err(504, { code: "check_timeout", message: "The check took too long." });

function backend(routes: Record<string, Reply[]>) {
  const calls: { url: string; method: string }[] = [];
  const queues = new Map<string, Reply[]>();
  const all: Record<string, Reply[]> = {
    "GET /api/auth/me": [ok({ kind: "student", name: "Dana Test" })],
    "GET /api/labs": [ok([])],
    "GET /api/sessions/current": [
      ok({ id: SID, lab_id: "lab-1", status: "running", expires_at: new Date(Date.now() + 3_300_000).toISOString() }),
    ],
    [`POST ${API}/heartbeat`]: [{ status: 204 }],
    [`POST ${API}/end`]: [{ status: 204 }],
    ...routes,
  };
  vi.spyOn(globalThis, "fetch").mockImplementation((async (url: RequestInfo | URL, init?: RequestInit) => {
    const u = String(url);
    const method = (init?.method ?? "GET").toUpperCase();
    calls.push({ url: u, method });
    const key = `${method} ${u}`;
    let r: Reply = { status: 404, body: { detail: "Not Found" } };
    if (all[key]) {
      if (!queues.has(key)) queues.set(key, [...all[key]]);
      const q = queues.get(key) as Reply[];
      r = q.length > 1 ? (q.shift() as Reply) : q[0];
    }
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

const panel = () => screen.getByTestId("task-panel");
const button = (name: string) => within(panel()).getByRole("button", { name });
const count = (calls: { url: string; method: string }[], method: string, path: string) =>
  calls.filter((c) => c.method === method && c.url === path).length;

async function open(task: object, routes: Record<string, Reply[]>, lang: "en" | "he") {
  const calls = backend({ [`GET ${API}/tasks/current`]: [ok(task)], ...routes });
  window.history.replaceState({}, "", "/labs/lab-1");
  window.localStorage.setItem("lf-lang", lang);
  render(<App />);
  await screen.findByTestId("task-panel");
  return calls;
}

beforeEach(() => window.localStorage.clear());
afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("the dictionaries word a Check timeout", () => {
  test.each(LANGS)("lab.error.check_timeout exists in %s and is not the setup wording", (lang) => {
    const d = DICTS[lang];
    expect((d["lab.error.check_timeout"] ?? "").trim().length).toBeGreaterThan(0);
    expect(d["lab.error.check_timeout"]).not.toBe(d["lab.error.setup_timeout"]);
  });

  test("the English text talks about the check, the Hebrew text is Hebrew", () => {
    expect(DICTS.en["lab.error.check_timeout"].toLowerCase()).toContain("check");
    expect(DICTS.en["lab.error.check_timeout"]).not.toMatch(/setting up/i);
    expect(DICTS.he["lab.error.check_timeout"]).toMatch(/[֐-׿]/);
  });
});

describe.each(LANGS)("a Check answering 504 check_timeout in %s", (lang) => {
  const d = DICTS[lang];

  test("shows the Check wording, not the setup wording, and a retry works", async () => {
    const calls = await open(HANDS, { [`POST ${API}/tasks/02-make-file/check`]: [CHECK_TIMEOUT, PASS] }, lang);
    fireEvent.click(button(d["lab.check"]));
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain(d["lab.error.check_timeout"]);
    expect(alert.textContent).not.toContain(d["lab.error.setup_timeout"]);
    expect(alert.textContent).not.toContain(d["lab.error.load"]);
    expect(within(panel()).getByRole("heading", { name: HANDS.title[lang] })).toBeTruthy();
    expect((button(d["lab.check"]) as HTMLButtonElement).disabled).toBe(false);
    fireEvent.click(button(d["lab.check"]));
    await within(panel()).findByRole("button", { name: d["lab.next"] });
    expect(count(calls, "POST", `${API}/tasks/02-make-file/check`)).toBe(2);
    expect(screen.queryByRole("alert")).toBeNull();
  });

  test("an Answer answering 504 check_timeout also shows the Check wording", async () => {
    await open(QUIZ, { [`POST ${API}/tasks/03-quiz/answer`]: [CHECK_TIMEOUT] }, lang);
    fireEvent.click(within(panel()).getByRole("radio", { name: "ls" }));
    fireEvent.click(button(d["lab.answer"]));
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain(d["lab.error.check_timeout"]);
    expect(alert.textContent).not.toContain(d["lab.error.setup_timeout"]);
  });

  test("a Skip answering 504 setup_timeout still shows the setup wording", async () => {
    await open(
      HANDS,
      { [`POST ${API}/tasks/current/skip`]: [err(504, { code: "setup_timeout", message: "Setting up the task took too long." })] },
      lang,
    );
    fireEvent.click(button(d["lab.skip"]));
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain(d["lab.error.setup_timeout"]);
    expect(alert.textContent).not.toContain(d["lab.error.check_timeout"]);
  });
});
