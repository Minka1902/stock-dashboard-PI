import PaperTradeButton from "./PaperTradeButton";
import ScannerAlertsCard from "./ScannerAlertsCard";
import ScannerPanel from "./ScannerPanel";
import WatchButton from "./WatchButton";
import { useScanner } from "../hooks/useScanner";
import { formatRelativeTime, formatUntil } from "../lib/format";
import { sourceNote, sourceState } from "../lib/sources";
import styles from "./ScannerTab.module.css";

const SOURCE_LABEL = { scanner: "Scan", scan_history: "Daily history", universe: "Index lists" };

function statusText(s) {
  if (!s) return "not run yet";
  const state = sourceState(s.status);
  if (state === "ok") return sourceNote(s.status) || "ok";
  if (state === "deferred") {
    const reason = s.status.replace(/^deferred:?\s*/, "");
    return s.next_attempt_at ? `${reason} — next ${formatUntil(s.next_attempt_at)}` : reason;
  }
  return s.status;
}

/**
 * The Scanner tab of the Trading Bot page: today's matches across the
 * configured universe, pushed live as each scan run finishes, plus the
 * opt-in alert settings and what each of the three scanner sources last did.
 */
export default function ScannerTab() {
  const { data, error, live, savePrefs } = useScanner();

  if (!data) return <p className={styles.placeholder}>{error || "Loading the scanner…"}</p>;

  const universes = data.universes.map((u) => (u.members == null
    ? `${u.name} (unknown — check STOCKS_SCANNER_UNIVERSES)`
    : `${u.label} (${u.members})`)).join(" + ");

  return (
    <div className={styles.root}>
      <section className={styles.head} aria-labelledby="scanner-title">
        <div className={styles.titleRow}>
          <h3 id="scanner-title" className={styles.title}>Market scanner</h3>
          <span className={styles.live} data-live={live ? "yes" : "no"}
                title={live ? "Matches arrive as each scan finishes" : "Reconnecting; checking every minute meanwhile"}>
            {live ? "Live" : "Polling"}
          </span>
        </div>
        <p className={styles.text}>
          {universes} → <strong>{data.tickers}</strong> tickers
          {data.tickers >= data.max_tickers && <> (capped at {data.max_tickers})</>}, {data.with_history} with daily history.
          Scans run every {Math.round(data.interval_seconds / 60)} min while a market is open, on completed daily
          bars plus today so far. A match is a signal to look, not a recommendation.
        </p>
        <dl className={styles.sources}>
          {Object.entries(SOURCE_LABEL).map(([key, label]) => {
            const s = data.sources[key];
            return (
              <div key={key} className={styles.source} data-state={s ? sourceState(s.status) : "none"}>
                <dt>{label}</dt>
                <dd>
                  {statusText(s)}
                  {s?.last_refreshed_at && <span className={styles.faint}> · {formatRelativeTime(s.last_refreshed_at)}</span>}
                </dd>
              </div>
            );
          })}
        </dl>
      </section>

      <ScannerAlertsCard prefs={data.prefs} onSave={savePrefs} />

      <section aria-label="Scanner matches">
        <ScannerPanel
          hits={data.hits}
          scans={data.scans}
          emptyText={data.day ? "Nothing matches right now." : "No matches yet. They appear here once a scan runs during market hours."}
          rowActions={(ticker) => (
            <>
              <WatchButton ticker={ticker} />
              <PaperTradeButton ticker={ticker} />
            </>
          )}
        />
      </section>
    </div>
  );
}
