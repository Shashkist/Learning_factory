/**
 * Exam for #172 (follow-up of #11 slice 1, D59): the Cohorts page gets an edit form per cohort, a
 * student list per cohort, and import errors shown as translated text. en and he.
 * The API is described in labs/manager/tests/acceptance/test_issue_11_cohorts.py.
 *
 * NOTE for the manager: this exam REPLACES one #11 expectation. issue-11-cohorts.test.tsx
 * ("the result shows the added count and one row per line with the server's messages") expects an
 * error row to show the server's `message` verbatim. #172 says the opposite: the row shows the
 * translation of `code`. That one #11 test has to be updated when this exam goes green. Everything
 * else in the #11 exam stays valid, so the new labels below have DIFFERENT keys from the create
 * form's (screen.getByLabelText on the create labels must keep finding exactly one input).
 *
 * CONTRACT the implementation must honour (names chosen by the exam)
 *
 * Edit form (one per cohort), data-testid="cohort-edit-<id>"
 *   - three inputs found by label inside the form: name (admin.cohorts.edit.name), password
 *     (admin.cohorts.edit.password, type=password) and access date (admin.cohorts.edit.access,
 *     type=date), and a submit button (admin.cohorts.save).
 *   - the name and the date start with the cohort's current values; the password starts empty.
 *   - submit -> PATCH /api/admin/cohorts/<id> (same-origin, JSON). The body carries the typed
 *     `name` and `access_until`; `password` is in the body only when the field is not empty.
 *     Then GET /api/admin/cohorts again, so the list shows the new name. Any non-2xx or network
 *     failure shows role=alert with admin.error.save and keeps what was typed. After a success the
 *     password field is empty again. Passwords are never rendered as text.
 * Student list, inside the row data-testid="cohort-<id>"
 *   - a button (admin.students.show). Clicking it calls GET /api/admin/cohorts/<id>/students and
 *     renders data-testid="students-<id>" (anywhere on the page) listing the students in the
 *     API's order, each with its name, and its note when there is one; a disabled student shows
 *     the text admin.students.disabled; an empty list shows admin.students.empty instead; a failed
 *     request shows role=alert with admin.error.load. No student call is made before the click.
 * Import errors
 *   - an error row (data-testid="import-row-<line>") shows t("admin.import.error."+code) and NOT
 *     the server's `message`; the row still contains `${admin.import.line} ${line}`. A code
 *     without a key of its own shows admin.import.error.unknown. An added row is unchanged.
 *
 * i18n keys (flat, both files, same key set, Hebrew in he, none equal to the English text)
 *   admin.cohorts.edit.name, admin.cohorts.edit.password, admin.cohorts.edit.access,
 *   admin.cohorts.save, admin.students.show, admin.students.disabled, admin.students.empty,
 *   admin.import.error.invalid_id, .missing_name, .invalid_name, .duplicate_in_paste,
 *   .already_exists, .unknown
 *   The exam does not pin the wording, only that the shown text is the dictionary's text.
 */
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import App from "../../src/App";
import en from "../../src/i18n/en.json";
import he from "../../src/i18n/he.json";

type Dict = Record<string, string>;
const EN = en as Dict;
const HE = he as Dict;
const DICTS: Record<"en" | "he", Dict> = { en: EN, he: HE };
const LANGS = ["en", "he"] as const;

type Cohort = { id: number; name: string; access_until: string; students: number };
type Student = { name: string; note: string | null; disabled: boolean; access_until: string | null };
type Reply = { status: number; body?: unknown } | "network";
type Calls = { url: string; method: string; body?: string }[];

const ADMIN: Reply = { status: 200, body: { kind: "admin", name: "fake-admin" } };
const SEED: Cohort[] = [
  { id: 7, name: "Spring group", access_until: "2027-03-01", students: 3 },
  { id: 9, name: "Evening group", access_until: "2027-06-30", students: 0 },
];
const STUDENTS: Student[] = [
  { name: "Fake Alef", note: "sits at the front", disabled: false, access_until: null },
  { name: "Fake Bet", note: null, disabled: true, access_until: "2027-01-01" },
  { name: "Fake Gimel", note: null, disabled: false, access_until: null },
];
const PASSWORD = "new-class-pass-for-exam";

