import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import { I18nProvider } from "./i18n";
import en from "./i18n/en.json";
import LiveSessions from "./LiveSessions";

const EN = en as Record<string, string>;

const sess = (n: number) => ({
  id: `id-${n}`, key: `key-${n}`, name: `Fake ${n}`, cohort: "G", lab_id: "l",
  lab_title: null, started_at: "2026-01-01T10:00:00+00:00", last_heartbeat: null,
});

afterEach(() => vi.restoreAllMocks());

test("two confirms can be open at once and ending one leaves the other open", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation((async (_u: string, init?: RequestInit) =>
    init?.method === "POST"
      ? ({ ok: true, status: 204, json: async () => ({}) } as Response)
      : ({ ok: true, status: 200, json: async () => ({ sessions: [sess(1), sess(2)] }) } as Response)) as typeof fetch);
  render(
    <I18nProvider>
      <LiveSessions open={() => {}} />
    </I18nProvider>,
  );
  await screen.findByTestId("live-table");
  fireEvent.click(screen.getByTestId("live-end-0"));
  fireEvent.click(screen.getByTestId("live-end-1"));
  const confirms = screen.getAllByRole("button", { name: EN["admin.live.confirm"] });
  expect(confirms).toHaveLength(2);
  fireEvent.click(confirms[0]);
  await waitFor(() => expect(screen.queryAllByTestId(/^live-row-/)).toHaveLength(1));
  expect(within(screen.getByTestId("live-row-0")).getByRole("button", { name: EN["admin.live.confirm"] })).toBeTruthy();
});
