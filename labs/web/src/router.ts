import { useEffect, useState } from "react";

const clean = () => window.location.pathname.replace(/\/+$/, "") || "/";

export function usePath(): [string, (to: string) => void] {
  const [path, setPath] = useState(clean);
  useEffect(() => {
    const sync = () => setPath(clean());
    window.addEventListener("popstate", sync);
    return () => window.removeEventListener("popstate", sync);
  }, []);
  const go = (to: string) => {
    window.history.pushState({}, "", to);
    setPath(clean());
  };
  return [path, go];
}
