import { fireEvent, render, screen } from "@testing-library/react";

import { I18nProvider, useI18n } from ".";

function Probe() {
  const { t } = useI18n();
  return <p>{t("labs.title")}</p>;
}

test("unknown language in storage falls back to English; toggle persists", () => {
  window.localStorage.setItem("lf-lang", "fr");
  render(
    <I18nProvider>
      <Probe />
    </I18nProvider>,
  );
  expect(screen.getByText("My labs")).toBeTruthy();
  fireEvent.click(screen.getByRole("button"));
  expect(window.localStorage.getItem("lf-lang")).toBe("he");
  expect(screen.getByTestId("app-root").getAttribute("dir")).toBe("rtl");
});
