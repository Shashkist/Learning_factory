import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import Cohorts from "./Cohorts";
import { I18nProvider } from "./i18n";
import en from "./i18n/en.json";

type Call = { url: string; method: string };

function backend(hang: string[] = [], held: Record<string, Promise<Response>> = {}) {
  const calls: Call[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation((async (url: RequestInfo | URL, init?: RequestInit) => {
    const u = String(url);
    const method = (init?.method ?? "GET").toUpperCase();
    calls.push({ url: u, method });
    if (hang.includes(`${method} ${u}`)) return new Promise<Response>(() => {});
    if (`${method} ${u}` in held) return held[`${method} ${u}`];
    const body =
      u === "/api/admin/cohorts" && method === "GET"
        ? [
            { id: 7, name: "Fake group", access_until: "2027-03-01", students: 0 },
            { id: 8, name: "Other group", access_until: "2027-04-01", students: 0 },
          ]
        : { added: 0, rows: [] };
    return { ok: true, status: 200, json: async () => body } as Response;
  }) as typeof fetch);
  return calls;
}

afterEach(() => vi.restoreAllMocks());

const lines = (n: number) => Array.from({ length: n }, (_, i) => `${100000000 + i}, Fake ${i}`).join("\n");

async function paste(text: string) {
  window.localStorage.setItem("lf-lang", "en");
  render(
    <I18nProvider>
      <Cohorts />
    </I18nProvider>,
  );
  fireEvent.change(await screen.findByLabelText(en["admin.import.text"]), { target: { value: text } });
  fireEvent.click(screen.getByRole("button", { name: en["admin.import.submit"] }));
}

test("more than 2,000 non-blank lines are refused before sending, with the limit named", async () => {
  const calls = backend();
  await paste(lines(2001));
  expect(await screen.findByText(en["admin.import.too_many"])).toBeTruthy();
  expect(calls.filter((c) => c.url.endsWith("/import"))).toEqual([]);
});

test("2,000 lines plus blank lines are sent", async () => {
  const calls = backend();
  await paste(`${lines(2000)}\n\n   \n`);
  await screen.findByTestId("import-summary");
  expect(calls.filter((c) => c.url.endsWith("/import"))).toHaveLength(1);
  expect(screen.queryByText(en["admin.import.too_many"])).toBeNull();
});

/*
 * Issue #186, item 1. DOM contract: each cohort row's edit form has a submit button named
 * en["admin.cohorts.save"] (one per row, inside li data-testid="cohort-<id>"). While any other
 * request runs (create, import, another row's save) every such button is `disabled`; when idle
 * they are enabled.
 */
describe("a row's Save is disabled while another request runs (#186)", () => {
  const rowSave = (id: number) =>
    within(screen.getByTestId(`cohort-${id}`)).getByRole("button", { name: en["admin.cohorts.save"] }) as HTMLButtonElement;

  async function mount() {
    window.localStorage.setItem("lf-lang", "en");
    render(
      <I18nProvider>
        <Cohorts />
      </I18nProvider>,
    );
    await screen.findByTestId("cohort-7");
  }

  test("Save is enabled when nothing is running", async () => {
    backend();
    await mount();
    expect(rowSave(7).disabled).toBe(false);
    expect(rowSave(8).disabled).toBe(false);
  });

  test("Save is disabled while a create is running", async () => {
    const calls = backend(["POST /api/admin/cohorts"]);
    await mount();
    fireEvent.click(screen.getByRole("button", { name: en["admin.cohorts.create"] }));
    await waitFor(() => expect(rowSave(7).disabled).toBe(true));
    expect(rowSave(8).disabled).toBe(true);
    fireEvent.click(rowSave(7));
    expect(calls.filter((c) => c.method === "PATCH")).toEqual([]);
  });

  test("Save is disabled while an import is running", async () => {
    backend(["POST /api/admin/cohorts/7/import"]);
    await mount();
    fireEvent.change(screen.getByLabelText(en["admin.import.text"]), { target: { value: "100000000, Fake 0" } });
    fireEvent.click(screen.getByRole("button", { name: en["admin.import.submit"] }));
    await waitFor(() => expect(rowSave(7).disabled).toBe(true));
    expect(rowSave(8).disabled).toBe(true);
  });

  test("Save is disabled on every row while one row's save is running", async () => {
    const calls = backend(["PATCH /api/admin/cohorts/7"]);
    await mount();
    fireEvent.click(rowSave(7));
    await waitFor(() => expect(rowSave(8).disabled).toBe(true));
    expect(rowSave(7).disabled).toBe(true);
    fireEvent.click(rowSave(8));
    expect(calls.filter((c) => c.method === "PATCH")).toHaveLength(1);
  });

  test.each([
    ["succeeds", { ok: true, status: 200, json: async () => ({ id: 7, name: "Fake group", access_until: "2027-03-01" }) }],
    ["fails", { ok: false, status: 500, json: async () => ({ detail: "boom" }) }],
  ])("every Save is enabled again once a row's save %s", async (_, answer) => {
    let finish: (r: Response) => void = () => {};
    backend([], { "PATCH /api/admin/cohorts/7": new Promise<Response>((resolve) => (finish = resolve)) });
    await mount();
    fireEvent.click(rowSave(7));
    await waitFor(() => expect(rowSave(8).disabled).toBe(true));
    finish(answer as unknown as Response);
    await waitFor(() => expect(rowSave(7).disabled).toBe(false));
    expect(rowSave(8).disabled).toBe(false);
  });
});
