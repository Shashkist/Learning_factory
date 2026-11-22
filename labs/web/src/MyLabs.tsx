import { useEffect, useRef, useState } from "react";

import { useI18n, type Lang } from "./i18n";
import "./styles/student.css";

type Bi = { en: string; he: string };
export type Lab = {
  id: string;
  title: Bi;
  description: Bi;
  module: number;
  lesson: number;
  tasks: number;
  progress: string;
  session_id: string | null;
};
type Conflict = { labId: string; open: Bi };

export function groupByModule(labs: Lab[]): [number, Lab[]][] {
  const groups = new Map<number, Lab[]>();
  for (const l of labs) groups.set(l.module, [...(groups.get(l.module) ?? []), l]);
  return [...groups.entries()].sort((a, b) => a[0] - b[0]);
}

function conflictOf(body: unknown, labId: string): Conflict | null {
  const d = (body as { detail?: { code?: string; title?: Partial<Bi>; lab_id?: string } } | null)?.detail;
  if (!d || typeof d !== "object" || d.code !== "another_lab_open" || !d.title) return null;
  const fallback = d.lab_id ?? labId; // the title is {} when the open lab left the catalog
  return { labId, open: { en: d.title.en ?? fallback, he: d.title.he ?? fallback } };
}

export default function MyLabs({ open, signedOut }: { open: (labId: string) => void; signedOut: () => void }) {
  const { t, lang } = useI18n();
  const [labs, setLabs] = useState<Lab[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [conflict, setConflict] = useState<Conflict | null>(null);
  const [pending, setPending] = useState(false);
  const busy = useRef(false); // set at once, so a second click in the same tick is dropped

  useEffect(() => {
    fetch("/api/labs", { credentials: "same-origin" })
      .then(async (r) => {
        if (r.status === 401) return signedOut();
        if (!r.ok) throw new Error(String(r.status));
        setLabs((await r.json()) as Lab[]);
      })
      .catch(() => {
        setLabs([]);
        setError("labs.error.load");
      });
  }, []);

  const start = async (labId: string, force = false) => {
    if (busy.current) return;
    busy.current = true;
    setPending(true);
    setError(null);
    try {
      const r = await fetch("/api/sessions", {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(force ? { lab_id: labId, force: true } : { lab_id: labId }),
      });
      if (r.status === 401) {
        setConflict(null);
        signedOut();
        return;
      }
      if (r.ok) {
        setConflict(null);
        open(labId);
        return;
      }
      const c = r.status === 409 && !force ? conflictOf(await r.json().catch(() => null), labId) : null;
      if (c) setConflict(c);
      else {
        setConflict(null);
        setError("labs.error.start");
      }
    } catch {
      setConflict(null);
      setError("labs.error.start");
    } finally {
      busy.current = false;
      setPending(false);
    }
  };

  const button = (l: Lab) =>
    l.progress === "done"
      ? "labs.redo"
      : l.progress === "not_started" || l.session_id === null
        ? "labs.start"
        : "labs.continue";
  const text = (v: Bi, l: Lang = lang) => v[l];

  if (labs === null) return null; // nothing flashes while the catalog loads
  return (
    <>
      <h1>{t("labs.title")}</h1>
      {error && <p role="alert">{t(error)}</p>}
      {labs.length === 0 && !error && <p>{t("labs.empty")}</p>}
      {labs
        .filter((l) => l.session_id !== null)
        .map((l) => (
          <p key={l.id} className="alert lab-live-banner">
            {t("labs.live.banner")} <a href={`#lab-title-${l.id}`}><bdi>{text(l.title)}</bdi></a>
          </p>
        ))}
      {groupByModule(labs).map(([n, group]) => (
          <section key={n} data-testid={`module-${n}`}>
            <h2>
              {t("labs.module")} {n}
            </h2>
            <div className="lab-grid">
            {group.map((l) => {
              const done = l.progress === "done";
              const notStarted = l.progress === "not_started";
              const passed = done ? l.tasks : notStarted ? 0 : Number(l.progress.split("/")[0]) || 0;
              const status = done ? "done" : notStarted ? "not_started" : "in_progress";
              const titleId = `lab-title-${l.id}`;
              return (
                <article
                  key={l.id}
                  className={l.session_id !== null ? "card lab-card lab-card--live" : "card lab-card"}
                  data-testid={`lab-card-${l.id}`}
                >
                  <div className="lab-card__head">
                    <h3 id={titleId}>{text(l.title)}</h3>
                    <span className="lab-card__badges">
                      {l.session_id !== null && <span className="badge live">{t("labs.live.badge")}</span>}
                      <span className={done ? "badge success" : notStarted ? "badge" : "badge warning"}>
                        {done && <span aria-hidden="true">✓ </span>}
                        {t(`labs.status.${status}`)}
                      </span>
                    </span>
                  </div>
                  <p className="lab-card__meta">
                    {t("labs.module")} {l.module} · {t("labs.lesson")} {l.lesson} · {l.tasks} {t("labs.tasks")}
                  </p>
                  <p className="lab-card__desc">{text(l.description)}</p>
                  <div className="lab-card__progress">
                    <div
                      className="lab-card__bar"
                      role="progressbar"
                      aria-label={t("labs.progress.label")}
                      aria-valuemin={0}
                      aria-valuemax={l.tasks}
                      aria-valuenow={passed}
                      aria-valuetext={t("labs.progress.value").replace("{n}", String(passed)).replace("{m}", String(l.tasks))}
                    >
                      <div
                        className="lab-card__bar-fill"
                        style={{ inlineSize: `${l.tasks > 0 ? Math.round((passed / l.tasks) * 100) : 0}%` }}
                      />
                    </div>
                    <span className="lab-card__count">{`${passed}/${l.tasks}`}</span>
                  </div>
                  <button className="primary" aria-describedby={titleId} disabled={pending} onClick={() => start(l.id)}>
                    {t(button(l))}
                  </button>
                </article>
              );
            })}
            </div>
          </section>
        ))}
      {conflict && (
        <div role="dialog" aria-modal="true" aria-labelledby="conflict-title">
          <h2 id="conflict-title">{t("labs.open_conflict.title")}</h2>
          <p>
            {t("labs.open_conflict.body")} <bdi>{text(conflict.open)}</bdi>
          </p>
          <button disabled={pending} onClick={() => start(conflict.labId, true)}>{t("labs.open_conflict.force")}</button>
          <button disabled={pending} onClick={() => setConflict(null)}>
            {t("labs.open_conflict.cancel")}
          </button>
        </div>
      )}
    </>
  );
}
