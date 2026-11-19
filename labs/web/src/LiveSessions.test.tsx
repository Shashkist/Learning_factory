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

function serve(post: () => Promise<Response> | Response) {
  const posts: string[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation((async (u: string, init?: RequestInit) => {
    if (init?.method === "POST") {
      posts.push(String(u));
      return post();
    }
    return { ok: true, status: 200, json: async () => ({ sessions: [sess(1), sess(2)] }) } as Response;
  }) as typeof fetch);
  render(
    <I18nProvider>
      <LiveSessions open={() => {}} />
    </I18nProvider>,
  );
  return posts;
}

const confirmRow0 = () =>
  fireEvent.click(within(screen.getByTestId("live-row-0")).getByRole("button", { name: EN["admin.live.confirm"] }));

test("the times show hours and minutes in local time, not only the date", async () => {
  serve(() => ({ ok: true, status: 204 }) as Response);
  await screen.findByTestId("live-table");
  const d = new Date("2026-01-01T10:00:00+00:00");
  const hhmm = `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
  expect(screen.getByTestId("live-started-0").textContent).toContain(hhmm);
});

test("End stays disabled while its request is in flight, so one confirm sends one POST", async () => {
  let settle: (r: Response) => void = () => {};
  const posts = serve(() => new Promise<Response>((res) => (settle = res)));
  await screen.findByTestId("live-table");
  fireEvent.click(screen.getByTestId("live-end-0"));
  confirmRow0();
  const again = screen.getByTestId("live-end-0") as HTMLButtonElement;
  expect(again.disabled).toBe(true);
  fireEvent.click(again);
  expect(within(screen.getByTestId("live-row-0")).queryByRole("button", { name: EN["admin.live.confirm"] })).toBeNull();
  settle({ ok: true, status: 204 } as Response);
  await waitFor(() => expect(screen.queryAllByTestId(/^live-row-/)).toHaveLength(1));
  expect(posts).toHaveLength(1);
});

test("a 404 on End drops the stale row without an alert", async () => {
  serve(() => ({ ok: false, status: 404, json: async () => ({ detail: "no_session" }) }) as Response);
  await screen.findByTestId("live-table");
  fireEvent.click(screen.getByTestId("live-end-0"));
  confirmRow0();
  await waitFor(() => expect(screen.queryAllByTestId(/^live-row-/)).toHaveLength(1));
  expect(screen.queryByRole("alert")).toBeNull();
});

test("a 404 that is not no_session keeps the row and shows the error", async () => {
  serve(() => ({ ok: false, status: 404, json: async () => ({ detail: "Not Found" }) }) as Response);
  await screen.findByTestId("live-table");
  fireEvent.click(screen.getByTestId("live-end-0"));
  confirmRow0();
  expect((await screen.findByRole("alert")).textContent).toContain(EN["admin.live.end_error"]);
  expect(screen.queryAllByTestId(/^live-row-/)).toHaveLength(2);
});
