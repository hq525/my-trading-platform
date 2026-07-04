import {
  addMoney, bigToMoney, formatUsd, gtMoney, isNeg, moneyToBig, mulMoney, subMoney,
} from "@/lib/money";

it("round-trips API money strings", () => {
  expect(bigToMoney(moneyToBig("99000"))).toBe("99000");
  expect(bigToMoney(moneyToBig("100.5"))).toBe("100.5");
  expect(bigToMoney(moneyToBig("105.0000"))).toBe("105");
  expect(bigToMoney(moneyToBig("-0.25"))).toBe("-0.25");
});

it("rejects invalid money strings", () => {
  expect(() => moneyToBig("abc")).toThrow();
  expect(() => moneyToBig("1e5")).toThrow();
  expect(() => moneyToBig("")).toThrow();
});

it("multiplies price by integer qty exactly", () => {
  expect(mulMoney("100", 10)).toBe("1000");
  expect(mulMoney("123.45", 3)).toBe("370.35");
  expect(mulMoney("0.1", 3)).toBe("0.3"); // no float 0.30000000000000004
});

it("adds and subtracts exactly", () => {
  expect(addMoney("99000", "1100")).toBe("100100");
  expect(subMoney("100100", "100000")).toBe("100");
  expect(subMoney("100", "100.5")).toBe("-0.5");
});

it("compares", () => {
  expect(gtMoney("1000.0001", "1000")).toBe(true);
  expect(gtMoney("1000", "1000")).toBe(false);
  expect(isNeg("-3")).toBe(true);
  expect(isNeg("3")).toBe(false);
});

it("formats USD with grouping and fixed decimals (truncating)", () => {
  expect(formatUsd("99000")).toBe("$99,000.00");
  expect(formatUsd("-1234.5")).toBe("-$1,234.50");
  expect(formatUsd("100.0499")).toBe("$100.04"); // truncates, never rounds
  expect(formatUsd("105", 4)).toBe("$105.0000");
  expect(formatUsd("0")).toBe("$0.00");
});
