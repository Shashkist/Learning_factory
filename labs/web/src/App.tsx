import { useEffect, useState } from "react";

import Admin from "./Admin";
import { I18nProvider, useI18n } from "./i18n";
import Login, { type User } from "./Login";
import { usePath } from "./router";

function labId(path: string): string | null {
  const m = /^\/labs\/([^/]+)$/.exec(path);
  if (!m) return null;
  try {
    return decodeURIComponent(m[1]);
  } catch {
    return null; // a malformed %-escape is a page that does not exist, not a crash
  }
}

function Student() {
  const { t } = useI18n();
  const [path, go] = usePath();
  const [user, setUser] = useState<User | null | undefined>(undefined);
  const [logoutFailed, setLogoutFailed] = useState(false);

  useEffect(() => {
    fetch("/api/auth/me", { credentials: "same-origin" })
      .then(async (r) => {
        const u = r.ok ? ((await r.json()) as User) : null;
        if (u?.kind === "admin") window.location.assign("/admin"); // admins have their own page
        else setUser(u);
      })
      .catch(() => setUser(null));
  }, []);

  const logout = async () => {
    // A failed logout keeps the session cookie: never pretend the student is signed out.
    const r = await fetch("/api/auth/logout", { method: "POST", credentials: "same-origin" }).catch(() => null);
    if (!r?.ok) {
      setLogoutFailed(true);
      return;
    }
    setLogoutFailed(false);
    setUser(null);
    go("/login");
  };

  if (user === undefined) return null;
  if (!user)
    return (
      <Login
        onUser={(u) => {
          setUser(u);
          go("/labs");
        }}
      />
    );

  const lab = labId(path);
  return (
    <main>
      <span>{user.name}</span>
      <button onClick={logout}>{t("nav.logout")}</button>
      {logoutFailed && <p role="alert">{t("nav.logout_failed")}</p>}
      {["/", "/labs", "/login"].includes(path) ? (
        <>
          <h1>{t("labs.title")}</h1>
          <p>{t("labs.empty")}</p>
        </>
      ) : lab ? (
        <p>
          {t("lab.placeholder")} <span dir="ltr">{lab}</span>
        </p>
      ) : (
        <p>{t("notFound.title")}</p>
      )}
    </main>
  );
}

export default function App() {
  if (window.location.pathname.replace(/\/+$/, "") === "/admin") return <Admin />;
  return (
    <I18nProvider>
      <Student />
    </I18nProvider>
  );
}
