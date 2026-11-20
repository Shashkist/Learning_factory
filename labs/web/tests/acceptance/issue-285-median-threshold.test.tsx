/**
 * Exam for #285 (D87): the API sends the class-median threshold as whole seconds (ceil of the
 * median, here 1001 for a median of 1000.5). The Student page shows it unchanged, in both languages.
 * The API side is pinned in labs/manager/tests/acceptance/test_issue_285_median_threshold.py.
 */
import { render, screen } from "@testing-library/react";

import App from "../../src/App";
import en from "../../src/i18n/en.json";
import he from "../../src/i18n/he.json";

type Dict = Record<string, string>;
const DICTS: Record<"en" | "he", Dict> = { en: en as Dict, he: he as Dict };
const KEY = "5b0e2f6c-7a1d-4c53-9d58-0f1f1f6f9a11";

const FLAG = {
  id: "f-median", scope: "lab", task_id: null, reason: "STORED-ENGLISH", dismissed_at: null,
  reason_code: "fast_lab_median", seconds: 100, threshold: 1001,
};

beforeEach(() => {
  window.localStorage.clear();
  vi.spyOn(globalThis, "fetch").mockImplementation((async (url: RequestInfo | URL) => {
    const path = new URL(String(url), "http://localhost").pathname;
    let body: unknown = { detail: "Not Found" };
    let status = 404;
    if (path === "/api/auth/me") [status, body] = [200, { kind: "admin", name: "fake-admin" }];
    if (path === "/api/admin/cohorts") [status, body] = [200, []];
    if (path === `/api/admin/students/${KEY}`)
      [status, body] = [200, {
        key: KEY, name: "Fake Alef", cohort: "Spring group", note: null, disabled: false,
        access_until: null, cohort_access_until: "2027-03-01",
        labs: [{
          id: "linux-1", title: { en: "Linux basics", he: "יסודות לינוקס" }, status: "none",
          tasks_passed: 0, tasks_total: 1, attempts: 0, flags: [FLAG],
          tasks: [{ id: "01-one", passed: false, attempts: 0, seconds: null, median_seconds: null }],
        }],
      }];
    return {
      ok: status === 200, status, json: async () => body, text: async () => JSON.stringify(body),
    } as Response;
  }) as typeof fetch);
});
afterEach(() => {
  vi.restoreAllMocks();
});

test.each(["en", "he"] as const)("a median threshold of 1001 s is shown as 1001 in %s", async (lang) => {
  window.history.replaceState({}, "", `/admin/students/${KEY}`);
  window.localStorage.setItem("lf-lang", lang);
  render(<App />);
  await screen.findByTestId("student-page");
  const want = DICTS[lang]["admin.student.reason.fast_lab_median"]
    .split("{seconds}").join("100").split("{threshold}").join("1001");
  const text = screen.getByTestId("student-flag-f-median").textContent ?? "";
  expect(text).toContain(want);
  expect(text).toContain("1001");
});
