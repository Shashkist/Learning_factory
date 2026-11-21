/**
 * Shared helper of the lab screen exams since #349 (D108 stays: End lab is ONE click, no confirmation).
 *
 * The lab screen has a TOP BAR: a header region of the lab screen that holds the lab title, the countdown
 * (role="timer"), the session state and the End lab button. The exams find it by behaviour, not by tag or
 * class: it is the smallest element that contains both the countdown and the End lab button. It must be a
 * strip of its own: it never contains the task panel (data-testid="task-panel") or the terminal iframe, it
 * comes before them in the document, and End lab is NOT inside the task panel (one End lab button on the
 * screen, so `getByRole("button", { name: "End lab" })` stays unambiguous).
 */
import { screen, within } from "@testing-library/react";

export function topBar(endName: string): HTMLElement {
  const timer = screen.getByRole("timer");
  let el: HTMLElement | null = timer.parentElement;
  while (el && el !== document.body && !within(el).queryByRole("button", { name: endName }))
    el = el.parentElement;
  if (!el || el === document.body) throw new Error(`no element holds both the countdown and "${endName}"`);
  const panel = screen.getByTestId("task-panel");
  if (el.contains(panel)) throw new Error("the element holding the countdown and End lab is the whole lab screen, not a top bar (it contains the task panel)");
  if (el.querySelector("iframe")) throw new Error("the top bar must not contain the terminal iframe");
  if (!(el.compareDocumentPosition(panel) & Node.DOCUMENT_POSITION_FOLLOWING))
    throw new Error("the top bar must come before the task panel");
  return el;
}

/** The End lab button, looked up in the top bar (never in the task panel). */
export const endButton = (endName: string): HTMLElement =>
  within(topBar(endName)).getByRole("button", { name: endName });
