import Icon from "./Icon";
import Skeleton from "./Skeleton";
import ViewAll from "./ViewAll";
import CollapseToggle from "./CollapseToggle";
import SortHeader from "./SortHeader";
import { useSortableRows } from "../hooks/useSortableRows";
import { formatDate } from "../lib/format";
import styles from "./CongressPanel.module.css";
import Tooltip from "./Tooltip";
import InfoTip from "./InfoTip";
import ScrollHint from "./ScrollHint";

const COMPACT_LIMIT = 5;

// Amount is a bucket string ("$1,001 - $15,000"); sort by its lower bound.
const AMOUNT_RE = /[\d,]+/;
const COLUMNS = {
  representative: (t) => t.representative,
  party: (t) => t.party,
  ticker: (t) => t.ticker,
  type: (t) => t.transaction_type,
  amount: (t) => {
    const m = (t.amount_range || "").match(AMOUNT_RE);
    return m ? Number(m[0].replace(/,/g, "")) : null;
  },
  date: (t) => Date.parse(t.transaction_date) || 0,
};

function partyTone(party) {
  if (party === "D") return "dem";
  if (party === "R") return "rep";
  return "neutral";
}

function typeTone(type) {
  if (type === "Purchase") return "buy";
  if (type === "Sale") return "sell";
  return "neutral";
}

function SkeletonRows({ rows = 8 }) {
  return Array.from({ length: rows }).map((_, i) => (
    <tr key={i}>
      <td><Skeleton w="140px" /></td>
      <td><Skeleton w="28px" /></td>
      <td><Skeleton w="46px" /></td>
      <td><Skeleton w="70px" /></td>
      <td><Skeleton w="120px" /></td>
      <td><Skeleton w="64px" /></td>
      <td><Skeleton w="32px" /></td>
    </tr>
  ));
}

export default function CongressPanel({ data, loading, busy, onRefresh, compact = false, onViewAll, collapsible = false, collapsed = false, onToggleCollapse }) {
  const showEmpty = !loading && data.length === 0;
  const { sorted, sortProps } = useSortableRows(data, COLUMNS);
  const rows = compact ? sorted.slice(0, COMPACT_LIMIT) : sorted;

  return (
    <section className={styles.panel} id="congress">
      <header className={styles.head}>
        {collapsible && <CollapseToggle collapsed={collapsed} onClick={onToggleCollapse} label="Congress" />}
        <div>
          <h2 className={styles.title}>Congressional Trades <InfoTip term="congress_trade" /></h2>
          <p className={styles.subtitle}>
            STOCK Act disclosures · House &amp; Senate · legislators trading ahead of policy booms
          </p>
        </div>
        {compact && onViewAll && <ViewAll onClick={onViewAll} />}
      </header>

      {!collapsed && (showEmpty ? (
        <div className={styles.empty}>
          <span className={styles.emptyIcon}><Icon name="layers" size={24} /></span>
          <p className={styles.emptyTitle}>No congressional trades loaded yet</p>
          <button className={styles.emptyBtn} onClick={onRefresh} disabled={busy}>
            {busy ? "Refreshing…" : "Refresh now"}
          </button>
        </div>
      ) : (
        <ScrollHint className={styles.tableWrap}>
          <table className={styles.table}>
            <thead>
              <tr>
                <SortHeader label="Representative" {...sortProps("representative")} />
                <SortHeader label="Party" {...sortProps("party")} />
                <SortHeader label="Ticker" {...sortProps("ticker")} />
                <SortHeader label="Type" {...sortProps("type")} />
                <SortHeader label="Amount" {...sortProps("amount")} />
                <SortHeader label="Date" {...sortProps("date")} />
                <th>Filing</th>
              </tr>
            </thead>
            <tbody>
              {loading ? (
                <SkeletonRows />
              ) : (
                rows.map((t) => (
                  <tr key={t.trade_hash}>
                    <td className={styles.rep}>
                      <Tooltip truncate content={t.state ? `${t.representative} (${t.state})` : t.representative}>
                        <span className={styles.repName}>{t.representative}</span>
                      </Tooltip>
                      {t.state && <span className={styles.state}>{t.state}</span>}
                    </td>
                    <td>
                      {t.party ? (
                        <span className={styles.badge} data-tone={partyTone(t.party)}>
                          {t.party}
                        </span>
                      ) : "—"}
                    </td>
                    <td>
                      {t.asset_description ? (
                        <Tooltip content={t.asset_description}>
                          <span className={styles.ticker}>{t.ticker || "—"}</span>
                        </Tooltip>
                      ) : <span className={styles.ticker}>{t.ticker || "—"}</span>}
                    </td>
                    <td>
                      <span className={styles.badge} data-tone={typeTone(t.transaction_type)}>
                        {t.transaction_type}
                      </span>
                    </td>
                    <td className={styles.amount}>{t.amount_range || "—"}</td>
                    <td className={styles.muted}>{formatDate(t.transaction_date)}</td>
                    <td>
                      {t.source_url ? (
                        <a className={styles.link} href={t.source_url} target="_blank" rel="noreferrer"
                           title={t.doc_id ? `House Clerk PTR ${t.doc_id}, filed ${t.filed_at}` : "Senate Stock Watcher"}>
                          {t.doc_id ? "PTR ↗" : "source ↗"}
                        </a>
                      ) : <span className={styles.muted}>—</span>}
                    </td>
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
