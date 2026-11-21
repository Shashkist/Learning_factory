import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";

import App from "./App";
import en from "./i18n/en.json";

const EN = en as Record<string, string>;
type Reply = { status: number; body?: unknown } | "hang";
type Route = Reply | Reply[];
type Calls = { url: string; method: string }[];

const SID = "s1";
const API = `/api/sessions/${SID}`;
const T0 = Date.now(); // the real clock at load: no case depends on the date it runs on
const ok = (body: unknown): Reply => ({ status: 200, body });
const err = (status: number, detail: string): Reply => ({
  status,
  body: { detail },
});
const session = (extra: object = {}) =>
  ok({
    id: SID,
    lab_id: "lab-1",
    status: "running",
    expires_at: new Date(T0 + 3_600_000).toISOString(),
    ...extra,
  });

const TEXT = {
  en: "# Tree\n\nRun **mkdir** in `~/inbox` now <b>raw</b>.\n\n```text\nprojects/\n  website/\n```\n\n1. one\n2. two\n",
  he: "# עץ\n\nהריצו `~/inbox`.\n",
};
const task = (extra: object = {}) => ({
  id: "01-a",
  index: 1,
  total: 2,
  kind: "hands-on",
  state: "shown",
  title: { en: "Tree", he: "עץ" },
  text: TEXT,
  checklist: [{ n: 1, text: { en: "Exists", he: "קיים" } }],
  ...extra,
});

/** Routes are "METHOD path". An array is a queue (the last entry repeats). */
function backend(routes: Record<string, Route>) {
  const calls: Calls = [];
  const all: Record<string, Route> = {
    "GET /api/auth/me": ok({ kind: "student", name: "Dana Test" }),
    "GET /api/labs": ok([]),
    "GET /api/sessions/current": session(),
    [`GET ${API}/tasks/current`]: ok(task()),
    [`POST ${API}/heartbeat`]: { status: 204 },
    ...routes,
  };
  const queues = new Map<string, Reply[]>();
  vi.spyOn(globalThis, "fetch").mockImplementation((async (
    url: RequestInfo | URL,
    init?: RequestInit,
  ) => {
    const method = (init?.method ?? "GET").toUpperCase();
    calls.push({ url: String(url), method });
    const key = `${method} ${String(url)}`;
    const h = all[key];
    let r: Reply = err(404, "Not Found");
    if (Array.isArray(h)) {
      if (!queues.has(key)) queues.set(key, [...h]);
      const q = queues.get(key) as Reply[];
      r = q.length > 1 ? (q.shift() as Reply) : q[0];
    } else if (h) r = h;
    if (r === "hang") return new Promise<Response>(() => {});
    return {
      ok: r.status < 300,
      status: r.status,
      json: async () => r.body ?? {},
    } as Response;
  }) as typeof fetch);
  return calls;
}

const n = (calls: Calls, method: string, url: string) =>
  calls.filter((c) => c.method === method && c.url === url).length;
const open = async (path = "/labs/lab-1") => {
  window.history.replaceState({}, "", path);
  const view = render(<App />);
  await screen.findByTestId("task-panel");
  return view;
};
// End lab lives in the top bar, outside the task panel.
const endBtn = () => screen.getByRole("button", { name: EN["lab.end"] }) as HTMLButtonElement;
const btn = (name: string) =>
  within(screen.getByTestId("task-panel")).getByRole("button", {
    name,
  }) as HTMLButtonElement;

