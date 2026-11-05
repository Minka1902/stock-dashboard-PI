import { useEffect, useState } from "react";
import ViewAll from "./ViewAll";
import CollapseToggle from "./CollapseToggle";
import EmptyState from "./EmptyState";
import PaperTradeButton from "./PaperTradeButton";
import TickerLabel from "./TickerLabel";
import { navigateTo, openTickerTab, routeToPath } from "../lib/nav";
import { getSuggestionLog } from "../api";
import { formatRelativeTime } from "../lib/format";
import { RISK_TAB_ROUTE } from "../lib/regime";
import styles from "./SuggestionsPanel.module.css";
import Tooltip from "./Tooltip";
import Term from "./Term";

function statusTone(status) {
  if (status.startsWith("sent")) return "pos";
  if (status.startsWith("error")) return "neg";
  return "muted"; // skipped: ...
}

// Newest 2 email + newest 2 SMS deliveries; `alert` rows are excluded here.
function recentDeliveries(log) {
  const byChannel = (ch) =>
    log
      .filter((e) => e.channel === ch)
      .sort((a, b) => (a.created_at < b.created_at ? 1 : -1))
      .slice(0, 2);
  return [...byChannel("email"), ...byChannel("sms")].sort((a, b) =>
    a.created_at < b.created_at ? 1 : -1,
  );
}

