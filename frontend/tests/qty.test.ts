import { formatQty, isCryptoSymbol, isValidQty } from "@/lib/qty";

it("dash means crypto", () => {
  expect(isCryptoSymbol("BTC-USD")).toBe(true);
  expect(isCryptoSymbol("AAPL")).toBe(false);
});

it("stock qty must be a whole number", () => {
  expect(isValidQty("10", false)).toBe(true);
  expect(isValidQty("10.5", false)).toBe(false);
  expect(isValidQty("0", false)).toBe(false);
});

it("crypto qty allows up to 8 decimal places", () => {
  expect(isValidQty("0.005", true)).toBe(true);
  expect(isValidQty("0.12345678", true)).toBe(true);
  expect(isValidQty("0.123456789", true)).toBe(false); // 9 places
  expect(isValidQty("10", true)).toBe(true); // whole numbers still fine for crypto
  expect(isValidQty("0", true)).toBe(false);
});

it("rejects garbage input", () => {
  expect(isValidQty("", false)).toBe(false);
  expect(isValidQty("abc", true)).toBe(false);
  expect(isValidQty("1.2.3", true)).toBe(false);
});

it("formatQty trims trailing zeros", () => {
  expect(formatQty("0.010000")).toBe("0.01");
  expect(formatQty("10")).toBe("10");
  expect(formatQty("10.00")).toBe("10");
});
