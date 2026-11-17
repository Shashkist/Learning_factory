/**
 * Exam for #163 (slice 2 of #11, D59): the Class table page in the admin shell, en and he.
 * The API behind it is described in labs/manager/tests/acceptance/test_issue_163_class_table.py.
 *
 * CONTRACT the implementation must honour (names chosen by the exam)
 *
 * Route and shell
 *   - /admin/class renders the admin shell (the same sign-in states as /admin) with the Class
 *     table page; /admin stays the Cohorts page and makes no /api/admin/class call.
 *   - The one <nav> of the shell now holds two links: admin.nav.cohorts (href /admin) and
 *     admin.nav.class (href /admin/class). The link of the page being shown has
 *     aria-current="page", the other one has not. Clicking a nav link switches the page without
 *     a full reload (history.pushState; the URL changes, the shell stays).
 *   - Nobody signed in (me 401): no /api/admin call at all on /admin/class either.
 *
 * Network (credentials same-origin)
 *   GET /api/admin/class[?cohort=<id>][&flagged=1]
 *       -> {labs:[{id,title:{en,he}}], rows:[{name,cohort,last_activity|null,
 *           cells:{<lab id>:{status:"done"|"<n>/<m>"|"none",flag:boolean}}}]}
 *   GET /api/admin/cohorts   (existing) fills the cohort filter.
 *   The first class request has no `cohort` and no `flagged=1`. Every filter change makes a new
 *   request carrying the current filters, and the page shows what that response says (the page
 *   does no filtering of its own). A failed request (non-2xx or network) shows role=alert with
 *   admin.class.error and no table; an empty `rows` shows admin.class.empty and no row.
 *
 * DOM
 *   heading (level 1 or 2)  admin.class.title
 *   cohort filter           <select> found by label admin.class.cohort; the first option has
 *                           value "" and text admin.class.all_cohorts, then one option per cohort
 *                           (text = name, value = id). Picking one requests cohort=<id>; picking
 *                           the first one again drops `cohort` from the request.
 *   flagged only            checkbox found by label admin.class.flagged (starts unchecked); checked
 *                           requests flagged=1, unchecked drops it.
 *   CSV link                <a> with the accessible name admin.class.csv, plain href (no fetch to
 *                           it) /api/admin/class.csv plus the current `cohort` / `flagged=1`
 *                           filters (it follows the filters as they change).
 *   table                   data-testid="class-table", shown only when there are rows. Column
 *                           headers (<th>) in this order: admin.class.col.name,
 *                           admin.class.col.cohort, admin.class.col.last_activity, then one per
 *                           lab in the API's order showing the lab title in the current language.
 *   row                     data-testid="class-row-<i>" (i = 0-based, the API's order) containing
 *                           the student name and the cohort name, and never an id or a hash.
 *   cell                    data-testid="class-cell-<i>-<lab id>": "done" -> "✓", "<n>/<m>" ->
 *                           that text, "none" -> "–" (an en dash). A flagged cell also has an
 *                           element with title=admin.class.flag whose text is "⚡"; an unflagged
 *                           cell has no ⚡ and no such element.
 *   last activity           data-testid="class-last-<i>": the first 10 characters of the ISO string
 *                           as sent (YYYY-MM-DD, no time zone conversion); when null the text
 *                           admin.class.never.
 *
 * i18n keys (flat, both files, same key set, Hebrew in he, none equal to the English text)
 *   admin.nav.class, admin.class.title, admin.class.cohort, admin.class.all_cohorts,
 *   admin.class.flagged, admin.class.csv, admin.class.flag, admin.class.col.name,
 *   admin.class.col.cohort, admin.class.col.last_activity, admin.class.never,
 *   admin.class.empty, admin.class.error
 *   The exam does not pin the wording, only that the shown text is the dictionary's text. The
 *   English texts of the existing #11 / #172 keys must keep their wording and must differ from
 *   these (the older exams find elements by text).
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

type Cell = { status: string; flag: boolean };
type Row = { name: string; cohort: string; last_activity: string | null; cells: Record<string, Cell> };
type Table = { labs: { id: string; title: { en: string; he: string } }[]; rows: Row[] };
type Cohort = { id: number; name: string; access_until: string; students: number };
type Reply = { status: number; body?: unknown } | "network";
type Calls = { url: string; method: string }[];

const ADMIN: Reply = { status: 200, body: { kind: "admin", name: "fake-admin" } };
const day = (offset: number) => new Date(Date.now() + offset * 86_400_000).toISOString().slice(0, 10);
const FUTURE = day(30);
const SEEN = day(-2); // a clock-relative "last seen" date

const COHORTS: Cohort[] = [
  { id: 7, name: "Spring group", access_until: FUTURE, students: 2 },
  { id: 9, name: "Evening group", access_until: FUTURE, students: 1 },
];
const LABS: Table["labs"] = [
  { id: "linux-1", title: { en: "Linux basics", he: "יסודות לינוקס" } },
  { id: "linux-2", title: { en: "Files and users", he: "קבצים ומשתמשים" } },
  { id: "git-1", title: { en: "First commits", he: "קומיטים ראשונים" } },
];
const ALL_ROWS: Row[] = [
  {
    name: "Fake Alef", cohort: "Spring group", last_activity: `${SEEN}T12:00:00+00:00`,
    cells: {
      "linux-1": { status: "done", flag: false },
      "linux-2": { status: "2/5", flag: true },
      "git-1": { status: "none", flag: false },
    },
  },
  {
    name: "Fake Bet", cohort: "Spring group", last_activity: null,
    cells: {
      "linux-1": { status: "none", flag: false },
      "linux-2": { status: "none", flag: false },
      "git-1": { status: "none", flag: false },
    },
  },
  {
    name: "Fake Gimel", cohort: "Evening group", last_activity: `${day(-9)}T08:30:00+00:00`,
    cells: {
      "linux-1": { status: "done", flag: true },
      "linux-2": { status: "1/5", flag: false },
      "git-1": { status: "3/4", flag: false },
    },
  },
];
const COHORT_OF: Record<number, string> = { 7: "Spring group", 9: "Evening group" };

/** A fake manager that filters like the real one, so a changed filter changes the rows. */
function serve(params: URLSearchParams, rows: Row[] = ALL_ROWS): Table {
  let out = rows;
  const cohort = params.get("cohort");
  if (cohort) out = out.filter((r) => r.cohort === COHORT_OF[Number(cohort)]);
  if (params.get("flagged") === "1") out = out.filter((r) => Object.values(r.cells).some((c) => c.flag));
  return { labs: LABS, rows: out };
}

