import { useEffect, useState } from "react";

import Admin from "./Admin";
import { I18nProvider, useI18n } from "./i18n";
import Login, { type User } from "./Login";
import { usePath } from "./router";

function Student() {
  const { t } = useI18n();
  const [path, go] = usePath();
  const [user, setUser] = useState<User | null | undefined>(undefined);

  useEffect(() => {
    fetch("/api/auth/me", { credentials: "same-origin" })
      .then(async (r) => setUser(r.ok ? ((await r.json()) as User) : null))
      .catch(() => setUser(null));
  }, []);

  const logout = async () => {
    await fetch("/api/auth/logout", { method: "POST", credentials: "same-origin" }).catch(() => {});
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

  const lab = /^\/labs\/([^/]+)$/.exec(path);
  return (
    <main>
      <span>{user.name}</span>
      <button onClick={logout}>{t("nav.logout")}</button>
      {["/", "/labs", "/login"].includes(path) ? (
        <>
          <h1>{t("labs.title")}</h1>
          <p>{t("labs.empty")}</p>
        </>
      ) : lab ? (
        <p>
          {t("lab.placeholder")} <span dir="ltr">{decodeURIComponent(lab[1])}</span>
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
