/**
 * Exam for #331 (slice 1 of the visual design epic #330, D105): design tokens, light and dark
 * theme, bundled font, shared app shell. This file covers acceptance criteria 1-5; criteria 6, 7
 * and 9 are in tests/acceptance/issue-331-design.pw.ts (Playwright, own config
 * tests/acceptance/playwright.design.config.ts). Criterion 8 is "the existing suites stay green".
 *
 * NOTE FOR THE IMPLEMENTER (CI wiring, not testable here): add to the `web` job of
 * .github/workflows/ci.yml, after the existing e2e step, a step that runs
 *   npx playwright test -c tests/acceptance/playwright.design.config.ts
 * (working directory labs/web), and an `actions/upload-artifact` step (`if: always()`) that
 * uploads `labs/web/test-results/design-screenshots/`. The PR body links that artifact (D105 (6)).
 *
 * CONTRACT the implementation must honour (names fixed by D105, the rest is the exam's)
 *   - labs/web/src/styles/tokens.css exists and is reached from src/main.tsx (main.tsx imports
 *     it, or imports a stylesheet that @imports it). It is the ONLY file in src with colour
 *     literals. Light values on `:root`, dark values in
 *     `@media (prefers-color-scheme: dark) { :root { ... } }`. Colour tokens (all 13):
 *     --color-bg --color-surface --color-text --color-text-muted --color-border --color-border-strong --color-primary
 *     --color-on-primary --color-success --color-warning --color-danger --color-on-danger
 *     --color-focus. Values may be hex, rgb() or hsl(), or var(--other-token).
 *   - Every page (student: /login signed out, /labs, /labs/<id>, unknown path; admin: /admin,
 *     /admin/class, /admin/live, /admin/students/<key>, and the Google / not-configured sign-in
 *     states) renders a <header> (role banner) holding the product name (the `app.title` text),
 *     exactly one language switch button (accessible name = `lang.switch.label`), and, when a
 *     session exists, a logout button (accessible name = en `nav.logout`, "Log out").
 *   - The built app (`vite build`) contains a woff2 font in assets/ (the Hebrew subset too),
 *     referenced from built CSS, and no other-origin URL in index.html or CSS.
 *
 * AC5 is checked by running `vite build` from this test into a throw-away temp directory
 * (node vite/bin/vite.js build --outDir <tmp> --emptyOutDir). That is robust: it needs no prior
 * `npm run build`, and it never touches dist/, which the Playwright config's server uses.
 * The `http://www.w3.org/` strings that React and libraries embed (SVG / MathML namespace
 * identifiers) are allowed: they are XML names, nothing ever requests them. React's production
 * bundle also names https://react.dev/errors/... and libraries link github.com / mdn pages in
 * error text: documentation strings, never requested. So index.html and CSS allow no URL at all,
 * while JS fails only on a URL that would be a request for a font, stylesheet or script (file
 * extension css, js, woff, woff2, ttf, or a font-service / CDN host).
 *
 * Scans skip test files (*.test.ts, *.test.tsx). Comments are stripped before scanning.
 */
import { render, screen, waitFor, within, cleanup } from "@testing-library/react";

import App from "../../src/App";
import en from "../../src/i18n/en.json";
import he from "../../src/i18n/he.json";

type Dict = Record<string, string>;
const EN = en as Dict;
const HE = he as Dict;

