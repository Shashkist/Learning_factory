/**
 * Exam for #216 (split from #204, D66): the Student page builds the text of a flag from structured
 * fields, in the language of the page. The API behind it is pinned in
 * labs/manager/tests/acceptance/test_issue_216_flag_reasons.py. Every name below is fake.
 *
 * CONTRACT the implementation must honour (names chosen by the exam)
 *
 * Each flag of GET /api/admin/students/<key> carries, next to the old fields (id, scope, task_id,
 * reason, dismissed_at), three new ones, all null for a row written before this change:
 *   reason_code  "fast_task" | "fast_lab_min" | "fast_lab_median" | null
 *   seconds      number | null   what the student took (fast_task: from the task appearing to the
 *                                pass; the two lab codes: active seconds of the lab run)
 *   threshold    number | null   fast_task: min_task_seconds (seconds); fast_lab_min: the lab's
 *                                `fast_lab_min` in SECONDS; fast_lab_median: the class median in seconds
 *
 * Text. The page shows, inside data-testid="student-flag-<id>", the text of the i18n template for
 * the code, in the page language, with the placeholders replaced:
 *   admin.student.reason.fast_task        placeholders {task} {seconds} {threshold}
 *   admin.student.reason.fast_lab_min     placeholders {seconds} {threshold}
 *   admin.student.reason.fast_lab_median  placeholders {seconds} {threshold}
 *   {task}      the flag's task_id
 *   {seconds}   `seconds` as a whole number (rounded)
 *   {threshold} `threshold` as a whole number of seconds, except for fast_lab_min where it is shown in
 *               MINUTES (threshold / 60), because lab.yaml states that minimum in minutes
 * The English text of the stored `reason` is not shown for such a flag.
 *
 * Fallback. A flag whose reason_code is null, or is not one of the three codes, or whose `seconds` or
 * `threshold` the template needs is null (or whose fast_task has no task_id), shows `reason` exactly
 * as sent, in both languages. The old behaviour of the #164 exam stays true for those rows.
 *
 * i18n: the three keys exist in both files with the same key set, Hebrew in he, differing from the
 * English, each English text unique among all English texts, each containing its placeholders.
 */
import { render, screen } from "@testing-library/react";

import App from "../../src/App";
import en from "../../src/i18n/en.json";
import he from "../../src/i18n/he.json";

type Dict = Record<string, string>;
const EN = en as Dict;
const HE = he as Dict;
const DICTS: Record<"en" | "he", Dict> = { en: EN, he: HE };
const LANGS = ["en", "he"] as const;

type Flag = {
  id: string; scope: "task" | "lab"; task_id: string | null; reason: string; dismissed_at: string | null;
  reason_code?: string | null; seconds?: number | null; threshold?: number | null;
};

const KEY = "5b0e2f6c-7a1d-4c53-9d58-0f1f1f6f9a11";
const STORED = "STORED-ENGLISH-SENTENCE-xyz";
const REASON_KEYS = ["fast_task", "fast_lab_min", "fast_lab_median"].map((c) => `admin.student.reason.${c}`);

const FLAGS: Flag[] = [
  { id: "f-task", scope: "task", task_id: "02-two", reason: STORED, dismissed_at: null,
    reason_code: "fast_task", seconds: 9, threshold: 20 },
  { id: "f-min", scope: "lab", task_id: null, reason: STORED, dismissed_at: null,
    reason_code: "fast_lab_min", seconds: 95, threshold: 600 },
  { id: "f-median", scope: "lab", task_id: null, reason: STORED, dismissed_at: null,
    reason_code: "fast_lab_median", seconds: 61, threshold: 410 },
  { id: "f-old", scope: "task", task_id: "01-one", reason: "old row sentence 77", dismissed_at: null,
    reason_code: null, seconds: null, threshold: null },
  { id: "f-absent", scope: "task", task_id: "01-one", reason: "no fields at all 88", dismissed_at: null },
  { id: "f-unknown", scope: "task", task_id: "01-one", reason: "unknown code 99", dismissed_at: null,
    reason_code: "from_the_future", seconds: 5, threshold: 20 },
  { id: "f-nosec", scope: "task", task_id: "01-one", reason: "missing seconds 11", dismissed_at: null,
    reason_code: "fast_task", seconds: null, threshold: 20 },
  { id: "f-notask", scope: "task", task_id: null, reason: "missing task 22", dismissed_at: null,
    reason_code: "fast_task", seconds: 4, threshold: 20 },
  { id: "f-nothr", scope: "lab", task_id: null, reason: "missing threshold 33", dismissed_at: null,
    reason_code: "fast_lab_median", seconds: 61, threshold: null },
];

function student(flags: Flag[]) {
  return {
    key: KEY, name: "Fake Alef", cohort: "Spring group", note: null, disabled: false,
    access_until: null, cohort_access_until: "2027-03-01",
    labs: [
      {
        id: "linux-1", title: { en: "Linux basics", he: "יסודות לינוקס" }, status: "none",
        tasks_passed: 0, tasks_total: 1, attempts: 0, flags,
        tasks: [{ id: "01-one", passed: false, attempts: 0, seconds: null, median_seconds: null }],
      },
    ],
  };
}

function backend(flags: Flag[]) {
  vi.spyOn(globalThis, "fetch").mockImplementation((async (url: RequestInfo | URL) => {
    const path = new URL(String(url), "http://localhost").pathname;
    let body: unknown = { detail: "Not Found" };
    let status = 404;
    if (path === "/api/auth/me") [status, body] = [200, { kind: "admin", name: "fake-admin" }];
    if (path === "/api/admin/cohorts") [status, body] = [200, []];
    if (path === `/api/admin/students/${KEY}`) [status, body] = [200, student(flags)];
    return {
      ok: status === 200, status, json: async () => body, text: async () => JSON.stringify(body),
    } as Response;
  }) as typeof fetch);
}

