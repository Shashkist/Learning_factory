/**
 * Exam for #10 slice 1 (D53): i18n foundation, student login, routing, logout.
 *
 * CONTRACT the implementation must honour (names chosen by the exam):
 *
 * Files
 *   src/App.tsx            default export <App/>; reads window.location, history based router
 *   src/i18n/en.json       flat object, dotted string keys (below); src/i18n/he.json same keys
 *
 * i18n keys (flat, no interpolation; the student's name is rendered as its own text)
 *   lang.switch                 text of the toggle button: "עב" in en.json, "EN" in he.json
 *   login.title                 heading of the login page
 *   login.id                    label of the ID input
 *   login.password              label of the class password input (type=password)
 *   login.submit                submit button
 *   login.error.invalid_credentials   401 invalid_credentials
 *   login.error.too_many_attempts     429 too_many_attempts
 *   login.error.account_disabled      403 account_disabled
 *   login.error.access_expired        403 access_expired
 *   login.error.unavailable           503 (busy, database unavailable, login not configured)
 *   login.error.generic               anything else (422, 500, network failure)
 *   labs.title                  heading of My labs
 *   labs.empty                  placeholder text on My labs
 *   lab.error.no_session        text on /labs/<id> with no live session (lab screen, #130)
 *   nav.logout                  logout button
 *   notFound.title              text for unknown paths
 *
 * DOM
 *   app root            element with data-testid="app-root": carries dir ("ltr"|"rtl") and lang ("en"|"he")
 *   toggle              a <button> whose visible text is the lang.switch text
 *   login inputs        found with getByLabelText(login.id / login.password), both dir="ltr"
 *   login error         element with role="alert" containing the message
 *   My labs             heading role named labs.title, student name as text, nav.logout button
 *   /labs/<id>          the lab id rendered as text inside an element (or ancestor) with dir="ltr"
 *
 * Storage: localStorage["lf-lang"] is "en" or "he"; default (nothing stored) is "en".
 * Network: GET /api/auth/me on load; POST /api/auth/login with
 *   Content-Type: application/json and body {"id": ..., "password": ...};
 *   POST /api/auth/logout. Error bodies are FastAPI style {"detail": "<code>"}.
 * Routing: after login the path is /labs; after logout /login; protected pages
 *   (/, /labs, /labs/<id>) show the login page when /me answers 401.
 */
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import App from "../../src/App";
import en from "../../src/i18n/en.json";
import he from "../../src/i18n/he.json";

type Dict = Record<string, string>;
const EN = en as Dict;
const HE = he as Dict;
const DICTS: Record<"en" | "he", Dict> = { en: EN, he: HE };

type Reply = { status: number; body?: unknown } | "network";
type Calls = { url: string; method: string; headers: Record<string, string>; body?: string }[];

function backend(replies: { me?: Reply; login?: Reply; logout?: Reply }) {
  const calls: Calls = [];
  globalThis.fetch = vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
    const u = String(url);
    const method = (init?.method ?? "GET").toUpperCase();
    calls.push({
      url: u,
      method,
      headers: Object.fromEntries(new Headers(init?.headers).entries()),
      body: typeof init?.body === "string" ? init.body : undefined,
    });
    let r: Reply | undefined;
    if (u === "/api/auth/me") r = replies.me ?? { status: 401, body: { detail: "not_authenticated" } };
    if (u === "/api/labs" && method === "GET") r = { status: 200, body: [] };
    if (u === "/api/auth/login" && method === "POST") r = replies.login;
    if (u === "/api/auth/logout" && method === "POST") r = replies.logout ?? { status: 204 };
    if (r === undefined) r = { status: 404, body: { detail: "Not Found" } };
    if (r === "network") throw new TypeError("offline");
    return {
      ok: r.status >= 200 && r.status < 300,
      status: r.status,
      json: async () => r.body ?? {},
      text: async () => JSON.stringify(r.body ?? {}),
    } as Response;
  }) as typeof fetch;
  return calls;
}

const STUDENT = { status: 200, body: { kind: "student", name: "Dana Test" } };
const NAME = "Dana Test";

function at(path: string, lang?: "en" | "he") {
  window.history.replaceState({}, "", path);
  if (lang) window.localStorage.setItem("lf-lang", lang);
  return render(<App />);
}

