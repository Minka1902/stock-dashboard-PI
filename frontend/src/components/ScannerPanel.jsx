import { useMemo, useState } from "react";
import ScrollHint from "./ScrollHint";
import Segmented from "./Segmented";
import SortHeader from "./SortHeader";
import { useSortableRows } from "../hooks/useSortableRows";
import { currencyForSymbol, formatPercentSigned, formatPrice, formatRelativeTime } from "../lib/format";
import { openTickerTab } from "../lib/nav";
import styles from "./ScannerPanel.module.css";

const COLUMNS = {
  ticker: (r) => r.ticker,
  change: (r) => r.change_pct,
  price: (r) => r.price,
  metric: (r) => r.metric,
  first: (r) => r.first_seen_at,
};

function groupByTicker(hits, order) {
  const by = new Map();
  for (const h of hits) {
    const row = by.get(h.ticker) || { ticker: h.ticker, hits: [], first_seen_at: h.first_seen_at, current: false };
    row.hits.push(h);
    row.current ||= h.current;
    if (h.first_seen_at < row.first_seen_at) row.first_seen_at = h.first_seen_at;
    by.set(h.ticker, row);
  }
  return [...by.values()].map((r) => {
    r.hits.sort((a, b) => order[a.scan] - order[b.scan]);
    const latest = r.hits.reduce((a, b) => (b.last_seen_at > a.last_seen_at ? b : a));
    return { ...r, price: latest.price, change_pct: latest.change_pct };
  });
}

/**
 * Today's scanner matches, one row per ticker with every scan it matched.
 * Filter by scan (the metric column follows the filter), sort any column,
 * show or hide matches that have since dropped off. Clicking a ticker opens
 * its stock page. Props: hits, scans ([{key, label, description,
 * metric_label}]), rowActions(ticker) → extra cells' content,
 * onOpen(ticker) (openTickerTab), emptyText.
 */
export default function ScannerPanel({
  hits, scans, rowActions = null, onOpen = openTickerTab,
  emptyText = "No matches yet today.",
}) {
  const [filter, setFilter] = useState("all");
  const [showEarlier, setShowEarlier] = useState(false);

  const order = useMemo(() => Object.fromEntries(scans.map((s, i) => [s.key, i])), [scans]);
  const meta = useMemo(() => Object.fromEntries(scans.map((s) => [s.key, s])), [scans]);
  const grouped = useMemo(() => groupByTicker(hits, order), [hits, order]);
  const visible = useMemo(() => grouped.filter((r) => showEarlier || r.current), [grouped, showEarlier]);
  const counts = useMemo(() => {
    const c = {};
    visible.forEach((r) => r.hits.forEach((h) => { c[h.scan] = (c[h.scan] || 0) + 1; }));
    return c;
  }, [visible]);
  const rows = useMemo(() => visible
    .filter((r) => filter === "all" || r.hits.some((h) => h.scan === filter))
    .map((r) => {
      const shown = filter === "all" ? r.hits[0] : r.hits.find((h) => h.scan === filter);
      return { ...r, metric: shown.metric, metricScan: shown.scan };
    }), [visible, filter]);
  const { sorted, sortProps } = useSortableRows(rows, COLUMNS, { key: "first", dir: "desc" });
  const earlier = grouped.filter((r) => !r.current).length;

  const options = [
    { value: "all", label: "All", badge: visible.length },
    ...scans.map((s) => ({ value: s.key, label: s.label, title: s.description, badge: counts[s.key] || 0 })),
  ];

  return (
    <div className={styles.root}>
      <div className={styles.controls}>
        <ScrollHint className={styles.filters}>
          <Segmented options={options} value={filter} onChange={setFilter} ariaLabel="Filter by scan" />
        </ScrollHint>
        {earlier > 0 && (
          <label className={styles.check}>
            <input type="checkbox" checked={showEarlier} onChange={(e) => setShowEarlier(e.target.checked)} />
            Include {earlier} that dropped off
          </label>
        )}
      </div>
      {sorted.length === 0 ? <p className={styles.empty}>{emptyText}</p> : (
        <ScrollHint className={styles.wrap}>
          <table className={styles.table}>
            <thead>
              <tr>
                <SortHeader label="Ticker" {...sortProps("ticker")} />
                <th>Matched</th>
                <SortHeader label="Price" className={styles.num} {...sortProps("price")} />
                <SortHeader label="Today" className={styles.num} {...sortProps("change")} />
                <SortHeader label={filter === "all" ? "Metric" : meta[filter]?.metric_label} className={styles.num}
                            {...sortProps("metric")} />
                <SortHeader label="First seen" {...sortProps("first")} />
                {rowActions && <th><span className={styles.srOnly}>Actions</span></th>}
              </tr>
            </thead>
            <tbody>
              {sorted.map((r) => (
                <tr key={r.ticker} data-current={r.current ? "yes" : "no"}>
                  <td data-col="ticker">
                    <button type="button" className={styles.ticker} onClick={() => onOpen(r.ticker)}>{r.ticker}</button>
                  </td>
                  <td data-col="scans">
                    {r.hits.map((h) => (
                      <span key={h.scan} className={styles.chip} title={meta[h.scan]?.description}>
                        {meta[h.scan]?.label || h.scan}
                      </span>
                    ))}
                  </td>
                  <td data-col="price" className={styles.num}>{formatPrice(r.price, currencyForSymbol(r.ticker))}</td>
                  <td data-col="change" className={styles.num} data-tone={r.change_pct > 0 ? "up" : r.change_pct < 0 ? "down" : "flat"}>
                    {r.change_pct == null ? "—" : formatPercentSigned(r.change_pct / 100, 2)}
                  </td>
                  <td data-col="metric" className={styles.num}>
                    {r.metric == null ? "—" : <><span className={styles.faint}>{meta[r.metricScan]?.metric_label} </span>{r.metric}</>}
                  </td>
                  <td data-col="first" className={styles.faint}>{formatRelativeTime(r.first_seen_at)}</td>
                  {rowActions && <td data-col="actions" className={styles.actions}>{rowActions(r.ticker)}</td>}
                </tr>
              ))}
            </tbody>
          </table>
        </ScrollHint>
      )}
    </div>
  );
}
