import { double } from "./math";

export function total(prices: number[]): number {
  return double(prices.reduce((sum, p) => sum + p, 0));
}