function backend(opts: {
  patch?: Reply; students?: Reply; import?: Reply; cohorts?: Cohort[];
} = {}) {
  const calls: Calls = [];
  const cohorts = (opts.cohorts ?? SEED).map((c) => ({ ...c }));
  vi.spyOn(globalThis, "fetch").mockImplementation((async (url: RequestInfo | URL, init?: RequestInit) => {
    const u = String(url);
    const method = (init?.method ?? "GET").toUpperCase();
    const body = typeof init?.body === "string" ? init.body : undefined;
    calls.push({ url: u, method, body });
    let r: Reply | undefined;
    if (u === "/api/auth/me") r = ADMIN;
    if (u === "/api/admin/cohorts" && method === "GET") r = { status: 200, body: cohorts };
    const patch = /^\/api\/admin\/cohorts\/(\d+)$/.exec(u);
    if (patch && method === "PATCH") {
      r = opts.patch;
      if (!r) {
        const b = JSON.parse(body ?? "{}");
        const c = cohorts.find((x) => x.id === Number(patch[1]));
        if (c) {
          if (b.name) c.name = b.name;
          if (b.access_until) c.access_until = b.access_until;
        }
        r = { status: 200, body: c };
      }
    }
    if (/^\/api\/admin\/cohorts\/\d+\/students$/.test(u) && method === "GET")
      r = opts.students ?? { status: 200, body: STUDENTS };
    if (/^\/api\/admin\/cohorts\/\d+\/import$/.test(u) && method === "POST")
      r = opts.import ?? { status: 200, body: { added: 0, rows: [] } };
    if (r === undefined) r = { status: 404, body: { detail: "Not Found" } };
    if (r === "network") throw new TypeError("offline");
    return {
      ok: r.status >= 200 && r.status < 300,
      status: r.status,
      json: async () => r.body ?? {},
      text: async () => JSON.stringify(r.body ?? {}),
    } as Response;
  }) as typeof fetch);
  return calls;
}

function at(lang: "en" | "he") {
  window.history.replaceState({}, "", "/admin");
  window.localStorage.setItem("lf-lang", lang);
  return render(<App />);
}

const patches = (calls: Calls) => calls.filter((c) => c.method === "PATCH");
const studentCalls = (calls: Calls) => calls.filter((c) => /\/students$/.test(c.url));
const listCalls = (calls: Calls) => calls.filter((c) => c.method === "GET" && c.url === "/api/admin/cohorts");

beforeEach(() => {
  window.localStorage.clear();
  window.history.replaceState({}, "", "/");
});
afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

// ------------------------------------------------------------------ i18n files

const CODES = ["invalid_id", "missing_name", "invalid_name", "duplicate_in_paste", "already_exists", "unknown"];
const KEYS = [
  "admin.cohorts.edit.name", "admin.cohorts.edit.password", "admin.cohorts.edit.access",
  "admin.cohorts.save", "admin.students.show", "admin.students.disabled", "admin.students.empty",
  ...CODES.map((c) => `admin.import.error.${c}`),
];

test("both language files have the #172 keys, non-empty, with the same key set", () => {
  expect(Object.keys(HE).sort()).toEqual(Object.keys(EN).sort());
  for (const d of [EN, HE])
    for (const k of KEYS) {
      expect(typeof d[k], k).toBe("string");
      expect(d[k].trim().length, k).toBeGreaterThan(0);
    }
});

test("the Hebrew #172 texts are Hebrew and differ from the English ones", () => {
  for (const k of KEYS) {
    expect(HE[k], k).toMatch(/[֐-׿]/);
    expect(HE[k], k).not.toBe(EN[k]);
  }
});

test("each import error code has its own text", () => {
  for (const d of [EN, HE]) {
    const texts = CODES.map((c) => d[`admin.import.error.${c}`]);
    expect(new Set(texts).size).toBe(texts.length);
  }
});

// ------------------------------------------------------------------ the edit form

