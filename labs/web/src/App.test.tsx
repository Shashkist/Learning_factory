import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import App from "./App";
import en from "./i18n/en.json";

function backend(logoutStatus: number) {
  const calls: string[] = [];
  globalThis.fetch = vi.fn(async (url: RequestInfo | URL) => {
    calls.push(String(url));
    if (String(url) === "/api/auth/me")
      return new Response(JSON.stringify({ kind: "student", name: "Dana Test" }), { status: 200 });
    if (String(url) === "/api/labs") return new Response("[]", { status: 200 });
    return new Response(null, { status: logoutStatus });
  }) as typeof fetch;
  return calls;
}

afterEach(() => {
  window.history.pushState({}, "", "/");
  window.localStorage.clear();
});

test("a failed logout keeps the student signed in and says so", async () => {
  backend(503);
  window.history.pushState({}, "", "/labs");
  render(<App />);
  fireEvent.click(await screen.findByRole("button", { name: en["nav.logout"] }));
  expect((await screen.findByRole("alert")).textContent).toBe(en["nav.logout_failed"]);
  expect(screen.getByRole("heading", { name: en["labs.title"] })).toBeTruthy();
  expect(window.location.pathname).toBe("/labs");
});

test("a malformed lab id is a not-found page, not a crash", async () => {
  backend(204);
  window.history.pushState({}, "", "/labs/%E0%A4%A");
  render(<App />);
  await waitFor(() => expect(screen.getByText(en["notFound.title"])).toBeTruthy());
});
