import {
  useEffect,
  useRef,
  useState,
  type KeyboardEvent,
  type PointerEvent,
} from "react";
import Markdown, { type Components } from "react-markdown";

import { useI18n } from "./i18n";

type Bi = { en: string; he: string };
type Session = { id: string; lab_id?: string; expires_at: string | null };
type Task = {
  id: string;
  index: number;
  total: number;
  kind: "hands-on" | "quiz";
  title: Bi;
  state: string;
  text?: Bi;
  checklist?: { n: number; text: Bi }[];
  question?: Bi;
  options?: Bi[];
};
type Row = { passed: boolean; hint: string | null };
type Verdict = "correct" | "incorrect" | null;
type Phase = "loading" | "preparing" | "ready" | "ended" | "mismatch";

class Busy extends Error {}
class Failure extends Error {
  constructor(
    public status: number,
    public detail: string | null,
  ) {
    super(String(status));
  }
}
const BUSY = new Set(["session_busy", "setup_running", "session_starting"]);
const GONE = new Set(["no_session", "session_ended"]);
const ACTION_ERRORS: Record<string, string> = {
  setup_failed: "lab.error.setup_failed",
  setup_timeout: "lab.error.setup_timeout",
  check_timeout: "lab.error.check_timeout",
  db_busy: "lab.error.db_busy",
};
const RETRY_MS = 2000;
const RETRY_MAX = 30;

// skew: server clock minus browser clock, from the Date header of the last answer (one per screen)
const makeCall = (skew: { current: number }) =>
  async function call<T>(
    method: string,
    url: string,
    body?: unknown,
  ): Promise<T> {
    const r = await fetch(url, {
      method,
      credentials: "same-origin",
      ...(body === undefined
        ? {}
        : {
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body),
          }),
    });
    const date = Date.parse(r.headers?.get("date") ?? "");
    if (!Number.isNaN(date)) skew.current = date - Date.now();
    if (!r.ok) {
      const d = (
        (await r.json().catch(() => null)) as { detail?: unknown } | null
      )?.detail;
      const code =
        d && typeof d === "object" ? (d as { code?: unknown }).code : d;
      const detail = typeof code === "string" ? code : null;
      if (r.status === 409 && detail && BUSY.has(detail)) throw new Busy();
      throw new Failure(r.status, detail);
    }
    return (r.status === 204 ? undefined : await r.json()) as T;
  };

// The session is over (ended, expired or gone) as far as the server is concerned.
const isEnded = (e: unknown) =>
  e instanceof Failure &&
  (e.status === 410 || (e.status === 404 && GONE.has(e.detail ?? "")));

const clock = (ms: number) => {
  const s = Math.max(0, Math.floor(ms / 1000));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
};

// Task text is author-written Markdown. No raw-HTML plugin: HTML in it stays text. Code is always left-to-right.
const parts: Components = {
  h1: ({ children }) => <h3>{children}</h3>,
  h2: ({ children }) => <h3>{children}</h3>,
  pre: ({ children }) => <pre dir="ltr">{children}</pre>,
  code: ({ children }) => <code dir="ltr">{children}</code>,
  img: ({ src, alt }) => {
    let own = false;
    try {
      own =
        typeof src === "string" &&
        new URL(src, window.location.href).origin === window.location.origin;
    } catch {
      /* not a URL */
    }
    return own ? <img src={src} alt={alt} /> : null;
  },
  a: ({ href, children }) => (
    <a href={href} target="_blank" rel="noreferrer noopener">
      {children}
    </a>
  ),
};

function Text({ md, title }: { md: string; title: string }) {
  const m = /^\s*#\s+(.*)\r?\n/.exec(md);
  const body = m && m[1].trim() === title ? md.slice(m[0].length) : md; // the title is shown already
  return <Markdown components={parts}>{body}</Markdown>;
}

