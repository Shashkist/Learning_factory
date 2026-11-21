/**
 * Exam for #348 (follow-up of #334, D110): the student page shows "Active time" and "Last activity"
 * summary cards from the two new API fields, and two review nits that are behaviour.
 *
 * The student API now returns, next to the existing fields (the fixtures of the #334 exam do not
 * have them, that exam stays valid):
 *   active_seconds: number         total active seconds over all the student's lab runs (0 for none)
 *   last_activity: string | null   ISO 8601 instant, or null when the student never started a lab
 *
 * What is pinned (the English card labels are in the issue; the Hebrew labels only have to be
 * Hebrew text from the dictionary, found through the English text, so the key names stay free):
 *   1. Two cards (class "card", outside the lab articles, before the first lab) labelled
 *      "Active time" and "Last activity" in en; in he the dictionary text of the key whose English
 *      text is that label (a key must exist, and the Hebrew text must differ from the English).
 *   2. Active time is formatted: for 3725 s the card shows neither "3725" nor "3,725", and shows
 *      an hours-and-minutes figure (1 hour, 2 minutes: the digits 1 and 2 in that order). For 0 it
 *      shows a 0 figure, not an empty card.
 *   3. Last activity shows the teacher's local time (the class table's localStamp format
 *      `YYYY-MM-DD HH:MM`), never the raw ISO text; null shows the "never" text of the class table
 *      (admin.class.never) and no "null", "undefined" or "Invalid Date".
 *   4. The cohort edit legend names its cohort (every legend used to read the same).
 *   5. The import "too many lines" error is inside the same fieldset as the textarea.
 */
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

import App from "../../src/App";
import en from "../../src/i18n/en.json";
import he from "../../src/i18n/he.json";
import { localStamp } from "../../src/localStamp";

type Dict = Record<string, string>;
const DICT: Record<"en" | "he", Dict> = { en: en as Dict, he: he as Dict };
const KEY = "6f1c2b0e-4d1a-4c57-9a43-0a1b2c3d4e5f";
const LANGS = ["en", "he"] as const;

const LAB = (id: string) => ({
  id,
  title: { en: `Lab ${id}`, he: `מעבדה ${id}` },
  status: "in_progress",
  tasks_passed: 1,
  tasks_total: 2,
  attempts: 1,
  tasks: [{ id: "t1", passed: true, attempts: 1, seconds: 30, median_seconds: 40 }],
  flags: [] as object[],
});
const student = (over: object = {}) => ({
  key: KEY,
  name: "Dana",
  cohort: "Alpha",
  note: null,
  disabled: false,
  access_until: null,
  cohort_access_until: "2027-01-31",
  active_seconds: 0,
  last_activity: null,
  labs: [LAB("linux"), LAB("git")],
  ...over,
});

const COHORTS = [
  { id: 1, name: "Alpha", access_until: "2027-01-31", students: 1 },
  { id: 2, name: "Beta", access_until: "2027-01-31", students: 0 },
];

function backend(page: object) {
  globalThis.fetch = vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
    const path = new URL(String(url), "http://localhost").pathname;
    const key = `${init?.method ?? "GET"} ${path}`;
    const table: Record<string, unknown> = {
      "GET /api/auth/me": { kind: "admin", name: "Teacher" },
      "GET /api/admin/cohorts": COHORTS,
      [`GET /api/admin/students/${KEY}`]: page,
    };
    const hit = table[key];
    return new Response(JSON.stringify(hit ?? { detail: "not_found" }), {
      status: hit === undefined ? 404 : 200,
      headers: { "Content-Type": "application/json" },
    });
  }) as typeof fetch;
}

function open(path: string, lang: "en" | "he" = "en") {
  window.localStorage.setItem("lf-lang", lang);
  window.history.pushState({}, "", path);
  return render(<App />);
}

afterEach(() => {
  cleanup();
  window.localStorage.clear();
  vi.restoreAllMocks();
});

/** The dictionary text, in `lang`, of the key whose English text is `label`. */
function labelIn(lang: "en" | "he", label: string): string {
  const keys = Object.keys(DICT.en).filter((k) => DICT.en[k] === label);
  expect(keys.length, `a dictionary key with the English text "${label}"`).toBeGreaterThan(0);
  if (lang === "he") for (const k of keys) expect(DICT.he[k], `${k} translated`).not.toBe(DICT.en[k]);
  return DICT[lang][keys[0]];
}

/** The summary cards (outside the lab articles, before the first lab), as elements. */
async function cards(): Promise<Element[]> {
  const firstLab = await screen.findByTestId("student-lab-linux");
  const page = firstLab.closest("section") ?? document.body;
  return Array.from(page.querySelectorAll(".card"))
    .filter((c) => !c.closest('[data-testid^="student-lab-"]'))
    .filter((c) => c.compareDocumentPosition(firstLab) & Node.DOCUMENT_POSITION_FOLLOWING);
}

