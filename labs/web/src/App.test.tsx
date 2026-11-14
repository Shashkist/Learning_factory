import { render, screen } from "@testing-library/react";

import App from "./App";

test("renders the page title", () => {
  render(<App />);
  expect(screen.getByRole("heading", { name: "Learning Factory Labs" })).toBeTruthy();
});
