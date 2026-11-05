import Icon from "./Icon";
import Skeleton from "./Skeleton";
import ViewAll from "./ViewAll";
import CollapseToggle from "./CollapseToggle";
import TickerLabel from "./TickerLabel";
import { formatDate, formatRelativeTime, freshnessTone } from "../lib/format";
import styles from "./AnalystPanel.module.css";
import { freshnessTip } from "../lib/freshness";
import Tooltip from "./Tooltip";
import Term from "./Term";
import InfoTip from "./InfoTip";
import ScrollHint from "./ScrollHint";

const COMPACT_LIMIT = 5;

function SkeletonRows({ rows = 6 }) {
  return Array.from({ length: rows }).map((_, i) => (
    <tr key={i}>
      <td><Skeleton w="52px" /></td>
      <td><Skeleton w="76px" /></td>
      <td><Skeleton w="32px" /></td>
      <td><Skeleton w="32px" /></td>
      <td><Skeleton w="32px" /></td>
      <td><Skeleton w="32px" /></td>
      <td><Skeleton w="56px" /></td>
      <td><Skeleton w="80px" /></td>
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

function actionTone(action) {
  if (!action) return "neutral";
  const a = action.toLowerCase();
  if (a === "up" || a === "init" || a === "reit") return "buy";
  if (a === "down") return "sell";
  return "neutral";
}

function actionLabel(action) {
  if (!action) return "—";
  const map = { up: "Upgrade", down: "Downgrade", init: "Initiate", reit: "Reiterate" };
  return map[action.toLowerCase()] ?? action;
}

export default function AnalystPanel({ data, loading, busy, onRefresh, compact = false, onViewAll, collapsible = false, collapsed = false, onToggleCollapse }) {
  const showEmpty = !loading && data.length === 0;
  const rows = compact ? data.slice(0, COMPACT_LIMIT) : data;

  return (
    <section className={styles.panel} id="analyst">
      <header className={styles.head}>
        {collapsible && <CollapseToggle collapsed={collapsed} onClick={onToggleCollapse} label="Analyst" />}
        <div>
          <h2 className={styles.title}>Analyst Ratings <InfoTip term="analyst_rating" /></h2>
          <p className={styles.subtitle}>Yahoo Finance · current consensus &amp; recent upgrades/downgrades</p>
        </div>
        {compact && onViewAll && <ViewAll onClick={onViewAll} />}
      </header>

      {!collapsed && (showEmpty ? (
        <div className={styles.empty}>
          <span className={styles.emptyIcon}><Icon name="star" size={24} /></span>
          <p className={styles.emptyTitle}>No analyst data loaded yet</p>
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
                <th>Next Earnings</th>
                <th className={styles.num}><Term tip="Number of analysts rating it Strong Buy">Str Buy</Term></th>
                <th className={styles.num}>Buy</th>
                <th className={styles.num}>Hold</th>
                <th className={styles.num}>Sell</th>
                <th><Term tip="The most recent rating action: upgrade, downgrade, initiation or reiteration">Latest</Term></th>
                <th>Firm</th>
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
                    <td className={styles.muted}>{s.next_earnings ? formatDate(s.next_earnings) : "—"}</td>
                    <td className={styles.num}>{s.rec_strong_buy ?? "—"}</td>
                    <td className={styles.num}>{s.rec_buy ?? "—"}</td>
                    <td className={styles.num}>{s.rec_hold ?? "—"}</td>
                    <td className={styles.num}>{s.rec_sell ?? "—"}</td>
                    <td>
                      <span className={styles.badge} data-tone={actionTone(s.latest_action)}>
                        {actionLabel(s.latest_action)}
                      </span>
                    </td>
                    <Tooltip truncate><td className={styles.firm}>{s.latest_firm || "—"}</td></Tooltip>
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
