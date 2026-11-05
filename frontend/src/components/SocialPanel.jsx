import Icon from "./Icon";
import Skeleton from "./Skeleton";
import ViewAll from "./ViewAll";
import CollapseToggle from "./CollapseToggle";
import TickerLabel from "./TickerLabel";
import styles from "./SocialPanel.module.css";
import Term from "./Term";
import Tooltip from "./Tooltip";
import InfoTip from "./InfoTip";
import ScrollHint from "./ScrollHint";

const COMPACT_LIMIT = 5;

function SkeletonRows({ rows = 6 }) {
  return Array.from({ length: rows }).map((_, i) => (
    <tr key={i}>
      <td><Skeleton w="52px" /></td>
      <td><Skeleton w="40px" /></td>
      <td><Skeleton w="60px" /></td>
      <td><Skeleton w="56px" /></td>
    </tr>
  ));
}

function RankChange({ change }) {
  if (change == null) return <span className={styles.muted}>—</span>;
  const places = (n) => `${n} place${n === 1 ? "" : "s"}`;
  if (change > 0) {
    return (
      <Tooltip content={`Up ${places(change)} in mention rank vs 24 hours ago`}>
        <span className={styles.rising}>↑{change}</span>
      </Tooltip>
    );
  }
  if (change < 0) {
    return (
      <Tooltip content={`Down ${places(Math.abs(change))} in mention rank vs 24 hours ago`}>
        <span className={styles.falling}>↓{Math.abs(change)}</span>
      </Tooltip>
    );
  }
  return <span className={styles.muted}>—</span>;
}

export default function SocialPanel({ data, loading, busy, onRefresh, compact = false, onViewAll, collapsible = false, collapsed = false, onToggleCollapse }) {
  const showEmpty = !loading && data.length === 0;
  const rows = compact ? data.slice(0, COMPACT_LIMIT) : data;

  return (
    <section className={styles.panel} id="social">
      <header className={styles.head}>
        {collapsible && <CollapseToggle collapsed={collapsed} onClick={onToggleCollapse} label="WSB Sentiment" />}
        <div>
          <h2 className={styles.title}>WSB Sentiment <InfoTip term="wsb_rank" /></h2>
          <p className={styles.subtitle}>ApeWisdom · Reddit mentions &amp; rank movement for watchlist tickers</p>
        </div>
        {compact && onViewAll && <ViewAll onClick={onViewAll} />}
      </header>

      {!collapsed && (showEmpty ? (
        <div className={styles.empty}>
          <span className={styles.emptyIcon}><Icon name="news" size={24} /></span>
          <p className={styles.emptyTitle}>No social sentiment data loaded yet</p>
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
                <th className={styles.num}><Term term="wsb_rank">Rank</Term></th>
                <th className={styles.num}><Term tip="Mention count across Reddit stock communities, as reported by ApeWisdom (last 24 hours)">Mentions</Term></th>
                <th><Term tip="Places gained (↑) or lost (↓) in the mention rank vs 24 hours ago">Rank Change</Term></th>
              </tr>
            </thead>
            <tbody>
              {loading ? (
                <SkeletonRows />
              ) : (
                rows.map((s) => (
                  <tr key={s.ticker}>
                    <td><TickerLabel ticker={s.ticker} className={styles.ticker} /></td>
                    <td className={styles.num}>{s.rank ?? "—"}</td>
                    <td className={styles.num}>{s.mentions != null ? s.mentions.toLocaleString() : "—"}</td>
                    <td><RankChange change={s.rank_change} /></td>
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
