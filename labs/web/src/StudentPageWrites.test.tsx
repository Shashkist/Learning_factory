import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import { I18nProvider } from "./i18n";
import en from "./i18n/en.json";
import StudentPage, { resetStudentQueue } from "./StudentPage";

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

beforeEach(() => {
  window.localStorage.clear();
  resetStudentQueue();
});
afterEach(() => vi.restoreAllMocks());

/** Records every request and answers each one only when the test opens its gate. */
function queued() {
  const calls: { method: string; open: (r: Reply) => void }[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation((async (_url: RequestInfo | URL, init?: RequestInit) => {
    const method = init?.method ?? "GET";
    if (method === "GET") return { ok: true, status: 200, json: async () => student() } as Response;
    const r = await new Promise<Reply>((open) => calls.push({ method, open }));
    return { ok: r.status < 300, status: r.status, json: async () => r.body ?? {} } as Response;
  }) as typeof fetch);
  return calls;
}

test("two writes are sent one after another", async () => {
  const calls = queued();
  const p = await open();
  fireEvent.click(within(p).getByRole("button", { name: EN["admin.student.disable"] }));
  fireEvent.click(dismissButton(p)!);
  await waitFor(() => expect(calls).toHaveLength(1));
  await new Promise((r) => setTimeout(r, 20));
  expect(calls).toHaveLength(1); // the dismiss waits for the disable answer
  expect(calls[0].method).toBe("PATCH");
  calls[0].open({ status: 200, body: student({ disabled: true }) });
  await waitFor(() => expect(calls).toHaveLength(2));
  expect(calls[1].method).toBe("POST");
  calls[1].open({ status: 200, body: dismissed({ disabled: true }) });
  expect(await within(p).findByRole("button", { name: EN["admin.student.enable"] })).toBeTruthy();
  await waitFor(() => expect(dismissButton(p)).toBeNull());
});

test("a failed write in the queue shows its error and the next write still goes out", async () => {
  const calls = queued();
  const p = await open();
  fireEvent.click(within(p).getByRole("button", { name: EN["admin.student.save_note"] }));
  fireEvent.click(dismissButton(p)!);
  await waitFor(() => expect(calls).toHaveLength(1));
  calls[0].open({ status: 500 });
  await waitFor(() => expect(calls).toHaveLength(2));
  calls[1].open({ status: 200, body: dismissed() });
  await waitFor(() => expect(dismissButton(p)).toBeNull());
  expect(screen.getByRole("alert").textContent).toContain(EN["admin.error.save"]);
});

test("a note typed while its save is queued keeps the draft", async () => {
  const calls = queued();
  const p = await open();
  const box = within(p).getByRole("textbox") as HTMLTextAreaElement;
  fireEvent.change(box, { target: { value: "sent" } });
  fireEvent.click(within(p).getByRole("button", { name: EN["admin.student.save_note"] }));
  fireEvent.change(box, { target: { value: "typed later" } });
  await waitFor(() => expect(calls).toHaveLength(1));
  calls[0].open({ status: 200, body: student({ note: "sent" }) });
  await waitFor(() => expect((within(p).getByRole("button", { name: EN["admin.student.save_note"] }) as HTMLButtonElement).disabled).toBe(false));
  expect(box.value).toBe("typed later");
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

test.each([["success", { status: 200, body: dismissed() }], ["failure", { status: 500 }]])(
  "a queued dismiss that answers late with a %s leaves focus in the note",
  async (_n, reply) => {
    const calls = queued();
    const p = await open();
    fireEvent.click(dismissButton(p)!);
    const box = within(p).getByRole("textbox") as HTMLTextAreaElement;
    box.focus();
    await waitFor(() => expect(calls).toHaveLength(1));
    calls[0].open(reply as Reply);
    await waitFor(() => expect(dismissButton(p) === null || !(dismissButton(p) as HTMLButtonElement).disabled).toBe(true));
    await new Promise((r) => setTimeout(r, 20));
    expect(document.activeElement).toBe(box);
  },
);

test("only the control whose write failed is marked invalid, and the alert names it", async () => {
  const calls = queued();
  const p = await open();
  fireEvent.click(within(p).getByRole("button", { name: EN["admin.student.save_note"] }));
  fireEvent.click(dismissButton(p)!);
  await waitFor(() => expect(calls).toHaveLength(1));
  calls[0].open({ status: 500 });
  await waitFor(() => expect(calls).toHaveLength(2));
  calls[1].open({ status: 200, body: dismissed() });
  await waitFor(() => expect(dismissButton(p)).toBeNull());
  const alert = screen.getByRole("alert");
  expect(alert.textContent).toContain(EN["admin.student.note"]);
  const box = within(p).getByRole("textbox");
  expect(box.getAttribute("aria-invalid")).toBe("true");
  expect(box.getAttribute("aria-describedby")).toBe(alert.id);
  expect(within(p).getByLabelText(EN["admin.student.access"]).getAttribute("aria-invalid")).toBeNull();
});

test("a reopened page reads only after the writes of the old page", async () => {
  const gets: string[] = [];
  let finish!: () => void;
  vi.spyOn(globalThis, "fetch").mockImplementation((async (_u: RequestInfo | URL, init?: RequestInit) => {
    const m = init?.method ?? "GET";
    gets.push(m);
    if (m === "PATCH") await new Promise<void>((r) => (finish = r));
    return { ok: true, status: 200, json: async () => student() } as Response;
  }) as typeof fetch);
  const a = render(
    <I18nProvider>
      <StudentPage studentKey={KEY} go={vi.fn()} />
    </I18nProvider>,
  );
  const pa = await screen.findByTestId("student-page");
  fireEvent.click(within(pa).getByRole("button", { name: EN["admin.student.save_note"] }));
  await waitFor(() => expect(gets).toEqual(["GET", "PATCH"]));
  a.unmount();
  render(
    <I18nProvider>
      <StudentPage studentKey={KEY} go={vi.fn()} />
    </I18nProvider>,
  );
  await new Promise((r) => setTimeout(r, 30));
  expect(gets).toEqual(["GET", "PATCH"]); // the new GET waits
  finish();
  await screen.findByTestId("student-page");
  expect(gets).toEqual(["GET", "PATCH", "GET"]);
});