describe.each(LANGS)("editing a cohort in %s", (lang) => {
  const d = DICTS[lang];
  const form = (id: number) => screen.getByTestId(`cohort-edit-${id}`);
  const field = (id: number, key: string) => within(form(id)).getByLabelText(d[key]) as HTMLInputElement;

  test("every cohort has an edit form prefilled with its name and date, password empty", async () => {
    backend();
    at(lang);
    await screen.findByTestId("cohort-7");
    expect(field(7, "admin.cohorts.edit.name").value).toBe("Spring group");
    expect(field(7, "admin.cohorts.edit.access").value).toBe("2027-03-01");
    expect(field(7, "admin.cohorts.edit.access").type).toBe("date");
    expect(field(7, "admin.cohorts.edit.password").value).toBe("");
    expect(field(7, "admin.cohorts.edit.password").type).toBe("password");
    expect(field(9, "admin.cohorts.edit.name").value).toBe("Evening group");
    expect(within(form(9)).getByRole("button", { name: d["admin.cohorts.save"] })).toBeTruthy();
  });

  test("the create form still has exactly one input per label", async () => {
    backend();
    at(lang);
    await screen.findByTestId("cohort-7");
    for (const k of ["admin.cohorts.name", "admin.cohorts.password", "admin.cohorts.access"])
      expect(screen.getAllByLabelText(d[k])).toHaveLength(1);
  });

  test("saving sends a PATCH with the typed name and date and shows the new name", async () => {
    const calls = backend();
    at(lang);
    await screen.findByTestId("cohort-7");
    fireEvent.change(field(7, "admin.cohorts.edit.name"), { target: { value: "Spring group B" } });
    fireEvent.change(field(7, "admin.cohorts.edit.access"), { target: { value: "2027-09-15" } });
    fireEvent.click(within(form(7)).getByRole("button", { name: d["admin.cohorts.save"] }));
    await waitFor(() => expect(screen.getByTestId("cohort-7").textContent).toContain("Spring group B"));
    const sent = patches(calls);
    expect(sent).toHaveLength(1);
    expect(sent[0].url).toBe("/api/admin/cohorts/7");
    const body = JSON.parse(sent[0].body ?? "null");
    expect(body.name).toBe("Spring group B");
    expect(body.access_until).toBe("2027-09-15");
    expect("password" in body).toBe(false);
    expect(listCalls(calls).length).toBeGreaterThanOrEqual(2); // re-read after the save
    expect(screen.getByTestId("cohort-7").textContent).toContain("2027-09-15");
    expect(patches(calls).every((c) => c.url !== "/api/admin/cohorts/9")).toBe(true);
  });

  test("a typed password is sent once and the field is emptied; it is never shown", async () => {
    const calls = backend();
    at(lang);
    await screen.findByTestId("cohort-9");
    fireEvent.change(field(9, "admin.cohorts.edit.password"), { target: { value: PASSWORD } });
    fireEvent.click(within(form(9)).getByRole("button", { name: d["admin.cohorts.save"] }));
    await waitFor(() => expect(patches(calls)).toHaveLength(1));
    expect(patches(calls)[0].url).toBe("/api/admin/cohorts/9");
    expect(JSON.parse(patches(calls)[0].body ?? "null").password).toBe(PASSWORD);
    await waitFor(() => expect(field(9, "admin.cohorts.edit.password").value).toBe(""));
    expect(document.body.textContent).not.toContain(PASSWORD);
  });

  test("a refused save (422) shows the save alert and keeps what was typed", async () => {
    backend({ patch: { status: 422, body: { detail: [{ type: "value_error", loc: ["body"], msg: "x" }] } } });
    at(lang);
    await screen.findByTestId("cohort-7");
    fireEvent.change(field(7, "admin.cohorts.edit.name"), { target: { value: "Typed name" } });
    fireEvent.click(within(form(7)).getByRole("button", { name: d["admin.cohorts.save"] }));
    expect((await screen.findByRole("alert")).textContent).toContain(d["admin.error.save"]);
    expect(field(7, "admin.cohorts.edit.name").value).toBe("Typed name");
    expect(screen.getByTestId("cohort-7").textContent).not.toContain("Typed name");
  });

  test("a network failure on save shows the save alert", async () => {
    backend({ patch: "network" });
    at(lang);
    await screen.findByTestId("cohort-7");
    fireEvent.click(within(form(7)).getByRole("button", { name: d["admin.cohorts.save"] }));
    expect((await screen.findByRole("alert")).textContent).toContain(d["admin.error.save"]);
  });
});

// ------------------------------------------------------------------ the student list

