// Exact money math on the API's decimal strings. BigInt at scale 4 —
// matches the backend's 4dp quantization. Never floats.

const SCALE = 4;
const FACTOR = 10n ** BigInt(SCALE);

export function moneyToBig(s: string): bigint {
  const m = /^(-?)(\d+)(?:\.(\d+))?$/.exec(s.trim());
  if (!m) throw new Error(`invalid money value: ${JSON.stringify(s)}`);
  const [, sign, whole, frac = ""] = m;
  const digits = whole + frac.padEnd(SCALE, "0").slice(0, SCALE);
  const value = BigInt(digits);
  return sign === "-" ? -value : value;
}

export function bigToMoney(v: bigint): string {
  const neg = v < 0n;
  const abs = neg ? -v : v;
  const whole = abs / FACTOR;
  const frac = (abs % FACTOR).toString().padStart(SCALE, "0").replace(/0+$/, "");
  return `${neg ? "-" : ""}${whole}${frac ? "." + frac : ""}`;
}

const QTY_SCALE = 8;
const QTY_FACTOR = 10n ** BigInt(QTY_SCALE);

function qtyToBig(s: string): bigint {
  const m = /^(-?)(\d+)(?:\.(\d+))?$/.exec(s.trim());
  if (!m) throw new Error(`invalid quantity: ${JSON.stringify(s)}`);
  const [, sign, whole, frac = ""] = m;
  if (frac.length > QTY_SCALE) {
    throw new Error(`quantity precision exceeds ${QTY_SCALE} decimal places: ${s}`);
  }
  const digits = whole + frac.padEnd(QTY_SCALE, "0");
  const value = BigInt(digits);
  return sign === "-" ? -value : value;
}

export function mulMoney(price: string, qty: string): string {
  const scaled = moneyToBig(price) * qtyToBig(qty);
  return bigToMoney(scaled / QTY_FACTOR);
}

export function addMoney(a: string, b: string): string {
  return bigToMoney(moneyToBig(a) + moneyToBig(b));
}

export function subMoney(a: string, b: string): string {
  return bigToMoney(moneyToBig(a) - moneyToBig(b));
}

export function gtMoney(a: string, b: string): boolean {
  return moneyToBig(a) > moneyToBig(b);
}

export function isNeg(s: string): boolean {
  return s.trim().startsWith("-");
}

export function formatUsd(s: string, dp = 2): string {
  const neg = isNeg(s);
  const [whole, frac = ""] = (neg ? s.trim().slice(1) : s.trim()).split(".");
  const grouped = whole.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  const fracOut = dp > 0 ? "." + frac.padEnd(dp, "0").slice(0, dp) : "";
  return `${neg ? "-" : ""}$${grouped}${fracOut}`;
}
