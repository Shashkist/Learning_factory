import { useEffect, useRef, useState, type FormEvent } from "react";

import { useI18n } from "./i18n";
import { localStamp } from "./localStamp";

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
  const [failed, setFailed] = useState<string[]>([]); // ids of the writes whose last try failed
  const [note, setNote] = useState("");
  const [until, setUntil] = useState("");
  const [pending, setPending] = useState<string[]>([]); // ids of the writes in flight
  const focusFlag = useRef<string | null>(null); // flag item to focus after its dismiss succeeded
  const focusButton = useRef<string | null>(null); // flag whose Dismiss button to focus after a failure
  const started = useRef(0); // sequence number of the last write sent
  const applied = useRef(0); // sequence number of the newest answer shown
  const inflight = useRef(0); // writes sent and not yet answered
  const stale = useRef(false); // an answer was dropped as out of order: the page may miss its change
  const wantFocus = useRef<string | null>(null); // dismissed flag whose own answer was dropped
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

  /** Show a page body unless a newer one is already shown. Returns whether it was shown. */
  const show = (seq: number, next: Student): boolean => {
    if (seq < applied.current) return false;
    applied.current = seq;
    const open = new Set<string>();
    for (const lab of next.labs) for (const f of lab.flags) if (!f.dismissed_at) open.add(`flag-${f.id}`);
    setFailed((f) => f.filter((x) => !x.startsWith("flag-") || open.has(x)));
    if (focusButton.current && !open.has(`flag-${focusButton.current}`)) focusButton.current = null;
    setStudent({ ...next }); // a fresh object: always re-render with the answer
    return true;
  };

  /** Once no write is in flight, re-read the student if an answer was dropped on the way. */
  const settleStale = async () => {
    if (!stale.current || inflight.current > 0) return;
    stale.current = false;
    const seq = ++started.current;
    const next = await call(url, "GET");
    if (!next || !show(seq, next)) return;
    const f = wantFocus.current;
    wantFocus.current = null;
    if (f && next.labs.some((l) => l.flags.some((x) => x.id === f && x.dismissed_at))) focusFlag.current = f;
  };

  /**
   * Send a change. The page shows the answer of the newest write only. An older answer that lands late
   * is dropped, but it may be the only one carrying its change, so the page re-reads the student once
   * the writes are done. Returns the body when it was shown, null on failure or when it was dropped.
   * `onOk` runs when this write's own answer is shown.
   */
  const write = async (
    id: string,
    method: string,
    path: string,
    body?: object,
    onOk?: () => void,
    onFail?: () => void,
  ): Promise<Student | null> => {
    if (pending.includes(id)) return null;
    const seq = ++started.current;
    inflight.current++;
    setPending((p) => [...p, id]);
    const next = await call(path, method, body);
    inflight.current--;
    setPending((p) => p.filter((x) => x !== id));
    if (!next) {
      onFail?.();
      setFailed((f) => (f.includes(id) ? f : [...f, id]));
      void settleStale();
      return null;
    }
    setFailed((f) => f.filter((x) => x !== id));
    const shown = show(seq, next);
    if (shown) onOk?.();
    else {
      stale.current = true;
      if (id.startsWith("flag-")) wantFocus.current = id.slice(5);
    }
    void settleStale();
    return shown ? next : null;
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
    <section data-testid="student-page">
      {back}
      <h1>
        <bdi>{student.name}</bdi>
      </h1>
      <p>
        <bdi>{student.cohort}</bdi> · {t("admin.student.cohort_access")}: <bdi>{student.cohort_access_until}</bdi>
      </p>
      {failed.length > 0 && <p role="alert">{t("admin.error.save")}</p>}
      {student.disabled && <p>{t("admin.student.disabled_notice")}</p>}
      <button disabled={pending.includes("disabled")} onClick={() => void write("disabled", "PATCH", url, { disabled: !student.disabled })}>
        {t(student.disabled ? "admin.student.enable" : "admin.student.disable")}
      </button>
      <form onSubmit={saveNote}>
        <label>
          {t("admin.student.note")}
          <textarea value={note} maxLength={1000} onChange={(e) => setNote(e.target.value)} />
        </label>
        <button type="submit" disabled={pending.includes("note")}>{t("admin.student.save_note")}</button>
      </form>
      <form onSubmit={extend}>
        <label>
          {t("admin.student.access")}
          <input type="date" value={until} onChange={(e) => setUntil(e.target.value)} />
        </label>
        <button type="submit" disabled={pending.includes("access")}>{t("admin.student.extend")}</button>
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
                  <bdi>{f.reason}</bdi>{" "}
                  {f.dismissed_at ? (
                    <>
                      <span>{t("admin.student.dismissed")}</span>{" "}
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
                      disabled={pending.includes(`flag-${f.id}`)}
                      onClick={() => {
                        void write(
                          `flag-${f.id}`,
                          "POST",
                          `${url}/flags/${encodeURIComponent(f.id)}/dismiss`,
                          undefined,
                          () => {
                            focusFlag.current = f.id;
                          },
                          () => {
                            focusButton.current = f.id;
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
