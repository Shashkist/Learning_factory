/**
 * Exam for #334 (slice 4 of the visual design epic #330, D105): the teacher pages (cohorts and
 * student import, class table, student page, live sessions). This file is what jsdom can see:
 * navigation, table semantics, text badges, busy and disabled row actions, form structure, the
 * student summary cards. Layout (no page scroll at 390 px, a wide table scrolling inside its own
 * container, sticky header) and the screenshots (AC6) are in tests/acceptance/issue-334-design.pw.ts
 * (run by the design Playwright config, whose testMatch is /issue-\d+-design\.pw\.ts$/).
 * AC1 ("only foundation tokens") is held by the #331 exam scans (colour literals only in
 * tokens.css, logical properties only); AC5 is "the existing exams stay green, unchanged".
 *
 * Nothing here pins wording: texts come from the dictionaries (en and he). No new i18n key is
 * required by the exam; if the implementer adds keys they follow the old rule (both files, Hebrew
 * differs from English, each English text unique among all English texts, because older exams find
 * by text).
 *
 * STRUCTURAL HOOKS the implementer must provide (semantic first, the foundation's classes where a
 * component is named by D105: .badge, .card, .alert in src/styles/base.css)
 *   1. Admin navigation: the <nav> (role navigation) is part of the shell: inside the <header>
 *      (role banner) or a sibling of it, NOT inside the page content of <main>'s page section.
 *      Its links keep the names admin.nav.cohorts / .class / .live, hrefs /admin, /admin/class,
 *      /admin/live; aria-current="page" on the link of the page shown and on no other.
 *   2. Every <th> has a scope attribute: scope="col" for column headers (also the empty actions
 *      header of the live table), scope="row" for row headers. Applies to the class table, the
 *      live table and the student page's task tables.
 *   3. Text badges (class "badge", with the text inside the badge element, not a tooltip only):
 *      - class table: a flagged cell holds a .badge whose text includes admin.class.flag. The
 *        existing glyph element (title admin.class.flag, textContent exactly the glyph) stays, it
 *        may sit inside the badge. An unflagged cell holds no such badge.
 *      - student page: a dismissed flag shows admin.student.dismissed inside a .badge.
 *      - student page: a disabled student's admin.student.disabled_notice is inside an .alert or a
 *        .badge.
 *   4. Busy row actions: while the request of a button is in flight the button is disabled AND has
 *      aria-busy="true", and keeps its text (live End, student Save note / Set access date /
 *      Dismiss flag). Once answered aria-busy is gone (or "false").
 *   5. Forms: the create-cohort fields and the import fields each sit in their own <fieldset> with
 *      a non-empty <legend>. A refused import (more than 2,000 lines) keeps role=alert with
 *      admin.import.too_many AND marks the import textarea aria-invalid="true" with
 *      aria-describedby naming an element that contains that text (inline, next to the field).
 *      The import result summary (data-testid="import-summary" stays) sits inside an element with
 *      class "alert" (the .alert component).
 *   6. Student page summary: at least three elements with class "card" that are not inside a lab
 *      article (data-testid^="student-lab-") and come before the first lab article in document
 *      order: labs done, active time, last activity. The labs-done card shows the count of done
 *      labs and the number of labs (for 3 done of 5 labs its text matches /3\D+5/).
 */
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import App from "../../src/App";
import en from "../../src/i18n/en.json";
import he from "../../src/i18n/he.json";

type Dict = Record<string, string>;
const DICT: Record<"en" | "he", Dict> = { en: en as Dict, he: he as Dict };
const D = DICT.en;

// ---------------------------------------------------------------- a fake backend with deferrable answers
type Reply = { status: number; body?: unknown };
type Handler = (init?: RequestInit) => Reply | Promise<Reply>;
const KEY = "6f1c2b0e-4d1a-4c57-9a43-0a1b2c3d4e5f";

const LAB = (id: string, status: string, over: object = {}) => ({
  id,
  title: { en: `Lab ${id}`, he: `מעבדה ${id}` },
  status,
  tasks_passed: status === "done" ? 2 : 1,
  tasks_total: 2,
  attempts: 3,
  tasks: [
    { id: "t1", passed: true, attempts: 1, seconds: 30, median_seconds: 40 },
    { id: "t2", passed: status === "done", attempts: 2, seconds: null, median_seconds: 50 },
  ],
  flags: [] as object[],
  ...over,
});
const flag = (id: string, dismissed: string | null) => ({
  id,
  scope: "task",
  task_id: "t1",
  reason: "task t1 in 3 s",
  dismissed_at: dismissed,
});
const student = (over: object = {}) => ({
  key: KEY,
  name: "Dana",
  cohort: "Alpha",
  note: null,
  disabled: false,
  access_until: null,
  cohort_access_until: "2027-01-31",
  labs: [LAB("linux", "done", { flags: [flag("f-live", null), flag("f-old", "2026-11-19T10:00:00Z")] }), LAB("git", "in_progress")],
  ...over,
});

