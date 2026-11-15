import { render, screen } from "@testing-library/react";

import Admin from "./Admin";

function answer(routes: Record<string, { ok: boolean; body?: unknown }>) {
  globalThis.fetch = vi.fn(async (url: RequestInfo | URL) => {
    const r = routes[String(url)] ?? { ok: false };
    return { ok: r.ok, json: async () => r.body ?? {} } as Response;
  }) as typeof fetch;
}

afterEach(() => vi.restoreAllMocks());

test("an admin sees who is signed in and a log out button", async () => {
  answer({ "/api/auth/me": { ok: true, body: { kind: "admin", name: "admin" } } });
  render(<Admin />);
  expect(await screen.findByText("Signed in as admin")).toBeTruthy();
  expect(screen.getByRole("button", { name: "Log out" })).toBeTruthy();
});

test("a visitor gets the Google link when Google is configured", async () => {
  answer({ "/api/auth/admin/methods": { ok: true, body: { google: true, password: false } } });
  render(<Admin />);
  const link = await screen.findByRole("link", { name: "Sign in with Google" });
  expect(link.getAttribute("href")).toBe("/auth/google/start");
});

test("a visitor is told when Google is not configured", async () => {
  answer({ "/api/auth/admin/methods": { ok: true, body: { google: false, password: true } } });
  render(<Admin />);
  expect(await screen.findByText("Google sign-in is not configured")).toBeTruthy();
});
