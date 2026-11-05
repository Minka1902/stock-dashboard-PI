import CollapseToggle from "./CollapseToggle";
import EmptyState from "./EmptyState";
import ScrollHint from "./ScrollHint";
import SortHeader from "./SortHeader";
import Tooltip from "./Tooltip";
import ViewAll from "./ViewAll";
import { useSortableRows } from "../hooks/useSortableRows";
import { formatCurrencyCompact, formatDate } from "../lib/format";
import { openTickerTab } from "../lib/nav";
import styles from "./InvestorsPanel.module.css";

const COMPACT_LIMIT = 8;
const MOVE_TONE = { NEW: "buy", ADDED: "buy", TRIMMED: "sell", "SOLD OUT": "sell" };
const COLUMNS = {
  filer: (m) => m.filer,
  move: (m) => m.status,
  ticker: (m) => m.ticker || m.issuer,
  change: (m) => m.change_pct,
  value: (m) => Math.max(m.value, m.prev_value),
  pct: (m) => m.pct,
};
const EDGAR = (f) => `https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=${f.cik}&type=13F-HR`;

function pctText(m) {
  if (m.status === "SOLD OUT") return "—";
  return m.pct === 0 && m.value > 0 ? "<0.01%" : `${m.pct.toFixed(2)}%`;
}

function changeText(m) {
  if (m.status === "NEW") return "new";
  if (m.status === "SOLD OUT") return "sold out";
  return m.change_pct == null ? "—" : `${m.change_pct > 0 ? "+" : ""}${m.change_pct.toFixed(1)}% shares`;
}

/**
 * Famous investors' 13F filings: what each tracked fund bought, added,
 * trimmed and sold out of in its latest quarter. Every row says the quarter
 * it's as of — a 13F is filed up to 45 days after the quarter ends. Props:
 * data ({filers, moves}), loading, busy, onRefresh, compact (top moves and
 * no filer list), onViewAll, collapsible / collapsed / onToggleCollapse.
 */
export default function InvestorsPanel({ data, loading, busy, onRefresh, compact = false, onViewAll,
  collapsible = false, collapsed = false, onToggleCollapse }) {
  const moves = data?.moves || [];
  const filers = data?.filers || [];
  const { sorted, sortProps } = useSortableRows(moves, COLUMNS);
  const rows = compact ? sorted.slice(0, COMPACT_LIMIT) : sorted;

  return (
    <section className={styles.panel} id="investors">
      <header className={styles.head}>
        {collapsible && <CollapseToggle collapsed={collapsed} onClick={onToggleCollapse} label="Famous investors" />}
        <div>
          <h2 className={styles.title}>Famous investors (13F)</h2>
          <p className={styles.subtitle}>
            Quarter-end holdings of {filers.length ? `${filers.length} fund${filers.length === 1 ? "" : "s"}` : "well-known funds"}
            {" "}from SEC 13F filings — filed up to 45 days after the quarter, long US positions only.
          </p>
        </div>
        {compact && onViewAll && <ViewAll onClick={onViewAll} />}
      </header>

      {!collapsed && (!loading && moves.length === 0 ? (
        <EmptyState icon="layers" title="No 13F filings loaded yet" onRetry={onRefresh} busy={busy} />
      ) : (
        <>
          <ScrollHint className={styles.tableWrap}>
            <table className={styles.table}>
              <thead>
                <tr>
                  <SortHeader label="Investor" {...sortProps("filer")} />
                  <SortHeader label="Move" {...sortProps("move")} />
                  <SortHeader label="Stock" {...sortProps("ticker")} />
                  <SortHeader label="Change" {...sortProps("change")} />
                  <SortHeader label="Value" className={styles.num} {...sortProps("value")} />
                  <SortHeader label="% of 13F" className={styles.num} {...sortProps("pct")} />
                  <th>As of</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((m) => (
                  <tr key={`${m.cik}:${m.cusip}`}>
                    <td>
                      <Tooltip content={m.person}><span className={styles.filer}>{m.filer}</span></Tooltip>
                    </td>
                    <td><span className={styles.badge} data-tone={MOVE_TONE[m.status]}>{m.status.toLowerCase()}</span></td>
                    <td>
                      {m.ticker ? (
                        <button type="button" className={styles.ticker} onClick={() => openTickerTab(m.ticker)}
                                title={`${m.issuer} · ticker matched by ${m.ticker_source}`}>{m.ticker}</button>
                      ) : <span className={styles.muted} title="no ticker matched">{m.issuer}</span>}
                    </td>
                    <td className={styles.muted}>{changeText(m)}</td>
                    <td className={styles.num}>{formatCurrencyCompact(Math.max(m.value, m.prev_value))}</td>
                    <td className={styles.num}>{pctText(m)}</td>
                    <td className={styles.muted}>{formatDate(m.period)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </ScrollHint>
          {!compact && filers.length > 0 && (
            <ul className={styles.filers}>
              {filers.map((f) => (
                <li key={f.cik}>
                  <a href={EDGAR(f)} target="_blank" rel="noreferrer" className={styles.filerLink}>{f.filer}</a>
                  <span className={styles.muted}>
                    {f.person} · as of {formatDate(f.period)}, filed {formatDate(f.filed)} · {f.positions} positions ·
                    {" "}{formatCurrencyCompact(f.total_value)}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </>
      ))}
    </section>
  );
}
