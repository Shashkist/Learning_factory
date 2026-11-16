import { useCallback, useEffect, useRef, useState, type KeyboardEvent } from "react";

import { useI18n } from "./i18n";

type Bi = { en: string; he: string };
type Session = { id: string; expires_at: string };
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

class Busy extends Error {}
const BUSY = new Set(["session_busy", "setup_running"]);

async function call<T>(method: string, url: string, body?: unknown): Promise<T> {
  const r = await fetch(url, {
    method,
    credentials: "same-origin",
    ...(body === undefined
      ? {}
      : { headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }),
  });
  if (r.status === 409) {
    const d = ((await r.json().catch(() => null)) as { detail?: unknown } | null)?.detail;
    if (typeof d === "string" && BUSY.has(d)) throw new Busy();
  }
  if (!r.ok) throw Object.assign(new Error(String(r.status)), { status: r.status });
  return (r.status === 204 ? undefined : await r.json()) as T;
}

const clock = (ms: number) => {
  const s = Math.max(0, Math.floor(ms / 1000));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
};

// Task text is author-written: render it as plain text only (headings and paragraphs), never as HTML.
function Text({ md, title }: { md: string; title: string }) {
  const blocks = md.split(/\n\s*\n/).map((b) => b.trim()).filter(Boolean);
  return (
    <>
      {blocks.map((b, i) => {
        const h = /^#+\s+(.*)$/.exec(b);
        if (h && i === 0 && h[1].trim() === title) return null; // the title is shown already
        return h ? <h3 key={i}>{h[1]}</h3> : <p key={i}>{b}</p>;
      })}
    </>
  );
}

