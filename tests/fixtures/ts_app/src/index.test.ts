import { describe, expect, it } from "vitest";

import { total } from "./index";

describe("total", () => {
  it("doubles the sum", () => {
    expect(total([1, 2])).toBe(6);
  });
});