beforeEach(() => {
  window.localStorage.clear();
});
afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe("markdown", () => {
  test("renders bold, numbered steps and code; code is left-to-right and HTML stays text", async () => {
    backend({});
    const view = await open();
    const panel = screen.getByTestId("task-panel");
    expect(panel.querySelector("strong")?.textContent).toBe("mkdir");
    expect(panel.querySelectorAll("ol > li")).toHaveLength(2);
    const inline = panel.querySelector("p code");
    expect(inline?.textContent).toBe("~/inbox");
    expect(inline?.getAttribute("dir")).toBe("ltr");
    const pre = panel.querySelector("pre");
    expect(pre?.getAttribute("dir")).toBe("ltr");
    expect(pre?.textContent).toBe("projects/\n  website/\n");
    expect(panel.querySelector("b")).toBeNull();
    expect(panel.textContent).toContain("<b>raw</b>");
    expect(panel.querySelectorAll("h2, h3")).toHaveLength(1); // the duplicate "# Tree" heading is dropped
    expect(view.container.querySelector("iframe")?.getAttribute("title")).toBe(
      EN["lab.terminal"],
    );
  });

  test("Hebrew inline code is left-to-right inside the right-to-left panel", async () => {
    window.localStorage.setItem("lf-lang", "he");
    backend({});
    await open();
    const code = screen.getByTestId("task-panel").querySelector("code");
    expect(code?.getAttribute("dir")).toBe("ltr");
    expect(screen.getByTestId("task-panel").getAttribute("dir")).toBe("rtl");
  });
});

describe("in-flight guard", () => {
  test("while a request is pending every action button is disabled and a second click sends nothing", async () => {
    const calls = backend({ [`POST ${API}/tasks/current/skip`]: "hang" });
    await open();
    fireEvent.click(btn(EN["lab.skip"]));
    await waitFor(() => expect(btn(EN["lab.skip"]).disabled).toBe(true));
    for (const k of ["lab.check", "lab.restart", "lab.skip"])
      expect(btn(EN[k]).disabled).toBe(true);
    expect(endBtn().disabled).toBe(false); // End lab is never blocked by a hung action
    fireEvent.click(btn(EN["lab.skip"]));
    fireEvent.click(btn(EN["lab.restart"]));
    expect(n(calls, "POST", `${API}/tasks/current/skip`)).toBe(1);
    expect(n(calls, "POST", `${API}/tasks/current/restart`)).toBe(0);
  });

  test("a busy alert does not stay over the next task", async () => {
    backend({
      [`POST ${API}/tasks/01-a/check`]: ok({
        passed: true,
        attempts: 1,
        reason: null,
        checklist: [{ n: 1, passed: true, hint: null }],
      }),
      [`POST ${API}/tasks/current/skip`]: err(409, "session_busy"),
      [`POST ${API}/tasks/current/next`]: ok(task({ id: "02-b", index: 2 })),
    });
    await open();
    fireEvent.click(btn(EN["lab.skip"]));
    expect((await screen.findByRole("alert")).textContent).toContain(
      EN["lab.busy"],
    );
    fireEvent.click(btn(EN["lab.check"]));
    await waitFor(() => expect(screen.queryByRole("alert")).toBeNull());
    fireEvent.click(
      await screen.findByRole("button", { name: EN["lab.next"] }),
    );
    await waitFor(() =>
      expect(screen.getByTestId("task-panel").textContent).toContain(
        `${EN["lab.question"]} 2`,
      ),
    );
    expect(screen.queryByRole("alert")).toBeNull();
  });
});

describe("the route's lab", () => {
  test("a live session for another lab is not shown under this URL", async () => {
    const calls = backend({
      "GET /api/sessions/current": session({ lab_id: "lab-9" }),
    });
    window.history.replaceState({}, "", "/labs/lab-1");
    const view = render(<App />);
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain(EN["lab.error.other_lab"]);
    expect(
      within(alert).getByRole("link", { name: EN["lab.to_labs"] }),
    ).toBeTruthy();
    expect(view.container.querySelector("iframe")).toBeNull();
    expect(calls.some((c) => c.url.includes("/tasks"))).toBe(false);
  });

  test("the id in the URL is what is compared", async () => {
    backend({ "GET /api/sessions/current": session({ lab_id: "lab-9" }) });
    await open("/labs/lab-9");
    expect(
      screen.queryByText(EN["lab.error.other_lab"], { exact: false }),
    ).toBeNull();
  });
});