// ---------------------------------------------------------------- sources under src (raw text)
// Read with node fs, not import.meta.glob(?raw): vitest blanks .css modules, even with ?raw.
// node: modules have no types in this app's tsconfig (types: vite/client, vitest/globals); a
// computed specifier keeps tsc quiet and costs nothing at run time.
type NodeFs = {
  mkdtempSync: (p: string) => string;
  readdirSync: (p: string) => string[];
  statSync: (p: string) => { isDirectory: () => boolean };
  readFileSync: (p: string, enc?: string) => string;
  rmSync: (p: string, o: object) => void;
};
const node = async (name: string) => (await import(/* @vite-ignore */ "node:" + name)) as never;
const fs = (await node("fs")) as NodeFs;
const url = (await node("url")) as { fileURLToPath: (u: string) => string };
const nodePath = (await node("path")) as { resolve: (...p: string[]) => string };
// Resolved from this file, not from the process folder, so any vitest --root or cwd works.
const WEB_ROOT = nodePath.resolve(url.fileURLToPath(import.meta.url), "../../..").split(String.fromCharCode(92)).join("/");
const SRC = `${WEB_ROOT}/src`;
const SOURCES: Record<string, string> = {};
const walkSrc = (dir: string) => {
  for (const name of fs.readdirSync(dir)) {
    const full = `${dir}/${name}`;
    if (fs.statSync(full).isDirectory()) walkSrc(full);
    else if (/\.(?:css|tsx?)$/.test(name)) SOURCES[`../../${full.slice(WEB_ROOT.length + 1)}`] = fs.readFileSync(full, "utf8");
  }
};
walkSrc(SRC);
const rel = (p: string) => p.replace("../../src/", "");
const isTest = (p: string) => /\.test\.tsx?$/.test(p);
const production = Object.entries(SOURCES)
  .filter(([p]) => !isTest(p))
  .map(([p, text]) => ({ file: rel(p), text }));
const cssFiles = production.filter((s) => s.file.endsWith(".css"));
const codeFiles = production.filter((s) => /\.tsx?$/.test(s.file));
const TOKENS = "styles/tokens.css";

const stripCssComments = (s: string) => s.replace(/\/\*[\s\S]*?\*\//g, "");
const stripCodeComments = (s: string) =>
  s.replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|[^:\w"'`])\/\/.*$/gm, "$1");

// ================================================================ AC1: every page in the shell
type Reply = { status: number; body?: unknown };
function backend(me: Reply, extra: Record<string, Reply> = {}) {
  globalThis.fetch = vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
    const path = new URL(String(url), "http://localhost").pathname;
    const key = `${init?.method ?? "GET"} ${path}`;
    let r: Reply = { status: 404, body: { detail: "not_found" } };
    if (path === "/api/auth/me") r = me;
    else if (extra[key]) r = extra[key];
    else if (key === "GET /api/labs") r = { status: 200, body: [] };
    else if (key === "GET /api/admin/cohorts") r = { status: 200, body: [] };
    else if (key === "GET /api/admin/class") r = { status: 200, body: { labs: [], rows: [] } };
    else if (key === "GET /api/admin/live") r = { status: 200, body: { sessions: [] } };
    return new Response(r.status === 204 ? null : JSON.stringify(r.body ?? {}), {
      status: r.status,
      headers: { "Content-Type": "application/json" },
    });
  }) as typeof fetch;
}

const STUDENT: Reply = { status: 200, body: { kind: "student", name: "Dana" } };
const ADMIN: Reply = { status: 200, body: { kind: "admin", name: "Teacher" } };
const SIGNED_OUT: Reply = { status: 401, body: { detail: "not_authenticated" } };
const KEY = "6f1c2b0e-4d1a-4c57-9a43-0a1b2c3d4e5f";

type Page = { name: string; path: string; me: Reply; extra?: Record<string, Reply>; logout: boolean };
const PAGES: Page[] = [
  { name: "student login (signed out)", path: "/login", me: SIGNED_OUT, logout: false },
  { name: "student My labs", path: "/labs", me: STUDENT, logout: true },
  { name: "student lab screen", path: "/labs/linux-basics", me: STUDENT, logout: true },
  { name: "student unknown path", path: "/no/such/page", me: STUDENT, logout: true },
  { name: "student unknown path signed out", path: "/no/such/page", me: SIGNED_OUT, logout: false },
  { name: "admin cohorts", path: "/admin", me: ADMIN, logout: true },
  { name: "admin class table", path: "/admin/class", me: ADMIN, logout: true },
  { name: "admin live sessions", path: "/admin/live", me: ADMIN, logout: true },
  { name: "admin student page", path: `/admin/students/${KEY}`, me: ADMIN, logout: true },
  {
    name: "admin Google sign-in state",
    path: "/admin",
    me: SIGNED_OUT,
    extra: { "GET /api/auth/admin/methods": { status: 200, body: { google: true } } },
    logout: false,
  },
  {
    name: "admin sign-in not configured state",
    path: "/admin",
    me: SIGNED_OUT,
    extra: { "GET /api/auth/admin/methods": { status: 200, body: { google: false } } },
    logout: false,
  },
];

