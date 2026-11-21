import { useEffect, useState } from "react";

import Admin from "./Admin";
import AppShell from "./AppShell";
import { I18nProvider, useI18n } from "./i18n";
import Login, { type User } from "./Login";
import LabScreen from "./LabScreen";
import MyLabs from "./MyLabs";
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
  const [unavailable, setUnavailable] = useState(false);

  useEffect(() => {
    fetch("/api/auth/me", { credentials: "same-origin" })
      .then(async (r) => {
        if (r.status !== 401 && !r.ok) throw new Error(String(r.status)); // only 401 is signed out
        const u = r.ok ? ((await r.json()) as User) : null;
        if (u?.kind === "admin") window.location.assign("/admin"); // admins have their own page
        else setUser(u);
      })
      .catch(() => setUnavailable(true));
  }, []);

  useEffect(() => {
    if (user && path === "/login") go("/labs", true);
  });

  const signedOut = () => {
    setLogoutFailed(false);
    setUser(null);
    go("/login", true);
  };

  const logout = async () => {
    // A failed logout keeps the session cookie: never pretend the student is signed out.
    const r = await fetch("/api/auth/logout", { method: "POST", credentials: "same-origin" }).catch(() => null);
    if (!r?.ok) {
      setLogoutFailed(true);
      return;
    }
    signedOut();
  };

  if (unavailable)
    return (
      <AppShell>
        <p role="alert">{t("login.error.unavailable")}</p>
      </AppShell>
    );
  if (user === undefined) return <AppShell>{null}</AppShell>;
  if (!user)
    return (
      <AppShell>
        <Login
          onUser={(u) => {
            setUser(u);
            go("/labs", true);
          }}
        />
      </AppShell>
    );

  const lab = labId(path);
  const onLab = Boolean(lab) && !["/", "/labs", "/login"].includes(path);
  return (
    <AppShell userName={user.name} onLogout={logout} wide={onLab}>
      {logoutFailed && <p role="alert">{t("nav.logout_failed")}</p>}
      {["/", "/labs", "/login"].includes(path) ? (
        <>
          <MyLabs
            open={(id) => go(`/labs/${encodeURIComponent(id)}`)}
            signedOut={signedOut}
          />
        </>
      ) : lab ? (
        <LabScreen key={lab} lab={lab} go={go} />
      ) : (
        <p>{t("notFound.title")}</p>
      )}
    </AppShell>
  );
}

export default function App() {
  const p = window.location.pathname.replace(/\/+$/, "");
  const admin = p === "/admin" || p.startsWith("/admin/");
  return <I18nProvider>{admin ? <Admin /> : <Student />}</I18nProvider>;
}