function DeliveryLog() {
  const [log, setLog] = useState([]);
  useEffect(() => {
    getSuggestionLog().then(setLog).catch(() => {});
  }, []);
  const rows = recentDeliveries(log);
  if (rows.length === 0) return null;
  return (
    <div className={styles.section}>
      <h3 className={styles.sectionTitle}>Recent deliveries</h3>
      <ul className={styles.log}>
        {rows.map((e, i) => (
          <li key={i} className={styles.logRow}>
            <span className={styles.logTime}>{formatRelativeTime(e.created_at)}</span>
            <span className={styles.logFor}>for {e.for_date}</span>
            <span className={styles.logChannel}>{e.channel}</span>
            <span className={styles.logStatus} data-tone={statusTone(e.status)}>{e.status}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

/** The symbol, clickable: opens that stock's analysis in a new tab. */
function Symbol({ ticker }) {
  return (
    <Tooltip content={`Open ${ticker} analysis in a new tab`}>
      <button
        type="button"
        className={styles.symbolBtn}
        onClick={() => openTickerTab(ticker)}
      >
        <TickerLabel ticker={ticker} className={styles.symbol} />
      </button>
    </Tooltip>
  );
}

/** Which of the user's watchlists this ticker sits on. */
function Lists({ lists }) {
  if (!lists || lists.length === 0) return null;
  return (
    <span className={styles.lists}>
      {lists.map((name) => (
        <span key={name} className={styles.listChip}>{name}</span>
      ))}
    </span>
  );
}

function AlertRow({ a }) {
  const tone = a.pl_pct == null ? "flat" : a.pl_pct >= 0 ? "pos" : "neg";
  const risky = /trim|watch|earnings|hedg/i.test(a.action);
  return (
    <li className={styles.alert}>
      <Symbol ticker={a.ticker} /><Lists lists={a.lists} />
      {a.pl_pct != null && (
        <span className={styles.pl} data-tone={tone}>
          {a.pl_pct >= 0 ? "+" : ""}{a.pl_pct.toFixed(1)}%
        </span>
      )}
      <span className={styles.action} data-risk={risky ? "yes" : "no"}>{a.action}</span>
      {a.reasons.length > 0 && (
        <span className={styles.reasons}>
          {a.reasons.map((r) => <span key={r} className={styles.chip}>{r}</span>)}
        </span>
      )}
    </li>
  );
}

/** The shared contents of an opportunity row, minus the <li> wrapper, so a
 *  tracked name and a new idea render identically apart from the Watch button. */
function OpportunityBody({ o }) {
  // Fresh deploy: TA not computed yet — fall back to the Boom read, flagged.
  if (o.ta_pending) {
    return (
      <>
        <Symbol ticker={o.ticker} /><Lists lists={o.lists} />
        <span className={styles.score}><Term term="boom_score">Boom {o.score}</Term></span>
        <span className={styles.reasons}>
          {o.signals.map((s) => <span key={s} className={styles.chip} data-tone="bull">{s}</span>)}
          <Tooltip content="Technical analysis for this ticker hasn't been computed yet, so this row shows its Boom Score signals instead.">
            <span className={styles.chip} data-tone="muted" tabIndex={0}>TA pending</span>
          </Tooltip>
        </span>
      </>
    );
  }
  const tone = o.recommendation === "buy" ? "bull" : o.recommendation === "sell" ? "bear" : "muted";
  return (
    <>
      <Symbol ticker={o.ticker} /><Lists lists={o.lists} />
      <span className={styles.chip} data-tone={tone}>{(o.recommendation || "hold").toUpperCase()}</span>
      <span className={styles.action} data-risk="no">
        <Term term="analysis_conviction">conv {o.conviction}</Term>
        {o.rr != null && <> · <Term term="r_multiple">R/R {o.rr}</Term></>}
        {o.entry != null ? ` · entry ${o.entry} / stop ${o.stop}` : ""}
      </span>
      <span className={styles.reasons}>
        {(o.evidence || []).map((e) => <span key={e} className={styles.chip}>{e}</span>)}
        {o.score != null && (
          <Term term="boom_score"><span className={styles.chip} data-tone="muted">Boom {o.score}</span></Term>
        )}
      </span>
    </>
  );
}

function OpportunityRow({ o, forDate }) {
  return (
    <li className={styles.alert}>
      <OpportunityBody o={o} />
      {!o.ta_pending && <PaperTradeButton ticker={o.ticker} suggestionForDate={forDate} className={styles.watchBtn} />}
    </li>
  );
}

/** A candidate you don't track yet — adoptable in one click. */
function NewIdeaRow({ o, onWatch }) {
  const [adding, setAdding] = useState(false);
  const [added, setAdded] = useState(false);
  const add = () => {
    setAdding(true);
    Promise.resolve(onWatch(o.ticker, "from suggestions"))
      .then(() => setAdded(true))
      .catch(() => {})
      .finally(() => setAdding(false));
  };
  return (
    <li className={styles.alert}>
      <OpportunityBody o={o} />
      {onWatch && (
        <Tooltip content={added
          ? `${o.ticker} is on your watchlist`
          : adding ? `Adding ${o.ticker}…` : `Add ${o.ticker} to your watchlist`}>
          <button
            type="button"
            className={styles.watchBtn}
            onClick={add}
            disabled={adding || added}
          >
            {added ? "✓ Watching" : adding ? "Adding…" : "+ Watch"}
          </button>
        </Tooltip>
      )}
    </li>
  );
}

export default function SuggestionsPanel({ data, loading, busy, onRefresh, onAddWatch, compact = false, onViewAll, collapsible = false, collapsed = false, onToggleCollapse }) {
  const newIdeas = data?.new_ideas || [];
  const empty = !loading && data
    && data.holdings_alerts.length === 0
    && data.opportunities.length === 0
    && newIdeas.length === 0
    && data.seasonality.length === 0
    && !data.paused?.length;

  return (
    <section className={styles.panel} id="suggestions">
      <header className={styles.head}>
        {collapsible && <CollapseToggle collapsed={collapsed} onClick={onToggleCollapse} label="Suggestions" />}
        <div>
          <h2 className={styles.title}>Suggestions for {data ? data.for_date : "the next trading day"}</h2>
          <p className={styles.subtitle}>
            {data ? data.market_context.summary : "Tailored to your portfolio and watchlist — the same digest that gets emailed/texted."}
          </p>
        </div>
        {compact && onViewAll && <ViewAll onClick={onViewAll} />}
      </header>

      {!collapsed && (loading || !data ? (
        <div className={styles.placeholder}>Loading suggestions…</div>
      ) : empty ? (
        <EmptyState
          icon="spark"
          title="No suggestions yet"
          text="Add tickers to your watchlist and portfolio, then refresh signals."
          onRetry={onRefresh}
          busy={busy}
        />
      ) : (
        <div className={styles.body}>
          {data.paused?.length > 0 && (
            <p className={styles.paused} role="status">
              <strong>New buys paused</strong> — {data.paused.join(" · ")}. Holdings alerts still come through.{" "}
              <a href={routeToPath(RISK_TAB_ROUTE)} onClick={(e) => { e.preventDefault(); navigateTo(RISK_TAB_ROUTE); }}>
                Review it on the Trading Bot page
              </a>
            </p>
          )}
          {data.downgraded && (
            <p className={styles.downgraded} role="status">
              <strong>New buys shown as watch</strong> — {data.downgraded}.{" "}
              <a href={routeToPath(RISK_TAB_ROUTE)} onClick={(e) => { e.preventDefault(); navigateTo(RISK_TAB_ROUTE); }}>
                See the regime
              </a>
            </p>
          )}
          {data.holdings_alerts.length > 0 && (
            <div className={styles.section}>
              <h3 className={styles.sectionTitle}>Your holdings</h3>
              <ul className={styles.list}>
                {data.holdings_alerts.map((a) => <AlertRow key={a.ticker} a={a} />)}
              </ul>
            </div>
          )}

          {data.opportunities.length > 0 && (
            <div className={styles.section}>
              <h3 className={styles.sectionTitle}>On your watchlists</h3>
              <ul className={styles.list}>
                {data.opportunities.map((o) => <OpportunityRow key={o.ticker} o={o} forDate={data.for_date} />)}
              </ul>
            </div>
          )}

          {newIdeas.length > 0 && (
            <div className={styles.section}>
              <h3 className={styles.sectionTitle}>
                New ideas
                <span className={styles.sectionHint}>
                  surfaced by congress buys, insider clusters, upgrades, social spikes
                  or large contracts — not on any of your lists yet
                </span>
              </h3>
              <ul className={styles.list}>
                {newIdeas.map((o) => (
                  <NewIdeaRow key={o.ticker} o={o} onWatch={onAddWatch} />
                ))}
              </ul>
            </div>
          )}

          {data.seasonality.length > 0 && (
            <div className={styles.section}>
              <h3 className={styles.sectionTitle}>Seasonal edge (this time of year)</h3>
              <ul className={styles.list}>
                {data.seasonality.map((s) => (
                  <li key={s.ticker} className={styles.alert}>
                    <Symbol ticker={s.ticker} /><Lists lists={s.lists} />
                    <span className={styles.action} data-risk={s.kind === "headwind" ? "yes" : "no"}>
                      {s.window_label}: avg {s.avg_pct >= 0 ? "+" : ""}{s.avg_pct}%, win {Math.round(s.win_rate * 100)}% / {s.n}y
                    </span>
                    <span className={styles.chip} data-tone={s.kind === "tailwind" ? "bull" : "bear"}>
                      {s.kind}{s.held ? " · held" : ""}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {!compact && <DeliveryLog />}

          <p className={styles.disclaimer}>{data.disclaimer}</p>
        </div>
      ))}
    </section>
  );
}
