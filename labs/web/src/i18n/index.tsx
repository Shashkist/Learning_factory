import { createContext, useContext, useEffect, useState, type ReactNode } from "react";

import en from "./en.json";
import he from "./he.json";

export type Lang = "en" | "he";
const DICTS: Record<Lang, Record<string, string>> = { en, he };

// Storage can be blocked (SecurityError); the language then lives for this page only.
function stored(): Lang {
  try {
    return window.localStorage.getItem("lf-lang") === "he" ? "he" : "en";
  } catch {
    return "en";
  }
}

type Ctx = { lang: Lang; toggle: () => void; t: (key: string) => string };
const I18n = createContext<Ctx>({ lang: "en", toggle: () => {}, t: (k) => k });

export function I18nProvider({ children }: { children: ReactNode }) {
  const [lang, setLang] = useState<Lang>(stored);
  const toggle = () => {
    const next = lang === "en" ? "he" : "en";
    try {
      window.localStorage.setItem("lf-lang", next);
    } catch {
      /* storage blocked: keep the choice in memory */
    }
    setLang(next);
  };
  const t = (key: string) => DICTS[lang][key] ?? key;
  useEffect(() => {
    document.documentElement.lang = lang;
    document.documentElement.dir = lang === "he" ? "rtl" : "ltr";
    document.title = DICTS[lang]["app.title"];
  }, [lang]);
  return (
    <I18n.Provider value={{ lang, toggle, t }}>
      <div data-testid="app-root" lang={lang} dir={lang === "he" ? "rtl" : "ltr"}>
        <button onClick={toggle} aria-label={t("lang.switch.label")}>
          <span lang={lang === "en" ? "he" : "en"}>{t("lang.switch")}</span>
        </button>
        {children}
      </div>
    </I18n.Provider>
  );
}

export const useI18n = () => useContext(I18n);
