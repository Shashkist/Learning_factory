/**
 * Exam for #444 (follow-up of #154, D128, D55): the teacher views show when a student opened a
 * task's solution. The API behind it is described in
 * labs/manager/tests/acceptance/test_issue_444_teacher_solution_views.py.
 *
 * CONTRACT the implementation must honour (names chosen by the exam)
 *
 * Student page (/admin/students/<key>, data-testid="student-page")
 *   GET /api/admin/students/<key> -> every task of every lab may carry `solution_viewed_at`:
 *   an ISO string or null (a missing field counts as null).
 *   Inside the task row data-testid="student-task-<lab id>-<task id>" there is an element
 *   data-testid="student-solution-<lab id>-<task id>":
 *     - viewed: its text contains admin.student.solution_viewed and the first 10 characters of
 *       the ISO string as sent (YYYY-MM-DD, no time zone conversion), and not
 *       admin.student.solution_not_viewed;
 *     - not viewed (null): its text is admin.student.solution_not_viewed and contains no date.
 *   Every task row has this element (viewed or not). The page makes no new request.
 *
 * Class table (/admin/class)
 *   GET /api/admin/class -> every cell may carry `solution_viewed: boolean` (missing = false).
 *   A cell with solution_viewed true holds an element with title=admin.class.solution_viewed whose
 *   text is "📖". A cell with false or missing has no such element. The mark is separate from the
 *   fast flag: the ⚡ element (title=admin.class.flag, text exactly "⚡") shows only for `flag`,
 *   never for the view, and the view element does not contain the ⚡. The status text of the cell
 *   ("✓", "<n>/<m>", "–") is unchanged by the mark. The "flagged only" checkbox is not about views
 *   (the page does no filtering of its own, so it still just sends flagged=1).
 *
 * i18n keys (flat, both files, same key set, Hebrew in he, none equal to the English text, and
 * each English text unique among all English texts)
 *   admin.student.solution_viewed, admin.student.solution_not_viewed, admin.class.solution_viewed
 */
import { render, screen, waitFor, within } from "@testing-library/react";

import App from "../../src/App";
import en from "../../src/i18n/en.json";
import he from "../../src/i18n/he.json";

type Dict = Record<string, string>;
const EN = en as Dict;
const HE = he as Dict;
const DICTS: Record<"en" | "he", Dict> = { en: EN, he: HE };
const LANGS = ["en", "he"] as const;

const ADMIN = { status: 200, body: { kind: "admin", name: "fake-admin" } };
const KEY = "5b0e2f6c-7a1d-4c53-9d58-0f1f1f6f9a11";
const OTHER_KEY = "9c1d7e22-3b4a-4f6e-8a10-2d3c4b5a6e77";
const SEEN = "2026-11-20T23:30:00+00:00";

type Task = {
  id: string; passed: boolean; attempts: number; seconds: number | null;
  median_seconds: number | null; solution_viewed_at?: string | null;
};
const student = () => ({
  key: KEY, name: "Fake Alef", cohort: "Spring group", note: null, disabled: false,
  access_until: null, cohort_access_until: "2027-03-01",
  labs: [
    {
      id: "linux-1", title: { en: "Linux basics", he: "יסודות לינוקס" }, status: "1/3",
      tasks_passed: 1, tasks_total: 3, attempts: 4, flags: [],
      tasks: [
        { id: "01-one", passed: true, attempts: 1, seconds: 41, median_seconds: 25, solution_viewed_at: SEEN },
        { id: "02-two", passed: false, attempts: 3, seconds: null, median_seconds: 33, solution_viewed_at: null },
        { id: "03-three", passed: false, attempts: 0, seconds: null, median_seconds: null },
      ] as Task[],
    },
  ],
});

const LABS = [
  { id: "linux-1", title: { en: "Linux basics", he: "יסודות לינוקס" } },
  { id: "git-1", title: { en: "First commits", he: "קומיטים ראשונים" } },
];
type Cell = { status: string; flag: boolean; solution_viewed?: boolean };
const ROWS: { key: string; name: string; cohort: string; last_activity: string | null; cells: Record<string, Cell> }[] = [
  { key: KEY, name: "Fake Alef", cohort: "Spring group", last_activity: null,
    cells: { "linux-1": { status: "2/3", flag: false, solution_viewed: true }, "git-1": { status: "none", flag: false, solution_viewed: false } } },
  { key: OTHER_KEY, name: "Fake Bet", cohort: "Spring group", last_activity: null,
    cells: { "linux-1": { status: "done", flag: true, solution_viewed: false }, "git-1": { status: "1/4", flag: false } } },
  { key: "3e5f8a10-aaaa-4bbb-8ccc-0d1e2f3a4b5c", name: "Fake Gimel", cohort: "Spring group", last_activity: null,
    cells: { "linux-1": { status: "done", flag: true, solution_viewed: true }, "git-1": { status: "none", flag: false, solution_viewed: false } } },
];

