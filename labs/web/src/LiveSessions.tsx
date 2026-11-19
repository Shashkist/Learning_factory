import { useEffect, useState } from "react";

import { useI18n } from "./i18n";

type Sess = {
  id: string;
  key: string;
  name: string;
  cohort: string;
  lab_id: string;
  lab_title: { en: string; he: string } | null;
  started_at: string;
  last_heartbeat: string | null;
};

export default function LiveSessions({ open }: { open: (key: string) => void }) {
  const { t, lang } = useI18n();
  const [rows, setRows] = useState<Sess[] | null>(null);
  const [failed, setFailed] = useState(false);
  const [asking, setAsking] = useState<string[]>([]); // ids with an open confirm
  const [endFailed, setEndFailed] = useState(false);

  useEffect(() => {
    fetch("/api/admin/live", { credentials: "same-origin" })
      .then((r) => {
        if (!r.ok) throw new Error(String(r.status));
        return r.json();
      })
      .then((body) => setRows((body as { sessions: Sess[] }).sessions))
      .catch(() => setFailed(true));
  }, []);

  const ask = (id: string, on: boolean) =>
    setAsking((a) => (on ? [...a, id] : a.filter((x) => x !== id)));

  const end = async (id: string) => {
    setEndFailed(false);
    ask(id, false);
    try {
      const r = await fetch(`/api/admin/live/${encodeURIComponent(id)}/end`, {
        method: "POST",
        credentials: "same-origin",
      });
      if (!r.ok) throw new Error(String(r.status));
      setRows((all) => (all ?? []).filter((s) => s.id !== id));
    } catch {
      setEndFailed(true);
    }
  };

  return (
    <section>
      <h1>{t("admin.live.title")}</h1>
      {failed && <p role="alert">{t("admin.live.error")}</p>}
      {endFailed && <p role="alert">{t("admin.live.end_error")}</p>}
      {rows && rows.length === 0 && <p>{t("admin.live.empty")}</p>}
      {rows && rows.length > 0 && (
        <table data-testid="live-table">
          <thead>
            <tr>
              {["name", "cohort", "lab", "started", "heartbeat"].map((c) => (
                <th key={c}>{t(`admin.live.col.${c}`)}</th>
              ))}
              <th />
            </tr>
          </thead>
          <tbody>
            {rows.map((s, i) => (
              <tr key={s.id} data-testid={`live-row-${i}`}>
                <td>
                  <a
                    href={`/admin/students/${encodeURIComponent(s.key)}`}
                    onClick={(e) => {
                      e.preventDefault();
                      open(s.key);
                    }}
                  >
                    {s.name}
                  </a>
                </td>
                <td>{s.cohort}</td>
                <td>{s.lab_title ? s.lab_title[lang] : s.lab_id}</td>
                <td data-testid={`live-started-${i}`}>{s.started_at.slice(0, 10)}</td>
                <td data-testid={`live-heartbeat-${i}`}>
                  {s.last_heartbeat ? s.last_heartbeat.slice(0, 10) : t("admin.live.no_heartbeat")}
                </td>
                <td>
                  {asking.includes(s.id) ? (
                    <>
                      <button onClick={() => void end(s.id)}>{t("admin.live.confirm")}</button>
                      <button onClick={() => ask(s.id, false)}>{t("admin.live.cancel")}</button>
                    </>
                  ) : (
                    <button data-testid={`live-end-${i}`} onClick={() => ask(s.id, true)}>
                      {t("admin.live.end")}
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}
