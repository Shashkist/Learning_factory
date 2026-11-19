import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import Admin from "./Admin";
import ClassTable from "./ClassTable";
import { I18nProvider } from "./i18n";
import en from "./i18n/en.json";
import StudentPage from "./StudentPage";

const EN = en as Record<string, string>;
const KEY = "5b0e2f6c-7a1d-4c53-9d58-0f1f1f6f9a11";

const student = (over: object = {}) => ({
  key: KEY, name: "Fake Dalet", cohort: "Fake group", note: null, disabled: false,
  access_until: "2027-01-31", cohort_access_until: "2026-12-31",
  labs: [
    {
      id: "l1", title: { en: "Lab one", he: "מעבדה" }, status: "done", tasks_passed: 1, tasks_total: 1,
      attempts: 2, flags: [],
      tasks: [{ id: "t1", passed: true, attempts: 2, seconds: 41.04, median_seconds: 12.6 }],
    },
  ],
  ...over,
});

type Handler = (url: string, init?: RequestInit) => { status: number; body?: unknown } | Promise<{ status: number; body?: unknown }>;

function serve(handler: Handler) {
  const calls: { url: string; method: string; body?: string }[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation((async (url: RequestInfo | URL, init?: RequestInit) => {
    calls.push({ url: String(url), method: init?.method ?? "GET", body: init?.body as string | undefined });
    const r = await handler(String(url), init);
    return { ok: r.status < 300, status: r.status, json: async () => r.body ?? {} } as Response;
  }) as typeof fetch);
  return calls;
}

function page(go = vi.fn()) {
  render(
    <I18nProvider>
      <StudentPage studentKey={KEY} go={go} />
    </I18nProvider>,
  );
  return go;
}

beforeEach(() => window.localStorage.clear());
afterEach(() => {
  vi.restoreAllMocks();
  window.history.replaceState({}, "", "/");
});

test("times are shown in whole seconds, and done labs get a check mark", async () => {
  serve(() => ({ status: 200, body: student() }));
  page();
  const row = await screen.findByTestId("student-task-l1-t1");
  expect(row.textContent).toContain(`${EN["admin.student.time"]}: 41`);
  expect(row.textContent).toContain(`${EN["admin.student.median"]}: 13`);
  expect(row.textContent).not.toContain("41.04");
  expect(screen.getByTestId("student-lab-l1").textContent).toContain("1/1 ✓");
});

test("the override and the class access date are both shown; the date field starts with the override", async () => {
  serve(() => ({ status: 200, body: student() }));
  page();
  const p = await screen.findByTestId("student-page");
  expect(p.textContent).toContain("2026-12-31");
  expect((within(p).getByLabelText(EN["admin.student.access"]) as HTMLInputElement).value).toBe("2027-01-31");
});

test("a successful save clears an earlier save alert", async () => {
  let fail = true;
  serve((_url, init) => {
    if (init?.method === "PATCH") return fail ? { status: 500 } : { status: 200, body: student({ note: "x" }) };
    return { status: 200, body: student() };
  });
  page();
  const p = await screen.findByTestId("student-page");
  fireEvent.click(within(p).getByRole("button", { name: EN["admin.student.save_note"] }));
  expect(await screen.findByRole("alert")).toBeTruthy();
  fail = false;
  fireEvent.click(within(p).getByRole("button", { name: EN["admin.student.save_note"] }));
  await waitFor(() => expect(screen.queryByRole("alert")).toBeNull());
  expect((within(p).getByLabelText(EN["admin.student.note"]) as HTMLTextAreaElement).value).toBe("x");
});

test("the flag id is escaped in the dismiss URL", async () => {
  const flagged = student();
  flagged.labs[0].flags = [
    { id: "task-a/b", scope: "task", task_id: "t1", reason: "fast", dismissed_at: null },
  ] as never;
  const calls = serve(() => ({ status: 200, body: flagged }));
  page();
  fireEvent.click(await screen.findByRole("button", { name: EN["admin.student.dismiss"] }));
  await waitFor(() => expect(calls.filter((c) => c.method === "POST")).toHaveLength(1));
  expect(calls.find((c) => c.method === "POST")?.url).toBe(`/api/admin/students/${KEY}/flags/task-a%2Fb/dismiss`);
});

test("the back link asks the shell to go to the class table", async () => {
  serve(() => ({ status: 200, body: student() }));
  const go = page();
  fireEvent.click(await screen.findByText(EN["admin.student.back"]));
  expect(go).toHaveBeenCalledWith("/admin/class");
});

test("a class-table row without a key shows the name as text, not a link", async () => {
  serve((url) =>
    url.startsWith("/api/admin/class")
      ? {
          status: 200,
          body: {
            labs: [],
            rows: [{ name: "Fake He", cohort: "C", last_activity: null, cells: {} }],
          },
        }
      : { status: 200, body: [] },
  );
  render(
    <I18nProvider>
      <ClassTable />
    </I18nProvider>,
  );
  const table = await screen.findByTestId("class-table");
  expect(table.textContent).toContain("Fake He");
  expect(within(table).queryByRole("link")).toBeNull();
});

test("a malformed escape in the student path is not a student page", async () => {
  const calls = serve((url) =>
    url === "/api/auth/me" ? { status: 200, body: { kind: "admin", name: "a" } } : { status: 200, body: [] },
  );
  window.history.replaceState({}, "", "/admin/students/%E0%A4%A");
  render(
    <I18nProvider>
      <Admin />
    </I18nProvider>,
  );
  await screen.findByRole("navigation");
  await new Promise((r) => setTimeout(r, 20));
  expect(calls.some((c) => c.url.startsWith("/api/admin/students/"))).toBe(false);
  expect(screen.queryByTestId("student-page")).toBeNull();
});

test("in Hebrew a flag's task id is isolated left-to-right inside the translated sentence", async () => {
  window.localStorage.setItem("lf-lang", "he");
  const flag = {
    id: "f1", scope: "task", task_id: "02-two", reason: "stored sentence", dismissed_at: null,
    reason_code: "fast_task", seconds: 9, threshold: 20,
  };
  serve(() => ({ status: 200, body: student({ labs: [{ ...student().labs[0], flags: [flag] }] }) }));
  page();
  const row = await screen.findByTestId("student-flag-f1");
  const ids = [...row.querySelectorAll('bdi[dir="ltr"]')].map((b) => b.textContent);
  expect(ids).toContain("02-two");
  expect(row.textContent).not.toContain("stored sentence");
});
