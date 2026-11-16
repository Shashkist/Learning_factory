import { useEffect, useRef, useState } from "react";

import { useI18n, type Lang } from "./i18n";

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
      {groupByModule(labs).map(([n, group]) => (
          <section key={n} data-testid={`module-${n}`}>
            <h2>
              {t("labs.module")} {n}
            </h2>
            {group.map((l) => (
              <article key={l.id} data-testid={`lab-card-${l.id}`}>
                <h3>{text(l.title)}</h3>
                <p>{text(l.description)}</p>
                <p>
                  {l.tasks} {t("labs.tasks")}
                </p>
                <p>
                  {l.progress === "done"
                    ? `✓ ${t("labs.progress.done")}`
                    : l.progress === "not_started"
                      ? t("labs.progress.not_started")
                      : l.progress}
                </p>
                <button disabled={pending} onClick={() => start(l.id)}>{t(button(l))}</button>
              </article>
            ))}
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
