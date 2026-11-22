/**
 * Exam for #458 (pins #444, D128 amended): the student page shows the time the solution was
 * opened in the teacher's local time, and the date sits in an LTR <bdi>.
 *
 * The suite runs with TZ=UTC (vite.config.ts), so the cases here switch the process time zone to
 * Asia/Jerusalem for their own duration (process.env.TZ, which Node applies at once) and restore it.
 *
 * CONTRACT
 *   A view at 2026-11-20T22:30:00+00:00 shows 2026-11-21 (00:30, UTC+2 in November) in
 *   data-testid="student-solution-<lab>-<task>", not the UTC day 2026-11-20, in en and he.
 *   The date text sits inside a <bdi dir="ltr"> within that element, so it does not reorder in
 *   Hebrew.
 */
import { render, screen, within } from "@testing-library/react";

import App from "../../src/App";
import en from "../../src/i18n/en.json";
import he from "../../src/i18n/he.json";

// The web tsconfig has no Node types; vitest runs in Node, where assigning TZ takes effect at once.
declare const process: { env: Record<string, string | undefined> };

type Dict = Record<string, string>;
const DICTS: Record<"en" | "he", Dict> = { en: en as Dict, he: he as Dict };
const LANGS = ["en", "he"] as const;

const KEY = "5b0e2f6c-7a1d-4c53-9d58-0f1f1f6f9a11";
const SEEN = "2026-11-20T22:30:00+00:00";

const student = () => ({
  key: KEY, name: "Fake Alef", cohort: "Spring group", note: null, disabled: false,
  access_until: null, cohort_access_until: "2027-03-01",
  labs: [
    {
      id: "linux-1", title: { en: "Linux basics", he: "יסודות לינוקס" }, status: "1/2",
      tasks_passed: 1, tasks_total: 2, attempts: 1, flags: [],
      tasks: [
        { id: "01-one", passed: true, attempts: 1, seconds: 41, median_seconds: 25, solution_viewed_at: SEEN },
        { id: "02-two", passed: false, attempts: 0, seconds: null, median_seconds: null, solution_viewed_at: null },
      ],
    },
  ],
});

function backend() {
  vi.spyOn(globalThis, "fetch").mockImplementation((async (url: RequestInfo | URL) => {
    const u = String(url);
    const path = new URL(u, "http://localhost").pathname;
    let body: unknown = { detail: "Not Found" };
    let status = 404;
    if (u === "/api/auth/me") ({ status, body } = { status: 200, body: { kind: "admin", name: "fake-admin" } });
    if (path === "/api/admin/cohorts") ({ status, body } = { status: 200, body: [] });
    if (path === `/api/admin/students/${KEY}`) ({ status, body } = { status: 200, body: student() });
    return {
      ok: status >= 200 && status < 300, status,
      json: async () => body ?? {}, text: async () => JSON.stringify(body ?? {}),
    } as Response;
  }) as typeof fetch);
}

const ORIGINAL_TZ = process.env.TZ;
beforeEach(() => {
  process.env.TZ = "Asia/Jerusalem";
  window.localStorage.clear();
});
afterEach(() => {
  if (ORIGINAL_TZ === undefined) delete process.env.TZ;
  else process.env.TZ = ORIGINAL_TZ;
  vi.restoreAllMocks();
});

async function solutionCell(lang: "en" | "he") {
  backend();
  window.history.replaceState({}, "", `/admin/students/${KEY}`);
  window.localStorage.setItem("lf-lang", lang);
  render(<App />);
  const page = await screen.findByTestId("student-page");
  return within(page).getByTestId("student-solution-linux-1-01-one");
}

test("the test process really runs in Asia/Jerusalem", () => {
  expect(new Date(SEEN).getDate()).toBe(21);
});

test.each(LANGS)("the solution view time shows in the teacher's local day, not the UTC day (%s)", async (lang) => {
  const cell = await solutionCell(lang);
  expect(cell.textContent).toContain(DICTS[lang]["admin.student.solution_viewed"]);
  expect(cell.textContent).toContain("2026-11-21");
  expect(cell.textContent).not.toContain("2026-11-20");
});

test.each(LANGS)("the solution view time is shown as YYYY-MM-DD HH:MM local (%s)", async (lang) => {
  const cell = await solutionCell(lang);
  expect(cell.textContent).toContain("2026-11-21 00:30");
});

test.each(LANGS)("the solution view date sits in an LTR bdi so it does not reorder in Hebrew (%s)", async (lang) => {
  const cell = await solutionCell(lang);
  const bdis = Array.from(cell.querySelectorAll("bdi"));
  const holder = bdis.find((b) => b.textContent?.includes("2026-11-21"));
  expect(holder, "a <bdi> holds the date").toBeTruthy();
  expect(holder!.getAttribute("dir")).toBe("ltr");
  expect(holder!.textContent).toMatch(/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$/);
});