const root = () => screen.getByTestId("app-root");
// Found by its visible text: since #129/#136 the toggle carries its own aria-label
// (lang.switch.label), which becomes the accessible name; since #147 the text sits in a span inside it.
const toggle = (lang: "en" | "he") =>
  screen.getByText(DICTS[lang]["lang.switch"]).closest("button") as HTMLButtonElement;

async function fillAndSubmit(lang: "en" | "he", id: string, password: string) {
  const d = DICTS[lang];
  fireEvent.change(await screen.findByLabelText(d["login.id"]), { target: { value: id } });
  fireEvent.change(screen.getByLabelText(d["login.password"]), { target: { value: password } });
  fireEvent.click(screen.getByRole("button", { name: d["login.submit"] }));
}

beforeEach(() => {
  window.localStorage.clear();
  window.history.replaceState({}, "", "/");
});
afterEach(() => vi.restoreAllMocks());

// ------------------------------------------------------------------ i18n files

test("en.json and he.json have exactly the same keys", () => {
  expect(Object.keys(HE).sort()).toEqual(Object.keys(EN).sort());
});

test("every text in both files is a non-empty string and the contract keys exist", () => {
  const keys = [
    "lang.switch", "login.title", "login.id", "login.password", "login.submit",
    "login.error.invalid_credentials", "login.error.too_many_attempts",
    "login.error.account_disabled", "login.error.access_expired",
    "login.error.unavailable", "login.error.generic",
    "labs.title", "labs.empty", "lab.error.no_session", "nav.logout", "notFound.title",
  ];
  for (const d of [EN, HE])
    for (const k of keys) {
      expect(typeof d[k], k).toBe("string");
      expect(d[k].trim().length, k).toBeGreaterThan(0);
    }
});

test("the Hebrew file is really Hebrew and the toggle shows the other language", () => {
  expect(HE["login.title"]).toMatch(/[֐-׿]/);
  expect(EN["login.title"]).not.toMatch(/[֐-׿]/);
  expect(EN["lang.switch"]).toBe("עב");
  expect(HE["lang.switch"]).toBe("EN");
});

test("the six login error messages are all different from each other (both languages)", () => {
  for (const d of [EN, HE]) {
    const msgs = Object.keys(d).filter((k) => k.startsWith("login.error.")).map((k) => d[k]);
    expect(msgs.length).toBeGreaterThanOrEqual(6);
    expect(new Set(msgs).size).toBe(msgs.length);
  }
});

// ------------------------------------------------------------------ load and routing

test("on load the app asks GET /api/auth/me", async () => {
  const calls = backend({});
  at("/");
  await waitFor(() =>
    expect(calls.some((c) => c.url === "/api/auth/me" && c.method === "GET")).toBe(true),
  );
});

test("an unauthenticated visitor on / sees the login page", async () => {
  backend({});
  at("/");
  expect(await screen.findByRole("heading", { name: EN["login.title"] })).toBeTruthy();
  expect(screen.getByLabelText(EN["login.id"])).toBeTruthy();
  expect(screen.getByLabelText(EN["login.password"])).toBeTruthy();
  expect(screen.getByRole("button", { name: EN["login.submit"] })).toBeTruthy();
});

test.each(["/login", "/labs", "/labs/linux-1"])(
  "an unauthenticated visitor on %s sees the login page",
  async (path) => {
    backend({});
    at(path);
    expect(await screen.findByRole("heading", { name: EN["login.title"] })).toBeTruthy();
    expect(screen.queryByRole("heading", { name: EN["labs.title"] })).toBeNull();
  },
);

test.each(["/", "/labs"])("an authenticated student on %s sees My labs with their name", async (path) => {
  backend({ me: STUDENT });
  at(path);
  expect(await screen.findByRole("heading", { name: EN["labs.title"] })).toBeTruthy();
  expect(screen.getByText(NAME, { exact: false })).toBeTruthy();
  expect(screen.getByText(EN["labs.empty"])).toBeTruthy();
  expect(screen.queryByRole("heading", { name: EN["login.title"] })).toBeNull();
});

test("/labs/<id> is the lab screen; with no live session it says so in the page language", async () => {
  // Since #130 the slice-1 placeholder is replaced by the lab screen (no session in this fake: 404).
  backend({ me: STUDENT });
  at("/labs/linux-1", "he");
  expect(await screen.findByText(HE["lab.error.no_session"], { exact: false })).toBeTruthy();
});

