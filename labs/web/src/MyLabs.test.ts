import { expect, test } from "vitest";

import { groupByModule, type Lab } from "./MyLabs";

const lab = (id: string, module: number) => ({ id, module }) as Lab;

test("groups by module in ascending order and keeps the API order inside a group", () => {
  const g = groupByModule([lab("c", 2), lab("b", 1), lab("a", 1)]);
  expect(g.map(([n, l]) => [n, l.map((x) => x.id)])).toEqual([
    [1, ["b", "a"]],
    [2, ["c"]],
  ]);
});
