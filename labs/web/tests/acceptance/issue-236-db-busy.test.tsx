/**
 * Exam for #236: the manager answers 503 {detail:{code:"db_busy"}} when the database pool is
 * exhausted. The lab screen words it (key `lab.error.db_busy`, in en and he) the way it words
 * `check_timeout`: an alert specific to "the service is busy", not the setup wording and not the
 * generic "could not be loaded" text. The task stays usable and a retry works.
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
const PASS = ok({
  passed: true, attempts: 1, reason: null,
  checklist: [{ n: 1, passed: true, hint: null }],
});
const DB_BUSY = err(503, { code: "db_busy", message: "The service is busy." });

function backend(routes: Record<string, Reply[]>) {
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
    const key = `${(init?.method ?? "GET").toUpperCase()} ${String(url)}`;
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
}

const panel = () => screen.getByTestId("task-panel");
const button = (name: string) => within(panel()).getByRole("button", { name });

beforeEach(() => window.localStorage.clear());
afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("the dictionaries word db_busy", () => {
  test.each(LANGS)("lab.error.db_busy exists in %s and is its own wording", (lang) => {
    const d = DICTS[lang];
    expect((d["lab.error.db_busy"] ?? "").trim().length).toBeGreaterThan(0);
    expect(d["lab.error.db_busy"]).not.toBe(d["lab.error.setup_timeout"]);
    expect(d["lab.error.db_busy"]).not.toBe(d["lab.error.check_timeout"]);
    expect(d["lab.error.db_busy"]).not.toBe(d["lab.error.load"]);
  });

  test("the Hebrew text is Hebrew", () => {
    expect(DICTS.he["lab.error.db_busy"] ?? "").toMatch(/[֐-׿]/);
  });
});

describe.each(LANGS)("a Check answering 503 db_busy in %s", (lang) => {
  const d = DICTS[lang];

  test("shows the busy wording and a retry works", async () => {
    backend({
      [`GET ${API}/tasks/current`]: [ok(HANDS)],
      [`POST ${API}/tasks/02-make-file/check`]: [DB_BUSY, PASS],
    });
    window.history.replaceState({}, "", "/labs/lab-1");
    window.localStorage.setItem("lf-lang", lang);
    render(<App />);
    await screen.findByTestId("task-panel");
    fireEvent.click(button(d["lab.check"]));
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain(d["lab.error.db_busy"]);
    expect(alert.textContent).not.toContain(d["lab.error.setup_timeout"]);
    expect(alert.textContent).not.toContain(d["lab.error.check_timeout"]);
    expect(alert.textContent).not.toContain(d["lab.error.load"]);
    expect((button(d["lab.check"]) as HTMLButtonElement).disabled).toBe(false);
    fireEvent.click(button(d["lab.check"]));
    await within(panel()).findByRole("button", { name: d["lab.next"] });
    expect(screen.queryByRole("alert")).toBeNull();
  });
});