export default function LabScreen({
  lab,
  go,
}: {
  lab: string;
  go: (to: string) => void;
}) {
  const { t, lang } = useI18n();
  const dir = lang === "he" ? "rtl" : "ltr";
  const [phase, setPhase] = useState<Phase>("loading");
  const [session, setSession] = useState<Session | null>(null);
  const [task, setTask] = useState<Task | null>(null);
  const [fatal, setFatal] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<"task" | "solution">("task");
  const [rows, setRows] = useState<Record<number, Row> | null>(null);
  const [checkFailed, setCheckFailed] = useState(false);
  const [passed, setPassed] = useState(false);
  const [choice, setChoice] = useState<number | null>(null);
  const [verdict, setVerdict] = useState<Verdict>(null);
  const [left, setLeft] = useState<number | null>(null);
  const [split, setSplit] = useState(40);
  const [dragging, setDragging] = useState(false);
  const [done, setDone] = useState(false);
  const [pending, setPending] = useState(false);
  const inFlight = useRef(false);
  const alive = useRef(true);
  const frame = useRef<HTMLDivElement>(null);
  const skew = useRef(0);
  const call = useRef(makeCall(skew)).current;
  const ending = useRef(false);
  const [open, setOpen] = useState<{ id: string; title: Bi }[] | null>(null);
  // A task opened from the open-tasks list takes the focus (its button unmounts with the list).
  const heading = useRef<HTMLHeadingElement>(null);
  const focusTask = useRef(false);

  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);

  const show = (next: Task) => {
    setDone(false);
    setOpen(null);
    setTask(next);
    setRows(null);
    setCheckFailed(false);
    setChoice(null);
    setVerdict(null);
    setError(null);
    setPassed(next.state === "passed");
    setTab("task");
  };

  useEffect(() => {
    let live = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const wait = (ms: number) =>
      new Promise<void>((r) => (timer = setTimeout(r, ms)));
    (async () => {
      for (let attempt = 1; ; attempt++) {
        let known = false;
        try {
          const s = await call<Session>("GET", "/api/sessions/current");
          if (!live) return;
          known = true;
          if (s.lab_id !== lab) {
            setPhase("mismatch"); // the live session is for another lab: never show its tasks under this URL
            return;
          }
          setSession(s);
          setPhase("preparing");
          const base = `/api/sessions/${s.id}/tasks`;
          let first: Task;
          try {
            first = await call<Task>("GET", `${base}/current`);
          } catch (e) {
            if (!(e instanceof Failure) || e.status !== 404 || isEnded(e))
              throw e;
            const list = await call<{ tasks: { id: string }[] }>("GET", base);
            if (!list.tasks.length) throw e;
            first = await call<Task>(
              "POST",
              `${base}/${list.tasks[0].id}/show`,
            );
          }
          if (!live) return;
          show(first);
          setPhase("ready");
          return;
        } catch (e) {
          if (!live) return;
          if (e instanceof Busy && attempt < RETRY_MAX) {
            setPhase("preparing");
            await wait(RETRY_MS);
            if (!live) return;
            continue;
          }
          if (known && isEnded(e)) setPhase("ended");
          else
            setFatal(
              e instanceof Failure && e.status === 404 && !known
                ? "lab.error.no_session"
                : "lab.error.load",
            );
          return;
        }
      }
    })();
    return () => {
      live = false;
      clearTimeout(timer);
    };
  }, [lab]);

  const sid = session?.id;
  const expires = session?.expires_at ? Date.parse(session.expires_at) : NaN;
  const running = phase === "ready";

  useEffect(() => {
    if (!sid || !running) return;
    const beat = setInterval(() => {
      fetch(`/api/sessions/${sid}/heartbeat`, {
        method: "POST",
        credentials: "same-origin",
      })
        .then((r) => {
          if (alive.current && (r.status === 404 || r.status === 410))
            setPhase("ended");
        })
        .catch(() => {});
    }, 15_000);
    return () => clearInterval(beat);
  }, [sid, running]);

  useEffect(() => {
    if (!sid || phase !== "ready" || Number.isNaN(expires)) return;
    const tick = () => {
      setLeft(expires - (Date.now() + skew.current)); // display only: the server ends the lab
    };
    tick();
    const clockId = setInterval(tick, 1000);
    return () => clearInterval(clockId);
  }, [sid, phase, expires]);

  useEffect(() => {
    if (open || !focusTask.current || !heading.current) return;
    focusTask.current = false;
    heading.current.focus();
  });

  const run = async (fn: () => Promise<void>, fallback = "lab.error.load") => {
    if (inFlight.current) return;
    inFlight.current = true;
    setPending(true);
    setError(null);
    try {
      await fn();
    } catch (e) {
      if (!alive.current) return;
      if (e instanceof Busy) setError("lab.busy");
      else if (isEnded(e)) setPhase("ended");
      else if (e instanceof Failure && e.detail && ACTION_ERRORS[e.detail])
        setError(ACTION_ERRORS[e.detail]);
      else setError(fallback);
    } finally {
      inFlight.current = false;
      if (alive.current) setPending(false);
    }
  };

  const toLabs = (
    <a
      href="/labs"
      onClick={(e) => {
        e.preventDefault();
        go("/labs");
      }}
    >
      {t("lab.to_labs")}
    </a>
  );

  if (fatal) return <p role="alert">{t(fatal)}</p>;
  if (phase === "loading") return null;
  if (phase === "preparing") return <p role="status">{t("lab.preparing")}</p>;
  if (phase === "mismatch")
    return (
      <p role="alert">
        {t("lab.error.other_lab")} {toLabs}
      </p>
    );
  if (phase === "ended")
    return (
      <div>
        {left !== null && <div role="timer">{clock(left)}</div>}
        <p role="status">{t("lab.ended")}</p>
        {toLabs}
      </div>
    );
  if (!session || !task) return null;

  const base = `/api/sessions/${session.id}/tasks`;
  const step = (action: string) =>
    run(async () => {
      try {
        // Next, Skip and Previous carry the task on screen, so a retry of an applied move is a no-op (#222).
        const from = action !== "restart" ? { from: task.id } : undefined;
        show(await call<Task>("POST", `${base}/current/${action}`, from));
      } catch (e) {
        if (!(e instanceof Failure)) throw e;
        if (e.detail === "task_changed") {
          show(await call<Task>("GET", `${base}/current`));
          return;
        }
        if (e.detail !== "no_next_task") throw e;
        // Skip on an unpassed last task completes nothing; on a passed one it is the same as Next.
        if (action !== "next" && !passed) {
          setError("lab.no_next");
          return;
        }
        // Complete only when every task is passed: ask the server.
        let list: { tasks: { id: string; title: Bi; state: string }[] };
        try {
          list = await call("GET", base);
        } catch (e2) {
          if (e2 instanceof Busy || isEnded(e2)) throw e2;
          setError("lab.error.tasks");
          return;
        }
        const rest = list.tasks.filter((x) => x.state !== "passed");
        if (rest.length) {
          setOpen(rest.map((x) => ({ id: x.id, title: x.title })));
          setDone(false);
        } else setDone(true);
      }
    });
  const check = () =>
    run(async () => {
      const r = await call<{
        passed: boolean;
        reason?: string | null;
        checklist: { n: number; passed: boolean; hint: string | null }[];
      }>("POST", `${base}/${task.id}/check`);
      if (r.reason === "timeout" || r.reason === "error") {
        setCheckFailed(true);
        return;
      }
      setCheckFailed(false);
      setRows(
        Object.fromEntries(
          r.checklist.map((c) => [c.n, { passed: c.passed, hint: c.hint }]),
        ),
      );
      setPassed(r.passed);
    });
  const answer = () =>
    run(async () => {
      const r = await call<{ correct: boolean }>(
        "POST",
        `${base}/${task.id}/answer`,
        { option: choice },
      );
      setVerdict(r.correct ? "correct" : "incorrect");
      setPassed(r.correct);
    });
  const end = async () => {
    if (ending.current) return;
    ending.current = true;
    try {
      await call("POST", `/api/sessions/${session.id}/end`);
      go("/labs");
    } catch (e) {
      if (!alive.current) return;
      if (isEnded(e)) setPhase("ended");
      else setError("lab.error.end_failed");
    } finally {
      ending.current = false;
    }
  };
  const arrow = (e: KeyboardEvent) => {
    const d = e.key === "ArrowRight" ? 1 : e.key === "ArrowLeft" ? -1 : 0;
    if (!d) return;
    e.preventDefault();
    const next = tab === "task" ? "solution" : "task"; // two tabs: either arrow wraps to the other
    setTab(next);
    document.getElementById(`lab-tab-${next}`)?.focus();
  };
  const resize = (e: KeyboardEvent) => {
    const d = e.key === "ArrowRight" ? 5 : e.key === "ArrowLeft" ? -5 : 0;
    if (d) setSplit((s) => Math.min(80, Math.max(20, s + d)));
  };
  const dragStart = (e: PointerEvent) => {
    e.preventDefault();
    e.currentTarget.setPointerCapture?.(e.pointerId); // the moves keep coming even over the iframe
    setDragging(true);
  };
  const dragMove = (e: PointerEvent) => {
    const box = frame.current?.getBoundingClientRect();
    if (!dragging || !box?.width) return;
    setSplit(
      Math.min(80, Math.max(20, ((e.clientX - box.left) / box.width) * 100)),
    );
  };
  const dragEnd = () => setDragging(false);

  const hands = task.kind === "hands-on";
  const verdictText = verdict
    ? t(`lab.${verdict}`)
    : checkFailed
      ? t("lab.check_failed")
      : "";
  return (
    <div ref={frame} dir="ltr" style={{ display: "flex", height: "80vh" }}>
      <section
        data-testid="task-panel"
        dir={dir}
        style={{ width: `${split}%`, overflow: "auto" }}
      >
        {left !== null && <div role="timer">{clock(left)}</div>}
        <div
          role="progressbar"
          aria-valuemin={1}
          aria-valuenow={task.index}
          aria-valuemax={task.total}
          aria-label={`${t("lab.question")} ${task.index} ${t("lab.of")} ${task.total}`}
        >
          <div
            style={{
              width: `${(task.index / task.total) * 100}%`,
              height: 4,
              background: "currentColor",
            }}
          />
        </div>
        <p>
          {t("lab.question")} {task.index} {t("lab.of")} {task.total}
        </p>
        <div role="tablist" onKeyDown={arrow}>
          {(["task", "solution"] as const).map((k) => (
            <button
              key={k}
              id={`lab-tab-${k}`}
              role="tab"
              aria-selected={tab === k}
              tabIndex={tab === k ? 0 : -1}
              aria-controls="lab-tabpanel"
              onClick={() => setTab(k)}
            >
              {t(`lab.${k}.tab`)}
            </button>
          ))}
        </div>
        {error && <p role="alert">{t(error)}</p>}
        <div
          id="lab-tabpanel"
          role="tabpanel"
          aria-labelledby={`lab-tab-${tab}`}
        >
          <p role="status" style={{ margin: 0 }}>
            {done
              ? t("lab.complete")
              : open
                ? open.length === 1
                  ? t("lab.open.one")
                  : `${open.length} ${t("lab.open.many")}`
                : ""}
          </p>
          {done && <p>{toLabs}</p>}
          {open ? (
              <ul>
                {open.map((o) => (
                  <li key={o.id}>
                    <button
                      type="button"
                      disabled={pending}
                      onClick={() =>
                        run(async () => {
                          const next = await call<Task>("POST", `${base}/${o.id}/show`);
                          focusTask.current = true;
                          show(next);
                        }, "lab.error.show")
                      }
                    >
                      {o.title[lang]}
                    </button>
                  </li>
                ))}
              </ul>
          ) : done ? (
            null
          ) : tab === "solution" ? (
            <p>{t("lab.solution.none")}</p>
          ) : (
            <>
              <h2 ref={heading} tabIndex={-1}>
                {task.title[lang]}
              </h2>
              {hands ? (
                <>
                  {task.text && (
                    <Text md={task.text[lang]} title={task.title[lang]} />
                  )}
                  <ul>
                    {(task.checklist ?? []).map((c) => {
                      const r = rows?.[c.n];
                      return (
                        <li key={c.n}>
                          {r && (r.passed ? "✓ " : "✗ ")}
                          {c.text[lang]}
                          {r && !r.passed && r.hint && <div>{r.hint}</div>}
                        </li>
                      );
                    })}
                  </ul>
                  <button onClick={check} disabled={pending}>
                    {t("lab.check")}
                  </button>
                </>
              ) : (
                <>
                  <p>{task.question?.[lang]}</p>
                  <div role="radiogroup" aria-label={task.question?.[lang]}>
                    {(task.options ?? []).map((o, i) => (
                      <label key={i} style={{ display: "block" }}>
                        <input
                          type="radio"
                          name="option"
                          checked={choice === i}
                          onChange={() => setChoice(i)}
                        />
                        {o[lang]}
                      </label>
                    ))}
                  </div>
                  <button
                    onClick={answer}
                    disabled={choice === null || pending}
                  >
                    {t("lab.answer")}
                  </button>
                </>
              )}
              <div aria-live="polite">
                {verdictText && <p>{verdictText}</p>}
              </div>
            </>
          )}
        </div>
        <div>
          <button
            onClick={() => step("previous")}
            disabled={task.index <= 1 || pending}
          >
            {t("lab.previous")}
          </button>
          {passed && (
            <button onClick={() => step("next")} disabled={pending}>
              {t("lab.next")}
            </button>
          )}
          <button onClick={() => step("skip")} disabled={pending}>
            {t("lab.skip")}
          </button>
          <button onClick={() => step("restart")} disabled={pending}>
            {t("lab.restart")}
          </button>
          <button onClick={end}>{t("lab.end")}</button>
        </div>
      </section>
      <div
        role="separator"
        aria-orientation="vertical"
        aria-label={t("lab.splitter")}
        aria-valuemin={20}
        aria-valuemax={80}
        aria-valuenow={Math.round(split)}
        tabIndex={0}
        onKeyDown={resize}
        onPointerDown={dragStart}
        onPointerMove={dragMove}
        onPointerUp={dragEnd}
        onPointerCancel={dragEnd}
        onLostPointerCapture={dragEnd}
        style={{
          width: 6,
          cursor: "col-resize",
          background: "#8888",
          touchAction: "none",
        }}
      />
      <iframe
        title={t("lab.terminal")}
        src={`/s/${session.id}/term/`}
        style={{
          flex: 1,
          border: 0,
          minWidth: 0,
          pointerEvents: dragging ? "none" : "auto",
        }}
      />
    </div>
  );
}
