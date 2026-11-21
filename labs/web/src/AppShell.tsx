import type { ReactNode } from "react";

import { useI18n } from "./i18n";

/** The frame of every page: header (product name, language switch, logout when signed in) and a centred column. */
export default function AppShell({
  userName,
  onLogout,
  wide,
  children,
}: {
  userName?: string;
  onLogout?: () => void;
  /** Full-width content column (the lab screen). */
  wide?: boolean;
  children: ReactNode;
}) {
  const { lang, toggle, t } = useI18n();
  return (
    <>
      <header className="app-header">
        <div className="app-header__inner">
          <span className="app-header__title">{t("app.title")}</span>
          {userName !== undefined && <bdi className="app-header__user">{userName}</bdi>}
          <button className="secondary" onClick={toggle} aria-label={t("lang.switch.label")}>
            <span lang={lang === "en" ? "he" : "en"}>{t("lang.switch")}</span>
          </button>
          {onLogout && (
            <button className="secondary" onClick={onLogout}>
              {t("nav.logout")}
            </button>
          )}
        </div>
      </header>
      <main className={wide ? "app-content app-content--wide" : "app-content"}>{children}</main>
    </>
  );
}
