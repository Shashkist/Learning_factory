import { fireEvent, render, screen } from "@testing-library/react";

import Cohorts from "./Cohorts";
import { I18nProvider } from "./i18n";
import en from "./i18n/en.json";

type Call = { url: string; method: string };

function backend() {
  const calls: Call[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation((async (url: RequestInfo | URL, init?: RequestInit) => {
    const u = String(url);
    const method = (init?.method ?? "GET").toUpperCase();
    calls.push({ url: u, method });
    const body =
      u === "/api/admin/cohorts" && method === "GET"
        ? [{ id: 7, name: "Fake group", access_until: "2027-03-01", students: 0 }]
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
