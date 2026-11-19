/**
 * Exam for #204 (D66 slice: review items 2, 3, 4 and 8 of #164, web only), en and he. Items 1 (flag
 * reasons in Hebrew), 5, 6, 7 are not here (#216, #217). The #164 and #172 exams stay as they are.
 *
 * CONTRACT the implementation must honour (names chosen by the exam)
 *
 * 2. Pending state on the student page (/admin/students/<key>)
 *    While a write is in flight (note PATCH, access-date PATCH, disable/enable PATCH, flag dismiss POST):
 *      - the control that started it is disabled (`disabled` attribute), so a second click sends no
 *        second request (exactly one request per write); it is enabled again once the answer arrived,
 *        whether it was a success or a failure;
 *      - the data fields are NOT disabled. DECISION: the note textarea and the date input stay editable
 *        while their save is in flight, and a late answer must not overwrite newer local edits: text
 *        typed after Save was clicked is still in the textarea after the PATCH answer arrived (the
 *        same for the access date). The page still shows what the server answered for everything else
 *        (e.g. the disabled notice).
 *      - The accessible names of the controls do not change while pending (no new i18n key needed).
 * 3. Dismissing a flag
 *      - after the POST answer, document.activeElement is the flag item data-testid="student-flag-<id>"
 *        (not <body>);
 *      - a dismissed flag shows admin.student.dismissed AND its dismissal time in the teacher's local
 *        time as `YYYY-MM-DD HH:MM` (src/localStamp.ts), inside the flag item; never the raw ISO text.
 *        This holds for a flag dismissed just now and for one that was already dismissed on load.
 *        Tests run with TZ=UTC (vite.config.ts), so "2026-11-18T23:30:00+02:00" shows 2026-11-18 21:30.
 * 4. Loading
 *      - while GET /api/admin/students/<key> has not answered the page shows the back link
 *        (<a> admin.student.back, href /admin/class) and the text admin.student.loading (new key). No
 *        student-page content (data-testid="student-page") yet. Once the answer arrived the loading
 *        text is gone.
 * 8. Cohorts page, student list (data-testid="students-<cohort id>", #172)
 *      - GET /api/admin/cohorts/<id>/students entries carry `key` (D63). Each student's name is a link
 *        (<a>, accessible name contains the name) with href /admin/students/<key>; clicking it opens
 *        that student's page without a reload (pushState, the shell stays, GET of the student is made).
 *        Note and disabled mark stay in the same list item as before.
 *
 * i18n keys (flat, both files, same key set, Hebrew in he, not equal to the English text, English text
 * unique among all English texts)
 *   admin.student.loading
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

type Reply = { status: number; body?: unknown } | "network";
type Calls = { url: string; method: string; body?: string }[];
type Flag = { id: string; scope: string; task_id: string | null; reason: string; dismissed_at: string | null };
type Student = {
  key: string; name: string; cohort: string; note: string | null; disabled: boolean;
  access_until: string | null; cohort_access_until: string;
  labs: {
    id: string; title: { en: string; he: string }; status: string; tasks_passed: number;
    tasks_total: number; attempts: number; flags: Flag[];
    tasks: { id: string; passed: boolean; attempts: number; seconds: number | null; median_seconds: number | null }[];
  }[];
};

const ADMIN: Reply = { status: 200, body: { kind: "admin", name: "fake-admin" } };
const KEY = "5b0e2f6c-7a1d-4c53-9d58-0f1f1f6f9a11";
const KEY_BET = "9c1d7e22-3b4a-4f6e-8a10-2d3c4b5a6e77";
const FLAG_REASON = "task 02-two passed 9 s after it appeared";
const DISMISSED_AT = "2026-11-18T23:30:00+02:00"; // 2026-11-18 21:30 in UTC
const DISMISSED_STAMP = "2026-11-18 21:30";
const OLD_DISMISSED_AT = "2026-11-17T08:05:00+00:00";

const fresh = (): Student => ({
  key: KEY, name: "Fake Alef", cohort: "Spring group", note: "sits at the front", disabled: false,
  access_until: null, cohort_access_until: "2027-03-01",
  labs: [
    {
      id: "linux-1", title: { en: "Linux basics", he: "יסודות לינוקס" }, status: "2/3",
      tasks_passed: 2, tasks_total: 3, attempts: 7,
      tasks: [{ id: "02-two", passed: true, attempts: 1, seconds: 9, median_seconds: 33 }],
      flags: [
        { id: "f-task-2", scope: "task", task_id: "02-two", reason: FLAG_REASON, dismissed_at: null },
        { id: "f-old", scope: "lab", task_id: null, reason: "old flag", dismissed_at: OLD_DISMISSED_AT },
      ],
    },
  ],
});

type Gates = { get?: Promise<void>; patch?: Promise<void>; dismiss?: Promise<void> };

/** A fake manager like the one of the #164 exam; `gates` hold an answer back until released. */
function backend(opts: { me?: Reply; patch?: Reply; dismiss?: Reply; gates?: Gates } = {}) {
  const calls: Calls = [];
  const state = fresh();
  const gates = opts.gates ?? {};
  vi.spyOn(globalThis, "fetch").mockImplementation((async (url: RequestInfo | URL, init?: RequestInit) => {
    const u = String(url);
    const method = (init?.method ?? "GET").toUpperCase();
    const body = typeof init?.body === "string" ? init.body : undefined;
    calls.push({ url: u, method, body });
    const path = new URL(u, "http://localhost").pathname;
    let r: Reply | undefined;
    if (u === "/api/auth/me") r = opts.me ?? ADMIN;
    if (path === "/api/admin/cohorts" && method === "GET") r = { status: 200, body: [] };
    if (path === `/api/admin/students/${KEY}` && method === "GET") {
      if (gates.get) await gates.get;
      r = { status: 200, body: state };
    }
    if (path === `/api/admin/students/${KEY}` && method === "PATCH") {
      const sentBody = JSON.parse(body ?? "{}");
      if (gates.patch) await gates.patch;
      r = opts.patch;
      if (!r) {
        Object.assign(state, sentBody);
        r = { status: 200, body: state };
      }
    }
    const dismiss = new RegExp(`^/api/admin/students/${KEY}/flags/([^/]+)/dismiss$`).exec(path);
    if (dismiss && method === "POST") {
      if (gates.dismiss) await gates.dismiss;
      r = opts.dismiss;
      if (!r) {
        for (const lab of state.labs)
          for (const f of lab.flags) if (f.id === decodeURIComponent(dismiss[1])) f.dismissed_at = DISMISSED_AT;
        r = { status: 200, body: state };
      }
    }
    if (r === undefined) r = { status: 404, body: { detail: "Not Found" } };
    if (r === "network") throw new TypeError("offline");
    return {
      ok: r.status >= 200 && r.status < 300,
      status: r.status,
      json: async () => JSON.parse(JSON.stringify(r.body ?? {})), // a snapshot, like a real body
      text: async () => JSON.stringify(r.body ?? {}),
    } as Response;
  }) as typeof fetch);
  return calls;
}

