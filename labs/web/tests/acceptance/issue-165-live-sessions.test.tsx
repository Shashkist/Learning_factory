/**
 * Exam for #165 (slice 4 of #11, D59): the Live sessions page in the admin shell, en and he.
 * The API behind it is described in labs/manager/tests/acceptance/test_issue_165_live_sessions.py.
 * The teacher Playwright flows of the issue moved to their own issue (D65).
 *
 * CONTRACT the implementation must honour (names chosen by the exam)
 *
 * Route and shell
 *   - /admin/live renders the admin shell (the same sign-in states as /admin) with the Live sessions
 *     page. The one <nav> of the shell now holds three links, in this order: admin.nav.cohorts
 *     (href /admin), admin.nav.class (href /admin/class), admin.nav.live (href /admin/live). The
 *     link of the page being shown has aria-current="page", the others have not. Clicking a nav
 *     link switches the page without a full reload (history.pushState; the shell stays).
 *   - /admin and /admin/class make no /api/admin/live call. Nobody signed in (me 401): no
 *     /api/admin call at all on /admin/live.
 *
 * Network (credentials same-origin)
 *   GET  /api/admin/live -> {sessions:[{id,key,name,cohort,lab_id,lab_title:{en,he}|null,
 *        started_at,last_heartbeat|null}]}   (no query parameters)
 *   POST /api/admin/live/<id>/end -> 204 (the id is the session's own `id`, URL-encoded)
 *   A failed list request (non-2xx or network) shows role=alert with admin.live.error and no
 *   table; an empty `sessions` shows admin.live.empty and no table.
 *
 * DOM
 *   heading (level 1 or 2)  admin.live.title
 *   table                   data-testid="live-table", shown only when there are sessions. Column
 *                           headers (<th>) include, in this order, admin.live.col.name,
 *                           admin.live.col.cohort, admin.live.col.lab, admin.live.col.started,
 *                           admin.live.col.heartbeat (an extra actions column header is free).
 *   row                     data-testid="live-row-<i>" (i = 0-based, the API's order) containing
 *                           the student name, the cohort name and the lab title in the current
 *                           language (the lab id when lab_title is null); never an id or a hash.
 *   name link               <a> in the row, accessible name = the student name, href
 *                           /admin/students/<key>; clicking opens the student page without a
 *                           reload (the key is the only identifier in the URL).
 *   started / heartbeat     data-testid="live-started-<i>" shows the date part (first 10
 *                           characters of the ISO string as sent, no time zone conversion);
 *                           data-testid="live-heartbeat-<i>" the same, or admin.live.no_heartbeat
 *                           when null.
 *   End                     in the row a <button> data-testid="live-end-<i>" with the accessible
 *                           name admin.live.end. Clicking it makes NO request: it asks to confirm
 *                           inside the row with a button admin.live.confirm and a button
 *                           admin.live.cancel (the End button is replaced by them). Cancel brings
 *                           the End button back, no request. Confirm sends one POST
 *                           /api/admin/live/<id>/end; a 2xx removes that row (the other rows stay,
 *                           their order too); with no row left the empty state shows. A failed POST
 *                           (non-2xx or network) shows role=alert with admin.live.end_error, the
 *                           row stays and offers End again. Several open confirms at a time are
 *                           allowed. window.confirm is not used.
 *
 * i18n keys (flat, both files, same key set, Hebrew in he, none equal to the English text, and
 * each English text unique among all English texts, because the older exams find by text)
 *   admin.nav.live, admin.live.title, admin.live.col.name, admin.live.col.cohort,
 *   admin.live.col.lab, admin.live.col.started, admin.live.col.heartbeat, admin.live.no_heartbeat,
 *   admin.live.end, admin.live.confirm, admin.live.cancel, admin.live.empty, admin.live.error,
 *   admin.live.end_error
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

type Sess = {
  id: string;
  key: string;
  name: string;
  cohort: string;
  lab_id: string;
  lab_title: { en: string; he: string } | null;
  started_at: string;
  last_heartbeat: string | null;
};
type Reply = { status: number; body?: unknown } | "network";
type Calls = { url: string; method: string }[];

const ADMIN: Reply = { status: 200, body: { kind: "admin", name: "fake-admin" } };
const day = (offset: number) => new Date(Date.now() + offset * 86_400_000).toISOString().slice(0, 10);
const TODAY = day(0);
const YESTERDAY = day(-1);

const SESSIONS: Sess[] = [
  {
    id: "5b1c2f0e-1111-4a6e-9a11-000000000001", key: "aaaaaaaa-0000-4000-8000-000000000001",
    name: "Fake Alef", cohort: "Spring group", lab_id: "linux-1",
    lab_title: { en: "Linux basics", he: "יסודות לינוקס" },
    started_at: `${TODAY}T09:15:00+00:00`, last_heartbeat: `${TODAY}T09:40:00+00:00`,
  },
  {
    id: "5b1c2f0e-2222-4a6e-9a11-000000000002", key: "aaaaaaaa-0000-4000-8000-000000000002",
    name: "Fake Bet", cohort: "Evening group", lab_id: "git-1",
    lab_title: { en: "First commits", he: "קומיטים ראשונים" },
    started_at: `${YESTERDAY}T20:05:00+00:00`, last_heartbeat: null,
  },
  {
    id: "5b1c2f0e-3333-4a6e-9a11-000000000003", key: "aaaaaaaa-0000-4000-8000-000000000003",
    name: "Fake Gimel", cohort: "Spring group", lab_id: "retired-lab", lab_title: null,
    started_at: `${YESTERDAY}T08:00:00+00:00`, last_heartbeat: `${YESTERDAY}T08:30:00+00:00`,
  },
];

type Opts = {
  me?: Reply;
  list?: Reply;
  sessions?: Sess[];
  end?: Reply | ((id: string) => Reply);
};

/** A fake manager that forgets a session once it is ended, like the real one. */
function backend(opts: Opts = {}) {
  const calls: Calls = [];
  let live = [...(opts.sessions ?? SESSIONS)];
  vi.spyOn(globalThis, "fetch").mockImplementation((async (url: RequestInfo | URL, init?: RequestInit) => {
    const u = String(url);
    const method = (init?.method ?? "GET").toUpperCase();
    calls.push({ url: u, method });
    let r: Reply | undefined;
    if (u === "/api/auth/me") r = opts.me ?? ADMIN;
    if (u === "/api/admin/live" && method === "GET") r = opts.list ?? { status: 200, body: { sessions: live } };
    if (u === "/api/admin/cohorts" && method === "GET") r = { status: 200, body: [] };
    if (u.startsWith("/api/admin/class") && method === "GET") r = { status: 200, body: { labs: [], rows: [] } };
    const m = /^\/api\/admin\/live\/([^/]+)\/end$/.exec(u);
    if (m && method === "POST") {
      const id = decodeURIComponent(m[1]);
      const given = typeof opts.end === "function" ? opts.end(id) : opts.end;
      r = given ?? { status: 204 };
      if (r !== "network" && r.status >= 200 && r.status < 300) live = live.filter((s) => s.id !== id);
    }
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

function at(lang: "en" | "he", path = "/admin/live") {
  window.history.replaceState({}, "", path);
  window.localStorage.setItem("lf-lang", lang);
  return render(<App />);
}

const posts = (calls: Calls) => calls.filter((c) => c.method === "POST" && c.url.includes("/api/admin/live/"));
const liveGets = (calls: Calls) => calls.filter((c) => c.method === "GET" && c.url.startsWith("/api/admin/live"));
const rowEls = () => screen.queryAllByTestId(/^live-row-\d+$/);
const rowText = () => rowEls().map((e) => e.textContent ?? "");
const endButton = (i: number, lang: "en" | "he") =>
  within(screen.getByTestId(`live-row-${i}`)).getByRole("button", { name: DICTS[lang]["admin.live.end"] });

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
  "admin.nav.live", "admin.live.title", "admin.live.col.name", "admin.live.col.cohort",
  "admin.live.col.lab", "admin.live.col.started", "admin.live.col.heartbeat",
  "admin.live.no_heartbeat", "admin.live.end", "admin.live.confirm", "admin.live.cancel",
  "admin.live.empty", "admin.live.error", "admin.live.end_error",
];

test("both language files have the live sessions keys, non-empty, and the same key set", () => {
  expect(Object.keys(HE).sort()).toEqual(Object.keys(EN).sort());
  for (const d of [EN, HE])
    for (const k of KEYS) {
      expect(typeof d[k], k).toBe("string");
      expect(d[k].trim().length, k).toBeGreaterThan(0);
    }
});

test("the Hebrew live sessions texts are Hebrew and differ from the English ones", () => {
  for (const k of KEYS) {
    expect(HE[k], k).toMatch(/[֐-׿]/);
    expect(HE[k], k).not.toBe(EN[k]);
  }
});

test("every English live sessions text is unique among all English texts", () => {
  for (const k of KEYS) {
    const same = Object.keys(EN).filter((o) => o !== k && EN[o] === EN[k]);
    expect(same, k).toEqual([]);
  }
});

// ------------------------------------------------------------------ the page

describe.each(LANGS)("the live sessions page in %s", (lang) => {
  const d = DICTS[lang];

  test("an admin sees the nav with three links in order and the page heading", async () => {
    backend();
    at(lang);
    expect(await screen.findByRole("heading", { name: d["admin.live.title"] })).toBeTruthy();
    const nav = screen.getByRole("navigation");
    const links = within(nav).getAllByRole("link");
    expect(links.map((a) => a.getAttribute("href"))).toEqual(["/admin", "/admin/class", "/admin/live"]);
    expect(links.map((a) => a.textContent?.trim())).toEqual([
      d["admin.nav.cohorts"], d["admin.nav.class"], d["admin.nav.live"],
    ]);
    expect(links.map((a) => a.getAttribute("aria-current"))).toEqual([null, null, "page"]);
    expect(screen.queryByRole("heading", { name: d["admin.cohorts.title"] })).toBeNull();
  });

  test("the document language and direction follow the language", async () => {
    backend();
    at(lang);
    await screen.findByTestId("live-table");
    expect(document.documentElement.lang).toBe(lang);
    expect(document.documentElement.dir).toBe(lang === "he" ? "rtl" : "ltr");
  });

  test("the headers are name, cohort, lab, started and heartbeat in that order", async () => {
    backend();
    at(lang);
    const table = await screen.findByTestId("live-table");
    const heads = within(table).getAllByRole("columnheader").map((h) => h.textContent?.trim());
    const want = ["name", "cohort", "lab", "started", "heartbeat"].map((c) => d[`admin.live.col.${c}`]);
    expect(heads.slice(0, 5)).toEqual(want);
  });

  test("rows come in the API's order with the name, cohort and lab title", async () => {
    backend();
    at(lang);
    await screen.findByTestId("live-table");
    const rows = rowText();
    expect(rows).toHaveLength(3);
    expect(rows[0]).toContain("Fake Alef");
    expect(rows[0]).toContain("Spring group");
    expect(rows[0]).toContain(SESSIONS[0].lab_title![lang]);
    expect(rows[1]).toContain("Fake Bet");
    expect(rows[1]).toContain("Evening group");
    expect(rows[1]).toContain(SESSIONS[1].lab_title![lang]);
    expect(rows[2]).toContain("Fake Gimel");
  });

  test("a lab with no title shows its id", async () => {
    backend();
    at(lang);
    await screen.findByTestId("live-table");
    expect(rowText()[2]).toContain("retired-lab");
  });

  test("started and last heartbeat show the date as sent, or the no-heartbeat text", async () => {
    backend();
    at(lang);
    await screen.findByTestId("live-table");
    expect(screen.getByTestId("live-started-0").textContent).toContain(TODAY);
    expect(screen.getByTestId("live-heartbeat-0").textContent).toContain(TODAY);
    expect(screen.getByTestId("live-started-1").textContent).toContain(YESTERDAY);
    expect(screen.getByTestId("live-heartbeat-1").textContent).toContain(d["admin.live.no_heartbeat"]);
    expect(screen.getByTestId("live-heartbeat-2").textContent).toContain(YESTERDAY);
    expect(screen.getByTestId("live-heartbeat-0").textContent).not.toContain(d["admin.live.no_heartbeat"]);
  });

  test("the student name links to the student page by key", async () => {
    backend();
    at(lang);
    await screen.findByTestId("live-table");
    const link = within(screen.getByTestId("live-row-1")).getByRole("link", { name: "Fake Bet" });
    expect(link.getAttribute("href")).toBe(`/admin/students/${SESSIONS[1].key}`);
  });

  test("the page shows no id or hash text", async () => {
    backend();
    at(lang);
    const table = await screen.findByTestId("live-table");
    expect(table.textContent).not.toMatch(/[0-9a-f]{32,}/);
    for (const s of SESSIONS) expect(table.textContent).not.toContain(s.id);
  });

  test("the list is requested once with no query and no write happens on load", async () => {
    const calls = backend();
    at(lang);
    await screen.findByTestId("live-table");
    expect(liveGets(calls)[0].url).toBe("/api/admin/live");
    expect(posts(calls)).toHaveLength(0);
  });

  test("every row has an End button and clicking it asks first without any request", async () => {
    const calls = backend();
    at(lang);
    await screen.findByTestId("live-table");
    for (let i = 0; i < 3; i++) expect(endButton(i, lang)).toBeTruthy();
    fireEvent.click(endButton(1, lang));
    const row = screen.getByTestId("live-row-1");
    expect(within(row).getByRole("button", { name: d["admin.live.confirm"] })).toBeTruthy();
    expect(within(row).getByRole("button", { name: d["admin.live.cancel"] })).toBeTruthy();
    expect(within(row).queryByRole("button", { name: d["admin.live.end"] })).toBeNull();
    expect(rowEls()).toHaveLength(3);
    await new Promise((r) => setTimeout(r, 20));
    expect(posts(calls)).toHaveLength(0);
  });

  test("cancel puts the End button back and sends nothing", async () => {
    const calls = backend();
    at(lang);
    await screen.findByTestId("live-table");
    fireEvent.click(endButton(0, lang));
    fireEvent.click(within(screen.getByTestId("live-row-0")).getByRole("button", { name: d["admin.live.cancel"] }));
    expect(endButton(0, lang)).toBeTruthy();
    expect(within(screen.getByTestId("live-row-0")).queryByRole("button", { name: d["admin.live.confirm"] })).toBeNull();
    await new Promise((r) => setTimeout(r, 20));
    expect(posts(calls)).toHaveLength(0);
    expect(rowEls()).toHaveLength(3);
  });

  test("confirming sends one POST for that session and the row leaves, the others stay in order", async () => {
    const calls = backend();
    at(lang);
    await screen.findByTestId("live-table");
    fireEvent.click(endButton(1, lang));
    fireEvent.click(within(screen.getByTestId("live-row-1")).getByRole("button", { name: d["admin.live.confirm"] }));
    await waitFor(() => expect(rowEls()).toHaveLength(2));
    expect(posts(calls)).toEqual([{ url: `/api/admin/live/${SESSIONS[1].id}/end`, method: "POST" }]);
    const rows = rowText();
    expect(rows[0]).toContain("Fake Alef");
    expect(rows[1]).toContain("Fake Gimel");
    expect(rows.join("|")).not.toContain("Fake Bet");
    expect(screen.queryByRole("alert")).toBeNull();
  });

  test("ending the last session shows the empty state", async () => {
    backend({ sessions: [SESSIONS[0]] });
    at(lang);
    await screen.findByTestId("live-table");
    fireEvent.click(endButton(0, lang));
    fireEvent.click(within(screen.getByTestId("live-row-0")).getByRole("button", { name: d["admin.live.confirm"] }));
    expect(await screen.findByText(d["admin.live.empty"])).toBeTruthy();
    expect(screen.queryByTestId("live-table")).toBeNull();
    expect(rowEls()).toHaveLength(0);
  });

  test("a failed end shows an alert, keeps the row and offers End again", async () => {
    const calls = backend({ end: { status: 500, body: {} } });
    at(lang);
    await screen.findByTestId("live-table");
    fireEvent.click(endButton(0, lang));
    fireEvent.click(within(screen.getByTestId("live-row-0")).getByRole("button", { name: d["admin.live.confirm"] }));
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain(d["admin.live.end_error"]);
    expect(rowEls()).toHaveLength(3);
    expect(rowText()[0]).toContain("Fake Alef");
    expect(endButton(0, lang)).toBeTruthy();
    expect(posts(calls)).toHaveLength(1);
  });

  test("a network failure on end shows the same alert and keeps the row", async () => {
    backend({ end: "network" });
    at(lang);
    await screen.findByTestId("live-table");
    fireEvent.click(endButton(2, lang));
    fireEvent.click(within(screen.getByTestId("live-row-2")).getByRole("button", { name: d["admin.live.confirm"] }));
    expect((await screen.findByRole("alert")).textContent).toContain(d["admin.live.end_error"]);
    expect(rowEls()).toHaveLength(3);
  });

  test("a retry after a failed end can succeed and clears the alert", async () => {
    let first = true;
    backend({
      end: () => {
        if (first) {
          first = false;
          return { status: 500, body: {} };
        }
        return { status: 204 };
      },
    });
    at(lang);
    await screen.findByTestId("live-table");
    fireEvent.click(endButton(0, lang));
    fireEvent.click(within(screen.getByTestId("live-row-0")).getByRole("button", { name: d["admin.live.confirm"] }));
    await screen.findByRole("alert");
    fireEvent.click(endButton(0, lang));
    fireEvent.click(within(screen.getByTestId("live-row-0")).getByRole("button", { name: d["admin.live.confirm"] }));
    await waitFor(() => expect(rowEls()).toHaveLength(2));
    await waitFor(() => expect(screen.queryByRole("alert")).toBeNull());
  });

  test("no sessions says so and shows no table", async () => {
    backend({ sessions: [] });
    at(lang);
    expect(await screen.findByText(d["admin.live.empty"])).toBeTruthy();
    expect(screen.queryByTestId("live-table")).toBeNull();
    expect(rowEls()).toHaveLength(0);
  });

  test("a failed list request shows an alert and no table", async () => {
    backend({ list: { status: 500, body: {} } });
    at(lang);
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain(d["admin.live.error"]);
    expect(screen.queryByTestId("live-table")).toBeNull();
    expect(screen.queryByText(d["admin.live.empty"])).toBeNull();
  });

  test("a network failure on the list shows the same alert", async () => {
    backend({ list: "network" });
    at(lang);
    expect((await screen.findByRole("alert")).textContent).toContain(d["admin.live.error"]);
  });
});

// ------------------------------------------------------------------ navigation and sign-in

test("the nav switches between Cohorts, Class table and Live sessions without a reload", async () => {
  const calls = backend();
  at("en", "/admin");
  await screen.findByRole("heading", { name: EN["admin.cohorts.title"] });
  expect(liveGets(calls)).toHaveLength(0);
  fireEvent.click(within(screen.getByRole("navigation")).getByText(EN["admin.nav.live"]));
  expect(await screen.findByRole("heading", { name: EN["admin.live.title"] })).toBeTruthy();
  expect(window.location.pathname).toBe("/admin/live");
  await screen.findByTestId("live-table");
  fireEvent.click(within(screen.getByRole("navigation")).getByText(EN["admin.nav.class"]));
  expect(await screen.findByRole("heading", { name: EN["admin.class.title"] })).toBeTruthy();
  expect(window.location.pathname).toBe("/admin/class");
  fireEvent.click(within(screen.getByRole("navigation")).getByText(EN["admin.nav.cohorts"]));
  expect(await screen.findByRole("heading", { name: EN["admin.cohorts.title"] })).toBeTruthy();
  expect(window.location.pathname).toBe("/admin");
});

test("the class table page makes no live sessions call", async () => {
  const calls = backend();
  at("en", "/admin/class");
  await screen.findByRole("heading", { name: EN["admin.class.title"] });
  await new Promise((r) => setTimeout(r, 20));
  expect(liveGets(calls)).toHaveLength(0);
});

test("the Live sessions link is shown next to the others on the Cohorts page", async () => {
  backend();
  at("en", "/admin");
  await screen.findByRole("heading", { name: EN["admin.cohorts.title"] });
  const nav = screen.getByRole("navigation");
  const link = within(nav).getByText(EN["admin.nav.live"]).closest("a");
  expect(link?.getAttribute("href")).toBe("/admin/live");
  expect(link?.getAttribute("aria-current")).toBeNull();
});

test("clicking a student name opens the student page without a reload", async () => {
  backend();
  at("en");
  await screen.findByTestId("live-table");
  fireEvent.click(within(screen.getByTestId("live-row-0")).getByRole("link", { name: "Fake Alef" }));
  await waitFor(() => expect(window.location.pathname).toBe(`/admin/students/${SESSIONS[0].key}`));
  expect(screen.getByRole("navigation")).toBeTruthy(); // the shell stays
});

test("nobody signed in: no admin call is made on /admin/live", async () => {
  const calls = backend({ me: { status: 401, body: { detail: "not_authenticated" } } });
  at("en");
  await waitFor(() => expect(calls.some((c) => c.url === "/api/auth/me")).toBe(true));
  await new Promise((r) => setTimeout(r, 20));
  expect(calls.filter((c) => c.url.startsWith("/api/admin"))).toHaveLength(0);
  expect(screen.queryByTestId("live-table")).toBeNull();
});

test("the EN / עב toggle switches the page texts and the lab titles", async () => {
  backend();
  at("en");
  await screen.findByTestId("live-table");
  expect(rowText()[0]).toContain("Linux basics");
  fireEvent.click(screen.getByRole("button", { name: EN["lang.switch.label"] }));
  await waitFor(() => expect(screen.getByRole("heading", { name: HE["admin.live.title"] })).toBeTruthy());
  expect(rowText()[0]).toContain("יסודות לינוקס");
  expect(document.documentElement.dir).toBe("rtl");
});