describe("end of the session", () => {
  test("a 410 from an action shows the ended message with a link, no terminal, no more heartbeats", async () => {
    backend({ [`POST ${API}/tasks/current/skip`]: err(410, "session_ended") });
    const view = await open();
    fireEvent.click(btn(EN["lab.skip"]));
    expect((await screen.findByText(EN["lab.ended"])).textContent).toBeTruthy();
    expect(screen.getByRole("link", { name: EN["lab.to_labs"] })).toBeTruthy();
    expect(view.container.querySelector("iframe")).toBeNull();
    expect(screen.queryByTestId("task-panel")).toBeNull();
  });

  test("a heartbeat answering 404 ends the screen and stops the heartbeat", async () => {
    vi.useFakeTimers({
      toFake: [
        "setInterval",
        "clearInterval",
        "setTimeout",
        "clearTimeout",
        "Date",
      ],
    });
    vi.setSystemTime(T0);
    const calls = backend({
      [`POST ${API}/heartbeat`]: err(404, "no_session"),
    });
    window.history.replaceState({}, "", "/labs/lab-1");
    render(<App />);
    for (let i = 0; i < 12; i++)
      await act(async () => void (await vi.advanceTimersByTimeAsync(0)));
    await act(async () => void (await vi.advanceTimersByTimeAsync(16_000)));
    expect(screen.queryByText(EN["lab.ended"])).not.toBeNull();
    const beats = n(calls, "POST", `${API}/heartbeat`);
    await act(async () => void (await vi.advanceTimersByTimeAsync(60_000)));
    expect(n(calls, "POST", `${API}/heartbeat`)).toBe(beats);
  });

  test("at 0:00 the timer stops at 0:00 and the terminal stays (only the server ends a lab)", async () => {
    vi.useFakeTimers({
      toFake: [
        "setInterval",
        "clearInterval",
        "setTimeout",
        "clearTimeout",
        "Date",
      ],
    });
    vi.setSystemTime(T0);
    backend({
      "GET /api/sessions/current": session({
        expires_at: new Date(T0 + 5_000).toISOString(),
      }),
    });
    window.history.replaceState({}, "", "/labs/lab-1");
    const view = render(<App />);
    for (let i = 0; i < 12; i++)
      await act(async () => void (await vi.advanceTimersByTimeAsync(0)));
    await act(async () => void (await vi.advanceTimersByTimeAsync(6_000)));
    expect(screen.queryByText(EN["lab.ended"])).toBeNull();
    expect(screen.getByRole("timer").textContent).toBe("0:00");
    expect(view.container.querySelector("iframe")).not.toBeNull();
  });

  test("expires_at null shows no timer", async () => {
    backend({ "GET /api/sessions/current": session({ expires_at: null }) });
    await open();
    expect(screen.queryByRole("timer")).toBeNull();
  });
});

describe("lab complete", () => {
  test("409 no_next_task on skip is a neutral message, not lab complete", async () => {
    backend({
      [`GET ${API}/tasks/current`]: ok(task({ index: 2, total: 2 })),
      [`POST ${API}/tasks/current/skip`]: err(409, "no_next_task"),
    });
    await open();
    fireEvent.click(btn(EN["lab.skip"]));
    expect((await screen.findByRole("alert")).textContent).toContain(
      EN["lab.no_next"],
    );
    expect(screen.queryByText(EN["lab.complete"], { exact: false })).toBeNull();
  });

  test("409 no_next_task on skip of a passed last task shows lab complete, not 'pass it'", async () => {
    backend({
      [`GET ${API}/tasks/current`]: ok(
        task({ index: 2, total: 2, state: "passed" }),
      ),
      [`POST ${API}/tasks/current/skip`]: err(409, "no_next_task"),
      [`GET ${API}/tasks`]: ok({
        tasks: [{ id: "a", state: "passed", title: { en: "A", he: "א" } }],
      }),
    });
    await open();
    fireEvent.click(btn(EN["lab.skip"]));
    expect(
      await screen.findByText(EN["lab.complete"], { exact: false }),
    ).toBeTruthy();
    expect(screen.queryByText(EN["lab.no_next"], { exact: false })).toBeNull();
  });

  test("409 no_next_task on next shows lab complete with a link, not an error", async () => {
    backend({
      [`GET ${API}/tasks/current`]: ok(
        task({ index: 2, total: 2, state: "passed" }),
      ),
      [`POST ${API}/tasks/current/next`]: err(409, "no_next_task"),
      [`GET ${API}/tasks`]: ok({
        tasks: [{ id: "a", state: "passed", title: { en: "A", he: "א" } }],
      }),
    });
    await open();
    fireEvent.click(btn(EN["lab.next"]));
    expect(
      await screen.findByText(EN["lab.complete"], { exact: false }),
    ).toBeTruthy();
    expect(screen.getByRole("link", { name: EN["lab.to_labs"] })).toBeTruthy();
    expect(screen.queryByRole("alert")).toBeNull();
  });
});