describe("AC1: every page renders inside the shared shell", () => {
  afterEach(() => {
    cleanup();
    window.localStorage.clear();
  });

  for (const lang of ["en", "he"] as const) {
    const D = lang === "en" ? EN : HE;
    for (const p of PAGES) {
      it(`${p.name} has a header with the product name and the language switch (${lang})`, async () => {
        window.localStorage.setItem("lf-lang", lang);
        window.history.pushState({}, "", p.path);
        backend(p.me, p.extra);
        render(<App />);
        const header = await screen.findByRole("banner");
        expect(within(header).getByText(D["app.title"])).toBeTruthy();
        const switches = within(header).getAllByRole("button", { name: D["lang.switch.label"] });
        expect(switches).toHaveLength(1);
        // The one switch on the whole page lives in the header.
        expect(screen.getAllByRole("button", { name: D["lang.switch.label"] })).toHaveLength(1);
        // Exactly one banner landmark.
        expect(screen.getAllByRole("banner")).toHaveLength(1);
      });
    }
  }

  for (const p of PAGES) {
    it(`${p.name}: logout is in the header exactly when a session exists`, async () => {
      window.history.pushState({}, "", p.path);
      backend(p.me, p.extra);
      render(<App />);
      const header = await screen.findByRole("banner");
      const logout = () => within(header).queryAllByRole("button", { name: EN["nav.logout"] });
      if (p.logout) await waitFor(() => expect(logout()).toHaveLength(1));
      else {
        // Let the page settle, then there must still be no logout anywhere.
        await waitFor(() => expect(globalThis.fetch).toHaveBeenCalled());
        await new Promise((r) => setTimeout(r, 50));
        expect(screen.queryAllByRole("button", { name: EN["nav.logout"] })).toHaveLength(0);
      }
    });
  }
});

