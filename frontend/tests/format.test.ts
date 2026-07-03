import { dataAge, formatDateTime, utcDate } from "@/lib/format";

it("parses naive-UTC ISO strings as UTC", () => {
  expect(utcDate("2026-07-02T15:00:00").toISOString()).toBe("2026-07-02T15:00:00.000Z");
  expect(utcDate("2026-07-02T15:00:00Z").toISOString()).toBe("2026-07-02T15:00:00.000Z");
});

it("reports data age in humane units", () => {
  const now = new Date("2026-07-02T15:01:00Z");
  expect(dataAge("2026-07-02T15:00:42", now)).toBe("18s ago");
  expect(dataAge("2026-07-02T14:55:00", now)).toBe("6m ago");
  expect(dataAge("2026-07-02T12:00:00", now)).toBe("3h ago");
  expect(dataAge("2026-07-02T15:02:00", now)).toBe("0s ago"); // clock skew clamps to 0
});

it("formats datetimes without crashing", () => {
  expect(formatDateTime("2026-07-02T15:00:00")).toBeTruthy();
});