const CLASS_BODY = {
  labs: [
    { id: "linux", title: { en: "Linux basics", he: "יסודות לינוקס" } },
    { id: "git", title: { en: "Git basics", he: "יסודות גיט" } },
  ],
  rows: [
    { key: KEY, name: "Dana", cohort: "Alpha", last_activity: "2026-11-20T09:00:00Z", cells: { linux: { status: "done", flag: true }, git: { status: "2/4", flag: false } } },
    { key: "0b9e7a64-8c2d-4f10-b7a5-1d2e3f405162", name: "Noa", cohort: "Beta", last_activity: null, cells: { linux: { status: "none", flag: false }, git: { status: "none", flag: false } } },
  ],
};
const LIVE_BODY = {
  sessions: [
    { id: "s-1", key: KEY, name: "Dana", cohort: "Alpha", lab_id: "linux", lab_title: { en: "Linux basics", he: "יסודות לינוקס" }, started_at: "2026-11-20T09:00:00Z", last_heartbeat: null },
  ],
};

function backend(extra: Record<string, Handler> = {}) {
  const calls: string[] = [];
  globalThis.fetch = vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
    const path = new URL(String(url), "http://localhost").pathname;
    const key = `${init?.method ?? "GET"} ${path}`;
    calls.push(key);
    const defaults: Record<string, Reply> = {
      "GET /api/auth/me": { status: 200, body: { kind: "admin", name: "Teacher" } },
      "GET /api/admin/cohorts": { status: 200, body: [{ id: 1, name: "Alpha", access_until: "2027-01-31", students: 1 }, { id: 2, name: "Beta", access_until: "2027-01-31", students: 0 }] },
      "GET /api/admin/class": { status: 200, body: CLASS_BODY },
      "GET /api/admin/live": { status: 200, body: LIVE_BODY },
      [`GET /api/admin/students/${KEY}`]: { status: 200, body: student() },
    };
    const r = extra[key] ? await extra[key](init) : (defaults[key] ?? { status: 404, body: { detail: "not_found" } });
    return new Response(r.status === 204 ? null : JSON.stringify(r.body ?? {}), { status: r.status, headers: { "Content-Type": "application/json" } });
  }) as typeof fetch;
  return calls;
}

/** A reply the test releases by hand, so the in-flight (busy) state can be looked at. */
const gates: (() => void)[] = [];
function gate(reply: Reply) {
  let release: () => void = () => {};
  const wait = new Promise<void>((r) => (release = r));
  gates.push(() => release());
  const handler: Handler = async () => {
    await wait;
    return reply;
  };
  return { handler, release: () => release() };
}

function open(path: string, lang: "en" | "he" = "en") {
  window.localStorage.setItem("lf-lang", lang);
  window.history.pushState({}, "", path);
  return render(<App />);
}

afterEach(() => {
  // a test that failed half way must not leave a request hanging for the next one
  while (gates.length) gates.pop()?.();
  cleanup();
  window.localStorage.clear();
  vi.restoreAllMocks();
});

