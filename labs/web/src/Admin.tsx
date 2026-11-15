import { useEffect, useState } from "react";

type State = "loading" | "admin" | "google" | "none";

async function getJson(url: string): Promise<{ ok: boolean; body: Record<string, unknown> }> {
  try {
    const r = await fetch(url, { credentials: "same-origin" });
    return { ok: r.ok, body: r.ok ? await r.json() : {} };
  } catch {
    return { ok: false, body: {} };
  }
}

export default function Admin() {
  const [state, setState] = useState<State>("loading");

  useEffect(() => {
    void (async () => {
      const me = await getJson("/api/auth/me");
      if (me.ok && me.body.kind === "admin") return setState("admin");
      const methods = await getJson("/api/auth/admin/methods");
      setState(methods.body.google === true ? "google" : "none");
    })();
  }, []);

  const logout = async () => {
    await fetch("/api/auth/logout", { method: "POST", credentials: "same-origin" });
    window.location.reload();
  };

  if (state === "loading") return <p>Loading</p>;
  if (state === "admin")
    return (
      <div>
        <p>Signed in as admin</p>
        <button onClick={logout}>Log out</button>
      </div>
    );
  if (state === "google") return <a href="/auth/google/start">Sign in with Google</a>;
  return <p>Google sign-in is not configured</p>;
}