// ================================================================ AC2: logical properties only
const PHYSICAL_CSS: [string, RegExp][] = [
  ["left / right property", /(?:^|[;{\s])(?:left|right)\s*:/m],
  ["margin-left / margin-right", /(?:^|[;{\s])margin-(?:left|right)\s*:/m],
  ["padding-left / padding-right", /(?:^|[;{\s])padding-(?:left|right)\s*:/m],
  ["border-left* / border-right*", /(?:^|[;{\s])border-(?:left|right)(?:-[a-z]+)*\s*:/m],
  ["text-align left / right", /text-align\s*:\s*(?:left|right)\b/],
  ["float left / right", /(?:^|[;{\s])float\s*:\s*(?:left|right)\b/m],
  ["clear left / right", /(?:^|[;{\s])clear\s*:\s*(?:left|right)\b/m],
];
// camelCase style properties are unambiguous: checked anywhere in a .tsx file.
const PHYSICAL_CAMEL = /\b(?:margin|padding)(?:Left|Right)\b|\bborder(?:Left|Right)[A-Za-z]*\b/;
// Values and bare keys are only checked inside style={{ ... }}.
const PHYSICAL_INLINE_VALUE: [string, RegExp][] = [
  ["left / right key", /(?:^|[,\s{])(?:left|right)\s*:/],
  ["textAlign left / right", /\btextAlign\s*:\s*["'`](?:left|right)["'`]/],
  ["float left / right", /\bfloat\s*:\s*["'`](?:left|right)["'`]/],
  ["clear left / right", /\bclear\s*:\s*["'`](?:left|right)["'`]/],
];

describe("AC2: no physical (left / right) CSS in src", () => {
  it("a stylesheet exists: src/styles/tokens.css is present and reached from main.tsx", () => {
    expect(cssFiles.map((f) => f.file)).toContain(TOKENS);
    const main = SOURCES["../../src/main.tsx"];
    const imports = [...main.matchAll(/import\s+(?:[^"']*from\s+)?["']([^"']+\.css)["']/g)].map((m) => m[1]);
    expect(imports.length).toBeGreaterThan(0);
    const reached = imports.some((i) => {
      if (i.endsWith("tokens.css")) return true;
      const file = cssFiles.find((f) => f.file.endsWith(i.replace(/^\.\//, "")));
      return !!file && /@import[^;]*tokens\.css/.test(file.text);
    });
    expect(reached).toBe(true);
  });

  it("no .css file uses a physical property", () => {
    expect(cssFiles.length).toBeGreaterThan(0); // an empty scan must not pass
    const hits: string[] = [];
    for (const f of cssFiles) {
      const text = stripCssComments(f.text);
      for (const [name, re] of PHYSICAL_CSS) if (re.test(text)) hits.push(`${f.file}: ${name}`);
    }
    expect(hits).toEqual([]);
  });

  it("no style={{...}} object or camelCase style property in .tsx uses a physical property", () => {
    const hits: string[] = [];
    for (const f of codeFiles.filter((c) => c.file.endsWith(".tsx"))) {
      const text = stripCodeComments(f.text);
      if (PHYSICAL_CAMEL.test(text)) hits.push(`${f.file}: camelCase physical property`);
      const inline = [...text.matchAll(/style=\{\{([\s\S]*?)\}\}/g)].map((m) => m[1]).join("\n");
      for (const [name, re] of PHYSICAL_INLINE_VALUE) if (re.test(inline)) hits.push(`${f.file}: ${name}`);
    }
    expect(hits).toEqual([]);
  });
});

// ================================================================ AC3: colours only from tokens
const COLOUR_LITERAL = /#[0-9a-fA-F]{3,8}\b|(?<![\w-])(?:rgba?|hsla?|hwb|lab|lch|oklab|oklch)\(/;

describe("AC3: no colour literal outside the token file", () => {
  it("the token file is the one place with colour literals", () => {
    const tokens = cssFiles.find((f) => f.file === TOKENS);
    expect(tokens, "src/styles/tokens.css must exist").toBeTruthy();
    expect(COLOUR_LITERAL.test(stripCssComments(tokens!.text))).toBe(true);
  });

  it("no .css / .ts / .tsx file except tokens.css has a hex, rgb(), rgba(), hsl() literal", () => {
    expect(cssFiles.length).toBeGreaterThan(0); // no stylesheet at all is not a pass
    const hits: string[] = [];
    for (const f of production) {
      if (f.file === TOKENS) continue;
      const text = f.file.endsWith(".css") ? stripCssComments(f.text) : stripCodeComments(f.text);
      const m = COLOUR_LITERAL.exec(text);
      if (m) hits.push(`${f.file}: ${m[0]}`);
    }
    expect(hits).toEqual([]);
  });
});

// ================================================================ AC4: WCAG AA over the tokens
type RGBA = [number, number, number, number];

function parseColour(raw: string): RGBA {
  const v = raw.trim().toLowerCase();
  let m = /^#([0-9a-f]{3,8})$/.exec(v);
  if (m) {
    let h = m[1];
    if (h.length === 3 || h.length === 4) h = [...h].map((c) => c + c).join("");
    if (h.length !== 6 && h.length !== 8) throw new Error(`bad hex ${raw}`);
    const n = (i: number) => parseInt(h.slice(i, i + 2), 16);
    return [n(0), n(2), n(4), h.length === 8 ? n(6) / 255 : 1];
  }
  const alpha = (s: string | undefined) =>
    s === undefined ? 1 : s.endsWith("%") ? parseFloat(s) / 100 : parseFloat(s);
  m = /^rgba?\(([^)]+)\)$/.exec(v);
  if (m) {
    const parts = m[1].split(/[\s,/]+/).filter(Boolean);
    const ch = (s: string) => (s.endsWith("%") ? (parseFloat(s) / 100) * 255 : parseFloat(s));
    return [ch(parts[0]), ch(parts[1]), ch(parts[2]), alpha(parts[3])];
  }
  m = /^hsla?\(([^)]+)\)$/.exec(v);
  if (m) {
    const parts = m[1].split(/[\s,/]+/).filter(Boolean);
    const h = (((parseFloat(parts[0]) % 360) + 360) % 360) / 360;
    const s = parseFloat(parts[1]) / 100;
    const l = parseFloat(parts[2]) / 100;
    const q = l < 0.5 ? l * (1 + s) : l + s - l * s;
    const p = 2 * l - q;
    const f = (t: number) => {
      t = (t + 1) % 1;
      if (t < 1 / 6) return p + (q - p) * 6 * t;
      if (t < 1 / 2) return q;
      if (t < 2 / 3) return p + (q - p) * (2 / 3 - t) * 6;
      return p;
    };
    return [f(h + 1 / 3) * 255, f(h) * 255, f(h - 1 / 3) * 255, alpha(parts[3])];
  }
  throw new Error(`unsupported colour value: ${raw}`);
}

function luminance([r, g, b]: RGBA): number {
  const lin = (c: number) => {
    const s = c / 255;
    return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
  };
  return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b);
}
function over(fg: RGBA, bg: RGBA): RGBA {
  const a = fg[3];
  return [fg[0] * a + bg[0] * (1 - a), fg[1] * a + bg[1] * (1 - a), fg[2] * a + bg[2] * (1 - a), 1];
}
/** WCAG contrast ratio; a translucent colour is composited over white (bg) / over bg (fg). */
function contrast(fg: RGBA, bg: RGBA): number {
  const solidBg = over(bg, [255, 255, 255, 1]);
  const f = luminance(over(fg, solidBg));
  const b = luminance(solidBg);
  return (Math.max(f, b) + 0.05) / (Math.min(f, b) + 0.05);
}

/** Top-level `{...}` blocks of a stylesheet: [{head, body}] (body may hold nested blocks). */
function blocks(css: string): { head: string; body: string }[] {
  const out: { head: string; body: string }[] = [];
  let depth = 0;
  let start = 0;
  let head = "";
  for (let i = 0; i < css.length; i++) {
    if (css[i] === "{") {
      if (depth === 0) {
        head = css.slice(start, i).trim();
        start = i + 1;
      }
      depth++;
    } else if (css[i] === "}") {
      depth--;
      if (depth === 0) {
        out.push({ head, body: css.slice(start, i) });
        start = i + 1;
      }
    }
  }
  return out;
}
const declarations = (body: string) =>
  Object.fromEntries(
    [...body.matchAll(/(--[\w-]+)\s*:\s*([^;]+?)\s*(?:;|$)/g)].map((m) => [m[1], m[2]]),
  ) as Record<string, string>;

function themes(css: string) {
  const light: Record<string, string> = {};
  const dark: Record<string, string> = {};
  for (const b of blocks(stripCssComments(css))) {
    if (/^@media/.test(b.head)) {
      if (/prefers-color-scheme\s*:\s*dark/.test(b.head))
        for (const inner of blocks(b.body)) if (/:root/.test(inner.head)) Object.assign(dark, declarations(inner.body));
    } else if (/:root/.test(b.head)) Object.assign(light, declarations(b.body));
  }
  return { light, dark };
}
function resolve(name: string, vars: Record<string, string>, depth = 0): string {
  const raw = vars[name];
  if (raw === undefined) throw new Error(`token ${name} is not defined`);
  const ref = /^var\(\s*(--[\w-]+)\s*(?:,[^)]*)?\)$/.exec(raw);
  if (ref) {
    if (depth > 10) throw new Error(`token ${name}: reference loop`);
    return resolve(ref[1], vars, depth + 1);
  }
  return raw;
}

const COLOUR_TOKENS = [
  "bg", "surface", "text", "text-muted", "border", "border-strong", "primary", "on-primary",
  "success", "warning", "danger", "on-danger", "focus",
].map((n) => `--color-${n}`);
// D105 (2): [foreground, background, minimum ratio]
const PAIRS: [string, string, number][] = (
  [
    ["text", "bg", 4.5], ["text", "surface", 4.5],
    ["text-muted", "bg", 4.5], ["text-muted", "surface", 4.5],
    ["on-primary", "primary", 4.5], ["on-danger", "danger", 4.5],
    ["primary", "bg", 4.5], ["primary", "surface", 4.5],
    ["focus", "bg", 3], ["focus", "surface", 3],
    ["border-strong", "bg", 3], ["border-strong", "surface", 3],
  ] as [string, string, number][]
).map(([f, b, r]) => [`--color-${f}`, `--color-${b}`, r]);

describe("AC4: tokens meet WCAG AA in both themes", () => {
  const file = cssFiles.find((f) => f.file === TOKENS);
  const t = file ? themes(file.text) : { light: {}, dark: {} };

  it("tokens.css exists", () => {
    expect(file, "src/styles/tokens.css must exist").toBeTruthy();
  });

  it("every D105 colour token is defined on :root and resolves to a colour", () => {
    expect(file, "src/styles/tokens.css must exist").toBeTruthy();
    for (const name of COLOUR_TOKENS) {
      expect(t.light[name], `${name} on :root`).toBeDefined();
      expect(() => parseColour(resolve(name, t.light)), `${name} (light)`).not.toThrow();
    }
  });

  it("the dark theme (prefers-color-scheme: dark, :root) defines every D105 colour token", () => {
    expect(file, "src/styles/tokens.css must exist").toBeTruthy();
    for (const name of COLOUR_TOKENS) {
      expect(t.dark[name], `${name} in the dark block`).toBeDefined();
      expect(() => parseColour(resolve(name, { ...t.light, ...t.dark })), `${name} (dark)`).not.toThrow();
    }
  });

  for (const theme of ["light", "dark"] as const) {
    for (const [fg, bg, min] of PAIRS) {
      it(`${theme}: ${fg} on ${bg} is at least ${min}:1`, () => {
        expect(file, "src/styles/tokens.css must exist").toBeTruthy();
        const vars = theme === "light" ? t.light : { ...t.light, ...t.dark };
        const ratio = contrast(parseColour(resolve(fg, vars)), parseColour(resolve(bg, vars)));
        expect(ratio).toBeGreaterThanOrEqual(min);
      });
    }
  }

  it("the two themes really differ (dark is not a copy of light)", () => {
    expect(file, "src/styles/tokens.css must exist").toBeTruthy();
    const dark = { ...t.light, ...t.dark };
    expect(resolve("--color-bg", dark)).not.toEqual(resolve("--color-bg", t.light));
    expect(resolve("--color-text", dark)).not.toEqual(resolve("--color-text", t.light));
  });
});

// ================================================================ AC5: self-contained build
describe("AC5: the built app makes no other-origin request", () => {
  let out = "";
  const files: Record<string, string> = {};

  beforeAll(async () => {
    const os = (await node("os")) as { tmpdir: () => string };
    const path = (await node("path")) as { join: (...p: string[]) => string };
    const cp = (await node("child_process")) as {
      execFileSync: (c: string, a: string[], o: object) => unknown;
    };
    out = fs.mkdtempSync(path.join(os.tmpdir(), "lf331-dist-"));
    const proc = (globalThis as unknown as { process: { execPath: string } }).process;
    cp.execFileSync(
      proc.execPath,
      [path.join(WEB_ROOT, "node_modules", "vite", "bin", "vite.js"), "build", "--outDir", out, "--emptyOutDir", "--logLevel", "error"],
      { cwd: WEB_ROOT, stdio: "pipe" },
    );
    const walk = (dir: string, prefix: string) => {
      for (const name of fs.readdirSync(dir)) {
        const full = path.join(dir, name);
        if (fs.statSync(full).isDirectory()) walk(full, `${prefix}${name}/`);
        else files[`${prefix}${name}`] = full;
      }
    };
    walk(out, "");
  }, 240_000);

  afterAll(() => {
    if (out) fs.rmSync(out, { recursive: true, force: true });
  });

  const read = (name: string) => fs.readFileSync(files[name], "utf8");
  const assets = (ext: RegExp) => Object.keys(files).filter((n) => n.startsWith("assets/") && ext.test(n));
  const URL_RE = /(?:https?:)?\/\/[a-z0-9][a-z0-9.-]*\.[a-z]{2,}[^\s"'`)<>]*/gi;
  // Protocol-relative matches inside code are noisy (regexes, comments); look for them only in HTML / CSS.
  const urls = (text: string, allowDocs: boolean) =>
    (text.match(URL_RE) ?? []).filter((u) => {
      if (u.startsWith("http://www.w3.org/")) return false; // XML namespace identifiers, never requested
      if (allowDocs && /^https:\/\/(react\.dev|reactjs\.org)\//.test(u)) return false; // error-message text
      if (allowDocs && u.startsWith("//")) return false;
      return true;
    });

  it("the built index.html has no http(s) or protocol-relative URL", () => {
    expect(files["index.html"]).toBeTruthy();
    expect(urls(read("index.html"), false)).toEqual([]);
  });

  it("every built CSS file has no http(s) or protocol-relative URL, and the build emits a stylesheet", () => {
    const css = assets(/\.css$/);
    expect(css.length, "the build must emit a stylesheet").toBeGreaterThan(0);
    for (const f of css) expect(urls(read(f), false), f).toEqual([]);
  });

  it("no built JS file names a font, stylesheet or script URL on another origin", () => {
    // Library JS carries documentation links in comments and error text (github.com, mdn, ...):
    // strings that are never requested. What would be a request is a font / style / script
    // resource or a CDN or font-service host, so those are what fail here.
    const loads = (u: string) =>
      /\.(?:css|m?js|woff2?|ttf|otf|eot)(?:[?#].*)?$/i.test(u) ||
      /\/\/(?:[^/]*\.)?(?:fonts\.googleapis\.com|fonts\.gstatic\.com|use\.typekit\.net|unpkg\.com|cdn\.jsdelivr\.net|cdnjs\.cloudflare\.com|[^/]*cdn[^/]*)\//i.test(u + "/");
    const js = assets(/\.js$/);
    expect(js.length).toBeGreaterThan(0);
    for (const f of js) expect(urls(read(f), true).filter(loads), f).toEqual([]);
  });

  it("the build includes a woff2 font in assets/", () => {
    expect(assets(/\.woff2$/).length).toBeGreaterThan(0);
  });

  it("the built CSS references the bundled woff2 font by a relative URL (self-hosted)", () => {
    const woff = assets(/\.woff2$/).map((f) => f.replace(/^assets\//, ""));
    expect(woff.length).toBeGreaterThan(0);
    const css = assets(/\.css$/).map(read).join("\n");
    expect(css).toMatch(/@font-face/);
    for (const m of css.matchAll(/url\(\s*["']?([^"')]+\.woff2)["']?\s*\)/g)) expect(m[1]).not.toMatch(/^(?:https?:)?\/\//);
    expect(woff.some((w) => css.includes(w))).toBe(true);
  });

  it("the font covers Hebrew: a Hebrew-subset woff2 ships and the CSS declares its unicode-range", () => {
    // @fontsource-variable/rubik splits the font by unicode-range; the Hebrew subset must ship.
    expect(assets(/hebrew.*\.woff2$/i).length, "a Hebrew-subset woff2 in assets/").toBeGreaterThan(0);
    const css = assets(/\.css$/).map(read).join("\n");
    expect(css).toMatch(/U\+0?5[0-9A-F]{2}/i); // the Hebrew block U+0590-05FF in a unicode-range
  });
});