// ================================================================ AC3: the current admin page is marked, in the shell
describe("AC3: admin navigation in the shell, current page marked", () => {
  const NAV = [
    ["/admin", "admin.nav.cohorts"],
    ["/admin/class", "admin.nav.class"],
    ["/admin/live", "admin.nav.live"],
  ] as const;

  for (const lang of ["en", "he"] as const) {
    for (const [path] of NAV) {
      it(`on ${path} only its own link has aria-current="page" (${lang})`, async () => {
        backend();
        open(path, lang);
        const nav = await screen.findByRole("navigation");
        const links = within(nav).getAllByRole("link");
        expect(links.map((a) => a.textContent?.trim())).toEqual(NAV.map(([, k]) => DICT[lang][k]));
        expect(links.map((a) => a.getAttribute("href"))).toEqual(NAV.map(([h]) => h));
        for (const [href, key] of NAV) {
          const link = within(nav).getByRole("link", { name: DICT[lang][key] });
          if (href === path) expect(link.getAttribute("aria-current")).toBe("page");
          else expect(link.hasAttribute("aria-current")).toBe(false);
        }
        expect(nav.querySelectorAll('[aria-current="page"]')).toHaveLength(1);
      });
    }
  }

  it("the navigation is part of the shell: in the header or beside it, not inside the page content", async () => {
    backend();
    open("/admin/class");
    const nav = await screen.findByRole("navigation");
    const header = screen.getByRole("banner");
    const main = screen.getByRole("main");
    const inHeader = header.contains(nav);
    const besideHeader = nav.parentElement === header.parentElement;
    expect(inHeader || besideHeader, "nav is in <header> or a sibling of it").toBe(true);
    // The page itself (its heading and table) is not a neighbour-of-nav wrapper: nav is not inside a <section>.
    expect(nav.closest("section"), "nav is outside the page's <section>").toBeNull();
    expect(main.contains(screen.getByRole("heading", { name: D["admin.class.title"] }))).toBe(true);
  });

  it("clicking a link moves the mark and keeps the single navigation", async () => {
    backend();
    open("/admin");
    const nav = await screen.findByRole("navigation");
    fireEvent.click(within(nav).getByRole("link", { name: D["admin.nav.live"] }));
    await waitFor(() => expect(within(screen.getByRole("navigation")).getByRole("link", { name: D["admin.nav.live"] }).getAttribute("aria-current")).toBe("page"));
    expect(within(screen.getByRole("navigation")).getByRole("link", { name: D["admin.nav.cohorts"] }).hasAttribute("aria-current")).toBe(false);
    expect(screen.getAllByRole("navigation")).toHaveLength(1);
  });
});

// ================================================================ AC2: tables keep their semantics
describe("AC2: tables keep <table> and <th scope>", () => {
  const scopes = (table: HTMLElement) => Array.from(table.querySelectorAll("th")).map((th) => th.getAttribute("scope"));

  it("class table: column headers scope=col, student names scope=row", async () => {
    backend();
    open("/admin/class");
    const table = await screen.findByTestId("class-table");
    expect(table.tagName).toBe("TABLE");
    const heads = Array.from(table.querySelectorAll("thead th"));
    expect(heads.length).toBeGreaterThanOrEqual(5);
    for (const th of heads) expect(th.getAttribute("scope"), `thead th "${th.textContent}"`).toBe("col");
    const rowHeads = Array.from(table.querySelectorAll("tbody th"));
    expect(rowHeads).toHaveLength(2);
    for (const th of rowHeads) expect(th.getAttribute("scope")).toBe("row");
  });

  it("live table: every header has a scope, columns are scope=col", async () => {
    backend();
    open("/admin/live");
    const table = await screen.findByTestId("live-table");
    expect(table.tagName).toBe("TABLE");
    const heads = Array.from(table.querySelectorAll("thead th"));
    expect(heads.length).toBeGreaterThanOrEqual(5);
    for (const th of heads) expect(th.getAttribute("scope"), `thead th "${th.textContent}"`).toBe("col");
  });

  it("student page: the task tables keep a caption and row headers with scope=row", async () => {
    backend();
    open(`/admin/students/${KEY}`);
    const lab = await screen.findByTestId("student-lab-linux");
    const table = within(lab).getByRole("table");
    expect(table.querySelector("caption")?.textContent).toContain(D["admin.student.tasks"]);
    const s = scopes(table);
    expect(s.length).toBeGreaterThan(0);
    for (const v of s) expect(v === "row" || v === "col", `scope ${v}`).toBe(true);
    for (const th of Array.from(table.querySelectorAll("tbody th"))) expect(th.getAttribute("scope")).toBe("row");
  });
});