function backend(opts: { me?: Reply; klass?: Reply; rows?: Row[] } = {}) {
  const calls: Calls = [];
  vi.spyOn(globalThis, "fetch").mockImplementation((async (url: RequestInfo | URL, init?: RequestInit) => {
    const u = String(url);
    const method = (init?.method ?? "GET").toUpperCase();
    calls.push({ url: u, method });
    const parsed = new URL(u, "http://localhost");
    let r: Reply | undefined;
    if (u === "/api/auth/me") r = opts.me ?? ADMIN;
    if (u === "/api/admin/cohorts" && method === "GET") r = { status: 200, body: COHORTS };
    if (parsed.pathname === "/api/admin/class" && method === "GET")
      r = opts.klass ?? { status: 200, body: serve(parsed.searchParams, opts.rows) };
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

function at(lang: "en" | "he", path = "/admin/class") {
  window.history.replaceState({}, "", path);
  window.localStorage.setItem("lf-lang", lang);
  return render(<App />);
}

const classCalls = (calls: Calls) =>
  calls.filter((c) => c.method === "GET" && new URL(c.url, "http://localhost").pathname === "/api/admin/class");
const lastClass = (calls: Calls) => new URL(classCalls(calls).at(-1)!.url, "http://localhost").searchParams;
const cellOf = (i: number, lab: string) => screen.getByTestId(`class-cell-${i}-${lab}`);
const rowEls = () => screen.queryAllByTestId(/^class-row-\d+$/);
const names = () => rowEls().map((e) => e.textContent ?? "");

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
  "admin.nav.class", "admin.class.title", "admin.class.cohort", "admin.class.all_cohorts",
  "admin.class.flagged", "admin.class.csv", "admin.class.flag", "admin.class.col.name",
  "admin.class.col.cohort", "admin.class.col.last_activity", "admin.class.never",
  "admin.class.empty", "admin.class.error",
];

test("both language files have the class table keys, non-empty, and the same key set", () => {
  expect(Object.keys(HE).sort()).toEqual(Object.keys(EN).sort());
  for (const d of [EN, HE])
    for (const k of KEYS) {
      expect(typeof d[k], k).toBe("string");
      expect(d[k].trim().length, k).toBeGreaterThan(0);
    }
});

test("the Hebrew class table texts are Hebrew and differ from the English ones", () => {
  for (const k of KEYS) {
    expect(HE[k], k).toMatch(/[֐-׿]/);
    expect(HE[k], k).not.toBe(EN[k]);
  }
});

test("the new nav text and page title differ from the Cohorts ones", () => {
  for (const d of [EN, HE]) {
    expect(d["admin.nav.class"]).not.toBe(d["admin.nav.cohorts"]);
    expect(d["admin.class.title"]).not.toBe(d["admin.cohorts.title"]);
  }
});

// ------------------------------------------------------------------ the shell and the page

describe.each(LANGS)("the class table in %s", (lang) => {
  const d = DICTS[lang];

  test("an admin sees the nav with both links and the page heading", async () => {
    backend();
    at(lang);
    expect(await screen.findByRole("heading", { name: d["admin.class.title"] })).toBeTruthy();
    const nav = screen.getByRole("navigation");
    const cohorts = within(nav).getByText(d["admin.nav.cohorts"]).closest("a");
    const klass = within(nav).getByText(d["admin.nav.class"]).closest("a");
    expect(cohorts?.getAttribute("href")).toBe("/admin");
    expect(klass?.getAttribute("href")).toBe("/admin/class");
    expect(klass?.getAttribute("aria-current")).toBe("page");
    expect(cohorts?.getAttribute("aria-current")).toBeNull();
    expect(screen.queryByRole("heading", { name: d["admin.cohorts.title"] })).toBeNull();
  });

  test("the document language and direction follow the language", async () => {
    backend();
    at(lang);
    await screen.findByTestId("class-table");
    expect(document.documentElement.lang).toBe(lang);
    expect(document.documentElement.dir).toBe(lang === "he" ? "rtl" : "ltr");
  });

  test("the headers are name, cohort, last activity and one per lab in the lab's language", async () => {
    backend();
    at(lang);
    const table = await screen.findByTestId("class-table");
    const heads = within(table).getAllByRole("columnheader").map((h) => h.textContent?.trim());
    expect(heads).toEqual([
      d["admin.class.col.name"], d["admin.class.col.cohort"], d["admin.class.col.last_activity"],
      ...LABS.map((l) => l.title[lang]),
    ]);
  });

  test("rows come in the API's order with the name and the cohort", async () => {
    backend();
    at(lang);
    await screen.findByTestId("class-table");
    const rows = rowEls();
    expect(rows).toHaveLength(3);
    expect(rows[0].textContent).toContain("Fake Alef");
    expect(rows[0].textContent).toContain("Spring group");
    expect(rows[1].textContent).toContain("Fake Bet");
    expect(rows[2].textContent).toContain("Fake Gimel");
    expect(rows[2].textContent).toContain("Evening group");
  });

  test("cells read ✓ when done, n/m in progress and – when not started", async () => {
    backend();
    at(lang);
    await screen.findByTestId("class-table");
    expect(cellOf(0, "linux-1").textContent).toContain("✓");
    expect(cellOf(0, "linux-2").textContent).toContain("2/5");
    expect(cellOf(0, "git-1").textContent).toContain("–");
    expect(cellOf(2, "git-1").textContent).toContain("3/4");
    expect(cellOf(1, "linux-1").textContent).toContain("–");
    expect(cellOf(0, "git-1").textContent).not.toContain("none");
    expect(cellOf(0, "linux-1").textContent).not.toContain("done");
    expect(cellOf(0, "linux-1").textContent).not.toContain("2/5");
  });

  test("a ⚡ marks the flagged cells only, with its title", async () => {
    backend();
    at(lang);
    await screen.findByTestId("class-table");
    for (const [i, lab] of [[0, "linux-2"], [2, "linux-1"]] as const) {
      const c = cellOf(i, lab);
      expect(c.textContent).toContain("⚡");
      expect(within(c).getByTitle(d["admin.class.flag"]).textContent).toBe("⚡");
    }
    for (const [i, lab] of [[0, "linux-1"], [0, "git-1"], [1, "linux-2"], [2, "linux-2"]] as const) {
      const c = cellOf(i, lab);
      expect(c.textContent).not.toContain("⚡");
      expect(within(c).queryByTitle(d["admin.class.flag"])).toBeNull();
    }
  });

  test("a flagged done cell shows both ✓ and ⚡", async () => {
    backend();
    at(lang);
    await screen.findByTestId("class-table");
    expect(cellOf(2, "linux-1").textContent).toContain("✓");
    expect(cellOf(2, "linux-1").textContent).toContain("⚡");
  });

  test("last activity shows the date as sent, or the never text", async () => {
    backend();
    at(lang);
    await screen.findByTestId("class-table");
    expect(screen.getByTestId("class-last-0").textContent).toContain(SEEN);
    expect(screen.getByTestId("class-last-1").textContent).toContain(d["admin.class.never"]);
    expect(screen.getByTestId("class-last-2").textContent).toContain(day(-9));
  });

  test("the page shows no id or hash text", async () => {
    backend();
    at(lang);
    await screen.findByTestId("class-table");
    expect(screen.getByTestId("class-table").textContent).not.toMatch(/[0-9a-f]{32,}/);
  });

  test("the first request carries no filter and the filters start empty", async () => {
    const calls = backend();
    at(lang);
    await screen.findByTestId("class-table");
    const first = new URL(classCalls(calls)[0].url, "http://localhost").searchParams;
    expect(first.get("cohort")).toBeNull();
    expect(first.get("flagged")).not.toBe("1");
    const flagged = screen.getByLabelText(d["admin.class.flagged"]) as HTMLInputElement;
    expect(flagged.type).toBe("checkbox");
    expect(flagged.checked).toBe(false);
    expect((screen.getByLabelText(d["admin.class.cohort"]) as HTMLSelectElement).value).toBe("");
  });

  test("the cohort filter lists all cohorts first, then one option per cohort", async () => {
    backend();
    at(lang);
    await screen.findByTestId("class-table");
    const select = screen.getByLabelText(d["admin.class.cohort"]) as HTMLSelectElement;
    await waitFor(() => expect(select.options).toHaveLength(3));
    const opts = Array.from(select.options).map((o) => [o.value, o.textContent]);
    expect(opts).toEqual([
      ["", d["admin.class.all_cohorts"]], ["7", "Spring group"], ["9", "Evening group"],
    ]);
  });

  test("picking a cohort requests it and shows only its students", async () => {
    const calls = backend();
    at(lang);
    await screen.findByTestId("class-table");
    const select = screen.getByLabelText(d["admin.class.cohort"]) as HTMLSelectElement;
    await waitFor(() => expect(select.options).toHaveLength(3));
    fireEvent.change(select, { target: { value: "9" } });
    await waitFor(() => expect(lastClass(calls).get("cohort")).toBe("9"));
    await waitFor(() => expect(rowEls()).toHaveLength(1));
    expect(names()[0]).toContain("Fake Gimel");
    fireEvent.change(select, { target: { value: "" } });
    await waitFor(() => expect(lastClass(calls).get("cohort")).toBeNull());
    await waitFor(() => expect(rowEls()).toHaveLength(3));
  });

  test("flagged only requests flagged=1 and shows the flagged students only", async () => {
    const calls = backend();
    at(lang);
    await screen.findByTestId("class-table");
    fireEvent.click(screen.getByLabelText(d["admin.class.flagged"]));
    await waitFor(() => expect(lastClass(calls).get("flagged")).toBe("1"));
    await waitFor(() => expect(rowEls()).toHaveLength(2));
    expect(names().join("|")).toContain("Fake Alef");
    expect(names().join("|")).toContain("Fake Gimel");
    expect(names().join("|")).not.toContain("Fake Bet");
    fireEvent.click(screen.getByLabelText(d["admin.class.flagged"]));
    await waitFor(() => expect(lastClass(calls).get("flagged")).not.toBe("1"));
    await waitFor(() => expect(rowEls()).toHaveLength(3));
  });

  test("the two filters combine in one request", async () => {
    const calls = backend();
    at(lang);
    await screen.findByTestId("class-table");
    const select = screen.getByLabelText(d["admin.class.cohort"]) as HTMLSelectElement;
    await waitFor(() => expect(select.options).toHaveLength(3));
    fireEvent.change(select, { target: { value: "7" } });
    fireEvent.click(screen.getByLabelText(d["admin.class.flagged"]));
    await waitFor(() => {
      const p = lastClass(calls);
      expect(p.get("cohort")).toBe("7");
      expect(p.get("flagged")).toBe("1");
    });
    await waitFor(() => expect(rowEls()).toHaveLength(1));
    expect(names()[0]).toContain("Fake Alef");
  });

  test("the CSV link points at the export and follows the filters", async () => {
    const calls = backend();
    at(lang);
    await screen.findByTestId("class-table");
    const link = () => screen.getByRole("link", { name: d["admin.class.csv"] }) as HTMLAnchorElement;
    const target = () => new URL(link().getAttribute("href") ?? "", "http://localhost");
    expect(target().pathname).toBe("/api/admin/class.csv");
    expect(target().searchParams.get("cohort")).toBeNull();
    expect(target().searchParams.get("flagged")).not.toBe("1");
    const select = screen.getByLabelText(d["admin.class.cohort"]) as HTMLSelectElement;
    await waitFor(() => expect(select.options).toHaveLength(3));
    fireEvent.change(select, { target: { value: "7" } });
    fireEvent.click(screen.getByLabelText(d["admin.class.flagged"]));
    await waitFor(() => expect(target().searchParams.get("cohort")).toBe("7"));
    await waitFor(() => expect(target().searchParams.get("flagged")).toBe("1"));
    expect(target().pathname).toBe("/api/admin/class.csv");
    expect(calls.some((c) => c.url.includes("class.csv"))).toBe(false); // a link, not a fetch
  });

  test("an empty class says so and shows no row", async () => {
    backend({ rows: [] });
    at(lang);
    expect(await screen.findByText(d["admin.class.empty"])).toBeTruthy();
    expect(rowEls()).toHaveLength(0);
  });

  test("a failed request shows an alert and no table", async () => {
    backend({ klass: { status: 500, body: {} } });
    at(lang);
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain(d["admin.class.error"]);
    expect(screen.queryByTestId("class-table")).toBeNull();
    expect(screen.queryByText(d["admin.class.empty"])).toBeNull();
  });

  test("a network failure shows the same alert", async () => {
    backend({ klass: "network" });
    at(lang);
    expect((await screen.findByRole("alert")).textContent).toContain(d["admin.class.error"]);
  });
});

// ------------------------------------------------------------------ navigation and sign-in

test("the nav switches between Cohorts and Class table without a reload", async () => {
  const calls = backend();
  at("en", "/admin");
  await screen.findByRole("heading", { name: EN["admin.cohorts.title"] });
  expect(classCalls(calls)).toHaveLength(0);
  const nav = screen.getByRole("navigation");
  fireEvent.click(within(nav).getByText(EN["admin.nav.class"]));
  expect(await screen.findByRole("heading", { name: EN["admin.class.title"] })).toBeTruthy();
  expect(window.location.pathname).toBe("/admin/class");
  await screen.findByTestId("class-table");
  fireEvent.click(within(screen.getByRole("navigation")).getByText(EN["admin.nav.cohorts"]));
  expect(await screen.findByRole("heading", { name: EN["admin.cohorts.title"] })).toBeTruthy();
  expect(window.location.pathname).toBe("/admin");
});

test("the Cohorts page links to the class table and keeps its own heading", async () => {
  backend();
  at("en", "/admin");
  await screen.findByRole("heading", { name: EN["admin.cohorts.title"] });
  const nav = screen.getByRole("navigation");
  expect(within(nav).getByText(EN["admin.nav.cohorts"]).closest("a")?.getAttribute("aria-current")).toBe("page");
  expect(within(nav).getByText(EN["admin.nav.class"]).closest("a")?.getAttribute("href")).toBe("/admin/class");
});

test("nobody signed in: no admin call is made on /admin/class", async () => {
  const calls = backend({ me: { status: 401, body: { detail: "not_authenticated" } } });
  at("en");
  await waitFor(() => expect(calls.some((c) => c.url === "/api/auth/me")).toBe(true));
  await new Promise((r) => setTimeout(r, 20));
  expect(calls.filter((c) => c.url.startsWith("/api/admin"))).toHaveLength(0);
  expect(screen.queryByTestId("class-table")).toBeNull();
});

test("the EN / עב toggle switches the table headers and the heading", async () => {
  backend();
  at("en");
  await screen.findByTestId("class-table");
  fireEvent.click(screen.getByRole("button", { name: EN["lang.switch.label"] }));
  await waitFor(() =>
    expect(screen.getByRole("heading", { name: HE["admin.class.title"] })).toBeTruthy());
  const heads = within(screen.getByTestId("class-table")).getAllByRole("columnheader");
  expect(heads.map((h) => h.textContent?.trim())).toContain(LABS[0].title.he);
  expect(document.documentElement.dir).toBe("rtl");
});