describe.each(LANGS)("the student list in %s", (lang) => {
  const d = DICTS[lang];
  const show = (id: number) =>
    fireEvent.click(within(screen.getByTestId(`cohort-${id}`)).getByRole("button", { name: d["admin.students.show"] }));

  test("no student request is made until the button is clicked", async () => {
    const calls = backend();
    at(lang);
    await screen.findByTestId("cohort-7");
    await new Promise((r) => setTimeout(r, 20));
    expect(studentCalls(calls)).toHaveLength(0);
    expect(screen.queryByTestId("students-7")).toBeNull();
  });

  test("clicking loads the cohort's students and lists them in order with notes", async () => {
    const calls = backend();
    at(lang);
    await screen.findByTestId("cohort-7");
    show(7);
    const list = await screen.findByTestId("students-7");
    expect(studentCalls(calls).map((c) => c.url)).toEqual(["/api/admin/cohorts/7/students"]);
    const text = list.textContent ?? "";
    const at1 = text.indexOf("Fake Alef");
    const at2 = text.indexOf("Fake Bet");
    const at3 = text.indexOf("Fake Gimel");
    expect(at1).toBeGreaterThanOrEqual(0);
    expect(at2).toBeGreaterThan(at1);
    expect(at3).toBeGreaterThan(at2);
    expect(text).toContain("sits at the front");
  });

  test("a disabled student is marked, the others are not", async () => {
    backend();
    at(lang);
    await screen.findByTestId("cohort-7");
    show(7);
    const list = await screen.findByTestId("students-7");
    const text = list.textContent ?? "";
    const marks = text.split(d["admin.students.disabled"]).length - 1;
    expect(marks).toBe(1);
    const bet = text.indexOf("Fake Bet");
    const mark = text.indexOf(d["admin.students.disabled"]);
    const gimel = text.indexOf("Fake Gimel");
    expect(mark).toBeGreaterThan(bet);
    expect(mark).toBeLessThan(gimel);
  });

  test("an empty cohort says so", async () => {
    backend({ students: { status: 200, body: [] } });
    at(lang);
    await screen.findByTestId("cohort-9");
    show(9);
    await waitFor(() => expect(screen.getByText(d["admin.students.empty"])).toBeTruthy());
  });

  test("a failed request shows the load alert and no list", async () => {
    backend({ students: { status: 500, body: {} } });
    at(lang);
    await screen.findByTestId("cohort-7");
    show(7);
    expect((await screen.findByRole("alert")).textContent).toContain(d["admin.error.load"]);
    expect(screen.queryByTestId("students-7")).toBeNull();
  });
});

// ------------------------------------------------------------------ translated import errors

describe.each(LANGS)("import errors in %s", (lang) => {
  const d = DICTS[lang];

  const run = async (reply: Reply) => {
    backend({ import: reply });
    at(lang);
    const select = (await screen.findByLabelText(d["admin.import.cohort"])) as HTMLSelectElement;
    const option = within(select).getByRole("option", { name: "Spring group" }) as HTMLOptionElement;
    fireEvent.change(select, { target: { value: option.value } });
    fireEvent.change(screen.getByLabelText(d["admin.import.text"]), { target: { value: "x" } });
    fireEvent.click(screen.getByRole("button", { name: d["admin.import.submit"] }));
    await screen.findByTestId("import-summary");
  };

  test.each(CODES.filter((c) => c !== "unknown"))("code %s is shown as its translation, not the server message", async (code) => {
    const server = `SERVER-ENGLISH-${code}-text`;
    await run({
      status: 200,
      body: { added: 0, rows: [{ line: 4, status: "error", code, message: server }] },
    });
    const row = screen.getByTestId("import-row-4");
    expect(row.textContent).toContain(`${d["admin.import.line"]} 4`);
    expect(row.textContent).toContain(d[`admin.import.error.${code}`]);
    expect(row.textContent).not.toContain(server);
    expect(document.body.textContent).not.toContain(server);
  });

  test("an unknown code shows the generic translation, not the server message", async () => {
    await run({
      status: 200,
      body: { added: 0, rows: [{ line: 2, status: "error", code: "brand_new_code", message: "SERVER-ENGLISH-new" }] },
    });
    const row = screen.getByTestId("import-row-2");
    expect(row.textContent).toContain(d["admin.import.error.unknown"]);
    expect(row.textContent).not.toContain("SERVER-ENGLISH-new");
    expect(row.textContent).not.toContain("admin.import.error"); // no raw key
  });

  test("added rows and error rows mix; added rows keep the added text", async () => {
    await run({
      status: 200,
      body: {
        added: 1,
        rows: [
          { line: 1, status: "added" },
          { line: 2, status: "error", code: "already_exists", message: "SERVER-ENGLISH-dup" },
        ],
      },
    });
    expect(screen.getByTestId("import-row-1").textContent).toContain(d["admin.import.row_added"]);
    expect(screen.getByTestId("import-row-2").textContent).toContain(d["admin.import.error.already_exists"]);
    expect(screen.getByTestId("import-summary").textContent).toContain(`${d["admin.import.summary"]} 1`);
  });
});