function at(lang: "en" | "he") {
  window.history.replaceState({}, "", `/admin/students/${KEY}`);
  window.localStorage.setItem("lf-lang", lang);
  return render(<App />);
}

const fill = (tpl: string | undefined, vars: Record<string, string>) => {
  expect(typeof tpl, "the i18n template for this reason code exists").toBe("string");
  return Object.entries(vars).reduce((s, [k, v]) => s.split(`{${k}}`).join(v), tpl as string);
};
const flagText = (id: string) => screen.getByTestId(`student-flag-${id}`).textContent ?? "";

beforeEach(() => {
  window.localStorage.clear();
  window.history.replaceState({}, "", "/");
});
afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

// ------------------------------------------------------------------ i18n files

test("both language files have the three reason templates and the same key set", () => {
  expect(Object.keys(HE).sort()).toEqual(Object.keys(EN).sort());
  for (const d of [EN, HE])
    for (const k of REASON_KEYS) {
      expect(typeof d[k], k).toBe("string");
      expect(d[k].trim().length, k).toBeGreaterThan(0);
    }
});

test("each template holds the placeholders its code needs, in both languages", () => {
  for (const d of [EN, HE]) {
    for (const p of ["{task}", "{seconds}", "{threshold}"]) expect(d["admin.student.reason.fast_task"]).toContain(p);
    for (const k of ["fast_lab_min", "fast_lab_median"])
      for (const p of ["{seconds}", "{threshold}"]) expect(d[`admin.student.reason.${k}`]).toContain(p);
  }
});

test("the Hebrew templates are Hebrew and differ from the English ones", () => {
  for (const k of REASON_KEYS) {
    expect(HE[k], k).toMatch(/[֐-׿]/);
    expect(HE[k], k).not.toBe(EN[k]);
  }
});

test("every English reason template is unique among all English texts", () => {
  for (const k of REASON_KEYS) {
    expect(Object.keys(EN).filter((o) => o !== k && EN[o] === EN[k]), k).toEqual([]);
  }
});

// ------------------------------------------------------------------ the page, en and he

describe.each(LANGS)("flag reasons in %s", (lang) => {
  const d = DICTS[lang];

  test("a fast task flag is built from its task id, seconds and threshold", async () => {
    backend(FLAGS);
    at(lang);
    await screen.findByTestId("student-page");
    const want = fill(d["admin.student.reason.fast_task"], { task: "02-two", seconds: "9", threshold: "20" });
    expect(flagText("f-task")).toContain(want);
    expect(flagText("f-task")).not.toContain(STORED);
  });

  test("a fast lab flag under the minimum shows the threshold in minutes", async () => {
    backend(FLAGS);
    at(lang);
    await screen.findByTestId("student-page");
    const want = fill(d["admin.student.reason.fast_lab_min"], { seconds: "95", threshold: "10" });
    expect(flagText("f-min")).toContain(want);
    expect(flagText("f-min")).not.toContain(STORED);
  });

  test("a fast lab flag under the class median shows the median in seconds", async () => {
    backend(FLAGS);
    at(lang);
    await screen.findByTestId("student-page");
    const want = fill(d["admin.student.reason.fast_lab_median"], { seconds: "61", threshold: "410" });
    expect(flagText("f-median")).toContain(want);
    expect(flagText("f-median")).not.toContain(STORED);
  });

  test("seconds with a fraction are shown as a whole number", async () => {
    backend([{ ...FLAGS[0], id: "f-frac", seconds: 8.6, threshold: 20 }]);
    at(lang);
    await screen.findByTestId("student-page");
    const want = fill(d["admin.student.reason.fast_task"], { task: "02-two", seconds: "9", threshold: "20" });
    expect(flagText("f-frac")).toContain(want);
  });

  test("a flag without structured fields shows the reason as sent", async () => {
    backend(FLAGS);
    at(lang);
    await screen.findByTestId("student-page");
    expect(flagText("f-old")).toContain("old row sentence 77"); // fields null
    expect(flagText("f-absent")).toContain("no fields at all 88"); // fields not in the body
  });

  test("an unknown code, or a missing value the template needs, falls back to the reason", async () => {
    backend(FLAGS);
    at(lang);
    await screen.findByTestId("student-page");
    expect(flagText("f-unknown")).toContain("unknown code 99");
    expect(flagText("f-nosec")).toContain("missing seconds 11");
    expect(flagText("f-notask")).toContain("missing task 22");
    expect(flagText("f-nothr")).toContain("missing threshold 33");
  });

  test("the structured flags show no raw placeholder", async () => {
    backend(FLAGS);
    at(lang);
    await screen.findByTestId("student-page");
    for (const id of ["f-task", "f-min", "f-median"]) expect(flagText(id)).not.toMatch(/[{}]/);
  });
});

test("the same structured flag reads differently in Hebrew and in English", async () => {
  backend([FLAGS[0]]);
  const first = at("en");
  await screen.findByTestId("student-page");
  const english = flagText("f-task");
  first.unmount();
  at("he");
  await screen.findByTestId("student-page");
  const hebrew = flagText("f-task");
  expect(hebrew).toMatch(/[֐-׿]/);
  expect(hebrew).not.toBe(english);
});