export default function LabScreen({ go }: { go: (to: string) => void }) {
  const { t, lang } = useI18n();
  const dir = lang === "he" ? "rtl" : "ltr";
  const [session, setSession] = useState<Session | null>(null);
  const [task, setTask] = useState<Task | null>(null);
  const [fatal, setFatal] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<"task" | "solution">("task");
  const [rows, setRows] = useState<Record<number, Row> | null>(null);
  const [passed, setPassed] = useState(false);
  const [choice, setChoice] = useState<number | null>(null);
  const [verdict, setVerdict] = useState<Verdict>(null);
  const [left, setLeft] = useState(0);
  const [split, setSplit] = useState(40);
  const frame = useRef<HTMLDivElement>(null);

  const show = (next: Task) => {
    setTask(next);
    setRows(null);
    setChoice(null);
    setVerdict(null);
    setPassed(next.state === "passed");
    setTab("task");
  };

  useEffect(() => {
    let live = true;
    let known = false;
    (async () => {
      try {
        const s = await call<Session>("GET", "/api/sessions/current");
        if (!live) return;
        known = true;
        setSession(s);
        const base = `/api/sessions/${s.id}/tasks`;
        let first: Task;
        try {
          first = await call<Task>("GET", `${base}/current`);
        } catch (e) {
          if ((e as { status?: number }).status !== 404) throw e;
          const list = await call<{ tasks: { id: string }[] }>("GET", base);
          if (!list.tasks.length) throw e;
          first = await call<Task>("POST", `${base}/${list.tasks[0].id}/show`);
        }
        if (live) show(first);
      } catch (e) {
        if (live) setFatal((e as { status?: number }).status === 404 && !known ? "lab.error.no_session" : "lab.error.load");
      }
    })();
    return () => {
      live = false;
    };
  }, []);

  const sid = session?.id;
  const expires = session ? Date.parse(session.expires_at) : 0;
  useEffect(() => {
    if (!sid) return;
    const beat = setInterval(() => {
      fetch(`/api/sessions/${sid}/heartbeat`, { method: "POST", credentials: "same-origin" }).catch(() => {});
    }, 15_000);
    const tick = () => setLeft(expires - Date.now());
    tick();
    const clockId = setInterval(tick, 1000);
    return () => {
      clearInterval(beat);
      clearInterval(clockId);
    };
  }, [sid, expires]);

  const run = useCallback(
    async (fn: () => Promise<void>) => {
      setError(null);
      try {
        await fn();
      } catch (e) {
        setError(e instanceof Busy ? "lab.busy" : "lab.error.load");
      }
    },
    [],
  );

  if (fatal) return <p role="alert">{t(fatal)}</p>;
  if (!session || !task) return null;

  const base = `/api/sessions/${session.id}/tasks`;
  const go_ = (action: string) => run(async () => show(await call<Task>("POST", `${base}/current/${action}`)));
  const check = () =>
    run(async () => {
      const r = await call<{ passed: boolean; checklist: { n: number; passed: boolean; hint: string | null }[] }>(
        "POST",
        `${base}/${task.id}/check`,
      );
      setRows(Object.fromEntries(r.checklist.map((c) => [c.n, { passed: c.passed, hint: c.hint }])));
      setPassed(r.passed);
    });
  const answer = () =>
    run(async () => {
      const r = await call<{ correct: boolean }>("POST", `${base}/${task.id}/answer`, { option: choice });
      setVerdict(r.correct ? "correct" : "incorrect");
      setPassed(r.correct);
    });
  const end = () =>
    run(async () => {
      await call("POST", `/api/sessions/${session.id}/end`);
      go("/labs");
    });
  const resize = (e: KeyboardEvent) => {
    const d = e.key === "ArrowRight" ? 5 : e.key === "ArrowLeft" ? -5 : 0;
    if (d) setSplit((s) => Math.min(80, Math.max(20, s + d)));
  };
  const drag = (down: React.PointerEvent) => {
    const box = frame.current?.getBoundingClientRect();
    if (!box?.width) return;
    const move = (e: PointerEvent) =>
      setSplit(Math.min(80, Math.max(20, ((e.clientX - box.left) / box.width) * 100)));
    const up = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
    };
    down.preventDefault();
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  };

  const hands = task.kind === "hands-on";
  return (
    <div ref={frame} dir="ltr" style={{ display: "flex", height: "80vh" }}>
      <section data-testid="task-panel" dir={dir} style={{ width: `${split}%`, overflow: "auto" }}>
        <div role="timer">{clock(left)}</div>
        <div
          role="progressbar"
          aria-valuemin={1}
          aria-valuenow={task.index}
          aria-valuemax={task.total}
          aria-label={`${t("lab.question")} ${task.index} ${t("lab.of")} ${task.total}`}
        >
          <div style={{ width: `${(task.index / task.total) * 100}%`, height: 4, background: "currentColor" }} />
        </div>
        <p>
          {t("lab.question")} {task.index} {t("lab.of")} {task.total}
        </p>
        <div role="tablist">
          {(["task", "solution"] as const).map((k) => (
            <button key={k} role="tab" aria-selected={tab === k} onClick={() => setTab(k)}>
              {t(`lab.${k}.tab`)}
            </button>
          ))}
        </div>
        {error && <p role="alert">{t(error)}</p>}
        {tab === "solution" ? (
          <p>{t("lab.solution.none")}</p>
        ) : (
          <>
            <h2>{task.title[lang]}</h2>
            {hands ? (
              <>
                {task.text && <Text md={task.text[lang]} title={task.title[lang]} />}
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
                <button onClick={check}>{t("lab.check")}</button>
              </>
            ) : (
              <>
                <p>{task.question?.[lang]}</p>
                {(task.options ?? []).map((o, i) => (
                  <label key={i} style={{ display: "block" }}>
                    <input type="radio" name="option" checked={choice === i} onChange={() => setChoice(i)} />
                    {o[lang]}
                  </label>
                ))}
                <button onClick={answer} disabled={choice === null}>
                  {t("lab.answer")}
                </button>
                {verdict && <p>{t(`lab.${verdict}`)}</p>}
              </>
            )}
          </>
        )}
        <div>
          <button onClick={() => go_("previous")} disabled={task.index <= 1}>
            {t("lab.previous")}
          </button>
          {passed && <button onClick={() => go_("next")}>{t("lab.next")}</button>}
          <button onClick={() => go_("skip")}>{t("lab.skip")}</button>
          <button onClick={() => go_("restart")}>{t("lab.restart")}</button>
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
        onPointerDown={drag}
        style={{ width: 6, cursor: "col-resize", background: "#8888" }}
      />
      <iframe
        title="terminal"
        src={`/s/${session.id}/term/`}
        style={{ flex: 1, border: 0, minWidth: 0 }}
      />
    </div>
  );
}
