import Icon from "./Icon";
import Skeleton from "./Skeleton";
import ViewAll from "./ViewAll";
import CollapseToggle from "./CollapseToggle";
import TickerLabel from "./TickerLabel";
import { formatRelativeTime, freshnessTone } from "../lib/format";
import styles from "./ShortPanel.module.css";
import Tooltip from "./Tooltip";
import Term from "./Term";
import InfoTip from "./InfoTip";
import ScrollHint from "./ScrollHint";
import { freshnessTip } from "../lib/freshness";

const COMPACT_LIMIT = 5;

function SkeletonRows({ rows = 6 }) {
  return Array.from({ length: rows }).map((_, i) => (
    <tr key={i}>
      <td><Skeleton w="52px" /></td>
      <td><Skeleton w="64px" /></td>
      <td><Skeleton w="56px" /></td>
      <td><Skeleton w="70px" /></td>
      <td><Skeleton w="60px" /></td>
    </tr>
  ));
}

function FreshnessCell({ fetched_at }) {
  const text = formatRelativeTime(fetched_at);
  const tone = freshnessTone(fetched_at);
  return (
    <Tooltip content={freshnessTip(fetched_at)}>
      <span className={styles.freshness} data-tone={tone}>{text}</span>
    </Tooltip>
  );
}

function fmtPct(v) {
  if (v == null) return "—";
  return (v * 100).toFixed(1) + "%";
}

function fmtCover(v) {
  if (v == null) return "—";
  return v.toFixed(1) + "d";
}

export default function ShortPanel({ data, loading, busy, onRefresh, compact = false, onViewAll, collapsible = false, collapsed = false, onToggleCollapse }) {
  const showEmpty = !loading && data.length === 0;
  const rows = compact ? data.slice(0, COMPACT_LIMIT) : data;

  return (
    <section className={styles.panel} id="short-interest">
      <header className={styles.head}>
        {collapsible && <CollapseToggle collapsed={collapsed} onClick={onToggleCollapse} label="Short Interest" />}
        <div>
          <h2 className={styles.title}>Short Interest <InfoTip term="short_interest" /></h2>
          <p className={styles.subtitle}>Yahoo Finance · shares short &amp; days to cover per watchlist ticker</p>
        </div>
        {compact && onViewAll && <ViewAll onClick={onViewAll} />}
      </header>

      {!collapsed && (showEmpty ? (
        <div className={styles.empty}>
          <span className={styles.emptyIcon}><Icon name="trending" size={24} /></span>
          <p className={styles.emptyTitle}>No short interest data loaded yet</p>
          <button className={styles.emptyBtn} onClick={onRefresh} disabled={busy}>
            {busy ? "Refreshing…" : "Refresh now"}
          </button>
        </div>
      ) : (
        <ScrollHint className={styles.tableWrap}>
          <table className={styles.table}>
            <thead>
              <tr>
                <th>Ticker</th>
                <th className={styles.num}><Term term="short_interest">Short % Float</Term></th>
                <th className={styles.num}><Term term="days_to_cover">Days to Cover</Term></th>
                <th><Term term="short_squeeze">Squeeze</Term></th>
                <th>Updated</th>
              </tr>
            </thead>
            <tbody>
              {loading ? (
                <SkeletonRows />
              ) : (
                rows.map((s) => (
                  <tr key={s.ticker}>
                    <td><TickerLabel ticker={s.ticker} className={styles.ticker} /></td>
                    <td className={styles.num}>{fmtPct(s.short_pct_float)}</td>
                    <td className={styles.num}>{fmtCover(s.days_to_cover)}</td>
                    <td>
                      {s.squeeze_flag ? (
                        <span className={styles.badge} data-tone="squeeze">Squeeze</span>
                      ) : (
                        <span className={styles.badge}>Normal</span>
                      )}
                    </td>
                    <td><FreshnessCell fetched_at={s.fetched_at} /></td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </ScrollHint>
      ))}
    </section>
  );
}
