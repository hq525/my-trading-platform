// Quantity parsing/formatting and the stock/crypto symbol-shape rule shared
// by the order ticket, positions grouping, and orders/journal tagging.
// Not money — no $ prefix, no fixed 2dp; crypto allows up to 8 decimal
// places, stocks require whole numbers.

export function isCryptoSymbol(symbol: string): boolean {
  return symbol.includes("-");
}

export function isValidQty(s: string, allowFractional: boolean): boolean {
  const trimmed = s.trim();
  const pattern = allowFractional ? /^\d+(\.\d{1,8})?$/ : /^\d+$/;
  return pattern.test(trimmed) && Number(trimmed) > 0;
}

export function formatQty(s: string): string {
  const trimmed = s.trim();
  if (!trimmed.includes(".")) return trimmed;
  return trimmed.replace(/0+$/, "").replace(/\.$/, "");
}
