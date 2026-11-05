import { useMemo, useState } from "react";

/**
 * Reusable client-side table sort.
 *
 * `columns` maps a sort key → an accessor(row) returning the comparable value
 * (number or string). Pass it a *stable* object (define it at module scope) so
 * the memo doesn't recompute every render.
 *
 * Returns the sorted rows plus `sortProps(key)` — spread the result onto a
 * header control to wire `aria-sort`, the active direction and the click
 * handler. Clicking cycles ascending → descending on the same column; clicking
 * a different column starts ascending. Null/blank values always sort last.
 */
export function useSortableRows(rows, columns, initial = null) {
  const [sort, setSort] = useState(initial); // { key, dir: "asc" | "desc" } | null

  const sorted = useMemo(() => {
    if (!sort || !columns[sort.key]) return rows;
    const accessor = columns[sort.key];
    const dir = sort.dir === "asc" ? 1 : -1;
    // Decorate-sort-undecorate keeps the sort stable (ties hold input order).
    return rows
      .map((row, i) => [row, i])
      .sort(([a, ai], [b, bi]) => {
        const av = accessor(a);
        const bv = accessor(b);
        const aBlank = av == null || av === "";
        const bBlank = bv == null || bv === "";
        if (aBlank && bBlank) return ai - bi;
        if (aBlank) return 1; // blanks last regardless of direction
        if (bBlank) return -1;
        let cmp;
        if (typeof av === "number" && typeof bv === "number") cmp = av - bv;
        else cmp = String(av).localeCompare(String(bv), undefined, { numeric: true, sensitivity: "base" });
        return cmp !== 0 ? cmp * dir : ai - bi;
      })
      .map(([row]) => row);
  }, [rows, sort, columns]);

  const toggle = (key) =>
    setSort((s) => (s && s.key === key && s.dir === "asc" ? { key, dir: "desc" } : { key, dir: "asc" }));

  const sortProps = (key) => ({
    active: sort?.key === key,
    dir: sort?.key === key ? sort.dir : null,
    ariaSort: sort?.key === key ? (sort.dir === "asc" ? "ascending" : "descending") : "none",
    onSort: () => toggle(key),
  });

  return { sorted, sort, sortProps };
}
