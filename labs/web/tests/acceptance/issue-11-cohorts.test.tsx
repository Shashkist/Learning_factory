/**
 * Exam for #11 slice 1 (D59): the admin shell at /admin and its Cohorts page, en and he.
 * The API behind it is described in labs/manager/tests/acceptance/test_issue_11_cohorts.py.
 *
 * CONTRACT the implementation must honour (names chosen by the exam)
 *
 * Network (credentials same-origin, JSON bodies). After GET /api/auth/me answers
 * {kind:"admin"} the admin shell calls
 *   GET  /api/admin/cohorts                  -> [{id, name, access_until:"YYYY-MM-DD", students}]
 *   POST /api/admin/cohorts                  {name, password, access_until}   -> 201 cohort
 *   POST /api/admin/cohorts/<id>/import      {text}  -> {added, rows:[{line, status, code?, message?}]}
 * and re-reads GET /api/admin/cohorts after a create or an import. A page that is not an admin
 * session (me 401) makes no /api/admin call. The existing sign-in states of Admin.tsx (Google link,
 * "Google sign-in is not configured", "Could not load sign-in options") keep their English texts
 * and their tests (src/Admin.test.tsx): the shell wraps them, it does not replace them.
 *
 * i18n keys (flat, no interpolation, in both files, same key set)
 *   admin.nav.cohorts        the nav link/button of the page (inside a <nav>)
 *   admin.cohorts.title      heading (level 1 or 2) of the page
 *   admin.cohorts.empty      shown instead of the list when there are no cohorts
 *   admin.cohorts.name       label of the name input
 *   admin.cohorts.password   label of the class password input (type=password)
 *   admin.cohorts.access     label of the access date input (type=date)
 *   admin.cohorts.create     the create button
 *   admin.cohorts.students   word after the student count in a row: `${n} ${admin.cohorts.students}`
 *   admin.import.cohort      label of the <select> that picks the cohort to import into
 *   admin.import.text        label of the paste <textarea>
 *   admin.import.submit      the import button
 *   admin.import.summary     word before the added count in the summary: `${admin.import.summary} ${n}`
 *   admin.import.row_added   shown in a row that was saved
 *   admin.import.line        word before the line number in a row: `${admin.import.line} ${n}`
 *   admin.error.load         role=alert when GET /api/admin/cohorts fails
 *   admin.error.save         role=alert when create or import fails (any non-2xx or network)
 *
 * DOM
 *   list row       data-testid="cohort-<id>": the name, the access date as sent, `${n} ${students}`;
 *                  never any password or hash text; rows in the API's order
 *   create form    inputs found by their labels, button `admin.cohorts.create`; on 201 the form's
 *                  password field is empty again and the new cohort is in the list
 *   import form    select (one option per cohort, text = cohort name), textarea, button
 *   import result  data-testid="import-summary" containing `${admin.import.summary} ${added}`;
 *                  one data-testid="import-row-<line>" per row containing `${admin.import.line} ${line}`
 *                  and either admin.import.row_added (status added) or the server's `message`
 *                  verbatim (status error); the pasted text is left in the textarea so the teacher can fix it
 *   language       the existing EN / עב toggle works on /admin; <html lang dir> follow the language
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
type Reply = { status: number; body?: unknown } | "network";
type Calls = { url: string; method: string; body?: string }[];

const ADMIN: Reply = { status: 200, body: { kind: "admin", name: "fake-admin" } };
const SEED: Cohort[] = [
  { id: 7, name: "Spring group", access_until: "2027-03-01", students: 12 },
  { id: 9, name: "Evening group", access_until: "2027-06-30", students: 0 },
];
const PASSWORD = "class-pass-for-exam";

function backend(opts: {
  me?: Reply; list?: Reply; create?: Reply; import?: Reply; cohorts?: Cohort[];
} = {}) {
  const calls: Calls = [];
  const cohorts = [...(opts.cohorts ?? SEED)];
  vi.spyOn(globalThis, "fetch").mockImplementation((async (url: RequestInfo | URL, init?: RequestInit) => {
    const u = String(url);
    const method = (init?.method ?? "GET").toUpperCase();
    const body = typeof init?.body === "string" ? init.body : undefined;
    calls.push({ url: u, method, body });
    let r: Reply | undefined;
    if (u === "/api/auth/me") r = opts.me ?? ADMIN;
    if (u === "/api/admin/cohorts" && method === "GET") r = opts.list ?? { status: 200, body: cohorts };
    if (u === "/api/admin/cohorts" && method === "POST") {
      r = opts.create;
      if (!r) {
        const b = JSON.parse(body ?? "{}");
        const made = { id: 100 + cohorts.length, name: b.name, access_until: b.access_until, students: 0 };
        cohorts.push(made);
        r = { status: 201, body: made };
      }
    }
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

function at(lang?: "en" | "he") {
  window.history.replaceState({}, "", "/admin");
  if (lang) window.localStorage.setItem("lf-lang", lang);
  return render(<App />);
}

const row = (id: number) => screen.getByTestId(`cohort-${id}`);
const posts = (calls: Calls, re: RegExp) => calls.filter((c) => c.method === "POST" && re.test(c.url));
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

const KEYS = [
  "admin.nav.cohorts", "admin.cohorts.title", "admin.cohorts.empty", "admin.cohorts.name",
  "admin.cohorts.password", "admin.cohorts.access", "admin.cohorts.create", "admin.cohorts.students",
  "admin.import.cohort", "admin.import.text", "admin.import.submit", "admin.import.summary",
  "admin.import.row_added", "admin.import.line", "admin.error.load", "admin.error.save",
];

test("both language files have the admin keys, non-empty, and the same key set", () => {
  expect(Object.keys(HE).sort()).toEqual(Object.keys(EN).sort());
  for (const d of [EN, HE])
    for (const k of KEYS) {
      expect(typeof d[k], k).toBe("string");
      expect(d[k].trim().length, k).toBeGreaterThan(0);
    }
});

test("the Hebrew admin texts are Hebrew and differ from the English ones", () => {
  for (const k of KEYS) {
    expect(HE[k], k).toMatch(/[֐-׿]/);
    expect(HE[k], k).not.toBe(EN[k]);
  }
});

// ------------------------------------------------------------------ the shell

describe.each(LANGS)("the admin shell in %s", (lang) => {
  const d = DICTS[lang];

  test("an admin sees the nav and the Cohorts page heading", async () => {
    backend();
    at(lang);
    const nav = await screen.findByRole("navigation");
    expect(within(nav).getByText(d["admin.nav.cohorts"])).toBeTruthy();
    expect(screen.getByRole("heading", { name: d["admin.cohorts.title"] })).toBeTruthy();
  });

  test("the document language and direction follow the language", async () => {
    backend();
    at(lang);
    await screen.findByRole("navigation");
    expect(document.documentElement.lang).toBe(lang);
    expect(document.documentElement.dir).toBe(lang === "he" ? "rtl" : "ltr");
  });

  test("the cohorts are listed with name, date and student count, in the API order", async () => {
    const calls = backend();
    at(lang);
    await screen.findByTestId("cohort-7");
    expect(listCalls(calls).length).toBeGreaterThanOrEqual(1);
    expect(row(7).textContent).toContain("Spring group");
    expect(row(7).textContent).toContain("2027-03-01");
    expect(row(7).textContent).toContain(`12 ${d["admin.cohorts.students"]}`);
    expect(row(9).textContent).toContain(`0 ${d["admin.cohorts.students"]}`);
    const order = screen.getAllByTestId(/^cohort-/).map((e) => e.getAttribute("data-testid"));
    expect(order).toEqual(["cohort-7", "cohort-9"]);
    expect(screen.queryByText(d["admin.cohorts.empty"])).toBeNull();
  });

  test("an empty list says so", async () => {
    backend({ cohorts: [] });
    at(lang);
    expect(await screen.findByText(d["admin.cohorts.empty"])).toBeTruthy();
    expect(screen.queryAllByTestId(/^cohort-/)).toHaveLength(0);
  });

  test("a failed list request shows an alert and no list", async () => {
    backend({ list: { status: 500, body: {} } });
    at(lang);
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain(d["admin.error.load"]);
    expect(screen.queryAllByTestId(/^cohort-/)).toHaveLength(0);
    expect(screen.queryByText(d["admin.cohorts.empty"])).toBeNull();
  });

  test("a network failure on the list shows the same alert", async () => {
    backend({ list: "network" });
    at(lang);
    expect((await screen.findByRole("alert")).textContent).toContain(d["admin.error.load"]);
  });
});

test("the shell and the page do not call the admin API when nobody is signed in", async () => {
  const calls = backend({ me: { status: 401, body: { detail: "not_authenticated" } } });
  at("en");
  await waitFor(() => expect(calls.some((c) => c.url === "/api/auth/me")).toBe(true));
  await new Promise((r) => setTimeout(r, 20));
  expect(calls.filter((c) => c.url.startsWith("/api/admin"))).toHaveLength(0);
  expect(screen.queryByTestId("cohort-7")).toBeNull();
});

test("the EN / עב toggle switches the admin page language", async () => {
  backend();
  at("en");
  await screen.findByRole("navigation");
  fireEvent.click(screen.getByRole("button", { name: EN["lang.switch.label"] }));
  await waitFor(() =>
    expect(screen.getByRole("heading", { name: HE["admin.cohorts.title"] })).toBeTruthy());
  expect(document.documentElement.dir).toBe("rtl");
});

test("the list shows no password or hash text", async () => {
  backend();
  at("en");
  await screen.findByTestId("cohort-7");
  expect(document.body.textContent).not.toMatch(/argon2|\$argon|password_hash/i);
});

// ------------------------------------------------------------------ create

describe.each(LANGS)("creating a cohort in %s", (lang) => {
  const d = DICTS[lang];

  const fill = (name: string, password: string, date: string) => {
    fireEvent.change(screen.getByLabelText(d["admin.cohorts.name"]), { target: { value: name } });
    fireEvent.change(screen.getByLabelText(d["admin.cohorts.password"]), { target: { value: password } });
    fireEvent.change(screen.getByLabelText(d["admin.cohorts.access"]), { target: { value: date } });
  };

  test("the form has a name, a password (hidden) and a date input", async () => {
    backend();
    at(lang);
    await screen.findByTestId("cohort-7");
    expect((screen.getByLabelText(d["admin.cohorts.password"]) as HTMLInputElement).type).toBe("password");
    expect((screen.getByLabelText(d["admin.cohorts.access"]) as HTMLInputElement).type).toBe("date");
    expect(screen.getByRole("button", { name: d["admin.cohorts.create"] })).toBeTruthy();
  });

  test("submitting posts name, password and date, then shows the new cohort", async () => {
    const calls = backend();
    at(lang);
    await screen.findByTestId("cohort-7");
    fill("Winter group", PASSWORD, "2027-12-31");
    fireEvent.click(screen.getByRole("button", { name: d["admin.cohorts.create"] }));
    await screen.findByText("Winter group");
    const sent = posts(calls, /^\/api\/admin\/cohorts$/);
    expect(sent).toHaveLength(1);
    expect(JSON.parse(sent[0].body ?? "null")).toEqual({
      name: "Winter group", password: PASSWORD, access_until: "2027-12-31",
    });
    expect(listCalls(calls).length).toBeGreaterThanOrEqual(2); // re-read after the create
    expect((screen.getByLabelText(d["admin.cohorts.password"]) as HTMLInputElement).value).toBe("");
    expect(document.body.textContent).not.toContain(PASSWORD);
  });

  test("a refused create (422) shows the save alert and keeps what was typed", async () => {
    backend({ create: { status: 422, body: { detail: "reserved" } } });
    at(lang);
    await screen.findByTestId("cohort-7");
    fill("lf-smoke", PASSWORD, "2027-12-31");
    fireEvent.click(screen.getByRole("button", { name: d["admin.cohorts.create"] }));
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain(d["admin.error.save"]);
    expect((screen.getByLabelText(d["admin.cohorts.name"]) as HTMLInputElement).value).toBe("lf-smoke");
    for (const r of screen.getAllByTestId(/^cohort-/)) expect(r.textContent).not.toContain("lf-smoke");
  });

  test("a network failure on create shows the save alert", async () => {
    backend({ create: "network" });
    at(lang);
    await screen.findByTestId("cohort-7");
    fill("Winter group", PASSWORD, "2027-12-31");
    fireEvent.click(screen.getByRole("button", { name: d["admin.cohorts.create"] }));
    expect((await screen.findByRole("alert")).textContent).toContain(d["admin.error.save"]);
  });
});

// ------------------------------------------------------------------ import

describe.each(LANGS)("paste import in %s", (lang) => {
  const d = DICTS[lang];
  const PASTE = "123456789, Fake Alpha\nabc, Fake Beta\n123456789, Fake Gamma\n";

  const doImport = async (cohortName: string, text: string) => {
    const select = (await screen.findByLabelText(d["admin.import.cohort"])) as HTMLSelectElement;
    const option = within(select).getByRole("option", { name: cohortName }) as HTMLOptionElement;
    fireEvent.change(select, { target: { value: option.value } });
    fireEvent.change(screen.getByLabelText(d["admin.import.text"]), { target: { value: text } });
    fireEvent.click(screen.getByRole("button", { name: d["admin.import.submit"] }));
  };

  const RESULT = {
    status: 200,
    body: {
      added: 1,
      rows: [
        { line: 1, status: "added" },
        { line: 2, status: "error", code: "invalid_id", message: "Line 2: the ID is not valid." },
        { line: 3, status: "error", code: "duplicate_in_paste", message: "Same ID as an earlier line." },
      ],
    },
  } as Reply;

  test("the select offers every cohort by name", async () => {
    backend();
    at(lang);
    const select = await screen.findByLabelText(d["admin.import.cohort"]);
    const names = within(select).getAllByRole("option").map((o) => o.textContent);
    expect(names).toEqual(expect.arrayContaining(["Spring group", "Evening group"]));
  });

  test("submitting posts the pasted text to the chosen cohort", async () => {
    const calls = backend({ import: RESULT });
    at(lang);
    await doImport("Evening group", PASTE);
    await screen.findByTestId("import-summary");
    const sent = posts(calls, /\/import$/);
    expect(sent).toHaveLength(1);
    expect(sent[0].url).toBe("/api/admin/cohorts/9/import");
    expect(JSON.parse(sent[0].body ?? "null")).toEqual({ text: PASTE });
  });

  test("the result shows the added count and one row per line with the translated error per code (#172)", async () => {
    backend({ import: RESULT });
    at(lang);
    await doImport("Spring group", PASTE);
    const summary = await screen.findByTestId("import-summary");
    expect(summary.textContent).toContain(`${d["admin.import.summary"]} 1`);
    const r1 = screen.getByTestId("import-row-1");
    expect(r1.textContent).toContain(`${d["admin.import.line"]} 1`);
    expect(r1.textContent).toContain(d["admin.import.row_added"]);
    const r2 = screen.getByTestId("import-row-2");
    expect(r2.textContent).toContain(`${d["admin.import.line"]} 2`);
    expect(r2.textContent).toContain(d["admin.import.error.invalid_id"]);
    expect(r2.textContent).not.toContain(d["admin.import.row_added"]);
    const r3 = screen.getByTestId("import-row-3");
    expect(r3.textContent).toContain(d["admin.import.error.duplicate_in_paste"]);
    expect(screen.getAllByTestId(/^import-row-/)).toHaveLength(3);
  });

  test("after an import the cohort list is read again and the paste stays in the box", async () => {
    const calls = backend({ import: RESULT });
    at(lang);
    await doImport("Spring group", PASTE);
    await screen.findByTestId("import-summary");
    await waitFor(() => expect(listCalls(calls).length).toBeGreaterThanOrEqual(2));
    expect((screen.getByLabelText(d["admin.import.text"]) as HTMLTextAreaElement).value).toBe(PASTE);
  });

  test("a failed import shows the save alert and no result rows", async () => {
    backend({ import: { status: 500, body: {} } });
    at(lang);
    await doImport("Spring group", PASTE);
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain(d["admin.error.save"]);
    expect(screen.queryByTestId("import-summary")).toBeNull();
    expect(screen.queryAllByTestId(/^import-row-/)).toHaveLength(0);
  });

  test("a second import replaces the previous result", async () => {
    const calls = backend({ import: RESULT });
    at(lang);
    await doImport("Spring group", PASTE);
    await screen.findByTestId("import-row-3");
    vi.mocked(fetch).mockClear();
    backend({ import: { status: 200, body: { added: 2, rows: [
      { line: 1, status: "added" }, { line: 2, status: "added" }] } } });
    fireEvent.click(screen.getByRole("button", { name: d["admin.import.submit"] }));
    await waitFor(() =>
      expect(screen.getByTestId("import-summary").textContent).toContain(`${d["admin.import.summary"]} 2`));
    expect(screen.queryByTestId("import-row-3")).toBeNull();
    expect(calls.length).toBeGreaterThan(0);
  });
});