function backend() {
  const calls: string[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation((async (url: RequestInfo | URL) => {
    const u = String(url);
    calls.push(u);
    const path = new URL(u, "http://localhost").pathname;
    let body: unknown = { detail: "Not Found" };
    let status = 404;
    if (u === "/api/auth/me") ({ status, body } = ADMIN);
    if (path === "/api/admin/cohorts") ({ status, body } = { status: 200, body: [] });
    if (path === "/api/admin/class") ({ status, body } = { status: 200, body: { labs: LABS, rows: ROWS } });
    if (path === `/api/admin/students/${KEY}`) ({ status, body } = { status: 200, body: student() });
    return {
      ok: status >= 200 && status < 300, status,
      json: async () => body ?? {}, text: async () => JSON.stringify(body ?? {}),
    } as Response;
  }) as typeof fetch);
  return calls;
}

function at(lang: "en" | "he", path: string) {
  window.history.replaceState({}, "", path);
  window.localStorage.setItem("lf-lang", lang);
  return render(<App />);
}

beforeEach(() => {
  window.localStorage.clear();
  window.history.replaceState({}, "", "/");
});
afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

// ------------------------------------------------------------------ i18n files

const KEYS = ["admin.student.solution_viewed", "admin.student.solution_not_viewed", "admin.class.solution_viewed"];

test("both language files have the solution viewed keys, non-empty, and the same key set", () => {
  expect(Object.keys(HE).sort()).toEqual(Object.keys(EN).sort());
  for (const d of [EN, HE])
    for (const k of KEYS) {
      expect(typeof d[k], k).toBe("string");
      expect(d[k].trim().length, k).toBeGreaterThan(0);
    }
});

test("the Hebrew solution viewed texts are Hebrew and differ from the English ones", () => {
  for (const k of KEYS) {
    expect(HE[k], k).toMatch(/[֐-׿]/);
    expect(HE[k], k).not.toBe(EN[k]);
  }
});

test("every English solution viewed text is unique among all English texts", () => {
  for (const k of KEYS) {
    const same = Object.keys(EN).filter((o) => o !== k && EN[o] === EN[k]);
    expect(same, k).toEqual([]);
  }
});

// ------------------------------------------------------------------ student page

test.each(LANGS)("student page shows per task whether and when the solution was opened (%s)", async (lang) => {
  const d = DICTS[lang];
  backend();
  at(lang, `/admin/students/${KEY}`);
  const page = await screen.findByTestId("student-page");
  const viewed = within(page).getByTestId("student-solution-linux-1-01-one");
  expect(viewed.textContent).toContain(d["admin.student.solution_viewed"]);
  expect(viewed.textContent).toContain("2026-11-20");
  expect(viewed.textContent).not.toContain(d["admin.student.solution_not_viewed"]);
  for (const task of ["02-two", "03-three"]) {
    const not = within(page).getByTestId(`student-solution-linux-1-${task}`);
    expect(not.textContent, task).toContain(d["admin.student.solution_not_viewed"]);
    expect(not.textContent, task).not.toMatch(/\d{4}-\d{2}-\d{2}/);
  }
});

test("the solution element sits inside its task row and the page makes no new request", async () => {
  const calls = backend();
  at("en", `/admin/students/${KEY}`);
  const page = await screen.findByTestId("student-page");
  for (const task of ["01-one", "02-two", "03-three"]) {
    const row = within(page).getByTestId(`student-task-linux-1-${task}`);
    expect(within(row).getByTestId(`student-solution-linux-1-${task}`)).toBeTruthy();
  }
  const api = calls.filter((c) => c.startsWith("/api/admin")).map((c) => new URL(c, "http://x").pathname);
  expect(api.filter((p) => p === `/api/admin/students/${KEY}`)).toHaveLength(1);
  expect(api.filter((p) => p.startsWith("/api/admin/students/") && p !== `/api/admin/students/${KEY}`)).toEqual([]);
});

// ------------------------------------------------------------------ class table

const cellOf = (i: number, lab: string) => screen.getByTestId(`class-cell-${i}-${lab}`);

test.each(LANGS)("class table marks the cells where a solution was opened, apart from the bolt (%s)", async (lang) => {
  const d = DICTS[lang];
  backend();
  at(lang, "/admin/class");
  await screen.findByTestId("class-table");
  const title = d["admin.class.solution_viewed"];

  const viewedOnly = within(cellOf(0, "linux-1")).getByTitle(title);
  expect(viewedOnly.textContent).toBe("📖");
  expect(within(cellOf(0, "linux-1")).queryByTitle(d["admin.class.flag"])).toBeNull();

  // flag only: the bolt, no view mark
  expect(within(cellOf(1, "linux-1")).queryByTitle(title)).toBeNull();
  expect(within(cellOf(1, "linux-1")).getByTitle(d["admin.class.flag"]).textContent).toBe("⚡");

  // both: two separate elements
  const both = cellOf(2, "linux-1");
  const mark = within(both).getByTitle(title);
  const bolt = within(both).getByTitle(d["admin.class.flag"]);
  expect(mark).not.toBe(bolt);
  expect(mark.textContent).toBe("📖");
  expect(bolt.textContent).toBe("⚡");
  expect(mark.contains(bolt) || bolt.contains(mark)).toBe(false);
});

test("cells with false or a missing solution_viewed have no mark, and the status text is unchanged", async () => {
  backend();
  at("en", "/admin/class");
  await screen.findByTestId("class-table");
  const title = EN["admin.class.solution_viewed"];
  expect(within(cellOf(0, "git-1")).queryByTitle(title)).toBeNull(); // false
  expect(within(cellOf(1, "git-1")).queryByTitle(title)).toBeNull(); // missing
  expect(cellOf(0, "git-1").textContent).toBe("–");
  expect(cellOf(1, "git-1").textContent).toBe("1/4");
  expect(cellOf(0, "linux-1").textContent).toContain("2/3");
  expect(cellOf(1, "linux-1").textContent).toContain("✓");
});

test("the view mark shows up only in cells of the class table that the API marks", async () => {
  backend();
  at("en", "/admin/class");
  await screen.findByTestId("class-table");
  await waitFor(() => expect(screen.getAllByTitle(EN["admin.class.solution_viewed"])).toHaveLength(2));
  expect(screen.getAllByTitle(EN["admin.class.flag"])).toHaveLength(2);
});
