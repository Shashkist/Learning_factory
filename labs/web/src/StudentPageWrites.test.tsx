import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import { I18nProvider } from "./i18n";
import en from "./i18n/en.json";
import StudentPage from "./StudentPage";

const EN = en as Record<string, string>;
const KEY = "5b0e2f6c-7a1d-4c53-9d58-0f1f1f6f9a11";

const student = (over: object = {}) => ({
  key: KEY, name: "Fake Dalet", cohort: "Fake group", note: null, disabled: false,
  access_until: null, cohort_access_until: "2026-12-31",
  labs: [
    {
      id: "l1", title: { en: "Lab one", he: "מעבדה" }, status: "1/1", tasks_passed: 1, tasks_total: 1,
      attempts: 2, tasks: [],
      flags: [{ id: "f1", scope: "lab", task_id: null, reason: "fake reason", dismissed_at: null }],
    },
  ],
  ...over,
});
const dismissed = (over: object = {}) => {
  const s = student(over);
  (s.labs[0].flags[0] as { dismissed_at: string | null }).dismissed_at = "2026-11-18T10:00:00+00:00";
  return s;
};

function gate() {
  let open!: () => void;
  const promise = new Promise<void>((r) => (open = r));
  return { promise, open };
}

type Reply = { status: number; body?: unknown };
/** GET answers at once; a PATCH and a POST wait for their gate. */
function serve(patch: { gate: Promise<void>; reply: Reply }, post: { gate: Promise<void>; reply: Reply }) {
  vi.spyOn(globalThis, "fetch").mockImplementation((async (_url: RequestInfo | URL, init?: RequestInit) => {
    const m = init?.method ?? "GET";
    let r: Reply = { status: 200, body: student() };
    if (m === "PATCH") {
      await patch.gate;
      r = patch.reply;
    }
    if (m === "POST") {
      await post.gate;
      r = post.reply;
    }
    return { ok: r.status < 300, status: r.status, json: async () => r.body ?? {} } as Response;
  }) as typeof fetch);
}

async function open() {
  render(
    <I18nProvider>
      <StudentPage studentKey={KEY} go={vi.fn()} />
    </I18nProvider>,
  );
  return screen.findByTestId("student-page");
}
const dismissButton = (p: HTMLElement) => within(p).queryByRole("button", { name: EN["admin.student.dismiss"] });

beforeEach(() => window.localStorage.clear());
afterEach(() => vi.restoreAllMocks());

test("an older answer that lands after a newer one does not undo it", async () => {
  const patch = gate();
  const post = gate();
  serve(
    { gate: patch.promise, reply: { status: 200, body: student({ disabled: true }) } },
    { gate: post.promise, reply: { status: 200, body: dismissed() } },
  );
  const p = await open();
  fireEvent.click(within(p).getByRole("button", { name: EN["admin.student.disable"] }));
  fireEvent.click(dismissButton(p)!);
  post.open(); // the newer write answers first
  await waitFor(() => expect(dismissButton(p)).toBeNull());
  patch.open(); // then the old disable answer, which still lists the flag as open
  await waitFor(() => expect((within(p).getByRole("button", { name: EN["admin.student.disable"] }) as HTMLButtonElement).disabled).toBe(false));
  expect(dismissButton(p)).toBeNull();
});

test("a later success does not hide an earlier failure of another write", async () => {
  const patch = gate();
  const post = gate();
  serve(
    { gate: patch.promise, reply: { status: 500 } },
    { gate: post.promise, reply: { status: 200, body: dismissed() } },
  );
  const p = await open();
  fireEvent.click(within(p).getByRole("button", { name: EN["admin.student.save_note"] }));
  patch.open();
  expect((await screen.findByRole("alert")).textContent).toContain(EN["admin.error.save"]);
  fireEvent.click(dismissButton(p)!);
  post.open();
  await waitFor(() => expect(dismissButton(p)).toBeNull());
  expect(screen.getByRole("alert")).toBeTruthy();
});

test("focus is not on the flag item while dismissing, and goes back to the button after a failure", async () => {
  const post = gate();
  serve({ gate: Promise.resolve(), reply: { status: 200 } }, { gate: post.promise, reply: { status: 500 } });
  const p = await open();
  fireEvent.click(dismissButton(p)!);
  await waitFor(() => expect((dismissButton(p) as HTMLButtonElement).disabled).toBe(true));
  expect(document.activeElement).not.toBe(screen.getByTestId("student-flag-f1"));
  post.open();
  await waitFor(() => expect(document.activeElement).toBe(dismissButton(p)));
});
