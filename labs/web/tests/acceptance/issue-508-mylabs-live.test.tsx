/**
 * Exam for #508 (review follow-ups of #507 / #332, D105, D108, D109): My labs live lab, layout
 * size tokens, loading state.
 *
 * CONTRACT (names chosen by the exam)
 *
 *   AC1 live lab: with a session open on a lab, My labs shows a banner (a link whose accessible
 *   name is the lab title, next to the text labs.live.banner) whose href is `#lab-title-<id>`
 *   and whose target is the heading of that lab's card. The live card carries a text badge
 *   (labs.live.badge, exact text of one element in the card); other cards do not. A live
 *   session on a lab whose progress is "done" (a re-run) keeps the Redo button and shows no
 *   labs.continue wording on its card or in the banner.
 *   (These cases may already pass on current main; they are coverage the issue asks for.)
 *
 *   AC2 layout tokens, in src/styles/tokens.css, each a length:
 *     --form-max   max width of the login form
 *     --card-min   minimum width of a lab card in the My labs grid
 *     --bar-h      height of a progress bar
 *   student.css (login + My labs) uses var(--form-max), var(--card-min) and var(--bar-h) and has
 *   no literal length for those three. teacher.css holds no literal length in a grid minmax(),
 *   and lab-screen.css sizes its progress bar with var(--bar-h).
 *
 *   AC3 loading: while GET /api/labs is pending, My labs shows one element with role `status`
 *   whose text is the new i18n key `labs.loading` (both locales, non-empty, Hebrew in he), and
 *   no card or empty text yet. When the list arrives the status is gone.
 */
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";

import App from "../../src/App";
import en from "../../src/i18n/en.json";
import he from "../../src/i18n/he.json";

type Dict = Record<string, string>;
const DICTS: Record<"en" | "he", Dict> = { en: en as Dict, he: he as Dict };
const LANGS = ["en", "he"] as const;
const HEBREW = /[֐-׿]/;
const LEN = "[\\d.]+(?:rem|px|em|ch)";

// node: modules have no types in this app's tsconfig; a computed specifier keeps tsc quiet.
const nodeFs = (await import(/* @vite-ignore */ "node:" + "fs")) as unknown as {
  readFileSync: (p: string, e: string) => string;
};
const css = (name: string) => nodeFs.readFileSync(`src/styles/${name}`, "utf8").replace(/\/\*[\s\S]*?\*\//g, "");

const LABS = [
  {
    id: "cat-b", title: { en: "Shell basics", he: "יסודות המעטפת" },
    description: { en: "First steps.", he: "צעדים ראשונים." },
    module: 1, lesson: 1, tasks: 4, progress: "not_started", session_id: null,
  },
  {
    id: "cat-a", title: { en: "Files and folders", he: "קבצים ותיקיות" },
    description: { en: "Move around.", he: "מסתובבים." },
    module: 1, lesson: 2, tasks: 3, progress: "2/3", session_id: "sess-a",
  },
  {
    id: "cat-c", title: { en: "Permissions", he: "הרשאות" },
    description: { en: "Who may do what.", he: "מי רשאי מה." },
    module: 2, lesson: 1, tasks: 2, progress: "done", session_id: "sess-c",
  },
];

function backend(labs: () => Promise<unknown[]> | unknown[]) {
  vi.spyOn(globalThis, "fetch").mockImplementation((async (url: RequestInfo | URL) => {
    const u = String(url);
    let body: unknown = { detail: "Not Found" };
    let status = 404;
    if (u === "/api/auth/me") [status, body] = [200, { kind: "student", name: "Dana Test" }];
    if (u === "/api/labs") [status, body] = [200, await labs()];
    return { ok: status === 200, status, json: async () => body, text: async () => JSON.stringify(body) } as Response;
  }) as typeof fetch);
}
function at(lang: "en" | "he") {
  window.history.replaceState({}, "", "/labs");
  window.localStorage.setItem("lf-lang", lang);
  return render(<App />);
}
beforeEach(() => window.localStorage.clear());
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});
const cardOf = (title: string) => screen.getByRole("heading", { name: title }).closest("article") as HTMLElement;