// ================================================================ AC4: badges have text, not colour alone
describe("AC4: flags and statuses are text badges", () => {
  it("class table: a flagged cell holds a badge with the flag text, an unflagged one none", async () => {
    backend();
    open("/admin/class");
    await screen.findByTestId("class-table");
    const flagged = screen.getByTestId("class-cell-0-linux");
    const badge = Array.from(flagged.querySelectorAll(".badge")).find((b) => (b.textContent ?? "").includes(D["admin.class.flag"]));
    expect(badge, "a .badge with the visible flag text").toBeTruthy();
    // the old glyph with its title is still there (the #163 exam)
    expect(within(flagged).getByTitle(D["admin.class.flag"]).textContent).toBe("⚡");
    for (const id of ["class-cell-0-git", "class-cell-1-linux"]) {
      const cell = screen.getByTestId(id);
      expect(cell.textContent).not.toContain(D["admin.class.flag"]);
    }
  });

  it("class table: the flag text is shown in he too", async () => {
    backend();
    open("/admin/class", "he");
    await screen.findByTestId("class-table");
    const flagged = screen.getByTestId("class-cell-0-linux");
    expect(Array.from(flagged.querySelectorAll(".badge")).some((b) => (b.textContent ?? "").includes(DICT.he["admin.class.flag"]))).toBe(true);
  });

  it("student page: a dismissed flag shows its state as badge text", async () => {
    backend();
    open(`/admin/students/${KEY}`);
    const old = await screen.findByTestId("student-flag-f-old");
    const badge = Array.from(old.querySelectorAll(".badge")).find((b) => (b.textContent ?? "").includes(D["admin.student.dismissed"]));
    expect(badge, "a .badge with admin.student.dismissed").toBeTruthy();
    // the live flag is not marked dismissed
    expect(screen.getByTestId("student-flag-f-live").textContent).not.toContain(D["admin.student.dismissed"]);
  });

  it("student page: the disabled notice is an alert or a badge, with its text", async () => {
    backend({ [`GET /api/admin/students/${KEY}`]: () => ({ status: 200, body: student({ disabled: true }) }) });
    open(`/admin/students/${KEY}`);
    const notice = await screen.findByText(D["admin.student.disabled_notice"]);
    expect(notice.closest(".alert, .badge"), "inside .alert or .badge").not.toBeNull();
  });
});

// ================================================================ busy and disabled row actions
describe("busy row actions are disabled, aria-busy and keep their text", () => {
  it("live sessions: End in flight", async () => {
    const g = gate({ status: 204 });
    backend({ "POST /api/admin/live/s-1/end": g.handler });
    open("/admin/live");
    fireEvent.click(await screen.findByTestId("live-end-0"));
    fireEvent.click(await screen.findByRole("button", { name: D["admin.live.confirm"] }));
    const end = await screen.findByTestId("live-end-0");
    expect(end.textContent).toBe(D["admin.live.end"]);
    expect((end as HTMLButtonElement).disabled).toBe(true);
    expect(end.getAttribute("aria-busy")).toBe("true");
    g.release();
    await waitFor(() => expect(screen.queryByTestId("live-row-0")).toBeNull());
    expect(screen.getByText(D["admin.live.empty"])).toBeTruthy();
  });

  it("live sessions: a failed End ends the busy state and offers End again", async () => {
    const g = gate({ status: 500 });
    backend({ "POST /api/admin/live/s-1/end": g.handler });
    open("/admin/live");
    fireEvent.click(await screen.findByTestId("live-end-0"));
    fireEvent.click(await screen.findByRole("button", { name: D["admin.live.confirm"] }));
    expect((await screen.findByTestId("live-end-0")).getAttribute("aria-busy")).toBe("true");
    g.release();
    await screen.findByText(D["admin.live.end_error"]);
    const end = screen.getByTestId("live-end-0") as HTMLButtonElement;
    expect(end.disabled).toBe(false);
    expect(end.getAttribute("aria-busy")).not.toBe("true");
  });

  it("student page: Save note in flight", async () => {
    const g = gate({ status: 200, body: student({ note: "hello" }) });
    backend({ [`PATCH /api/admin/students/${KEY}`]: g.handler });
    open(`/admin/students/${KEY}`);
    fireEvent.change(await screen.findByLabelText(D["admin.student.note"]), { target: { value: "hello" } });
    const save = screen.getByRole("button", { name: D["admin.student.save_note"] }) as HTMLButtonElement;
    expect(save.getAttribute("aria-busy")).not.toBe("true");
    fireEvent.click(save);
    await waitFor(() => expect(save.disabled).toBe(true));
    expect(save.getAttribute("aria-busy")).toBe("true");
    expect(save.textContent).toBe(D["admin.student.save_note"]);
    g.release();
    await waitFor(() => expect(save.disabled).toBe(false));
    expect(save.getAttribute("aria-busy")).not.toBe("true");
  });

  it("student page: Dismiss flag in flight", async () => {
    const after = student({ labs: [LAB("linux", "done", { flags: [flag("f-live", "2026-11-21T10:00:00Z")] }), LAB("git", "in_progress")] });
    const g = gate({ status: 200, body: after });
    backend({ [`POST /api/admin/students/${KEY}/flags/f-live/dismiss`]: g.handler });
    open(`/admin/students/${KEY}`);
    const item = await screen.findByTestId("student-flag-f-live");
    const dismiss = within(item).getByRole("button", { name: D["admin.student.dismiss"] }) as HTMLButtonElement;
    fireEvent.click(dismiss);
    await waitFor(() => expect(dismiss.disabled).toBe(true));
    expect(dismiss.getAttribute("aria-busy")).toBe("true");
    expect(dismiss.textContent).toBe(D["admin.student.dismiss"]);
    g.release();
    await waitFor(() => expect(within(screen.getByTestId("student-flag-f-live")).queryByRole("button", { name: D["admin.student.dismiss"] })).toBeNull());
  });
});

