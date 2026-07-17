// OCC option-contract symbols: classification, parsing, display labels.
// Mirrors backend app/assets.py — compact OCC (ROOT + YYMMDD + C/P +
// strike*1000 zero-padded to 8 digits). Classification order everywhere:
// option -> crypto -> stock. Strike stays a string — no float math.

const OCC_RE = /^([A-Z]{1,6})(\d{2})(\d{2})(\d{2})([CP])(\d{8})$/;

export interface OccContract {
  underlying: string;
  expiry: string; // YYYY-MM-DD
  right: "call" | "put";
  strike: string; // trailing zeros stripped: "625", "7.5"
}

export function isOptionSymbol(symbol: string): boolean {
  const m = OCC_RE.exec(symbol);
  if (!m) return false;
  const [, , yy, mm, dd] = m;
  const month = Number(mm);
  const day = Number(dd);
  // Round-trip through Date.UTC: invalid dates (month 13, Feb 30) roll over
  // and fail the equality check, mirroring the backend's strptime guard.
  const d = new Date(Date.UTC(2000 + Number(yy), month - 1, day));
  return d.getUTCMonth() === month - 1 && d.getUTCDate() === day;
}

export function parseOcc(symbol: string): OccContract {
  const m = OCC_RE.exec(symbol);
  if (!m || !isOptionSymbol(symbol)) {
    throw new Error(`not an OCC option symbol: ${symbol}`);
  }
  const [, underlying, yy, mm, dd, right, strikeRaw] = m;
  // 8 digits = strike * 1000: first 5 are dollars, last 3 thousandths.
  const whole = strikeRaw.slice(0, 5).replace(/^0+(?=\d)/, "");
  const frac = strikeRaw.slice(5).replace(/0+$/, "");
  return {
    underlying,
    expiry: `20${yy}-${mm}-${dd}`,
    right: right === "C" ? "call" : "put",
    strike: frac ? `${whole}.${frac}` : whole,
  };
}

export function formatStrike(strike: string): string {
  if (!strike.includes(".")) return strike;
  const [whole, frac] = strike.split(".");
  return `${whole}.${frac.padEnd(2, "0")}`;
}

export function formatOptionLabel(symbol: string): string {
  const c = parseOcc(symbol);
  const yy = c.expiry.slice(2, 4);
  const month = c.expiry.slice(5, 7);
  const day = c.expiry.slice(8, 10);
  return `${c.underlying} ${month}/${day}/${yy} $${formatStrike(c.strike)} ${
    c.right === "call" ? "C" : "P"}`;
}
