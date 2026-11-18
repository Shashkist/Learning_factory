/**
 * Exam for #164 (slice 3 of #11, D59, D63): the Student page in the admin shell, opened from the
 * class table, en and he. The API behind it is described in
 * labs/manager/tests/acceptance/test_issue_164_student_page.py.
 *
 * CONTRACT the implementation must honour (names chosen by the exam)
 *
 * Opening the page
 *   - Class table (#163) rows carry `key` (D63). In row i the student's name is a link (<a>, accessible
 *     name = the name) with href /admin/students/<key>. Clicking it opens the page without a full
 *     reload (history.pushState; the URL changes, the shell stays). The key is the only identifier
 *     in the URL; no id or hash is ever shown or requested.
 *   - /admin/students/<key> renders the admin shell with the page, data-testid="student-page". Same
 *     sign-in states as /admin; nobody signed in (me 401): no /api/admin call at all.
 *
 * Network (credentials same-origin, JSON)
 *   GET   /api/admin/students/<key> -> the student body of the API exam (name, cohort, note,
 *         disabled, access_until, cohort_access_until, labs[...]).
 *   PATCH /api/admin/students/<key> -> the same body; the page shows what it answers.
 *   POST  /api/admin/students/<key>/flags/<flag id>/dismiss -> the same body.
 *   404 on the GET shows admin.student.not_found (role=alert not needed) and no student-page
 *   content; any other failed GET shows role=alert with admin.error.load. A failed PATCH or POST
 *   (non-2xx or network) shows role=alert with admin.error.save and keeps what was typed.
 *
 * DOM (inside student-page)
 *   heading (level 1 or 2)  the student's name
 *   back link               <a> admin.student.back, href /admin/class
 *   cohort                  the cohort name is shown
 *   note                    <textarea> or <input> found by label admin.student.note, starting with
 *                           the current note (empty when null) + button admin.student.save_note ->
 *                           PATCH {note: <typed text>} (only `note` in the body).
 *   disable / enable        one button: admin.student.disable while the student is enabled ->
 *                           PATCH {disabled: true}; admin.student.enable while disabled ->
 *                           PATCH {disabled: false}. A disabled student also shows the text
 *                           admin.student.disabled_notice (an enabled one does not).
 *   extend access           <input type=date> found by label admin.student.access, starting with the
 *                           override date (empty when null) + button admin.student.extend ->
 *                           PATCH {access_until: <typed date>}. Clearing the field and pressing the
 *                           button sends {access_until: null}.
 *   labs                    data-testid="student-lab-<lab id>" per lab of the API, in its order, with
 *                           the lab title in the current language, the status text of
 *                           `tasks_passed`/`tasks_total` as "<n>/<m>", and `attempts` labelled by
 *                           admin.student.attempts (text "<label>: <n>").
 *   task row                data-testid="student-task-<lab id>-<task id>": the task id, the seconds
 *                           (admin.student.time) and the median (admin.student.median) as numbers
 *                           (a null `seconds` shows "–", an en dash).
 *   flags                   data-testid="student-flag-<flag id>" inside its lab, showing the reason
 *                           as sent. A live flag (dismissed_at null) has a button
 *                           admin.student.dismiss -> POST .../flags/<flag id>/dismiss; a dismissed
 *                           one shows admin.student.dismissed and has no such button. A lab without
 *                           flags shows admin.student.no_flags.
 *
 * i18n keys (flat, both files, same key set, Hebrew in he, none equal to the English text, and
 * each English text unique among all English texts, because the older exams find by text)
 *   admin.student.back, .note, .save_note, .disable, .enable, .disabled_notice, .access,
 *   .extend, .attempts, .time, .median, .dismiss, .dismissed, .no_flags, .not_found
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
const OTHER_KEY = "9c1d7e22-3b4a-4f6e-8a10-2d3c4b5a6e77";
const FLAG_REASON = "task 02-two passed 9 s after it appeared";

const fresh = (): Student => ({
  key: KEY, name: "Fake Alef", cohort: "Spring group", note: "sits at the front", disabled: false,
  access_until: null, cohort_access_until: "2027-03-01",
  labs: [
    {
      id: "linux-1", title: { en: "Linux basics", he: "יסודות לינוקס" }, status: "2/3",
      tasks_passed: 2, tasks_total: 3, attempts: 7,
      tasks: [
        { id: "01-one", passed: true, attempts: 2, seconds: 41, median_seconds: 25 },
        { id: "02-two", passed: true, attempts: 1, seconds: 9, median_seconds: 33 },
        { id: "03-three", passed: false, attempts: 4, seconds: null, median_seconds: 60 },
      ],
      flags: [{ id: "f-task-2", scope: "task", task_id: "02-two", reason: FLAG_REASON, dismissed_at: null }],
    },
    {
      id: "git-1", title: { en: "First commits", he: "קומיטים ראשונים" }, status: "none",
      tasks_passed: 0, tasks_total: 1, attempts: 0,
      tasks: [{ id: "01-init", passed: false, attempts: 0, seconds: null, median_seconds: null }],
      flags: [],
    },
  ],
});

/** A fake manager that applies PATCH and dismiss to its own state, like the real one. */
function backend(opts: {
  me?: Reply; get?: Reply; patch?: Reply; dismiss?: Reply; student?: Student;
} = {}) {
  const calls: Calls = [];
  const state = opts.student ?? fresh();
  vi.spyOn(globalThis, "fetch").mockImplementation((async (url: RequestInfo | URL, init?: RequestInit) => {
    const u = String(url);
    const method = (init?.method ?? "GET").toUpperCase();
    const body = typeof init?.body === "string" ? init.body : undefined;
    calls.push({ url: u, method, body });
    const path = new URL(u, "http://localhost").pathname;
    let r: Reply | undefined;
    if (u === "/api/auth/me") r = opts.me ?? ADMIN;
    if (path === "/api/admin/cohorts" && method === "GET") r = { status: 200, body: [] };
    if (path === "/api/admin/class" && method === "GET")
      r = {
        status: 200,
        body: {
          labs: [{ id: "linux-1", title: state.labs[0].title }],
          rows: [
            { key: KEY, name: "Fake Alef", cohort: "Spring group", last_activity: null,
              cells: { "linux-1": { status: "2/3", flag: true } } },
            { key: OTHER_KEY, name: "Fake Alef", cohort: "Spring group", last_activity: null,
              cells: { "linux-1": { status: "none", flag: false } } },
          ],
        },
      };
    if (path === `/api/admin/students/${KEY}` && method === "GET") r = opts.get ?? { status: 200, body: state };
    if (path === `/api/admin/students/${KEY}` && method === "PATCH") {
      r = opts.patch;
      if (!r) {
        Object.assign(state, JSON.parse(body ?? "{}"));
        r = { status: 200, body: state };
      }
    }
    const dismiss = new RegExp(`^/api/admin/students/${KEY}/flags/([^/]+)/dismiss$`).exec(path);
    if (dismiss && method === "POST") {
      r = opts.dismiss;
      if (!r) {
        for (const lab of state.labs)
          for (const f of lab.flags) if (f.id === decodeURIComponent(dismiss[1])) f.dismissed_at = "2026-11-18T10:00:00+00:00";
        r = { status: 200, body: state };
      }
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

function at(lang: "en" | "he", path = `/admin/students/${KEY}`) {
  window.history.replaceState({}, "", path);
  window.localStorage.setItem("lf-lang", lang);
  return render(<App />);
}

const writes = (calls: Calls, method: string) =>
  calls.filter((c) => c.method === method && c.url.startsWith("/api/admin/students/"));
const sent = (c: Calls[number]) => JSON.parse(c.body ?? "{}");
const pageEl = () => screen.findByTestId("student-page");

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
  "back", "note", "save_note", "disable", "enable", "disabled_notice", "access", "extend",
  "attempts", "time", "median", "dismiss", "dismissed", "no_flags", "not_found",
].map((k) => `admin.student.${k}`);

test("both language files have the student page keys, non-empty, and the same key set", () => {
  expect(Object.keys(HE).sort()).toEqual(Object.keys(EN).sort());
  for (const d of [EN, HE])
    for (const k of KEYS) {
      expect(typeof d[k], k).toBe("string");
      expect(d[k].trim().length, k).toBeGreaterThan(0);
    }
});

test("the Hebrew student page texts are Hebrew and differ from the English ones", () => {
  for (const k of KEYS) {
    expect(HE[k], k).toMatch(/[֐-׿]/);
    expect(HE[k], k).not.toBe(EN[k]);
  }
});

test("every English student page text is unique among all English texts", () => {
  for (const k of KEYS) {
    const same = Object.keys(EN).filter((o) => o !== k && EN[o] === EN[k]);
    expect(same, k).toEqual([]);
  }
});

// ------------------------------------------------------------------ opening the page

test("the class table names link to the student page by key, so two equal names stay apart", async () => {
  backend();
  at("en", "/admin/class");
  await screen.findByTestId("class-table");
  const links = within(screen.getByTestId("class-table")).getAllByRole("link", { name: "Fake Alef" });
  expect(links.map((a) => a.getAttribute("href"))).toEqual([
    `/admin/students/${KEY}`, `/admin/students/${OTHER_KEY}`,
  ]);
});

test("clicking a name in the class table opens that student's page without a reload", async () => {
  const calls = backend();
  at("en", "/admin/class");
  await screen.findByTestId("class-table");
  fireEvent.click(within(screen.getByTestId("class-row-0")).getByRole("link", { name: "Fake Alef" }));
  await pageEl();
  expect(window.location.pathname).toBe(`/admin/students/${KEY}`);
  expect(calls.some((c) => c.method === "GET" && c.url === `/api/admin/students/${KEY}`)).toBe(true);
  expect(screen.getByRole("navigation")).toBeTruthy(); // the shell stays
});

test("the back link returns to the class table", async () => {
  backend();
  at("en");
  const page = await pageEl();
  const back = within(page).getByText(EN["admin.student.back"]).closest("a");
  expect(back?.getAttribute("href")).toBe("/admin/class");
  fireEvent.click(back!);
  await screen.findByTestId("class-table");
  expect(window.location.pathname).toBe("/admin/class");
});

test("nobody signed in: no admin call is made on a student page", async () => {
  const calls = backend({ me: { status: 401, body: { detail: "not_authenticated" } } });
  at("en");
  await waitFor(() => expect(calls.some((c) => c.url === "/api/auth/me")).toBe(true));
  await new Promise((r) => setTimeout(r, 20));
  expect(calls.filter((c) => c.url.startsWith("/api/admin"))).toHaveLength(0);
  expect(screen.queryByTestId("student-page")).toBeNull();
});

test("an unknown key says the student was not found", async () => {
  backend({ get: { status: 404, body: { detail: "Not Found" } } });
  at("en");
  expect(await screen.findByText(EN["admin.student.not_found"])).toBeTruthy();
  expect(screen.queryByTestId("student-page")).toBeNull();
});

test("a failed load shows the load alert and no page", async () => {
  backend({ get: { status: 500, body: {} } });
  at("en");
  expect((await screen.findByRole("alert")).textContent).toContain(EN["admin.error.load"]);
  expect(screen.queryByTestId("student-page")).toBeNull();
});

// ------------------------------------------------------------------ the page, en and he

describe.each(LANGS)("the student page in %s", (lang) => {
  const d = DICTS[lang];

  test("the heading, cohort, document language and direction", async () => {
    backend();
    at(lang);
    const page = await pageEl();
    expect(within(page).getByRole("heading", { name: "Fake Alef" })).toBeTruthy();
    expect(page.textContent).toContain("Spring group");
    expect(document.documentElement.lang).toBe(lang);
    expect(document.documentElement.dir).toBe(lang === "he" ? "rtl" : "ltr");
  });

  test("a lab shows its title in the language, passed over total and attempts", async () => {
    backend();
    at(lang);
    await pageEl();
    const lab = screen.getByTestId("student-lab-linux-1");
    expect(lab.textContent).toContain(lang === "he" ? "יסודות לינוקס" : "Linux basics");
    expect(lab.textContent).toContain("2/3");
    expect(lab.textContent).toContain(`${d["admin.student.attempts"]}: 7`);
    const order = screen.getAllByTestId(/^student-lab-/).map((e) => e.getAttribute("data-testid"));
    expect(order).toEqual(["student-lab-linux-1", "student-lab-git-1"]);
  });

  test("a task row shows its time next to the class median, and a dash when never passed", async () => {
    backend();
    at(lang);
    await pageEl();
    const first = screen.getByTestId("student-task-linux-1-01-one");
    expect(first.textContent).toContain("41");
    expect(first.textContent).toContain("25");
    expect(first.textContent).toContain(d["admin.student.time"]);
    expect(first.textContent).toContain(d["admin.student.median"]);
    expect(screen.getByTestId("student-task-linux-1-03-three").textContent).toContain("–");
  });

  test("a live flag shows its reason and a dismiss button; a lab without flags says so", async () => {
    backend();
    at(lang);
    await pageEl();
    const flag = screen.getByTestId("student-flag-f-task-2");
    expect(flag.textContent).toContain(FLAG_REASON);
    expect(within(flag).getByRole("button", { name: d["admin.student.dismiss"] })).toBeTruthy();
    expect(within(screen.getByTestId("student-lab-git-1")).getByText(d["admin.student.no_flags"])).toBeTruthy();
  });

  test("dismissing a flag posts it and then shows it as dismissed without the button", async () => {
    const calls = backend();
    at(lang);
    await pageEl();
    fireEvent.click(within(screen.getByTestId("student-flag-f-task-2")).getByRole("button", { name: d["admin.student.dismiss"] }));
    await waitFor(() => expect(within(screen.getByTestId("student-flag-f-task-2")).getByText(d["admin.student.dismissed"])).toBeTruthy());
    expect(within(screen.getByTestId("student-flag-f-task-2")).queryByRole("button", { name: d["admin.student.dismiss"] })).toBeNull();
    expect(calls.filter((c) => c.method === "POST").map((c) => c.url)).toEqual([
      `/api/admin/students/${KEY}/flags/f-task-2/dismiss`,
    ]);
  });

  test("saving the note sends only the note", async () => {
    const calls = backend();
    at(lang);
    const page = await pageEl();
    const note = within(page).getByLabelText(d["admin.student.note"]) as HTMLInputElement;
    expect(note.value).toBe("sits at the front");
    fireEvent.change(note, { target: { value: "needs a seat by the door" } });
    fireEvent.click(within(page).getByRole("button", { name: d["admin.student.save_note"] }));
    await waitFor(() => expect(writes(calls, "PATCH")).toHaveLength(1));
    expect(sent(writes(calls, "PATCH")[0])).toEqual({ note: "needs a seat by the door" });
  });

  test("extending access sends the date, and clearing it sends null", async () => {
    const calls = backend();
    at(lang);
    const page = await pageEl();
    const date = within(page).getByLabelText(d["admin.student.access"]) as HTMLInputElement;
    expect(date.type).toBe("date");
    expect(date.value).toBe("");
    fireEvent.change(date, { target: { value: "2027-05-20" } });
    fireEvent.click(within(page).getByRole("button", { name: d["admin.student.extend"] }));
    await waitFor(() => expect(writes(calls, "PATCH")).toHaveLength(1));
    expect(sent(writes(calls, "PATCH")[0])).toEqual({ access_until: "2027-05-20" });
    await waitFor(() => expect((within(page).getByLabelText(d["admin.student.access"]) as HTMLInputElement).value).toBe("2027-05-20"));
    fireEvent.change(within(page).getByLabelText(d["admin.student.access"]), { target: { value: "" } });
    fireEvent.click(within(page).getByRole("button", { name: d["admin.student.extend"] }));
    await waitFor(() => expect(writes(calls, "PATCH")).toHaveLength(2));
    expect(sent(writes(calls, "PATCH")[1])).toEqual({ access_until: null });
  });

  test("disable then enable flips the button and the notice", async () => {
    const calls = backend();
    at(lang);
    const page = await pageEl();
    expect(within(page).queryByText(d["admin.student.disabled_notice"])).toBeNull();
    expect(within(page).queryByRole("button", { name: d["admin.student.enable"] })).toBeNull();
    fireEvent.click(within(page).getByRole("button", { name: d["admin.student.disable"] }));
    expect(await within(page).findByText(d["admin.student.disabled_notice"])).toBeTruthy();
    expect(sent(writes(calls, "PATCH")[0])).toEqual({ disabled: true });
    expect(within(page).queryByRole("button", { name: d["admin.student.disable"] })).toBeNull();
    fireEvent.click(within(page).getByRole("button", { name: d["admin.student.enable"] }));
    await waitFor(() => expect(within(page).queryByText(d["admin.student.disabled_notice"])).toBeNull());
    expect(sent(writes(calls, "PATCH")[1])).toEqual({ disabled: false });
  });

  test("a failed save shows the save alert and keeps the typed note", async () => {
    backend({ patch: { status: 500, body: {} } });
    at(lang);
    const page = await pageEl();
    const note = within(page).getByLabelText(d["admin.student.note"]) as HTMLInputElement;
    fireEvent.change(note, { target: { value: "typed but not saved" } });
    fireEvent.click(within(page).getByRole("button", { name: d["admin.student.save_note"] }));
    expect((await screen.findByRole("alert")).textContent).toContain(d["admin.error.save"]);
    expect((within(page).getByLabelText(d["admin.student.note"]) as HTMLInputElement).value).toBe("typed but not saved");
  });

  test("a failed dismiss shows the save alert and keeps the button", async () => {
    backend({ dismiss: "network" });
    at(lang);
    await pageEl();
    const flag = screen.getByTestId("student-flag-f-task-2");
    fireEvent.click(within(flag).getByRole("button", { name: d["admin.student.dismiss"] }));
    expect((await screen.findByRole("alert")).textContent).toContain(d["admin.error.save"]);
    expect(within(screen.getByTestId("student-flag-f-task-2")).getByRole("button", { name: d["admin.student.dismiss"] })).toBeTruthy();
  });

  test("the page shows no id or hash text", async () => {
    backend();
    at(lang);
    const page = await pageEl();
    expect(page.textContent).not.toMatch(/[0-9a-f]{32,}/);
    expect(page.textContent).not.toContain(KEY);
  });
});
