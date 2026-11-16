import { useEffect, useState, type FormEvent } from "react";

import { useI18n } from "./i18n";

type Cohort = { id: number; name: string; access_until: string; students: number };
type Row = { line: number; status: "added" | "error"; message?: string };
type Result = { added: number; rows: Row[] };

async function send(url: string, body: unknown): Promise<Response | null> {
  try {
    const r = await fetch(url, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    return r.ok ? r : null;
  } catch {
    return null;
  }
}

export default function Cohorts() {
  const { t } = useI18n();
  const [cohorts, setCohorts] = useState<Cohort[] | null>(null);
  const [loadFailed, setLoadFailed] = useState(false);
  const [saveFailed, setSaveFailed] = useState(false);
  const [name, setName] = useState("");
  const [password, setPassword] = useState("");
  const [until, setUntil] = useState("");
  const [target, setTarget] = useState("");
  const [text, setText] = useState("");
  const [result, setResult] = useState<Result | null>(null);
  const [busy, setBusy] = useState(false); // one request at a time: no double create or import

  const load = async () => {
    try {
      const r = await fetch("/api/admin/cohorts", { credentials: "same-origin" });
      if (!r.ok) throw new Error(String(r.status));
      setCohorts((await r.json()) as Cohort[]);
      setLoadFailed(false);
    } catch {
      setLoadFailed(true);
      setCohorts(null);
    }
  };
  useEffect(() => {
    void load();
  }, []);

  const create = async (e: FormEvent) => {
    e.preventDefault();
    if (busy) return;
    setBusy(true);
    setSaveFailed(false);
    const r = await send("/api/admin/cohorts", { name, password, access_until: until });
    setBusy(false);
    if (!r) return setSaveFailed(true);
    setName("");
    setPassword("");
    setUntil("");
    await load();
  };

  const runImport = async (e: FormEvent) => {
    e.preventDefault();
    if (busy) return;
    setBusy(true);
    setSaveFailed(false);
    const id = target || String(cohorts?.[0]?.id);
    const r = await send(`/api/admin/cohorts/${id}/import`, { text });
    setBusy(false);
    if (!r) {
      setResult(null);
      return setSaveFailed(true);
    }
    setResult((await r.json()) as Result);
    await load();
  };

  return (
    <section>
      <h1>{t("admin.cohorts.title")}</h1>
      {loadFailed && <p role="alert">{t("admin.error.load")}</p>}
      {saveFailed && <p role="alert">{t("admin.error.save")}</p>}
      {cohorts && cohorts.length === 0 && <p>{t("admin.cohorts.empty")}</p>}
      {cohorts && cohorts.length > 0 && (
        <ul>
          {cohorts.map((c) => (
            <li key={c.id} data-testid={`cohort-${c.id}`}>
              {c.name} {c.access_until} {`${c.students} ${t("admin.cohorts.students")}`}
            </li>
          ))}
        </ul>
      )}

      <form onSubmit={create}>
        <label>
          {t("admin.cohorts.name")}
          <input value={name} onChange={(e) => setName(e.target.value)} />
        </label>
        <label>
          {t("admin.cohorts.password")}
          <input type="password" autoComplete="new-password" value={password} onChange={(e) => setPassword(e.target.value)} />
        </label>
        <label>
          {t("admin.cohorts.access")}
          <input type="date" value={until} onChange={(e) => setUntil(e.target.value)} />
        </label>
        <button type="submit" disabled={busy}>
          {t("admin.cohorts.create")}
        </button>
      </form>

      {cohorts && cohorts.length > 0 && (
        <form onSubmit={runImport}>
          <label>
            {t("admin.import.cohort")}
            <select value={target || String(cohorts[0].id)} onChange={(e) => setTarget(e.target.value)}>
              {cohorts.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                </option>
              ))}
            </select>
          </label>
          <label>
            {t("admin.import.text")}
            <textarea value={text} onChange={(e) => setText(e.target.value)} />
          </label>
          <button type="submit" disabled={busy}>
            {t("admin.import.submit")}
          </button>
        </form>
      )}

      {result && (
        <div>
          <p data-testid="import-summary">{`${t("admin.import.summary")} ${result.added}`}</p>
          <ul>
            {result.rows.map((r) => (
              <li key={r.line} data-testid={`import-row-${r.line}`}>
                {`${t("admin.import.line")} ${r.line}`}: {r.status === "added" ? t("admin.import.row_added") : r.message}
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}