function gate() {
  let open!: () => void;
  const promise = new Promise<void>((res) => {
    open = res;
  });
  return { promise, open };
}

function at(lang: "en" | "he", path = `/admin/students/${KEY}`) {
  window.history.replaceState({}, "", path);
  window.localStorage.setItem("lf-lang", lang);
  return render(<App />);
}

const writes = (calls: Calls, method: string) =>
  calls.filter((c) => c.method === method && c.url.startsWith("/api/admin/students/"));
const sent = (c: Calls[number]) => JSON.parse(c.body ?? "{}");
const pageEl = () => screen.findByTestId("student-page");
const settle = () => new Promise((r) => setTimeout(r, 20));

beforeEach(() => {
  window.localStorage.clear();
  window.history.replaceState({}, "", "/");
});
afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

// ------------------------------------------------------------------ i18n files

test("both language files have the loading key, in Hebrew, different from English and unique", () => {
  const k = "admin.student.loading";
  expect(Object.keys(HE).sort()).toEqual(Object.keys(EN).sort());
  for (const d of [EN, HE]) {
    expect(typeof d[k], k).toBe("string");
    expect(d[k].trim().length, k).toBeGreaterThan(0);
  }
  expect(HE[k]).toMatch(/[֐-׿]/);
  expect(HE[k]).not.toBe(EN[k]);
  expect(Object.keys(EN).filter((o) => o !== k && EN[o] === EN[k])).toEqual([]);
});

