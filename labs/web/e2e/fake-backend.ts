import type { Page, Route } from "@playwright/test";

const bi = (en: string, he: string) => ({ en, he });
const TASKS = [
  {
    id: "t1",
    title: bi("List the files", "הצגת הקבצים"),
    text: bi("Run `ls` in the terminal.", "הריצו `ls` בטרמינל."),
    checklist: [{ n: 1, text: bi("You ran ls", "הרצתם ls") }],
  },
  {
    id: "t2",
    title: bi("Make a folder", "יצירת תיקייה"),
    text: bi("Run `mkdir demo`.", "הריצו `mkdir demo`."),
    checklist: [{ n: 1, text: bi("demo exists", "demo קיימת") }],
  },
];
const LAB = {
  id: "linux-basics",
  title: bi("Linux basics", "יסודות לינוקס"),
  description: bi("First steps in the shell", "צעדים ראשונים במעטפת"),
  module: 1,
  lesson: 1,
  tasks: TASKS.length,
  progress: "not_started",
  session_id: null,
};

export const SESSION_ID = "s-e2e";

export const json = (route: Route, body: unknown, status = 200) =>
  route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });

// The /api/* calls the student UI makes, answered from memory. The request log lets a test
// assert what the UI sent; `unmocked` lists every call the fake does not know (each test
// asserts it stays empty, so a new or renamed call fails the suite even when the UI hides it).
export async function fakeBackend(page: Page) {
  const log: string[] = [];
  const unmocked: string[] = [];
  const passedIds = new Set<string>();
  let started = false;
  let signedIn = false;
  let session: { id: string; lab_id: string; expires_at: string } | null = null;
  let index = 0;
  const task = (i: number) => ({
    ...TASKS[i],
    index: i + 1,
    total: TASKS.length,
    kind: "hands-on",
    state: "shown",
  });
  const progress = () =>
    passedIds.size === TASKS.length ? "done" : started ? `${passedIds.size}/${TASKS.length}` : "not_started";
  await page.route("**/api/**", (route) => {
    const req = route.request();
    const path = new URL(req.url()).pathname;
    const key = `${req.method()} ${path}`;
    log.push(key);
    switch (true) {
      case key === "GET /api/auth/me":
        return signedIn ? json(route, { kind: "student", name: "Dana" }) : json(route, {}, 401);
      case key === "POST /api/auth/login":
        signedIn = true;
        return json(route, { kind: "student", name: "Dana" });
      case key === "POST /api/auth/logout":
        signedIn = false;
        return route.fulfill({ status: 204 });
      case key === "GET /api/labs":
        // As catalog.py: "done", "not_started" before any session, else "<passed>/<total>"
        // (an ended session still counts; session_id is only set while one is live).
        return json(route, [{ ...LAB, session_id: session?.id ?? null, progress: progress() }]);
      case key === "POST /api/sessions":
        started = true;
        session = { id: SESSION_ID, lab_id: LAB.id, expires_at: new Date(Date.now() + 3_600_000).toISOString() };
        return json(route, session, 201);
      case key === "GET /api/sessions/current":
        return session ? json(route, session) : json(route, { detail: "no_session" }, 404);
      case key === `GET /api/sessions/${SESSION_ID}/tasks/current`:
        return json(route, task(index));
      case key === `POST /api/sessions/${SESSION_ID}/tasks/${TASKS[index]?.id}/check`:
        passedIds.add(TASKS[index].id);
        return json(route, { passed: true, checklist: [{ n: 1, passed: true, hint: null }] });
      case key === `POST /api/sessions/${SESSION_ID}/tasks/current/next`:
        if (index + 1 >= TASKS.length) return json(route, { detail: "no_next_task" }, 409);
        return json(route, task(++index));
      case key === `POST /api/sessions/${SESSION_ID}/end`:
        session = null;
        return route.fulfill({ status: 204 });
      case key === `POST /api/sessions/${SESSION_ID}/heartbeat`:
        return route.fulfill({ status: 204 });
      default:
        unmocked.push(key);
        return json(route, { detail: `unmocked ${key}` }, 500);
    }
  });
  // The terminal is another service behind the proxy; a stub page stands in for it.
  await page.route(`**/s/${SESSION_ID}/term/`, (route) =>
    route.fulfill({ contentType: "text/html", body: "<body><pre id=term>student@lab:~$ </pre></body>" }),
  );
  return { log, unmocked };
}
