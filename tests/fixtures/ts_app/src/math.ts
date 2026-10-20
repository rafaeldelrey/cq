export function double(n: number): number {
  return n * 2;
}

// knip: exported, never imported.
export function triple(n: number): number {
  return n * 3;
}

// eslint: @typescript-eslint/no-explicit-any.
export function loose(value: any): string {
  return String(value);
}

// tsc: TS2322, a string where a number goes.
export const answer: number = "forty-two";