async function card(lang: "en" | "he", label: string): Promise<Element> {
  const text = labelIn(lang, label);
  const found = (await cards()).filter((c) => (c.textContent ?? "").includes(text));
  expect(found, `a summary card labelled "${text}" (${lang})`).toHaveLength(1);
  return found[0];
}

// ================================================================ AC2: the two cards
describe("AC2: the student page shows Active time and Last activity cards", () => {
  for (const lang of LANGS) {
    it(`both cards exist before the first lab (${lang})`, async () => {
      backend(student({ active_seconds: 3725, last_activity: "2026-11-20T09:00:00Z" }));
      open(`/admin/students/${KEY}`, lang);
      await card(lang, "Active time");
      await card(lang, "Last activity");
    });

    it(`active time is formatted, not a raw seconds count (${lang})`, async () => {
      backend(student({ active_seconds: 3725 }));
      open(`/admin/students/${KEY}`, lang);
      const text = (await card(lang, "Active time")).textContent ?? "";
      expect(text).not.toContain("3725");
      expect(text).not.toContain("3,725");
      expect(text, "an hours-and-minutes figure for 1 h 2 min").toMatch(/1\D+2/);
    });

    it(`active time of a student with no run shows a zero figure (${lang})`, async () => {
      backend(student({ active_seconds: 0 }));
      open(`/admin/students/${KEY}`, lang);
      const label = labelIn(lang, "Active time");
      const text = ((await card(lang, "Active time")).textContent ?? "").replace(label, "");
      expect(text).toMatch(/0/);
      expect(text).not.toMatch(/undefined|null|NaN/);
    });

    it(`last activity shows the local time, not the raw ISO text (${lang})`, async () => {
      const iso = "2026-11-20T09:00:00Z";
      backend(student({ last_activity: iso }));
      open(`/admin/students/${KEY}`, lang);
      const text = (await card(lang, "Last activity")).textContent ?? "";
      expect(text).toContain(localStamp(iso));
      expect(text).not.toContain("T09:00:00");
      expect(text).not.toContain(iso);
    });

    it(`last activity of a student who never started shows the "never" text (${lang})`, async () => {
      backend(student({ last_activity: null }));
      open(`/admin/students/${KEY}`, lang);
      const text = (await card(lang, "Last activity")).textContent ?? "";
      expect(text).toContain(DICT[lang]["admin.class.never"]);
      expect(text).not.toMatch(/null|undefined|Invalid Date|NaN/);
    });
  }

  it("the old cards stay: labs done, tasks passed, open flags", async () => {
    backend(student());
    open(`/admin/students/${KEY}`);
    const text = (await cards()).map((c) => c.textContent ?? "").join(" | ");
    for (const key of ["admin.student.card.labs", "admin.student.card.tasks", "admin.student.card.flags"])
      expect(text).toContain(DICT.en[key]);
  });
});

// ================================================================ review nit: the cohort legend names its cohort
describe("the cohort edit legend names the cohort", () => {
  for (const lang of LANGS) {
    it(`each cohort's edit fieldset legend contains its own name (${lang})`, async () => {
      backend(student());
      open("/admin", lang);
      const a = await screen.findByTestId("cohort-edit-1");
      const b = await screen.findByTestId("cohort-edit-2");
      const legend = (form: HTMLElement) => form.querySelector("fieldset > legend")?.textContent ?? "";
      expect(legend(a)).toContain("Alpha");
      expect(legend(b)).toContain("Beta");
      expect(legend(a)).not.toBe(legend(b));
      expect(legend(a)).not.toContain("Beta");
    });
  }
});

// ================================================================ AC4 (the part jsdom can see)
describe("AC4: the import 'too many lines' error is inside the textarea's fieldset", () => {
  it("the alert with the error text sits in the same fieldset as the textarea", async () => {
    backend(student());
    open("/admin");
    const text = (await screen.findByLabelText(DICT.en["admin.import.text"])) as HTMLTextAreaElement;
    fireEvent.change(text, { target: { value: Array.from({ length: 2001 }, (_, i) => `${i + 1},Fake ${i}`).join("\n") } });
    fireEvent.click(screen.getByRole("button", { name: DICT.en["admin.import.submit"] }));
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain(DICT.en["admin.import.too_many"]);
    const fieldset = text.closest("fieldset");
    expect(fieldset, "the textarea is in a fieldset").not.toBeNull();
    expect(fieldset!.contains(alert), "the error is inside that fieldset").toBe(true);
    await waitFor(() => expect(text.getAttribute("aria-invalid")).toBe("true"));
  });
});
