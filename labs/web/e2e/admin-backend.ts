import type { Page } from "@playwright/test";

import { json } from "./fake-backend";

type Student = {
  key: string;
  name: string;
  cohort: number;
  note: string | null;
  disabled: boolean;
  access_until: string | null;
  flags: { id: string; reason: string; dismissed_at: string | null }[];
};

const LAB = { id: "linux-basics", title: { en: "Linux basics", he: "יסודות לינוקס" } };
const COHORT_ACCESS = "2027-01-31";

// The /api/admin/* calls the teacher UI makes, answered from memory with the shapes of
// app/teacher.py (cohorts, import), the class table (#163) and the student page (#164).
// Same contract as fakeBackend: every call the fake does not know answers 500 and is listed.
export async function fakeAdminBackend(page: Page) {
  const log: string[] = [];
  const unmocked: string[] = [];
  const bodies: Record<string, unknown[]> = {};
  const cohorts = [
    { id: 1, name: "Alpha", access_until: COHORT_ACCESS },
    { id: 2, name: "Beta", access_until: COHORT_ACCESS },
  ];
  const students: Student[] = [
    {
      key: "k-dana",
      name: "Dana",
      cohort: 1,
      note: null,
      disabled: false,
      access_until: null,
      flags: [{ id: "f1", reason: "task t1 in 3 s", dismissed_at: null }],
    },
    { key: "k-noa", name: "Noa", cohort: 2, note: null, disabled: false, access_until: null, flags: [] },
  ];
  const cohortName = (id: number) => cohorts.find((c) => c.id === id)?.name ?? "";
  const live = (s: Student) => s.flags.some((f) => !f.dismissed_at);
  const detail = (s: Student) => ({
    key: s.key,
    name: s.name,
    cohort: cohortName(s.cohort),
    note: s.note,
    disabled: s.disabled,
    access_until: s.access_until,
    cohort_access_until: COHORT_ACCESS,
    labs: [
      {
        ...LAB,
        status: "in_progress",
        tasks_passed: 1,
        tasks_total: 2,
        attempts: 2,
        tasks: [{ id: "t1", passed: true, attempts: 2, seconds: 3, median_seconds: 40 }],
        flags: s.flags.map((f) => ({ ...f, scope: "task", task_id: "t1" })),
      },
    ],
  });

  await page.route("**/api/**", (route) => {
    const req = route.request();
    const url = new URL(req.url());
    const key = `${req.method()} ${url.pathname}`;
    log.push(key);
    const body = req.postDataJSON() as Record<string, unknown> | null;
    if (body) (bodies[key] ??= []).push(body);
    const stu = /^\/api\/admin\/students\/([^/]+)(\/flags\/([^/]+)\/dismiss)?$/.exec(url.pathname);
    const student = students.find((s) => s.key === stu?.[1]);
    switch (true) {
      case key === "GET /api/auth/me":
        return json(route, { kind: "admin", name: "Teacher" });
      case key === "GET /api/admin/cohorts":
        return json(route, cohorts.map((c) => ({ ...c, students: students.filter((s) => s.cohort === c.id).length })));
      case key === "POST /api/admin/cohorts": {
        const id = cohorts.length + 1;
        cohorts.push({ id, name: String(body?.name), access_until: String(body?.access_until) });
        return json(route, cohorts[id - 1], 201);
      }
      case /^POST \/api\/admin\/cohorts\/\d+\/import$/.test(key): {
        const cohort = Number(url.pathname.split("/")[4]);
        const rows = String(body?.text)
          .split("\n")
          .map((l, i) => ({ l: l.trim(), line: i + 1 }))
          .filter((x) => x.l)
          .map(({ l, line }) => {
            const [id, name] = l.split(",").map((p) => p.trim());
            if (!/^\d{9}$/.test(id)) return { line, status: "error", code: "invalid_id", message: "invalid id" };
            students.push({ key: `k-${id}`, name, cohort, note: null, disabled: false, access_until: null, flags: [] });
            return { line, status: "added" };
          });
        return json(route, { added: rows.filter((r) => r.status === "added").length, rows });
      }
      case key === "GET /api/admin/class": {
        const only = url.searchParams.get("cohort");
        const flagged = url.searchParams.get("flagged") === "1";
        const rows = students
          .filter((s) => (!only || s.cohort === Number(only)) && (!flagged || live(s)))
          .map((s) => ({
            key: s.key,
            name: s.name,
            cohort: cohortName(s.cohort),
            last_activity: null,
            cells: { [LAB.id]: { status: "done", flag: live(s) } },
          }));
        return json(route, { labs: [LAB], rows });
      }
      case !!student && !stu?.[2] && req.method() === "GET":
        return json(route, detail(student!));
      case !!student && !stu?.[2] && req.method() === "PATCH": {
        Object.assign(student!, body);
        return json(route, detail(student!));
      }
      case !!student && req.method() === "POST" && !!stu?.[3]: {
        const flag = student!.flags.find((f) => f.id === stu![3]);
        if (flag) flag.dismissed_at = "2026-11-19T10:00:00Z";
        return json(route, detail(student!));
      }
      default:
        unmocked.push(key);
        return json(route, { detail: `unmocked ${key}` }, 500);
    }
  });
  return { log, unmocked, bodies };
}
