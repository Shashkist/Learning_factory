import { useEffect, useState, type FormEvent } from "react";

import { useI18n } from "./i18n";

type Cohort = { id: number; name: string; access_until: string; students: number };
type Row = { line: number; status: "added" | "error"; code?: string };
type Student = { key?: string; name: string; note: string | null; disabled: boolean };
type Result = { added: number; rows: Row[] };

const MAX_IMPORT_LINES = 2000; // app/teacher.py MAX_LINES

async function send(url: string, body: unknown, method = "POST"): Promise<Response | null> {
  try {
    const r = await fetch(url, {
      method,
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    return r.ok ? r : null;
  } catch {
    return null;
  }
}

const KNOWN_CODES = ["invalid_id", "missing_name", "invalid_name", "duplicate_in_paste", "already_exists"];

function CohortRow({
  cohort,
  onSave,
  open,
  busy,
}: {
  cohort: Cohort;
  busy: boolean;
  onSave: (id: number, body: object) => Promise<boolean>;
  open?: (key: string) => void;
}) {
  const { t } = useI18n();
  const [name, setName] = useState(cohort.name);
  const [until, setUntil] = useState(cohort.access_until);
  const [password, setPassword] = useState("");
  const [students, setStudents] = useState<Student[] | null>(null);
  const [failed, setFailed] = useState(false);

  const save = async (e: FormEvent) => {
    e.preventDefault();
    const body: Record<string, string> = { name, access_until: until };
    if (password) body.password = password;
    if (await onSave(cohort.id, body)) setPassword("");
  };

  const show = async () => {
    try {
      const r = await fetch(`/api/admin/cohorts/${cohort.id}/students`, { credentials: "same-origin" });
      if (!r.ok) throw new Error(String(r.status));
      setStudents((await r.json()) as Student[]);
      setFailed(false);
    } catch {
      setStudents(null);
      setFailed(true);
    }
  };

  return (
    <li data-testid={`cohort-${cohort.id}`}>
      {cohort.name} {cohort.access_until} {`${cohort.students} ${t("admin.cohorts.students")}`}
      <form data-testid={`cohort-edit-${cohort.id}`} onSubmit={save}>
        <fieldset>
        <legend>{t("admin.cohorts.edit.legend")}</legend>
        <label>
          {t("admin.cohorts.edit.name")}
          <input value={name} onChange={(e) => setName(e.target.value)} />
        </label>
        <label>
          {t("admin.cohorts.edit.password")}
          <input type="password" autoComplete="new-password" value={password} onChange={(e) => setPassword(e.target.value)} />
        </label>
        <label>
          {t("admin.cohorts.edit.access")}
          <input type="date" value={until} onChange={(e) => setUntil(e.target.value)} />
        </label>
        <button type="submit" disabled={busy}>{t("admin.cohorts.save")}</button>
        </fieldset>
      </form>
      <button type="button" onClick={() => void show()}>
        {t("admin.students.show")}
      </button>
      {failed && <p role="alert">{t("admin.error.load")}</p>}
      {students && students.length === 0 && <p>{t("admin.students.empty")}</p>}
      {students && students.length > 0 && (
        <ul data-testid={`students-${cohort.id}`}>
          {students.map((s, i) => (
            <li key={i}>
              {s.key ? (
                <a
                  href={`/admin/students/${encodeURIComponent(s.key)}`}
                  onClick={(e) => {
                    if (!open || !s.key || e.ctrlKey || e.metaKey || e.shiftKey || e.altKey || e.button !== 0) return;
                    e.preventDefault();
                    open(s.key);
                  }}
                >
                  <bdi>{s.name}</bdi>
                </a>
              ) : (
                <bdi>{s.name}</bdi>
              )}
              {s.note ? ` ${s.note}` : ""}
              {s.disabled ? ` ${t("admin.students.disabled")}` : ""}
            </li>
          ))}
        </ul>
      )}
    </li>
  );
}

export default function Cohorts({ open }: { open?: (key: string) => void } = {}) {
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
  const [tooMany, setTooMany] = useState(false);

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

  const saveCohort = async (id: number, body: object) => {
    if (busy) return false;
    setBusy(true);
    setSaveFailed(false);
    const r = await send(`/api/admin/cohorts/${id}`, body, "PATCH");
    setBusy(false);
    if (!r) {
      setSaveFailed(true);
      return false;
    }
    await load();
    return true;
  };

  const runImport = async (e: FormEvent) => {
    e.preventDefault();
    if (busy) return;
    setSaveFailed(false);
    // The server refuses more than MAX_IMPORT_LINES non-blank lines (422) and saves none of them.
    const tooLong = text.split("\n").filter((l) => l.trim()).length > MAX_IMPORT_LINES;
    setTooMany(tooLong);
    if (tooLong) return setResult(null);
    setBusy(true);
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
    <section className="teacher-page">
      <h1>{t("admin.cohorts.title")}</h1>
      {loadFailed && <p role="alert">{t("admin.error.load")}</p>}
      {saveFailed && <p role="alert">{t("admin.error.save")}</p>}
      {cohorts && cohorts.length === 0 && <p>{t("admin.cohorts.empty")}</p>}
      {cohorts && cohorts.length > 0 && (
        <ul>
          {cohorts.map((c) => (
            <CohortRow key={`${c.id}-${c.name}-${c.access_until}-${c.students}`} cohort={c} onSave={saveCohort} open={open} busy={busy} />
          ))}
        </ul>
      )}

      <form onSubmit={create}>
        <fieldset>
        <legend>{t("admin.cohorts.create.legend")}</legend>
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
        </fieldset>
      </form>

      {cohorts && cohorts.length > 0 && (
        <form onSubmit={runImport}>
          <fieldset>
          <legend>{t("admin.import.legend")}</legend>
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
            <textarea value={text} aria-invalid={tooMany ? "true" : undefined} aria-describedby={tooMany ? "import-too-many" : undefined} onChange={(e) => setText(e.target.value)} />
          </label>
          {tooMany && (
            <p role="alert" id="import-too-many">
              {t("admin.import.too_many")}
            </p>
          )}
          <button type="submit" disabled={busy}>
            {t("admin.import.submit")}
          </button>
          </fieldset>
        </form>
      )}

      {result && (
        <div className={`alert ${result.rows.some((r) => r.status === "error") ? "warning" : "success"}`} role="status">
          <p data-testid="import-summary">{`${t("admin.import.summary")} ${result.added}`}</p>
          <ul>
            {result.rows.map((r) => (
              <li key={r.line} data-testid={`import-row-${r.line}`}>
                {`${t("admin.import.line")} ${r.line}`}: {r.status === "added" ? t("admin.import.row_added") : t(`admin.import.error.${KNOWN_CODES.includes(r.code ?? "") ? r.code : "unknown"}`)}
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}
