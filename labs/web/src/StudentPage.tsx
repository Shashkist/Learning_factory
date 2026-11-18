import { useEffect, useState, type FormEvent } from "react";

import { useI18n } from "./i18n";

type Flag = { id: string; scope: "task" | "lab"; task_id: string | null; reason: string; dismissed_at: string | null };
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
    });
    return r.ok ? ((await r.json()) as Student) : null;
  } catch {
    return null;
  }
}

export default function StudentPage({ studentKey, go }: { studentKey: string; go: (to: string) => void }) {
  const { t, lang } = useI18n();
  const [student, setStudent] = useState<Student | null>(null);
  const [load, setLoad] = useState<Load>("loading");
  const [saveFailed, setSaveFailed] = useState(false);
  const [note, setNote] = useState("");
  const [until, setUntil] = useState("");
  const url = `/api/admin/students/${encodeURIComponent(studentKey)}`;

  useEffect(() => {
    let stale = false;
    fetch(url, { credentials: "same-origin" })
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

  /** Send a change; the page shows what the server answers, the typed values stay on failure. */
  const write = async (method: string, path: string, body?: object): Promise<Student | null> => {
    const next = await call(path, method, body);
    setSaveFailed(next === null);
    if (next) setStudent({ ...next }); // a fresh object: always re-render with the answer
    return next;
  };

  const saveNote = async (e: FormEvent) => {
    e.preventDefault();
    const next = await write("PATCH", url, { note });
    if (next) setNote(next.note ?? "");
  };

  const extend = async (e: FormEvent) => {
    e.preventDefault();
    const next = await write("PATCH", url, { access_until: until || null });
    if (next) setUntil(next.access_until ?? "");
  };

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

  if (load === "loading") return null;
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
    <section data-testid="student-page">
      {back}
      <h1>
        <bdi>{student.name}</bdi>
      </h1>
      <p>
        <bdi>{student.cohort}</bdi> · {t("admin.student.cohort_access")}: <bdi>{student.cohort_access_until}</bdi>
      </p>
      {saveFailed && <p role="alert">{t("admin.error.save")}</p>}
      {student.disabled && <p>{t("admin.student.disabled_notice")}</p>}
      <button onClick={() => void write("PATCH", url, { disabled: !student.disabled })}>
        {t(student.disabled ? "admin.student.enable" : "admin.student.disable")}
      </button>
      <form onSubmit={saveNote}>
        <label>
          {t("admin.student.note")}
          <textarea value={note} maxLength={1000} onChange={(e) => setNote(e.target.value)} />
        </label>
        <button type="submit">{t("admin.student.save_note")}</button>
      </form>
      <form onSubmit={extend}>
        <label>
          {t("admin.student.access")}
          <input type="date" value={until} onChange={(e) => setUntil(e.target.value)} />
        </label>
        <button type="submit">{t("admin.student.extend")}</button>
      </form>
      {student.labs.map((lab) => (
        <article key={lab.id} data-testid={`student-lab-${lab.id}`}>
          <h2>{lab.title[lang]}</h2>
          <p>
            <bdi dir="ltr">{`${lab.tasks_passed}/${lab.tasks_total}`}</bdi>
            {lab.status === "done" && " ✓"} · {`${t("admin.student.attempts")}: ${lab.attempts}`}
          </p>
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
          <h3>{t("admin.student.flags")}</h3>
          {lab.flags.length === 0 ? (
            <p>{t("admin.student.no_flags")}</p>
          ) : (
            <ul>
              {lab.flags.map((f) => (
                <li key={f.id} data-testid={`student-flag-${f.id}`}>
                  <span role="img" aria-label={t("admin.class.flag")}>
                    ⚡
                  </span>{" "}
                  <bdi>{f.reason}</bdi>{" "}
                  {f.dismissed_at ? (
                    <span>{t("admin.student.dismissed")}</span>
                  ) : (
                    <button
                      onClick={() =>
                        void write("POST", `${url}/flags/${encodeURIComponent(f.id)}/dismiss`)
                      }
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