test("an unknown path shows the not-found text", async () => {
  backend({ me: STUDENT });
  at("/no/such/page");
  expect(await screen.findByText(EN["notFound.title"], { exact: false })).toBeTruthy();
});

test("/admin still renders the existing Admin page", async () => {
  backend({ me: { status: 200, body: { kind: "admin", name: "admin" } } });
  at("/admin");
  expect(await screen.findByText("Signed in as admin")).toBeTruthy();
});

test("browser back/forward (popstate) changes the page", async () => {
  backend({ me: STUDENT });
  at("/labs");
  await screen.findByRole("heading", { name: EN["labs.title"] });
  window.history.pushState({}, "", "/labs/abc");
  act(() => {
    window.dispatchEvent(new PopStateEvent("popstate"));
  });
  expect(await screen.findByText(EN["lab.error.no_session"], { exact: false })).toBeTruthy();
});

// ------------------------------------------------------------------ login

test("submitting the form POSTs the id and password as JSON, then shows My labs", async () => {
  const calls = backend({ login: STUDENT });
  at("/login");
  await fillAndSubmit("en", "1234567", "class-pass");
  expect(await screen.findByRole("heading", { name: EN["labs.title"] })).toBeTruthy();
  const post = calls.find((c) => c.url === "/api/auth/login");
  expect(post?.method).toBe("POST");
  expect(post?.headers["content-type"]).toMatch(/application\/json/);
  expect(JSON.parse(post?.body ?? "null")).toEqual({ id: "1234567", password: "class-pass" });
  expect(screen.getByText(NAME, { exact: false })).toBeTruthy();
  expect(window.location.pathname).toBe("/labs");
});

const ERRORS: [number, unknown, string][] = [
  [401, { detail: "invalid_credentials" }, "login.error.invalid_credentials"],
  [429, { detail: "too_many_attempts" }, "login.error.too_many_attempts"],
  [403, { detail: "account_disabled" }, "login.error.account_disabled"],
  [403, { detail: "access_expired" }, "login.error.access_expired"],
  [503, { detail: "busy" }, "login.error.unavailable"],
  [503, { detail: "database unavailable" }, "login.error.unavailable"],
  [503, { detail: "login not configured" }, "login.error.unavailable"],
  [422, { detail: [{ loc: ["body", "id"], msg: "x", type: "y" }] }, "login.error.generic"],
  [500, {}, "login.error.generic"],
];

describe.each(["en", "he"] as const)("login errors in %s", (lang) => {
  test.each(ERRORS)("status %i %j shows %s and stays on the login page", async (status, body, key) => {
    backend({ login: { status, body } });
    at("/login", lang);
    await fillAndSubmit(lang, "1234567", "wrong");
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain(DICTS[lang][key]);
    expect(screen.getByLabelText(DICTS[lang]["login.id"])).toBeTruthy();
    expect(screen.queryByRole("heading", { name: DICTS[lang]["labs.title"] })).toBeNull();
  });

  test("a network failure shows the generic message", async () => {
    backend({ login: "network" });
    at("/login", lang);
    await fillAndSubmit(lang, "1234567", "x");
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain(DICTS[lang]["login.error.generic"]);
  });
});

test("wrong credentials and rate limit show different texts on screen", async () => {
  backend({ login: { status: 401, body: { detail: "invalid_credentials" } } });
  const first = at("/login");
  await fillAndSubmit("en", "1", "x");
  const a = (await screen.findByRole("alert")).textContent;
  first.unmount();
  backend({ login: { status: 429, body: { detail: "too_many_attempts" } } });
  at("/login");
  await fillAndSubmit("en", "1", "x");
  const b = (await screen.findByRole("alert")).textContent;
  expect(a).not.toBe(b);
});

test("the ID and password inputs are left-to-right even in Hebrew", async () => {
  backend({});
  at("/login", "he");
  const id = await screen.findByLabelText(HE["login.id"]);
  const pw = screen.getByLabelText(HE["login.password"]);
  expect(id.getAttribute("dir")).toBe("ltr");
  expect(pw.getAttribute("dir")).toBe("ltr");
  expect(pw.getAttribute("type")).toBe("password");
});

// ------------------------------------------------------------------ toggle and persistence

test("the default language is English, left-to-right", async () => {
  backend({});
  at("/login");
  await screen.findByRole("heading", { name: EN["login.title"] });
  expect(root().getAttribute("dir")).toBe("ltr");
  expect(root().getAttribute("lang")).toBe("en");
});

