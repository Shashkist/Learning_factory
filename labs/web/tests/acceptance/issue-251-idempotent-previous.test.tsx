/**
 * Exam for #251: the lab screen sends the task the student is on with Previous too, so a retry of
 * an applied Previous is a no-op on the server (wire shape: labs/manager/tests/acceptance/
 * test_issue_251_idempotent_previous.py).
 *
 *   POST /api/sessions/<sid>/tasks/current/previous   body JSON {"from": "<id of the task on screen>"}
 *
 * A retry after a failed answer (here 503) sends the same `from` again. After the screen shows
 * the previous task, the next Previous sends that task's id.
 */
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import App from "../../src/App";
import en from "../../src/i18n/en.json";

type Dict = Record<string, string>;
const EN = en as Dict;

type Reply = { status: number; body?: unknown };
type Calls = { url: string; method: string; body?: string }[];

const SID = "sess-1";
const API = `/api/sessions/${SID}`;
const ok = (body: unknown): Reply => ({ status: 200, body });
const err = (status: number, detail: unknown): Reply => ({ status, body: { detail } });

const task = (id: string, index: number) => ({
  id, index, total: 4, kind: "hands-on", state: "shown",
  title: { en: `Task ${id}`, he: `משימה ${id}` },
  text: { en: `# Task ${id}\n\nDo it.\n`, he: `# משימה ${id}\n\nעשו.\n` },
  checklist: [{ n: 1, text: { en: "Done", he: "בוצע" } }],
});
const T2 = task("02-make-file", 2);
const T3 = task("03-edit-file", 3);
const T4 = task("04-last-one", 4);
const DB_BUSY = err(503, { code: "db_busy", message: "The service is busy." });

function backend(routes: Record<string, Reply[]>): Calls {
  const calls: Calls = [];
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
    const method = (init?.method ?? "GET").toUpperCase();
    const key = `${method} ${String(url)}`;
    calls.push({ url: String(url), method, body: typeof init?.body === "string" ? init.body : undefined });
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
const posts = (calls: Calls, path: string) => calls.filter((c) => c.method === "POST" && c.url === path);
const sent = (c: Calls[number]) => JSON.parse(c.body ?? "null");

async function open() {
  window.history.replaceState({}, "", "/labs/lab-1");
  render(<App />);
  await screen.findByTestId("task-panel");
}

beforeEach(() => window.localStorage.clear());
afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("Previous sends the task on screen", () => {
  test("Previous sends {from: <current task id>}", async () => {
    const calls = backend({
      [`GET ${API}/tasks/current`]: [ok(T3)],
      [`POST ${API}/tasks/current/previous`]: [ok(T2)],
    });
    await open();
    fireEvent.click(button(EN["lab.previous"]));
    await within(panel()).findByRole("heading", { name: T2.title.en });
    const [call] = posts(calls, `${API}/tasks/current/previous`);
    expect(sent(call)).toEqual({ from: "03-edit-file" });
  });

  test("a retry after a 503 sends the same from again, and the next move sends the new task id", async () => {
    const calls = backend({
      [`GET ${API}/tasks/current`]: [ok(T4)],
      [`POST ${API}/tasks/current/previous`]: [DB_BUSY, ok(T3), ok(T2)],
    });
    await open();
    fireEvent.click(button(EN["lab.previous"]));
    await screen.findByRole("alert");
    await waitFor(() => expect((button(EN["lab.previous"]) as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(button(EN["lab.previous"]));
    await within(panel()).findByRole("heading", { name: T3.title.en });
    fireEvent.click(button(EN["lab.previous"]));
    await within(panel()).findByRole("heading", { name: T2.title.en });
    const bodies = posts(calls, `${API}/tasks/current/previous`).map(sent);
    expect(bodies).toEqual([{ from: "04-last-one" }, { from: "04-last-one" }, { from: "03-edit-file" }]);
  });
});
