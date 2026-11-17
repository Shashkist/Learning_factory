import { useEffect, useState } from "react";

import { useI18n } from "./i18n";

type Cell = { status: string; flag: boolean };
type Row = { name: string; cohort: string; last_activity: string | null; cells: Record<string, Cell> };
type Lab = { id: string; title: { en: string; he: string } };
type Table = { labs: Lab[]; rows: Row[] };
type Cohort = { id: number; name: string };

function mark(status: string): string {
  if (status === "done") return "✓";
  return status === "none" ? "–" : status;
}

/** Local date and time as `YYYY-MM-DD HH:MM`: the teacher's day, not the UTC day. */
function localStamp(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso.slice(0, 10);
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

export default function ClassTable() {
  const { t, lang } = useI18n();
  const [cohort, setCohort] = useState("");
  const [flagged, setFlagged] = useState(false);
  const [cohorts, setCohorts] = useState<Cohort[]>([]);
  const [table, setTable] = useState<Table | null>(null);
  const [failed, setFailed] = useState(false);

  const query = new URLSearchParams();
  if (cohort) query.set("cohort", cohort);
  if (flagged) query.set("flagged", "1");
  const qs = query.toString() ? `?${query.toString()}` : "";

  useEffect(() => {
    fetch("/api/admin/cohorts", { credentials: "same-origin" })
      .then((r) => (r.ok ? r.json() : []))
      .then((c) => setCohorts(c as Cohort[]))
      .catch(() => setCohorts([]));
  }, []);

  useEffect(() => {
    let stale = false;
    fetch(`/api/admin/class${qs}`, { credentials: "same-origin" })
      .then((r) => {
        if (!r.ok) throw new Error(String(r.status));
        return r.json();
      })
      .then((body) => {
        if (stale) return;
        setTable(body as Table);
        setFailed(false);
      })
      .catch(() => {
        if (stale) return;
        setTable(null);
        setFailed(true);
      });
    return () => {
      stale = true;
    };
  }, [qs]);

  return (
    <section>
      <h1>{t("admin.class.title")}</h1>
      <label>
        {t("admin.class.cohort")}
        <select value={cohort} onChange={(e) => setCohort(e.target.value)}>
          <option value="">{t("admin.class.all_cohorts")}</option>
          {cohorts.map((c) => (
            <option key={c.id} value={String(c.id)}>
              {c.name}
            </option>
          ))}
        </select>
      </label>
      <label>
        <input type="checkbox" checked={flagged} onChange={(e) => setFlagged(e.target.checked)} />
        {t("admin.class.flagged")}
      </label>
      <a href={`/api/admin/class.csv${qs}`}>{t("admin.class.csv")}</a>
      {failed && <p role="alert">{t("admin.class.error")}</p>}
      {table && table.rows.length === 0 && <p>{t("admin.class.empty")}</p>}
      {table && table.rows.length > 0 && (
        <table data-testid="class-table">
          <thead>
            <tr>
              <th>{t("admin.class.col.name")}</th>
              <th>{t("admin.class.col.cohort")}</th>
              <th>{t("admin.class.col.last_activity")}</th>
              {table.labs.map((l) => (
                <th key={l.id}>{l.title[lang]}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {table.rows.map((r, i) => (
              <tr key={i} data-testid={`class-row-${i}`}>
                <th scope="row">{r.name}</th>
                <td>{r.cohort}</td>
                <td data-testid={`class-last-${i}`}>
                  {r.last_activity ? localStamp(r.last_activity) : t("admin.class.never")}
                </td>
                {table.labs.map((l) => {
                  const c = r.cells[l.id];
                  return (
                    <td key={l.id} data-testid={`class-cell-${i}-${l.id}`}>
                      {mark(c.status)}
                      {c.flag && (
                        <span role="img" aria-label={t("admin.class.flag")} title={t("admin.class.flag")}>
                          ⚡
                        </span>
                      )}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}