// ------------------------------------------------------------------ the page, en and he

describe.each(LANGS)("the student page follow-ups in %s", (lang) => {
  const d = DICTS[lang];

  // ---- item 4
  test("while the student loads the page shows the back link and a loading line", async () => {
    const g = gate();
    backend({ gates: { get: g.promise } });
    at(lang);
    const loading = await screen.findByText(d["admin.student.loading"]);
    expect(loading).toBeTruthy();
    const back = screen.getByText(d["admin.student.back"]).closest("a");
    expect(back?.getAttribute("href")).toBe("/admin/class");
    expect(screen.queryByTestId("student-page")).toBeNull();
    g.open();
    await pageEl();
    expect(screen.queryByText(d["admin.student.loading"])).toBeNull();
  });

  // ---- item 3
  test("after Dismiss flag the focus is on the flag item, not on the body", async () => {
    backend();
    at(lang);
    await pageEl();
    const before = screen.getByTestId("student-flag-f-task-2");
    fireEvent.click(within(before).getByRole("button", { name: d["admin.student.dismiss"] }));
    await waitFor(() =>
      expect(within(screen.getByTestId("student-flag-f-task-2")).queryByRole("button", { name: d["admin.student.dismiss"] })).toBeNull());
    await waitFor(() => expect(document.activeElement).toBe(screen.getByTestId("student-flag-f-task-2")));
    expect(document.activeElement).not.toBe(document.body);
  });

  test("a flag dismissed now shows its dismissal in local time", async () => {
    backend();
    at(lang);
    await pageEl();
    fireEvent.click(within(screen.getByTestId("student-flag-f-task-2")).getByRole("button", { name: d["admin.student.dismiss"] }));
    await waitFor(() => expect(screen.getByTestId("student-flag-f-task-2").textContent).toContain(d["admin.student.dismissed"]));
    const text = screen.getByTestId("student-flag-f-task-2").textContent ?? "";
    expect(text).toContain(FLAG_REASON); // stays listed
    expect(text).toContain(DISMISSED_STAMP);
    expect(text).not.toContain("23:30");
    expect(text).not.toContain("T21:30");
  });

  test("a flag that was already dismissed on load shows its dismissal time too", async () => {
    backend();
    at(lang);
    await pageEl();
    const old = screen.getByTestId("student-flag-f-old").textContent ?? "";
    expect(old).toContain(d["admin.student.dismissed"]);
    expect(old).toContain("2026-11-17 08:05");
    expect(old).not.toContain("T08:05");
    expect(within(screen.getByTestId("student-flag-f-old")).queryByRole("button", { name: d["admin.student.dismiss"] })).toBeNull();
  });

  // ---- item 2
  test("while a dismiss is in flight its button is disabled and a second click sends nothing", async () => {
    const g = gate();
    const calls = backend({ gates: { dismiss: g.promise } });
    at(lang);
    await pageEl();
    const button = within(screen.getByTestId("student-flag-f-task-2")).getByRole("button", { name: d["admin.student.dismiss"] }) as HTMLButtonElement;
    fireEvent.click(button);
    await waitFor(() => expect(writes(calls, "POST")).toHaveLength(1));
    expect(button.disabled).toBe(true);
    fireEvent.click(button);
    await settle();
    expect(writes(calls, "POST")).toHaveLength(1);
    g.open();
    await waitFor(() => expect(screen.getByTestId("student-flag-f-task-2").textContent).toContain(d["admin.student.dismissed"]));
    expect(writes(calls, "POST")).toHaveLength(1);
  });

  test("a failed dismiss enables the button again", async () => {
    const g = gate();
    backend({ dismiss: "network", gates: { dismiss: g.promise } });
    at(lang);
    await pageEl();
    const button = within(screen.getByTestId("student-flag-f-task-2")).getByRole("button", { name: d["admin.student.dismiss"] }) as HTMLButtonElement;
    fireEvent.click(button);
    await waitFor(() => expect(button.disabled).toBe(true));
    g.open();
    expect((await screen.findByRole("alert")).textContent).toContain(d["admin.error.save"]);
    const again = within(screen.getByTestId("student-flag-f-task-2")).getByRole("button", { name: d["admin.student.dismiss"] }) as HTMLButtonElement;
    expect(again.disabled).toBe(false);
  });

  test("while the note is saving Save is disabled, and text typed meanwhile survives the answer", async () => {
    const g = gate();
    const calls = backend({ gates: { patch: g.promise } });
    at(lang);
    const page = await pageEl();
    const note = within(page).getByLabelText(d["admin.student.note"]) as HTMLTextAreaElement;
    const save = within(page).getByRole("button", { name: d["admin.student.save_note"] }) as HTMLButtonElement;
    fireEvent.change(note, { target: { value: "first draft" } });
    fireEvent.click(save);
    await waitFor(() => expect(writes(calls, "PATCH")).toHaveLength(1));
    expect(save.disabled).toBe(true);
    expect(note.disabled).toBe(false);
    fireEvent.change(note, { target: { value: "first draft and more" } });
    fireEvent.click(save); // disabled: nothing is sent
    await settle();
    expect(writes(calls, "PATCH")).toHaveLength(1);
    g.open();
    await waitFor(() => expect((within(page).getByRole("button", { name: d["admin.student.save_note"] }) as HTMLButtonElement).disabled).toBe(false));
    expect((within(page).getByLabelText(d["admin.student.note"]) as HTMLTextAreaElement).value).toBe("first draft and more");
    expect(sent(writes(calls, "PATCH")[0])).toEqual({ note: "first draft" });
  });

  test("a note that was not edited while saving shows what the server answered", async () => {
    backend();
    at(lang);
    const page = await pageEl();
    fireEvent.change(within(page).getByLabelText(d["admin.student.note"]), { target: { value: "plain save" } });
    fireEvent.click(within(page).getByRole("button", { name: d["admin.student.save_note"] }));
    await waitFor(() => expect((within(page).getByRole("button", { name: d["admin.student.save_note"] }) as HTMLButtonElement).disabled).toBe(false));
    expect((within(page).getByLabelText(d["admin.student.note"]) as HTMLTextAreaElement).value).toBe("plain save");
  });

  test("while the access date is saving Set date is disabled, and a date typed meanwhile survives", async () => {
    const g = gate();
    const calls = backend({ gates: { patch: g.promise } });
    at(lang);
    const page = await pageEl();
    const date = within(page).getByLabelText(d["admin.student.access"]) as HTMLInputElement;
    const extend = within(page).getByRole("button", { name: d["admin.student.extend"] }) as HTMLButtonElement;
    fireEvent.change(date, { target: { value: "2027-05-20" } });
    fireEvent.click(extend);
    await waitFor(() => expect(writes(calls, "PATCH")).toHaveLength(1));
    expect(extend.disabled).toBe(true);
    expect(date.disabled).toBe(false);
    fireEvent.change(date, { target: { value: "2027-06-01" } });
    g.open();
    await waitFor(() => expect((within(page).getByRole("button", { name: d["admin.student.extend"] }) as HTMLButtonElement).disabled).toBe(false));
    expect((within(page).getByLabelText(d["admin.student.access"]) as HTMLInputElement).value).toBe("2027-06-01");
    expect(sent(writes(calls, "PATCH")[0])).toEqual({ access_until: "2027-05-20" });
  });

  test("while disable is in flight its button is disabled; the answer shows the notice and re-enables it", async () => {
    const g = gate();
    const calls = backend({ gates: { patch: g.promise } });
    at(lang);
    const page = await pageEl();
    const button = within(page).getByRole("button", { name: d["admin.student.disable"] }) as HTMLButtonElement;
    fireEvent.click(button);
    await waitFor(() => expect(writes(calls, "PATCH")).toHaveLength(1));
    expect(button.disabled).toBe(true);
    fireEvent.click(button);
    await settle();
    expect(writes(calls, "PATCH")).toHaveLength(1);
    g.open();
    expect(await within(page).findByText(d["admin.student.disabled_notice"])).toBeTruthy();
    const enable = within(page).getByRole("button", { name: d["admin.student.enable"] }) as HTMLButtonElement;
    expect(enable.disabled).toBe(false);
  });

  test("a failed note save enables Save again and keeps the typed text", async () => {
    const g = gate();
    backend({ patch: { status: 500, body: {} }, gates: { patch: g.promise } });
    at(lang);
    const page = await pageEl();
    const save = within(page).getByRole("button", { name: d["admin.student.save_note"] }) as HTMLButtonElement;
    fireEvent.change(within(page).getByLabelText(d["admin.student.note"]), { target: { value: "kept text" } });
    fireEvent.click(save);
    await waitFor(() => expect(save.disabled).toBe(true));
    g.open();
    expect((await screen.findByRole("alert")).textContent).toContain(d["admin.error.save"]);
    expect((within(page).getByRole("button", { name: d["admin.student.save_note"] }) as HTMLButtonElement).disabled).toBe(false);
    expect((within(page).getByLabelText(d["admin.student.note"]) as HTMLTextAreaElement).value).toBe("kept text");
  });
});