test("the toggle switches the login page to Hebrew with rtl and back to English", async () => {
  backend({});
  at("/login");
  await screen.findByRole("heading", { name: EN["login.title"] });
  fireEvent.click(toggle("en"));
  expect(await screen.findByRole("heading", { name: HE["login.title"] })).toBeTruthy();
  expect(screen.getByLabelText(HE["login.id"])).toBeTruthy();
  expect(screen.getByLabelText(HE["login.password"])).toBeTruthy();
  expect(screen.getByRole("button", { name: HE["login.submit"] })).toBeTruthy();
  expect(root().getAttribute("dir")).toBe("rtl");
  expect(root().getAttribute("lang")).toBe("he");
  expect(window.localStorage.getItem("lf-lang")).toBe("he");

  fireEvent.click(toggle("he"));
  expect(await screen.findByRole("heading", { name: EN["login.title"] })).toBeTruthy();
  expect(root().getAttribute("dir")).toBe("ltr");
  expect(root().getAttribute("lang")).toBe("en");
  expect(window.localStorage.getItem("lf-lang")).toBe("en");
});

test("the chosen language is restored after a reload", async () => {
  backend({});
  const first = at("/login");
  await screen.findByRole("heading", { name: EN["login.title"] });
  fireEvent.click(toggle("en"));
  await screen.findByRole("heading", { name: HE["login.title"] });
  first.unmount();
  render(<App />);
  expect(await screen.findByRole("heading", { name: HE["login.title"] })).toBeTruthy();
  expect(root().getAttribute("dir")).toBe("rtl");
  expect(root().getAttribute("lang")).toBe("he");
});

test("an error message changes language with the toggle", async () => {
  backend({ login: { status: 429, body: { detail: "too_many_attempts" } } });
  at("/login");
  await fillAndSubmit("en", "1", "x");
  await screen.findByRole("alert");
  fireEvent.click(toggle("en"));
  await waitFor(() =>
    expect(screen.getByRole("alert").textContent).toContain(HE["login.error.too_many_attempts"]),
  );
});

test("My labs is translated and keeps rtl, the student name is still shown", async () => {
  backend({ me: STUDENT });
  at("/labs", "he");
  expect(await screen.findByRole("heading", { name: HE["labs.title"] })).toBeTruthy();
  expect(screen.getByText(HE["labs.empty"])).toBeTruthy();
  expect(screen.getByRole("button", { name: HE["nav.logout"] })).toBeTruthy();
  expect(screen.getByText(NAME, { exact: false })).toBeTruthy();
  expect(root().getAttribute("dir")).toBe("rtl");
});

test("not-found text is translated", async () => {
  backend({ me: STUDENT });
  at("/nope", "he");
  expect(await screen.findByText(HE["notFound.title"], { exact: false })).toBeTruthy();
});

test("no English text leaks into the Hebrew pages (no hard-coded strings)", async () => {
  const pages: [string, Parameters<typeof backend>[0]][] = [
    ["/login", {}],
    ["/labs", { me: STUDENT }],
    ["/labs/x", { me: STUDENT }],
    ["/nope", { me: STUDENT }],
  ];
  for (const [path, replies] of pages) {
    backend(replies);
    const view = at(path, "he");
    await waitFor(() => expect(view.container.textContent?.trim().length).toBeGreaterThan(0));
    await waitFor(() => expect(screen.queryByText("Loading")).toBeNull());
    const shown = view.container.textContent ?? "";
    for (const k of Object.keys(EN))
      if (EN[k] !== HE[k] && EN[k].length > 3) expect(shown, `${path} ${k}`).not.toContain(EN[k]);
    view.unmount();
  }
});

// ------------------------------------------------------------------ logout

test("logout POSTs /api/auth/logout and returns to the login page", async () => {
  const calls = backend({ me: STUDENT });
  at("/labs");
  await screen.findByRole("heading", { name: EN["labs.title"] });
  fireEvent.click(screen.getByRole("button", { name: EN["nav.logout"] }));
  expect(await screen.findByRole("heading", { name: EN["login.title"] })).toBeTruthy();
  expect(calls.some((c) => c.url === "/api/auth/logout" && c.method === "POST")).toBe(true);
  expect(screen.queryByRole("heading", { name: EN["labs.title"] })).toBeNull();
  expect(window.location.pathname).toBe("/login");
  expect(within(root()).queryByText(NAME, { exact: false })).toBeNull();
});
