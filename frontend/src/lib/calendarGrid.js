/** Calendar-grid helpers shared by the suggestion history views. */

export const DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

/** Calendar cells for a month: leading blanks so day 1 lands on its weekday. */
export function monthGrid(year, month) {
  const first = new Date(year, month, 1);
  const lead = (first.getDay() + 6) % 7; // Monday-first
  const days = new Date(year, month + 1, 0).getDate();
  const cells = Array.from({ length: lead }, () => null);
  for (let d = 1; d <= days; d += 1) {
    const iso = `${year}-${String(month + 1).padStart(2, "0")}-${String(d).padStart(2, "0")}`;
    cells.push({ day: d, iso });
  }
  return cells;
}

/** The last `count` days ending today, oldest first, as ISO date strings. */
export function recentDays(count, today = new Date()) {
  const out = [];
  for (let i = count - 1; i >= 0; i -= 1) {
    const d = new Date(today.getFullYear(), today.getMonth(), today.getDate() - i);
    out.push(
      `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`,
    );
  }
  return out;
}

/** Group entries by their for_date. */
export function groupByDay(entries) {
  const m = new Map();
  for (const e of entries) {
    if (!m.has(e.for_date)) m.set(e.for_date, []);
    m.get(e.for_date).push(e);
  }
  return m;
}
