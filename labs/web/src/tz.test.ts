// The exams name UTC dates while the pages show local time: vite.config.ts pins TZ for tests.
test("tests run in UTC", () => {
  expect(new Date("2026-01-01T00:00:00Z").getTimezoneOffset()).toBe(0);
});
