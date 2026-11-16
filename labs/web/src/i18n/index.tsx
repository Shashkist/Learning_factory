import { createContext, useContext, useState, type ReactNode } from "react";

import en from "./en.json";
import he from "./he.json";

export type Lang = "en" | "he";
const DICTS: Record<Lang, Record<string, string>> = { en, he };

function stored(): Lang {
  return window.localStorage.getItem("lf-lang") === "he" ? "he" : "en";
}

type Ctx = { lang: Lang; toggle: () => void; t: (key: string) => string };
const I18n = createContext<Ctx>({ lang: "en", toggle: () => {}, t: (k) => k });

export function I18nProvider({ children }: { children: ReactNode }) {
  const [lang, setLang] = useState<Lang>(stored);
  const toggle = () => {
    const next = lang === "en" ? "he" : "en";
    window.localStorage.setItem("lf-lang", next);
    setLang(next);
  };
  const t = (key: string) => DICTS[lang][key] ?? key;
  return (
    <I18n.Provider value={{ lang, toggle, t }}>
      <div data-testid="app-root" lang={lang} dir={lang === "he" ? "rtl" : "ltr"}>
        <button onClick={toggle}>{t("lang.switch")}</button>
        {children}
      </div>
    </I18n.Provider>
  );
}

export const useI18n = () => useContext(I18n);
