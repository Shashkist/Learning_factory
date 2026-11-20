import { useEffect, useState } from "react";

import AppShell from "./AppShell";
import ClassTable from "./ClassTable";
import Cohorts from "./Cohorts";
import LiveSessions from "./LiveSessions";
import { useI18n } from "./i18n";
import { usePath } from "./router";
import StudentPage from "./StudentPage";

type State = "loading" | "admin" | "google" | "none" | "error";

/** The opaque student key (D63) of `/admin/students/<key>`, or null for any other path. */
function studentKey(path: string): string | null {
  const m = /^\/admin\/students\/([^/]+)$/.exec(path);
  if (!m) return null;
  try {
    return decodeURIComponent(m[1]);
  } catch {
    return null; // a malformed %-escape is not a student
  }
}

async function getJson(url: string): Promise<{ ok: boolean; body: Record<string, unknown> }> {
  try {
    const r = await fetch(url, { credentials: "same-origin" });
    return { ok: r.ok, body: r.ok ? await r.json() : {} };
  } catch {
    return { ok: false, body: {} };
  }
}

export default function Admin() {
  const { t } = useI18n();
  const [state, setState] = useState<State>("loading");
  const [path, go] = usePath();

  useEffect(() => {
    void (async () => {
      const me = await getJson("/api/auth/me");
      if (me.ok && me.body.kind === "admin") return setState("admin");
      const methods = await getJson("/api/auth/admin/methods");
      if (!methods.ok) return setState("error");
      setState(methods.body.google === true ? "google" : "none");
    })();
  }, []);

  const logout = async () => {
    await fetch("/api/auth/logout", { method: "POST", credentials: "same-origin" });
    window.location.reload();
  };

  const student = studentKey(path);
  if (state === "loading")
    return (
      <AppShell>
        <p>Loading</p>
      </AppShell>
    );
  if (state === "admin")
    return (
      <AppShell onLogout={logout}>
        <p>Signed in as admin</p>
        <nav>
          {[
            ["/admin", "admin.nav.cohorts"],
            ["/admin/class", "admin.nav.class"],
            ["/admin/live", "admin.nav.live"],
          ].map(([href, key]) => (
            <a
              key={href}
              href={href}
              aria-current={path === href ? "page" : undefined}
              onClick={(e) => {
                e.preventDefault();
                go(href);
              }}
            >
              {t(key)}
            </a>
          ))}
        </nav>
        {student !== null ? (
          <StudentPage key={student} studentKey={student} go={go} />
        ) : path === "/admin/class" ? (
          <ClassTable open={(key) => go(`/admin/students/${encodeURIComponent(key)}`)} />
        ) : path === "/admin/live" ? (
          <LiveSessions open={(key) => go(`/admin/students/${encodeURIComponent(key)}`)} />
        ) : (
          <Cohorts open={(key) => go(`/admin/students/${encodeURIComponent(key)}`)} />
        )}
      </AppShell>
    );
  return (
    <AppShell>
      {state === "google" ? (
        <a href="/auth/google/start">Sign in with Google</a>
      ) : state === "error" ? (
        <p>Could not load sign-in options</p>
      ) : (
        <p>Google sign-in is not configured</p>
      )}
    </AppShell>
  );
}