// ================================================================ forms
describe("forms: grouped fields, inline error, result summary as an alert", () => {
  it("the create fields and the import fields each sit in a fieldset with a legend", async () => {
    backend();
    open("/admin");
    const importField = await screen.findByLabelText(D["admin.import.text"]); // shown once the cohorts are loaded
    const nameField = screen.getByLabelText(D["admin.cohorts.name"]);
    const a = nameField.closest("fieldset");
    const b = importField.closest("fieldset");
    expect(a, "create-cohort fieldset").not.toBeNull();
    expect(b, "import fieldset").not.toBeNull();
    expect(a).not.toBe(b);
    for (const fs of [a!, b!]) expect(fs.querySelector(":scope > legend")?.textContent?.trim()).toBeTruthy();
    // the password and date fields of the create form share the name field's group
    expect(a!.contains(screen.getByLabelText(D["admin.cohorts.password"]))).toBe(true);
    expect(a!.contains(screen.getByLabelText(D["admin.cohorts.access"]))).toBe(true);
    // and the import's cohort picker shares the textarea's group
    expect(b!.contains(screen.getByLabelText(D["admin.import.cohort"]))).toBe(true);
  });

  it("too many import lines: the alert stays and the textarea is marked invalid, described by it", async () => {
    const calls = backend();
    open("/admin");
    const text = (await screen.findByLabelText(D["admin.import.text"])) as HTMLTextAreaElement;
    expect(text.getAttribute("aria-invalid")).not.toBe("true");
    fireEvent.change(text, { target: { value: Array.from({ length: 2001 }, (_, i) => `${i + 1},Fake ${i}`).join("\n") } });
    fireEvent.click(screen.getByRole("button", { name: D["admin.import.submit"] }));
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain(D["admin.import.too_many"]);
    expect(text.getAttribute("aria-invalid")).toBe("true");
    const ids = (text.getAttribute("aria-describedby") ?? "").split(/\s+/).filter(Boolean);
    const described = ids.map((id) => document.getElementById(id)?.textContent ?? "").join(" ");
    expect(described).toContain(D["admin.import.too_many"]);
    expect(calls.some((c) => c.startsWith("POST /api/admin/cohorts/"))).toBe(false);
  });

  it("the import result summary is inside an alert component", async () => {
    backend({
      "POST /api/admin/cohorts/1/import": () => ({
        status: 200,
        body: { added: 1, rows: [{ line: 1, status: "added" }, { line: 2, status: "error", code: "invalid_id", message: "x" }] },
      }),
    });
    open("/admin");
    fireEvent.change(await screen.findByLabelText(D["admin.import.text"]), { target: { value: "123456782,Fake A\nxx,Fake B" } });
    fireEvent.click(screen.getByRole("button", { name: D["admin.import.submit"] }));
    const summary = await screen.findByTestId("import-summary");
    expect(summary.textContent).toContain(D["admin.import.summary"]);
    const box = summary.closest(".alert");
    expect(box, "summary inside .alert").not.toBeNull();
    // the per-line results are part of the same alert
    expect(within(box as HTMLElement).getByTestId("import-row-2").textContent).toContain(D["admin.import.error.invalid_id"]);
  });
});

// ================================================================ student page: summary cards above the lab detail
describe("student page: summary cards above the per-lab detail", () => {
  it("three cards come before the first lab, the first counts done labs", async () => {
    const labs = ["a", "b", "c", "d", "e"].map((id, i) => LAB(id, i < 3 ? "done" : "in_progress"));
    backend({ [`GET /api/admin/students/${KEY}`]: () => ({ status: 200, body: student({ labs }) }) });
    open(`/admin/students/${KEY}`);
    const firstLab = await screen.findByTestId("student-lab-a");
    const page = firstLab.closest("section") ?? document.body;
    const cards = Array.from(page.querySelectorAll(".card")).filter((c) => !c.closest('[data-testid^="student-lab-"]'));
    const before = cards.filter((c) => c.compareDocumentPosition(firstLab) & Node.DOCUMENT_POSITION_FOLLOWING);
    expect(before.length, "cards before the first lab").toBeGreaterThanOrEqual(3);
    expect(before.some((c) => /3\D+5/.test(c.textContent ?? "")), "a card shows 3 of 5 labs").toBe(true);
  });
});