/*
 * Issue #186, items 2 and 3. DOM contract for the open-tasks view: the "N tasks still open" line
 * (en["lab.open.one"], or `${n} ${en["lab.open.many"]}`) sits in ONE live region (role="status" or
 * aria-live) that holds that line only; the task buttons (and their list) are outside every live
 * region. The view is located by the count line and the task buttons, never by a status region
 * that holds the titles.
 */
const countLine = () =>
  screen.queryByText(EN["lab.open.one"]) ?? screen.queryByText(new RegExp(`^[0-9]+ ${EN["lab.open.many"]}$`));
const liveOf = (el: Element) => el.closest('[role="status"], [aria-live]');
const viewGone = () => {
  expect(screen.queryByText(EN["lab.open.one"])).toBeNull();
  expect(screen.queryByText(new RegExp(`${EN["lab.open.many"]}$`))).toBeNull();
};

describe("open tasks view", () => {
  const openRoutes = {
    [`GET ${API}/tasks/current`]: ok(
      task({ index: 2, total: 2, state: "passed" }),
    ),
    [`POST ${API}/tasks/current/next`]: err(409, "no_next_task"),
    [`GET ${API}/tasks`]: ok({
      tasks: [{ id: "a", state: "shown", title: { en: "Alpha", he: "א" } }],
    }),
  };

  test("Next from the complete screen into open tasks replaces the complete line with the count", async () => {
    backend({
      ...openRoutes,
      [`GET ${API}/tasks`]: [
        ok({ tasks: [{ id: "a", state: "passed", title: { en: "Alpha", he: "א" } }] }),
        ok({ tasks: [{ id: "a", state: "shown", title: { en: "Alpha", he: "א" } }] }),
      ],
    });
    await open();
    fireEvent.click(btn(EN["lab.next"]));
    expect(await screen.findByText(EN["lab.complete"], { exact: false })).toBeTruthy();
    fireEvent.click(btn(EN["lab.next"]));
    await waitFor(() =>
      expect(screen.getByRole("status").textContent).toBe(EN["lab.open.one"]),
    );
    expect(screen.queryByText(EN["lab.complete"], { exact: false })).toBeNull();
  });

  test("completing from the open-tasks view clears the list", async () => {
    backend({
      ...openRoutes,
      [`GET ${API}/tasks`]: [
        ok({ tasks: [{ id: "a", state: "shown", title: { en: "Alpha", he: "א" } }] }),
        ok({ tasks: [{ id: "a", state: "passed", title: { en: "Alpha", he: "א" } }] }),
      ],
    });
    await open();
    fireEvent.click(btn(EN["lab.next"]));
    await screen.findByRole("button", { name: "Alpha" });
    fireEvent.click(btn(EN["lab.next"]));
    await waitFor(() =>
      expect(screen.getByRole("status").textContent).toBe(EN["lab.complete"]),
    );
    expect(screen.queryByRole("button", { name: "Alpha" })).toBeNull();
    expect(screen.getAllByRole("status")).toHaveLength(1);
    expect(screen.getByRole("link", { name: EN["lab.to_labs"] })).toBeTruthy();
  });

  test("clears after Previous", async () => {
    backend({
      ...openRoutes,
      [`POST ${API}/tasks/current/previous`]: ok(task({ index: 1 })),
    });
    await open();
    fireEvent.click(btn(EN["lab.next"]));
    expect(await screen.findByText(EN["lab.open.one"])).toBeTruthy();
    fireEvent.click(btn(EN["lab.previous"]));
    await waitFor(viewGone);
  });

  test("a click on an open task opens it", async () => {
    const calls = backend({
      ...openRoutes,
      [`POST ${API}/tasks/a/show`]: ok(
        task({ id: "a", title: { en: "Alpha", he: "א" } }),
      ),
    });
    await open();
    fireEvent.click(btn(EN["lab.next"]));
    fireEvent.click(await screen.findByRole("button", { name: "Alpha" }));
    const heading = await screen.findByRole("heading", { name: "Alpha" });
    expect(n(calls, "POST", `${API}/tasks/a/show`)).toBe(1);
    viewGone();
    await waitFor(() => expect(document.activeElement).toBe(heading));
  });

  test("a failed show says the task could not be opened, not that the lab failed", async () => {
    backend({ ...openRoutes, [`POST ${API}/tasks/a/show`]: err(500, "boom") });
    await open();
    fireEvent.click(btn(EN["lab.next"]));
    fireEvent.click(await screen.findByRole("button", { name: "Alpha" }));
    expect((await screen.findByRole("alert")).textContent).toBe(EN["lab.error.show"]);
    expect(screen.getByText(EN["lab.open.one"])).toBeTruthy();
  });

  test("a second click while a show is pending sends one request", async () => {
    const calls = backend({ ...openRoutes, [`POST ${API}/tasks/a/show`]: "hang" });
    await open();
    fireEvent.click(btn(EN["lab.next"]));
    const alpha = await screen.findByRole("button", { name: "Alpha" });
    fireEvent.click(alpha);
    fireEvent.click(alpha);
    await waitFor(() => expect((alpha as HTMLButtonElement).disabled).toBe(true));
    expect(n(calls, "POST", `${API}/tasks/a/show`)).toBe(1);
  });

  test("the live region holds the count line only; the task buttons are outside it", async () => {
    backend(openRoutes);
    await open();
    fireEvent.click(btn(EN["lab.next"]));
    const line = await screen.findByText(EN["lab.open.one"]);
    const live = liveOf(line);
    expect(live).not.toBeNull();
    expect(within(live as HTMLElement).queryAllByRole("button")).toEqual([]);
    expect((live as HTMLElement).textContent).toBe(EN["lab.open.one"]);
    const alpha = screen.getByRole("button", { name: "Alpha" });
    expect(liveOf(alpha)).toBeNull();
  });

  test("with several open tasks the live region holds the plural count line, no buttons", async () => {
    backend({
      ...openRoutes,
      [`GET ${API}/tasks`]: ok({
        tasks: [
          { id: "a", state: "shown", title: { en: "Alpha", he: "א" } },
          { id: "b", state: "shown", title: { en: "Beta", he: "ב" } },
        ],
      }),
    });
    await open();
    fireEvent.click(btn(EN["lab.next"]));
    const line = await screen.findByText(`2 ${EN["lab.open.many"]}`);
    expect(countLine()).toBe(line);
    const live = liveOf(line) as HTMLElement;
    expect(live).not.toBeNull();
    expect(within(live).queryAllByRole("button")).toEqual([]);
    for (const name of ["Alpha", "Beta"]) expect(liveOf(screen.getByRole("button", { name }))).toBeNull();
  });

  test("while one show is pending the other open-task buttons are disabled", async () => {
    backend({
      ...openRoutes,
      [`GET ${API}/tasks`]: ok({
        tasks: [
          { id: "a", state: "shown", title: { en: "Alpha", he: "א" } },
          { id: "b", state: "shown", title: { en: "Beta", he: "ב" } },
        ],
      }),
      [`POST ${API}/tasks/a/show`]: "hang",
    });
    await open();
    fireEvent.click(btn(EN["lab.next"]));
    fireEvent.click(await screen.findByRole("button", { name: "Alpha" }));
    const beta = screen.getByRole("button", { name: "Beta" }) as HTMLButtonElement;
    await waitFor(() => expect(beta.disabled).toBe(true));
  });

  test("a failed re-read of the list says the list could not be loaded", async () => {
    backend({ ...openRoutes, [`GET ${API}/tasks`]: err(500, "boom") });
    await open();
    fireEvent.click(btn(EN["lab.next"]));
    expect((await screen.findByRole("alert")).textContent).toBe(
      EN["lab.error.tasks"],
    );
    expect(screen.getByTestId("task-panel")).toBeTruthy();
  });
});

