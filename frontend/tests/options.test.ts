import { formatOptionLabel, formatStrike, isOptionSymbol, parseOcc } from "@/lib/options";

it("classifies OCC symbols as options", () => {
  expect(isOptionSymbol("SPY260821C00625000")).toBe(true);
  expect(isOptionSymbol("F260918P00007500")).toBe(true);
});

it("rejects non-option symbols", () => {
  expect(isOptionSymbol("SPY")).toBe(false);
  expect(isOptionSymbol("BTC-USD")).toBe(false);
  expect(isOptionSymbol("spy260821c00625000")).toBe(false); // lowercase
  expect(isOptionSymbol("SPY260821X00625000")).toBe(false); // bad right
  expect(isOptionSymbol("SPY261341C00625000")).toBe(false); // month 13
  expect(isOptionSymbol("SPY260230C00625000")).toBe(false); // Feb 30
});

it("parses an OCC call", () => {
  expect(parseOcc("SPY260821C00625000")).toEqual({
    underlying: "SPY", expiry: "2026-08-21", right: "call", strike: "625",
  });
});

it("parses a fractional-strike put", () => {
  expect(parseOcc("F260918P00007500")).toEqual({
    underlying: "F", expiry: "2026-09-18", right: "put", strike: "7.5",
  });
});

it("throws on non-option symbols", () => {
  expect(() => parseOcc("SPY")).toThrow(/not an OCC option symbol/);
});

it("formats strikes with at least two decimals when fractional", () => {
  expect(formatStrike("625")).toBe("625");
  expect(formatStrike("7.5")).toBe("7.50");
  expect(formatStrike("7.125")).toBe("7.125");
});

it("formats human contract labels", () => {
  expect(formatOptionLabel("SPY260821C00625000")).toBe("SPY 08/21/26 $625 C");
  expect(formatOptionLabel("F260918P00007500")).toBe("F 09/18/26 $7.50 P");
});