// ------------------------------------------------------------------ item 8: Cohorts page links

type CohortStudent = { key: string; name: string; note: string | null; disabled: boolean; access_until: string | null };
const COHORT_STUDENTS: CohortStudent[] = [
  { key: KEY, name: "Fake Alef", note: "sits at the front", disabled: false, access_until: null },
  { key: KEY_BET, name: "Fake Bet", note: null, disabled: true, access_until: "2027-01-01" },
];

function cohortsBackend() {
  const calls: Calls = [];
  vi.spyOn(globalThis, "fetch").mockImplementation((async (url: RequestInfo | URL, init?: RequestInit) => {
    const u = String(url);
    const method = (init?.method ?? "GET").toUpperCase();
    calls.push({ url: u, method });
    let r: { status: number; body?: unknown } = { status: 404, body: { detail: "Not Found" } };
    if (u === "/api/auth/me") r = ADMIN as { status: number; body?: unknown };
    if (u === "/api/admin/cohorts" && method === "GET")
      r = { status: 200, body: [{ id: 7, name: "Spring group", access_until: "2027-03-01", students: 2 }] };
    if (u === "/api/admin/cohorts/7/students") r = { status: 200, body: COHORT_STUDENTS };
    if (u === `/api/admin/students/${KEY}`) r = { status: 200, body: fresh() };
    if (u === "/api/admin/class") r = { status: 200, body: { labs: [], rows: [] } };
    return {
      ok: r.status >= 200 && r.status < 300,
      status: r.status,
      json: async () => r.body ?? {},
      text: async () => JSON.stringify(r.body ?? {}),
    } as Response;
  }) as typeof fetch);
  return calls;
}