describe("End guard", () => {
  test("a second click while End is pending sends one request", async () => {
    const calls = backend({ [`POST ${API}/end`]: "hang" });
    await open();
    fireEvent.click(endBtn());
    fireEvent.click(endBtn());
    expect(n(calls, "POST", `${API}/end`)).toBe(1);
  });
});

describe("check reason", () => {
  test.each(["timeout", "error"])(
    "reason %s says the check itself failed and marks no row",
    async (reason) => {
      backend({
        [`POST ${API}/tasks/01-a/check`]: ok({
          passed: false,
          attempts: 1,
          reason,
          checklist: [{ n: 1, passed: false, hint: "h" }],
        }),
      });
      await open();
      fireEvent.click(btn(EN["lab.check"]));
      expect(await screen.findByText(EN["lab.check_failed"])).toBeTruthy();
      expect(screen.getByTestId("task-panel").textContent).not.toContain("✗");
      expect(
        screen
          .getByText(EN["lab.check_failed"])
          .closest("[aria-live]")
          ?.getAttribute("aria-live"),
      ).toBe("polite");
    },
  );
});

describe("first load while the lab is being prepared", () => {
  test.each(["setup_running", "session_busy", "session_starting"])(
    "%s is retried and then shows the task",
    async (code) => {
      vi.useFakeTimers({
        toFake: [
          "setInterval",
          "clearInterval",
          "setTimeout",
          "clearTimeout",
          "Date",
        ],
      });
      vi.setSystemTime(T0);
      backend({
        [`GET ${API}/tasks/current`]: [
          err(409, code),
          err(409, code),
          ok(task()),
        ],
      });
      window.history.replaceState({}, "", "/labs/lab-1");
      render(<App />);
      for (let i = 0; i < 12; i++)
        await act(async () => void (await vi.advanceTimersByTimeAsync(0)));
      expect(screen.queryByText(EN["lab.preparing"])).not.toBeNull();
      expect(screen.queryByRole("alert")).toBeNull();
      await act(async () => void (await vi.advanceTimersByTimeAsync(2_000)));
      await act(async () => void (await vi.advanceTimersByTimeAsync(2_000)));
      for (let i = 0; i < 12; i++)
        await act(async () => void (await vi.advanceTimersByTimeAsync(0)));
      expect(screen.queryByTestId("task-panel")).not.toBeNull();
    },
  );
});

describe("splitter", () => {
  test("pointer capture is taken on press and a move after release does nothing", async () => {
    backend({});
    await open();
    const sep = screen.getByRole("separator");
    const capture = vi.fn();
    (sep as unknown as { setPointerCapture: unknown }).setPointerCapture =
      capture;
    const frame = sep.parentElement as HTMLElement;
    frame.getBoundingClientRect = () => ({ left: 0, width: 1000 }) as DOMRect;
    fireEvent.pointerDown(sep, { pointerId: 1, clientX: 400 });
    expect(capture).toHaveBeenCalledWith(1);
    fireEvent.pointerMove(sep, { pointerId: 1, clientX: 600 });
    expect(sep.getAttribute("aria-valuenow")).toBe("60");
    fireEvent.pointerUp(sep, { pointerId: 1 });
    fireEvent.pointerMove(sep, { pointerId: 1, clientX: 300 });
    expect(sep.getAttribute("aria-valuenow")).toBe("60");
  });
});
