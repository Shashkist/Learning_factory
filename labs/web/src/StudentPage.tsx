import { Fragment, useEffect, useRef, useState, type FormEvent } from "react";

import { useI18n } from "./i18n";
import { localStamp } from "./localStamp";
import "./styles/teacher.css";

type Flag = {
  id: string; scope: "task" | "lab"; task_id: string | null; reason: string; dismissed_at: string | null;
  reason_code?: string | null; seconds?: number | null; threshold?: number | null;
};

const REASON_CODES = ["fast_task", "fast_lab_min", "fast_lab_median"];
type Task = { id: string; passed: boolean; attempts: number; seconds: number | null; median_seconds: number | null };
type Lab = {
  id: string;
  title: { en: string; he: string };
  status: string;
  tasks_passed: number;
  tasks_total: number;
  attempts: number;
  tasks: Task[];
  flags: Flag[];
};
type Student = {
  key: string;
  name: string;
  cohort: string;
  note: string | null;
  disabled: boolean;
  access_until: string | null;
  cohort_access_until: string;
  labs: Lab[];
};

type Load = "loading" | "ok" | "not_found" | "error";

/** Whole seconds, or an en dash when there is no time. */
function secs(value: number | null): string {
  return value === null ? "–" : String(Math.round(value));
}

async function call(url: string, method: string, body?: unknown): Promise<Student | null> {
  try {
    const r = await fetch(url, {
      method,
      credentials: "same-origin",
      headers: body === undefined ? undefined : { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: AbortSignal.timeout(30_000), // a stuck write fails visibly and the queue moves on
    });
    return r.ok ? ((await r.json()) as Student) : null;
  } catch {
    return null;
  }
}

// One queue for the student page writes of this browser page load: leaving and reopening a student
// must not start a second queue that overlaps the first.
let queue: Promise<unknown> = Promise.resolve();

/** Test only: forget writes left over from an earlier test. */
export function resetStudentQueue() {
  queue = Promise.resolve();
}

/** Is focus still on this flag's Dismiss button, or on nothing? Only then may a late answer move it. */
function focusIsFree(flag: string): boolean {
  const a = document.activeElement;
  return !a || a === document.body || a.getAttribute("data-dismiss") === flag;
}

export default function StudentPage({ studentKey, go }: { studentKey: string; go: (to: string) => void }) {
  const { t, lang } = useI18n();
  const [student, setStudent] = useState<Student | null>(null);
  const [load, setLoad] = useState<Load>("loading");
  const [failed, setFailed] = useState<string[]>([]); // ids of the writes whose last try failed
  const [note, setNote] = useState("");
  const [until, setUntil] = useState("");
  const [pending, setPending] = useState<string[]>([]); // ids of the writes in flight
  const focusFlag = useRef<string | null>(null); // flag item to focus after its dismiss succeeded
  const focusButton = useRef<string | null>(null); // flag whose Dismiss button to focus after a failure
  const queued = useRef<string[]>([]); // ids of the writes queued or in flight
  const url = `/api/admin/students/${encodeURIComponent(studentKey)}`;

  useEffect(() => {
    let stale = false;
    queue
      .then(() => fetch(url, { credentials: "same-origin" }))
      .then(async (r) => {
        if (stale) return;
        if (r.status === 404) return setLoad("not_found");
        if (!r.ok) throw new Error(String(r.status));
        const body = (await r.json()) as Student;
        if (stale) return;
        setStudent(body);
        setNote(body.note ?? "");
        setUntil(body.access_until ?? "");
        setLoad("ok");
      })
      .catch(() => {
        if (!stale) setLoad("error");
      });
    return () => {
      stale = true;
    };
  }, [url]);

  /** Replace the page with a server answer. */
  const show = (next: Student) => {
    const open = new Set<string>();
    for (const lab of next.labs) for (const f of lab.flags) if (!f.dismissed_at) open.add(`flag-${f.id}`);
    setFailed((f) => f.filter((x) => !x.startsWith("flag-") || open.has(x)));
    if (focusButton.current && !open.has(`flag-${focusButton.current}`)) focusButton.current = null;
    setStudent({ ...next }); // a fresh object: always re-render with the answer
  };

  /**
   * Send a change. All writes go through one queue, one request at a time in click order, so each answer
   * is the newest server state and replaces the page. Returns the body, or null on failure.
   * `onOk` runs when this write's own answer is shown; a failed write does not stop the queue.
   */
  const write = async (
    id: string,
    method: string,
    path: string,
    body?: object,
    onOk?: () => void,
    onFail?: () => void,
  ): Promise<Student | null> => {
    if (queued.current.includes(id)) return null;
    queued.current.push(id);
    setPending((p) => [...p, id]);
    const run = queue.then(() => call(path, method, body));
    queue = run;
    const next = await run;
    queued.current = queued.current.filter((x) => x !== id);
    setPending((p) => p.filter((x) => x !== id));
    if (!next) {
      onFail?.();
      setFailed((f) => (f.includes(id) ? f : [...f, id]));
      return null;
    }
    setFailed((f) => f.filter((x) => x !== id));
    show(next);
    onOk?.();
    return next;
  };

  const saveNote = async (e: FormEvent) => {
    e.preventDefault();
    const sent = note;
    const next = await write("note", "PATCH", url, { note: sent });
    // a late answer must not overwrite text typed since Save was clicked
    if (next) setNote((cur) => (cur === sent ? (next.note ?? "") : cur));
  };

  const extend = async (e: FormEvent) => {
    e.preventDefault();
    const sent = until;
    const next = await write("access", "PATCH", url, { access_until: sent || null });
    if (next) setUntil((cur) => (cur === sent ? (next.access_until ?? "") : cur));
  };

  const flagReason = (f: Flag) => {
    const code = f.reason_code;
    if (!code || !REASON_CODES.includes(code) || f.seconds == null || f.threshold == null) return f.reason;
    if (code === "fast_task" && !f.task_id) return f.reason;
    const threshold = code === "fast_lab_min" ? f.threshold / 60 : f.threshold;
    const fill = (text: string) =>
      text.split("{seconds}").join(String(Math.round(f.seconds ?? 0))).split("{threshold}").join(String(Math.round(threshold)));
    // the task id is isolated left-to-right, or "02-two" shows as "two-02" inside a Hebrew sentence
    const parts = t(`admin.student.reason.${code}`).split("{task}");
    return parts.map((text, i) => (
      <Fragment key={i}>
        {i > 0 && <bdi dir="ltr">{f.task_id}</bdi>}
        {fill(text)}
      </Fragment>
    ));
  };
  const mark = (id: string) => (failed.includes(id) ? { "aria-invalid": true, "aria-describedby": "student-write-error" } : {});
  const part = (id: string) =>
    id === "note" ? t("admin.student.note") : id === "access" ? t("admin.student.access") : id === "disabled" ? t("admin.student.status") : t("admin.student.flags");
  const failedNames = [...new Set(failed.map(part))];

  const back = (
    <a
      href="/admin/class"
      onClick={(e) => {
        e.preventDefault();
        go("/admin/class");
      }}
    >
      {t("admin.student.back")}
    </a>
  );

  if (load === "loading")
    return (
      <section>
        {back}
        <p role="status">{t("admin.student.loading")}</p>
      </section>
    );
  if (load === "not_found")
    return (
      <section>
        {back}
        <p>{t("admin.student.not_found")}</p>
      </section>
    );
  if (load === "error" || !student)
    return (
      <section>
        {back}
        <p role="alert">{t("admin.error.load")}</p>
      </section>
    );

  return (
    <section data-testid="student-page" className="teacher-page">
      {back}
      <h1>
        <bdi>{student.name}</bdi>
      </h1>
      <p>
        <bdi>{student.cohort}</bdi> · {t("admin.student.cohort_access")}: <bdi>{student.cohort_access_until}</bdi>
      </p>
      {failed.length > 0 && (
        <p role="alert" id="student-write-error">
          {t("admin.error.save")} {failedNames.join(", ")}
        </p>
      )}
      {student.disabled && <p className="alert warning">{t("admin.student.disabled_notice")}</p>}
      <div className="summary">
        <div className="card">
          <div className="card__label">{t("admin.student.card.labs")}</div>
          <bdi dir="ltr" className="card__value">{`${student.labs.filter((l) => l.status === "done").length} / ${student.labs.length}`}</bdi>
        </div>
        <div className="card">
          <div className="card__label">{t("admin.student.card.time")}</div>
          <bdi dir="ltr" className="card__value">{`${Math.round(student.labs.flatMap((l) => l.tasks).reduce((s, x) => s + (x.seconds ?? 0), 0))} s`}</bdi>
        </div>
        <div className="card">
          <div className="card__label">{t("admin.student.card.last")}</div>
          <bdi dir="ltr" className="card__value">–</bdi>
        </div>
      </div>
      <button {...mark("disabled")} disabled={pending.includes("disabled")} onClick={() => void write("disabled", "PATCH", url, { disabled: !student.disabled })}>
        {t(student.disabled ? "admin.student.enable" : "admin.student.disable")}
      </button>
      <form onSubmit={saveNote}>
        <label>
          {t("admin.student.note")}
          <textarea {...mark("note")} value={note} maxLength={1000} onChange={(e) => setNote(e.target.value)} />
        </label>
        <button type="submit" disabled={pending.includes("note")} aria-busy={pending.includes("note") ? "true" : undefined}>{t("admin.student.save_note")}</button>
      </form>
      <form onSubmit={extend}>
        <label>
          {t("admin.student.access")}
          <input type="date" {...mark("access")} value={until} onChange={(e) => setUntil(e.target.value)} />
        </label>
        <button type="submit" disabled={pending.includes("access")} aria-busy={pending.includes("access") ? "true" : undefined}>{t("admin.student.extend")}</button>
      </form>
      {student.labs.map((lab) => (
        <article key={lab.id} data-testid={`student-lab-${lab.id}`}>
          <h2>{lab.title[lang]}</h2>
          <p>
            <bdi dir="ltr">{`${lab.tasks_passed}/${lab.tasks_total}`}</bdi>
            {lab.status === "done" && " ✓"} · {`${t("admin.student.attempts")}: ${lab.attempts}`}
          </p>
          <div className="table-scroll">
          <table>
            <caption>{t("admin.student.tasks")}</caption>
            <tbody>
              {lab.tasks.map((task) => (
                <tr key={task.id} data-testid={`student-task-${lab.id}-${task.id}`}>
                  <th scope="row">
                    <bdi dir="ltr">{task.id}</bdi> {task.passed ? "✓" : ""}
                  </th>
                  <td>{`${t("admin.student.attempts")}: ${task.attempts}`}</td>
                  <td>{`${t("admin.student.time")}: ${secs(task.seconds)}`}</td>
                  <td>{`${t("admin.student.median")}: ${secs(task.median_seconds)}`}</td>
                </tr>
              ))}
            </tbody>
          </table>
          </div>
          <h3>{t("admin.student.flags")}</h3>
          {lab.flags.length === 0 ? (
            <p>{t("admin.student.no_flags")}</p>
          ) : (
            <ul>
              {lab.flags.map((f) => (
                <li
                  key={f.id}
                  data-testid={`student-flag-${f.id}`}
                  tabIndex={-1}
                  ref={(el) => {
                    if (el && focusFlag.current === f.id) {
                      focusFlag.current = null;
                      el.focus();
                    }
                  }}
                >
                  <span role="img" aria-label={t("admin.class.flag")}>
                    ⚡
                  </span>{" "}
                  <bdi>{flagReason(f)}</bdi>{" "}
                  {f.dismissed_at ? (
                    <>
                      <span className="badge success">{t("admin.student.dismissed")}</span>{" "}
                      <bdi dir="ltr">{localStamp(f.dismissed_at)}</bdi>
                    </>
                  ) : (
                    <button
                      ref={(el) => {
                        if (el && focusButton.current === f.id) {
                          focusButton.current = null;
                          el.focus();
                        }
                      }}
                      data-dismiss={f.id}
                      {...mark(`flag-${f.id}`)}
                      disabled={pending.includes(`flag-${f.id}`)}
                      aria-busy={pending.includes(`flag-${f.id}`) ? "true" : undefined}
                      onClick={() => {
                        void write(
                          `flag-${f.id}`,
                          "POST",
                          `${url}/flags/${encodeURIComponent(f.id)}/dismiss`,
                          undefined,
                          () => {
                            if (focusIsFree(f.id)) focusFlag.current = f.id;
                          },
                          () => {
                            if (focusIsFree(f.id)) focusButton.current = f.id;
                          },
                        );
                      }}
                    >
                      {t("admin.student.dismiss")}
                    </button>
                  )}
                </li>
              ))}
            </ul>
          )}
        </article>
      ))}
    </section>
  );
}