describe.each(LANGS)("the Cohorts student list links to the student page in %s", (lang) => {
  const d = DICTS[lang];
  const open = async () => {
    window.history.replaceState({}, "", "/admin");
    window.localStorage.setItem("lf-lang", lang);
    render(<App />);
    await screen.findByTestId("cohort-7");
    fireEvent.click(within(screen.getByTestId("cohort-7")).getByRole("button", { name: d["admin.students.show"] }));
    return screen.findByTestId("students-7");
  };

  test("each student name is a link to /admin/students/<key>", async () => {
    cohortsBackend();
    const list = await open();
    const alef = within(list).getByRole("link", { name: /Fake Alef/ });
    const bet = within(list).getByRole("link", { name: /Fake Bet/ });
    expect(alef.getAttribute("href")).toBe(`/admin/students/${KEY}`);
    expect(bet.getAttribute("href")).toBe(`/admin/students/${KEY_BET}`);
    expect(within(list).getAllByRole("link")).toHaveLength(2);
  });

  test("the note and the disabled mark stay in the list", async () => {
    cohortsBackend();
    const list = await open();
    expect(list.textContent).toContain("sits at the front");
    expect((list.textContent ?? "").split(d["admin.students.disabled"]).length - 1).toBe(1);
  });

  test("clicking a name opens that student's page without a reload", async () => {
    const calls = cohortsBackend();
    const list = await open();
    fireEvent.click(within(list).getByRole("link", { name: /Fake Alef/ }));
    await pageEl();
    expect(window.location.pathname).toBe(`/admin/students/${KEY}`);
    expect(calls.some((c) => c.method === "GET" && c.url === `/api/admin/students/${KEY}`)).toBe(true);
    expect(screen.getByRole("navigation")).toBeTruthy(); // the shell stays
  });
});