// ------------------------------------------------------------------ AC1
describe.each(LANGS)("AC1 live lab in %s", (lang) => {
  const d = DICTS[lang];
  const [, live, rerun] = LABS;

  test("the banner names the lab title and links to that card's heading", async () => {
    backend(() => LABS);
    at(lang);
    await screen.findByRole("heading", { name: live.title[lang] });
    for (const lab of [live, rerun]) {
      const link = screen.getByRole("link", { name: lab.title[lang] }) as HTMLAnchorElement;
      expect(link.getAttribute("href")).toBe(`#lab-title-${lab.id}`);
      expect(link.parentElement?.textContent).toContain(d["labs.live.banner"]);
      const heading = screen.getByRole("heading", { name: lab.title[lang] });
      expect(heading.id).toBe(`lab-title-${lab.id}`);
      expect(document.querySelector(link.getAttribute("href") ?? "")).toBe(heading);
    }
  });

  test("no banner for a lab without a session", async () => {
    backend(() => LABS);
    at(lang);
    await screen.findByRole("heading", { name: LABS[0].title[lang] });
    expect(screen.queryByRole("link", { name: LABS[0].title[lang] })).toBeNull();
    const bannerLinks = screen.getAllByRole("link").filter((a) => a.getAttribute("href")?.startsWith("#lab-title-"));
    expect(bannerLinks).toHaveLength(2);
  });

  test("the live card has a text badge, the others do not", async () => {
    backend(() => LABS);
    at(lang);
    await screen.findByRole("heading", { name: live.title[lang] });
    expect(d["labs.live.badge"].trim().length).toBeGreaterThan(0);
    expect(within(cardOf(live.title[lang])).getAllByText(d["labs.live.badge"], { exact: true })).toHaveLength(1);
    expect(within(cardOf(rerun.title[lang])).getAllByText(d["labs.live.badge"], { exact: true })).toHaveLength(1);
    expect(within(cardOf(LABS[0].title[lang])).queryByText(d["labs.live.badge"], { exact: true })).toBeNull();
  });

  test("a live session on a done lab shows Redo and no continue wording", async () => {
    backend(() => [rerun]);
    at(lang);
    await screen.findByRole("heading", { name: rerun.title[lang] });
    const card = cardOf(rerun.title[lang]);
    expect(within(card).getByRole("button", { name: d["labs.redo"] })).toBeTruthy();
    expect(within(card).queryByRole("button", { name: d["labs.continue"] })).toBeNull();
    const banner = screen.getByRole("link", { name: rerun.title[lang] }).parentElement as HTMLElement;
    const wording = new RegExp(`${d["labs.continue"]}|${lang === "en" ? "continue" : "המשך"}`, "i");
    expect(card.textContent).not.toMatch(wording);
    expect(banner.textContent).not.toMatch(wording);
  });
});

test("AC1: the live badge and banner strings exist in both locales (Hebrew in he)", () => {
  for (const k of ["labs.live.badge", "labs.live.banner"]) {
    expect(DICTS.en[k]?.trim().length, k).toBeGreaterThan(0);
    expect(DICTS.he[k], k).toMatch(HEBREW);
  }
});

// ------------------------------------------------------------------ AC2
describe("AC2: layout sizes come from tokens", () => {
  const tokens = css("tokens.css");
  const student = css("student.css");
  const teacher = css("teacher.css");
  const labScreen = css("lab-screen.css");

  test.each(["--form-max", "--card-min", "--bar-h"])("tokens.css defines %s as a length", (name) => {
    expect(tokens).toMatch(new RegExp(`${name}\\s*:\\s*${LEN}`));
  });
  test("student.css uses --form-max, --card-min and --bar-h", () => {
    expect(student).toMatch(/var\(--form-max\)/);
    expect(student).toMatch(/var\(--card-min\)/);
    expect(student).toMatch(/var\(--bar-h\)/);
  });
  test("student.css has no literal form width, card minimum or bar height", () => {
    expect(student).not.toMatch(new RegExp(`max-inline-size:\\s*${LEN}`));
    expect(student).not.toMatch(new RegExp(`minmax\\([^)]*${LEN}`));
    expect(student).not.toMatch(new RegExp(`\\.lab-card__bar\\s*\\{[^}]*block-size:\\s*${LEN}`));
  });
  test("teacher.css has no literal length inside a grid minmax()", () => {
    expect(teacher).not.toMatch(new RegExp(`minmax\\([^)]*${LEN}`));
  });
  test("lab-screen.css sizes its progress bar with --bar-h and has no literal bar height", () => {
    expect(labScreen).toMatch(/var\(--bar-h\)/);
    expect(labScreen).not.toMatch(new RegExp(`\\.lab-progress__fill\\s*\\{[^}]*block-size:\\s*${LEN}`));
  });
});

// ------------------------------------------------------------------ AC3
describe.each(LANGS)("AC3 loading state in %s", (lang) => {
  const d = DICTS[lang];

  test("the key exists in both locales", () => {
    expect(DICTS.en["labs.loading"]?.trim().length).toBeGreaterThan(0);
    expect(DICTS.he["labs.loading"]).toMatch(HEBREW);
  });

  test("a status shows the loading text while the list is pending, then goes away", async () => {
    let release: (l: unknown[]) => void = () => {};
    const pending = new Promise<unknown[]>((res) => (release = res));
    backend(() => pending);
    at(lang);
    const status = await screen.findByRole("status");
    expect(status.textContent).toBe(d["labs.loading"]);
    expect(screen.getAllByRole("status")).toHaveLength(1);
    expect(screen.queryAllByRole("article")).toHaveLength(0);
    expect(screen.queryByText(d["labs.empty"])).toBeNull();
    release(LABS);
    await screen.findByRole("heading", { name: LABS[0].title[lang] });
    await waitFor(() => expect(screen.queryByText(d["labs.loading"])).toBeNull());
  });
});
