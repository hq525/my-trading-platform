// Backend datetimes are naive UTC without a "Z" suffix.

export function utcDate(iso: string): Date {
  return new Date(iso.endsWith("Z") ? iso : iso + "Z");
}

export function dataAge(asOfIso: string, now: Date = new Date()): string {
  const secs = Math.max(0, Math.floor((now.getTime() - utcDate(asOfIso).getTime()) / 1000));
  if (secs < 60) return `${secs}s ago`;
  if (secs < 3600) return `${Math.floor(secs / 60)}m ago`;
  return `${Math.floor(secs / 3600)}h ago`;
}

export function formatDateTime(iso: string): string {
  return utcDate(iso).toLocaleString();
}
