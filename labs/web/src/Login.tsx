import { useState, type FormEvent } from "react";

import { useI18n } from "./i18n";

export type User = { kind: string; name: string };

const CODES = ["invalid_credentials", "too_many_attempts", "account_disabled", "access_expired"];

async function errorKey(r: Response): Promise<string> {
  if (r.status === 503) return "login.error.unavailable";
  const body = (await r.json().catch(() => ({}))) as { detail?: unknown };
  const code = typeof body.detail === "string" ? body.detail : "";
  return CODES.includes(code) ? `login.error.${code}` : "login.error.generic";
}

export default function Login({ onUser }: { onUser: (u: User) => void }) {
  const { t } = useI18n();
  const [id, setId] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (busy) return; // a double click must not spend two rate-limited attempts
    setBusy(true);
    setError(null);
    try {
      const r = await fetch("/api/auth/login", {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ id, password }),
      });
      if (r.ok) onUser((await r.json()) as User);
      else setError(await errorKey(r));
    } catch {
      setError("login.error.generic");
    } finally {
      setBusy(false);
    }
  };

  return (
    <form onSubmit={submit}>
      <h1>{t("login.title")}</h1>
      <label>
        {t("login.id")}
        <input dir="ltr" autoComplete="username" inputMode="numeric" required value={id} onChange={(e) => setId(e.target.value)} />
      </label>
      <label>
        {t("login.password")}
        <input dir="ltr" type="password" autoComplete="current-password" required value={password} onChange={(e) => setPassword(e.target.value)} />
      </label>
      <button type="submit" disabled={busy}>{t("login.submit")}</button>
      {error && <p role="alert">{t(error)}</p>}
    </form>
  );
}
