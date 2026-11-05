import { useEffect, useState } from "react";
import Icon from "./Icon";
import ChartPro from "./ChartPro";
import CollapsibleSection from "./CollapsibleSection";
import CompanyInfo from "./CompanyInfo";
import ExitRuleDialog from "./ExitRuleDialog";
import HeldBy13F from "./HeldBy13F";
import PaperTradeButton from "./PaperTradeButton";
import InsiderTrades from "./InsiderTrades";
import Skeleton from "./Skeleton";
import StockAlerts from "./StockAlerts";
import SuggestionHistoryStrip from "./SuggestionHistoryStrip";
import Segmented from "./Segmented";
import Term from "./Term";
import TickerLabel from "./TickerLabel";
import Tooltip from "./Tooltip";
import XPostCard from "./XPostCard";
import { getAnalyze, analysisReportUrl } from "../api";
import { currencyForSymbol, formatMoney, formatPrice } from "../lib/format";
import { useSettingsContext } from "../hooks/useSettingsContext";
import styles from "./StockDetailPanel.module.css";

const DIRECTIVE_TONE = {
  Accumulate: "buy", Hold: "hold", Reduce: "warn", Avoid: "sell",
};
const RECO_TONE = { buy: "buy", hold: "hold", sell: "sell" };
const FEAS_TONE = { base: "base", likely: "buy", possible: "hold", unlikely: "muted" };
const SIGNAL_TONE = { bullish: "pos", bearish: "neg", neutral: "" };
const BREAKOUT_TONE = {
  confirmed: "pos", approaching: "hold", broke_unconfirmed: "hold", failed: "neg",
};
// Conviction bands — mirror backend analysis.py (>=45, <=-15, <=-45).
const DIRECTIVE_TIP = {
  Accumulate: "Accumulate: conviction +45 or higher — the evidence leans clearly bullish.",
  Hold: "Hold: conviction between −15 and +45 — no strong lean either way.",
  Reduce: "Reduce: conviction −15 or lower — the evidence leans bearish.",
  Avoid: "Avoid: conviction −45 or lower — the evidence leans clearly bearish.",
};
const CCY_NAMES = { USD: "US dollars", ILS: "Israeli shekels", EUR: "euros", GBP: "British pounds" };

function n(v, d = 2) {
  return v == null ? "—" : Number(v).toFixed(d);
}

// Collapsed state lives in settings.collapsedSections under "stock:<id>".
// Deliberately NOT keyed per ticker: someone who never reads insider trades
// shouldn't have to collapse that pane again on every stock they open.
const SECTION_PREFIX = "stock:";
const sectionKey = (id) => `${SECTION_PREFIX}${id}`;

function Pane({ caption, right, collapsed, onToggle, children }) {
  return (
    <CollapsibleSection caption={caption} right={right} collapsed={collapsed} onToggle={onToggle}>
      {children}
    </CollapsibleSection>
  );
}

function Stat({ label, value, tone }) {
  return (
    <div className={styles.stat}>
      <span className={styles.statLabel}>{label}</span>
      <span className={styles.statValue} data-tone={tone}>{value}</span>
    </div>
  );
}

export default function StockDetailPanel({ ticker, onBack, watchlist, onAddWatch, held = false, focusAlertKey = null }) {
  // Track which ticker the loaded payload belongs to: switching tickers
  // shows the skeleton again without any synchronous setState in the effect.
  const [result, setResult] = useState(null);
  const [attempt, setAttempt] = useState(0);
  const [watchBusy, setWatchBusy] = useState(false);
  const [ruling, setRuling] = useState(false);
  const { settings, setSetting } = useSettingsContext();

  useEffect(() => {
    let alive = true;
    getAnalyze(ticker)
      .then((d) => { if (alive) setResult({ ticker, attempt, data: d, error: null }); })
      .catch((err) => {
        // A failed request is not "no analysis yet": keep the reason (rate
        // limit, timeout, server error) so the page can say so and retry.
        if (alive) setResult({ ticker, attempt, data: null, error: err });
      });
    return () => { alive = false; };
  }, [ticker, attempt]);

  const loading = result?.ticker !== ticker || result?.attempt !== attempt;
  const data = result?.data;
  const loadError = loading ? null : result?.error || null;
  const a = data?.analysis;
  const anchors = data?.seasonality_anchors || [];
  const xPosts = data?.x_posts || [];
  const company = data?.company || null;
  const insiderTrades = data?.insider_trades || [];
  const stockAlerts = data?.alerts || [];
  const companyInfo = settings.companyInfo || {};
  const lastClose = data?.daily?.length ? data.daily[data.daily.length - 1].close : null;
  const refPrice = a?.price ?? lastClose;
  // Every price on this page is in the listing's own currency (₪ for TASE —
  // Yahoo's agorot are divided into shekels server-side).
  const market = data?.market || null;
  const ccy = market?.currency || a?.currency || currencyForSymbol(ticker) || "USD";
  const px = (v) => formatPrice(v, ccy);
  const isTase = (market?.market || "") === "TASE" || /\.TA$/i.test(ticker);
  const notApplicable = market?.not_applicable || [];
  // Day change, derived from the same daily bars the chart draws and with the
  // same bar-to-bar formula as ChartPro's legend, so the two can't disagree on
  // the same screen. Not taken from /api/quotes: that only covers watchlist and
  // portfolio tickers, so an unwatched symbol would silently have no change at
  // all. null when there aren't two closes to compare — rendered as "—" rather
  // than a fabricated 0.00%.
  const prevClose = data?.daily?.length >= 2 ? data.daily[data.daily.length - 2].close : null;
  const changePct = prevClose && lastClose != null
    ? ((lastClose - prevClose) / prevClose) * 100
    : null;
  // Membership comes from the server across ALL of the user's lists — the old
  // check only saw the default list, so a ticker on a second list still
  // offered "Watch" (Task 18). Falls back to the passed-in list while loading.
  const memberOf = data?.watchlists ?? null;
  const watched = memberOf
    ? memberOf.length > 0
    : Boolean(watchlist?.some((w) => w.ticker === ticker));
  // `watchlist` is initialised to [] (truthy), so gate on the loaded payload
  // instead — otherwise "Watch" flashes before we know the answer.
  const canWatch = Boolean(onAddWatch) && !loading && !watched;

  // Confirmed by default: forming shapes are context, not conclusions, and
  // leading with them would overstate what the chart has actually done.
  const [patternFilter, setPatternFilter] = useState("confirmed");
  const allPatterns = a?.patterns || [];
  const confirmedCount = allPatterns.filter((p) => p.status !== "forming").length;
  const formingCount = allPatterns.length - confirmedCount;
  const shownPatterns = patternFilter === "all"
    ? allPatterns
    : allPatterns.filter((p) => p.status !== "forming");

  // Every section currently on the page, in render order — drives the
  // "Collapse all / Expand all" control. Mirrors the conditions in the JSX.
  const sectionIds = loading ? [] : [
    "chart",
    companyInfo.profile !== false && "company",
    companyInfo.insiders !== false && "insiders",
    "alerts",
    "history",
    anchors.length > 0 && "anchors",
    xPosts.length > 0 && "xwatch",
    ...(a ? ["plan", "structure", "patterns", "trendlines", "breakout", "candles", "why"] : []),
  ].filter(Boolean);
  const collapsedMap = settings.collapsedSections || {};
  // Arriving from an alert link (focusAlertKey) the Alerts pane renders open
  // even if it was collapsed — otherwise the alert you clicked through to
  // would be hidden. The override ends as soon as the user toggles it (or
  // uses Collapse all), and the stored preference is never rewritten by it.
  const [alertsOverrideDone, setAlertsOverrideDone] = useState(null);
  const forceAlertsOpen = Boolean(focusAlertKey) && alertsOverrideDone !== focusAlertKey;
  const isCollapsed = (id) =>
    !(id === "alerts" && forceAlertsOpen) && Boolean(collapsedMap[sectionKey(id)]);
  const setCollapsed = (ids, value) => {
    const next = { ...collapsedMap };
    for (const id of ids) {
      if (value) next[sectionKey(id)] = true;
      else delete next[sectionKey(id)];
    }
    setSetting("collapsedSections", next);
    if (ids.includes("alerts")) setAlertsOverrideDone(focusAlertKey);
  };
  // Props for one <Pane>: its effective state and a toggle that persists it.
  const sec = (id) => ({
    collapsed: isCollapsed(id),
    onToggle: () => setCollapsed([id], !isCollapsed(id)),
  });
  const allCollapsed = sectionIds.length > 0 && sectionIds.every(isCollapsed);
  const toggleAll = () => setCollapsed(sectionIds, !allCollapsed);

  const addToWatchlist = () => {
    setWatchBusy(true);
    Promise.resolve(onAddWatch(ticker, ""))
      .catch(() => {})
      .finally(() => setWatchBusy(false));
  };

  return (
    <div className={styles.wrap}>
      <div className={styles.topbar}>
        <button className={styles.back} onClick={onBack}>
          <Icon name="arrowRight" size={14} /> <span>Back</span>
        </button>
        {/* Ticker and price read as one unit: the name of the thing and what
            it costs. They used to sit at opposite ends of the flex row. */}
        <span className={styles.identity}>
          <TickerLabel ticker={ticker} className={styles.ticker} as="h2" />
          {refPrice != null && (
            <span className={styles.priceGroup}>
              <span className={styles.price}>{px(refPrice)}</span>
              <Tooltip content={changePct == null
                ? "Not enough daily history to compute a change"
                : "Change vs the previous daily close"}>
                <span
                  className={styles.change}
                  data-tone={changePct == null ? "flat" : changePct >= 0 ? "pos" : "neg"}
                  tabIndex={0}
                >
                  {changePct == null
                    ? "—"
                    : `${changePct >= 0 ? "+" : ""}${changePct.toFixed(2)}%`}
                </span>
              </Tooltip>
            </span>
          )}
          {isTase && (
            <span className={styles.marketChip}>
              <Term term="tase">TASE · {ccy}</Term>
            </span>
          )}
        </span>
        {a && a.recommendation && (
          <Tooltip content="Buy / Sell / Hold — the headline call">
            <span className={styles.directive} data-tone={RECO_TONE[a.recommendation]} tabIndex={0}>
              {a.recommendation.toUpperCase()}
            </span>
          </Tooltip>
        )}
        {a && (
          <Tooltip content={DIRECTIVE_TIP[a.directive] || "Finer-grained directive"}>
            <span className={styles.directive} data-tone={DIRECTIVE_TONE[a.directive]} tabIndex={0}>
              {a.directive}
            </span>
          </Tooltip>
        )}
        {a && (
          <span className={styles.conviction}>
            <span className={styles.convLabel}><Term term="analysis_conviction">conviction</Term></span>
            <span className={styles.convTrack}>
              <span className={styles.convFill} data-neg={a.conviction < 0 ? "yes" : "no"}
                    style={{ width: `${Math.min(100, Math.abs(a.conviction))}%` }} />
            </span>
            <span className={styles.convNum} data-neg={a.conviction < 0 ? "yes" : "no"}>{a.conviction}</span>
          </span>
        )}
        <span className={styles.spacer} />
        {sectionIds.length > 0 && (
          <Tooltip content={allCollapsed
            ? "Open every section on this page"
            : "Fold every section down to its header — remembered across stocks"}>
            <button
              type="button"
              className={styles.reportBtn}
              onClick={toggleAll}
            >
              {allCollapsed ? "Expand all" : "Collapse all"}
            </button>
          </Tooltip>
        )}
        {canWatch && (
          <Tooltip content="Track this stock: adds it to your watchlist so every signal source covers it">
            <button
              type="button"
              className={styles.reportBtn}
              onClick={addToWatchlist}
              disabled={watchBusy}
            >
              <Icon name="star" size={13} /> {watchBusy ? "Adding…" : "Watch"}
            </button>
          </Tooltip>
        )}
        {watched && (
          <Tooltip content={memberOf?.length
            ? `On your ${memberOf.join(", ")} watchlist${memberOf.length > 1 ? "s" : ""}`
            : "Already on your watchlist"}>
          <span className={styles.watching} tabIndex={0}>
            <Icon name="star" size={13} /> Watching
            {memberOf?.length > 0 && (
              <span className={styles.watchLists}>
                {memberOf.map((name) => (
                  <span key={name} className={styles.watchList}>{name}</span>
                ))}
              </span>
            )}
          </span>
          </Tooltip>
        )}
        <PaperTradeButton ticker={ticker} className={styles.reportBtn} />
        {held && (
          <Tooltip content="Stop, take-profit and trailing stop on your holding — alerts only">
            <button type="button" className={styles.reportBtn} onClick={() => setRuling(true)}>
              <Icon name="shield" size={13} /> Exit rule
            </button>
          </Tooltip>
        )}
        {a && (
          <span className={styles.reportBtns}>
            <Tooltip content="Download the full analysis as a standalone HTML report">
              <a className={styles.reportBtn} href={analysisReportUrl(ticker)}>
                <Icon name="news" size={13} /> Report
              </a>
            </Tooltip>
            <Tooltip content="Open the report print-ready — use the browser dialog to save as PDF">
              <a className={styles.reportBtn} href={analysisReportUrl(ticker, { print: true })}
                 target="_blank" rel="noreferrer">
                PDF
              </a>
            </Tooltip>
          </span>
        )}
      </div>

      {!loading && isTase && (
        <div className={styles.marketNote} role="note">
          <Icon name="info" size={14} />
          <span>
            Tel Aviv listing — prices in {CCY_NAMES[ccy] || ccy}, no pre-market or after-hours session.
            {notApplicable.length > 0 && (
              <>
                {" "}Not applicable to TASE listings:{" "}
                {notApplicable.map((na, i) => (
                  <span key={na.source}>
                    {i > 0 && ", "}
                    <Term tip={na.why}>{na.label}</Term>
                  </span>
                ))}
                {" "}— shown as not applicable rather than as missing data; the Boom Score is renormalized over the rest.
              </>
            )}
          </span>
        </div>
      )}

      {loading ? (
        <Skeleton w="100%" h="460px" />
      ) : (
        <>
          <Pane {...sec("chart")} caption="Chart">
            <ChartPro ticker={ticker} analysis={a} />
          </Pane>

          {/* Without the payload these panes would read "no filings", "nothing
              has tripped" — claims about data we never received. Only the
              chart (fetched on its own) and the error are shown. */}
          {!loadError && (
            <>
            {companyInfo.profile !== false && (
              <Pane {...sec("company")} caption="Company"
                    right={<span className={styles.muted}>who this is · who owns it</span>}>
                <CompanyInfo company={company} ticker={ticker} show={companyInfo} />
              </Pane>
            )}

            {companyInfo.insiders !== false && (
              <Pane {...sec("insiders")} caption="Insider trades"
                    right={<span className={styles.muted}>{isTase ? "not applicable to TASE listings" : "SEC Form 4 · newest first"}</span>}>
                {isTase ? (
                  <p className={styles.muted}>
                    Not applicable: SEC Form 4 covers US-listed companies. Israeli insiders report to the
                    Israel Securities Authority, which this dashboard does not ingest.
                  </p>
                ) : (
                  <InsiderTrades trades={insiderTrades} ticker={ticker} />
                )}
              </Pane>
            )}

            {!isTase && (
              <Pane {...sec("investors")} caption="Famous investors"
                    right={<span className={styles.muted}>SEC 13F · latest quarter-end</span>}>
                <HeldBy13F ticker={ticker} />
              </Pane>
            )}

            <Pane {...sec("alerts")} caption="Alerts"
                  right={<span className={styles.muted}>
                    {stockAlerts.length > 0
                      ? `${stockAlerts.length} fired · newest first`
                      : "nothing has tripped"}
                  </span>}>
              <StockAlerts alerts={stockAlerts} ticker={ticker} focusKey={focusAlertKey} />
            </Pane>

            <Pane {...sec("history")} caption="Suggestion history"
                  right={<span className={styles.muted}>what we said · what happened next</span>}>
              <SuggestionHistoryStrip ticker={ticker} daily={data?.daily || []} />
            </Pane>

            {anchors.length > 0 && (
              <Pane {...sec("anchors")} caption="This day in history"
                    right={<span className={styles.muted}>close on this date, past years</span>}>
                <div className={styles.anchors}>
                  {anchors.map((an) => {
                    const delta = refPrice && an.close
                      ? (refPrice / an.close - 1) * 100
                      : null;
                    const label = an.years_ago === "max" ? "earliest" : `${an.years_ago}y ago`;
                    return (
                      <div key={`${an.years_ago}`} className={styles.anchor}>
                        <span className={styles.anchorLabel}>{label}</span>
                        <span className={styles.anchorDate}>{an.date}</span>
                        <span className={styles.anchorClose}>{px(an.close)}</span>
                        {delta != null && (
                          <span className={styles.anchorDelta} data-tone={delta >= 0 ? "pos" : "neg"}>
                            {delta >= 0 ? "+" : ""}{delta.toFixed(1)}% since
                          </span>
                        )}
                      </div>
                    );
                  })}
                </div>
              </Pane>
            )}

            {xPosts.length > 0 && (
              <Pane {...sec("xwatch")} caption="X Watch"
                    right={<span className={styles.muted}>tracked-account posts mentioning {ticker}</span>}>
                <div className={styles.xFeed}>
                  {xPosts.map((p) => (
                    <XPostCard key={`${p.account}:${p.post_id}`} post={p} compact />
                  ))}
                </div>
              </Pane>
            )}

            </>
          )}

          {loadError && (
            <div className={styles.empty} role="alert">
              <p className={styles.emptyTitle}>Couldn&apos;t load the analysis for {ticker}</p>
              <p className={styles.emptyText}>
                {loadError.status === 429
                  ? "Too many analysis requests in the last minute — the server is rate-limiting. Wait a moment, then retry."
                  : `The request failed: ${loadError.message || "unknown error"}.`}
              </p>
              <button type="button" className={styles.reportBtn} onClick={() => setAttempt((n) => n + 1)}>
                <Icon name="refresh" size={13} /> Retry
              </button>
            </div>
          )}

          {!a && !loadError && (
            <div className={styles.empty}>
              <p className={styles.emptyTitle}>No analysis yet for {ticker}</p>
              <p className={styles.emptyText}>
                Price history is still loading, or there isn't enough of it yet —
                an honest read needs about 30 trading days. Try again in a minute.
              </p>
            </div>
          )}
        </>
      )}
      {!loading && a && (
        <>

          <div className={styles.grid}>
            <Pane {...sec("plan")} caption="Trade plan" right={a.rr != null && (
              <span className={styles.rr} data-tone={a.rr_pass ? "pos" : "neg"}>
                <Term term="r_multiple" tip={a.rr_pass ? "Meets the 3:1 professional threshold." : "Below 3:1 — a known skip."}>
                  {a.rr}:1 {a.rr_pass ? "✓" : "✗ <3"}
                </Term>
              </span>
            )}>
              {a.stop == null ? (
                <p className={styles.muted}>No valid stop below price yet — plan pending.</p>
              ) : (
                <>
                  <div className={styles.stats}>
                    <Stat label="Entry" value={px(a.entry)} />
                    <Stat label={`Stop (${a.stop_basis})`} value={px(a.stop)} tone="neg" />
                    <Stat label="Target 3R" value={px(a.target)} tone="pos" />
                    <Stat label="Risk / share" value={px(a.risk_per_share)} tone="neg" />
                    <Stat label="Reward / share" value={px(a.reward_per_share)} tone="pos" />
                    <Stat label="Shares" value={a.suggested_shares ?? "—"} />
                  </div>
                  <div className={styles.stopNote}>
                    ATR stop {px(a.stop_atr)} · structure stop {px(a.stop_structure)} → using the tighter.
                  </div>
                  <div className={styles.ladder}>
                    {a.targets.map((t) => (
                      <Tooltip key={t.r} content={t.why}>
                        <div className={styles.rung} tabIndex={0}>
                          <span className={styles.rungR}>{t.r}:1</span>
                          <span className={styles.rungPrice}>{px(t.price)}</span>
                          <span className={styles.feas} data-tone={FEAS_TONE[t.feasibility]}>{t.feasibility}</span>
                          <span className={styles.rungWhy}>{t.why}</span>
                        </div>
                      </Tooltip>
                    ))}
                  </div>
                  {a.account_size && (
                    <p className={styles.sizeNote}>
                      Sized to {a.risk_pct}% of a{" "}
                      {formatMoney(Number(a.account_size), a.account_currency || ccy, { digits: 0 })} account.
                      {a.sizing_note && <> {a.sizing_note}</>}
                    </p>
                  )}
                  {a.staging_note && <p className={styles.sizeNote}>{a.staging_note}</p>}
                </>
              )}
            </Pane>

            <Pane {...sec("structure")} caption="Structure">
              <div className={styles.stats}>
                <Stat label="Trend" value={a.trend} tone={a.trend === "up" ? "pos" : a.trend === "down" ? "neg" : ""} />
                <Stat label="MA stack" value={a.ma_alignment.replace("stacked_", "")} />
                <Stat label="MA state" value={(a.ma_state || "mixed").replace(/_/g, " ")}
                      tone={a.ma_state === "healthy_uptrend" || a.ma_state === "reclaiming" ? "pos"
                            : a.ma_state === "topping" || a.ma_state === "breaking_down" ? "neg" : ""} />
                <Stat label={<Term term="atr">ATR(14)</Term>} value={`${px(a.atr14)}${a.atr_pct ? ` (${n(a.atr_pct)}%)` : ""}`} />
                <Stat label={<Term tip="Distance of price from its 20-day average, in ATRs">Ext (ATR from MA20)</Term>}
                      value={a.ma_extension_atr != null ? `${n(a.ma_extension_atr)}×` : "—"} />
                <Stat label="MA20 / 50" value={`${n(a.ma20)} / ${n(a.ma50)}`} />
                <Stat label="MA150 / 200" value={`${n(a.ma150)} / ${n(a.ma200)}`} />
              </div>
              <div className={styles.levels}>
                <div>
                  <span className="caption">Resistance</span>
                  {a.resistance.length ? a.resistance.map((l, i) => (
                    <span key={i} className={styles.level} data-tone="neg">{px(l.price)} <em>{l.touches}×</em></span>
                  )) : <span className={styles.muted}>none above</span>}
                </div>
                <div>
                  <span className="caption">Support</span>
                  {a.support.length ? a.support.map((l, i) => (
                    <span key={i} className={styles.level} data-tone="pos">{px(l.price)} <em>{l.touches}×</em></span>
                  )) : <span className={styles.muted}>none below</span>}
                </div>
              </div>
              {a.gaps.filter((g) => !g.filled).length > 0 && (
                <div className={styles.gaps}>
                  <span className="caption">Unfilled gaps</span>
                  {a.gaps.filter((g) => !g.filled).map((g, i) => (
                    <span key={i} className={styles.level} data-tone={g.kind === "up" ? "pos" : "neg"}>
                      {g.kind} {n(g.pct)}% · {g.date}
                    </span>
                  ))}
                </div>
              )}
            </Pane>

            <Pane
              {...sec("patterns")}
              caption="Patterns"
              right={formingCount > 0 && (
                <Segmented
                  ariaLabel="Pattern filter"
                  value={patternFilter}
                  onChange={setPatternFilter}
                  options={[
                    { value: "confirmed", label: "Confirmed", badge: confirmedCount,
                      title: "Patterns that have actually triggered" },
                    { value: "all", label: "Incl. forming", badge: a.patterns.length,
                      title: "Also show shapes that are on the chart but haven't triggered yet" },
                  ]}
                />
              )}
            >
              {shownPatterns.length === 0 ? (
                <p className={styles.muted}>
                  {a.patterns.length === 0
                    ? "No classical pattern reads clearly right now."
                    : "Nothing confirmed — switch to “Incl. forming” for the shapes still developing."}
                </p>
              ) : (
                <ul className={styles.patterns}>
                  {shownPatterns.map((p, i) => (
                    <li key={i} className={styles.pattern} data-status={p.status}>
                      <span className={styles.patName}>{p.label}</span>
                      <span className={styles.patDir} data-tone={p.direction === "bullish" ? "pos" : p.direction === "bearish" ? "neg" : ""}>{p.direction}</span>
                      <span className={styles.patConf}>{Math.round(p.confidence * 100)}%</span>
                      {p.status === "forming" && (
                        <Tooltip content="The shape is there; the trigger hasn't happened">
                          <span className={styles.patForming} tabIndex={0}>
                            forming
                          </span>
                        </Tooltip>
                      )}
                      {p.measured_move && <span className={styles.patMove}>→ {px(p.measured_move)}</span>}
                      <span className={styles.patNote}>{p.note}</span>
                      {/* What's still outstanding. Showing why it isn't a pattern
                          yet is the honest version of "what it's heading towards". */}
                      {p.criteria?.length > 0 && (
                        <span className={styles.patCriteria}>
                          {p.criteria.map((c, j) => (
                            <em key={j} data-met={c.met ? "yes" : "no"}>
                              {c.met ? "✓" : "○"} {c.name}
                              {c.detail ? ` (${c.detail})` : ""}
                            </em>
                          ))}
                        </span>
                      )}
                      <span className={styles.patPivots}>
                        {p.pivots.map((pv, j) => <em key={j}>{pv.role} {px(pv.price)}</em>)}
                      </span>
                    </li>
                  ))}
                </ul>
              )}
            </Pane>

            {/* Trendlines were computed on every analysis and rendered nowhere. */}
            <Pane {...sec("trendlines")} caption="Trendlines"
                  right={<span className={styles.muted}>diagonal support &amp; resistance</span>}>
              {(a.trendlines || []).length === 0 ? (
                <p className={styles.muted}>No trendline has enough touches to be worth drawing.</p>
              ) : (
                <ul className={styles.patterns}>
                  {a.trendlines.map((t, i) => (
                    <li key={i} className={styles.pattern}>
                      <span className={styles.patName}>{t.kind === "support" ? "Rising support" : "Falling resistance"}</span>
                      <span className={styles.patDir} data-tone={t.kind === "support" ? "pos" : "neg"}>{t.kind}</span>
                      <span className={styles.patConf}>{t.touches} touches</span>
                      <span className={styles.patMove}>now ≈ {px(t.current_value)}</span>
                      {t.broken && <span className={styles.patForming}>broken</span>}
                    </li>
                  ))}
                </ul>
              )}
            </Pane>

            <Pane {...sec("breakout")} caption="Breakout / breakdown"
                  right={a.breakout && <span className={styles.rr} data-tone={BREAKOUT_TONE[a.breakout.status]}>{a.breakout.status.replace(/_/g, " ")}</span>}>
              {a.breakout ? (
                <>
                  <div className={styles.stats}>
                    <Stat label="Direction" value={a.breakout.direction}
                          tone={a.breakout.direction === "up" ? "pos" : "neg"} />
                    <Stat label="Level" value={px(a.breakout.level)} />
                    <Stat label="From" value={a.breakout.level_source} />
                    <Stat label="Volume" value={a.breakout.volume_confirmed ? "confirmed" : "unconfirmed"}
                          tone={a.breakout.volume_confirmed ? "pos" : ""} />
                  </div>
                  <p className={styles.muted}>{a.breakout.note}</p>
                </>
              ) : (
                <p className={styles.muted}>No level in play within striking distance right now.</p>
              )}
            </Pane>

            <Pane {...sec("candles")} caption="Candles & volume">
              {a.volume ? (
                <div className={styles.stats}>
                  <Stat label="Vol vs 20d" value={`${n(a.volume.ratio)}×`}
                        tone={a.volume.ratio >= 1.3 ? "pos" : ""} />
                  <Stat label="Close streak" value={a.volume.streak}
                        tone={a.volume.streak > 0 ? "pos" : a.volume.streak < 0 ? "neg" : ""} />
                  <Stat label="Volume state" value={a.volume.state} />
                </div>
              ) : (
                <p className={styles.muted}>No real volume in the data for this ticker.</p>
              )}
              {a.candles && a.candles.length > 0 ? (
                <ul className={styles.patterns}>
                  {a.candles.slice(-4).reverse().map((c, i) => (
                    <li key={i} className={styles.pattern}>
                      <span className={styles.patName}>{c.label}</span>
                      <span className={styles.patDir} data-tone={SIGNAL_TONE[c.direction]}>{c.direction}</span>
                      <span className={styles.patConf}>{c.date}</span>
                      <span className={styles.patNote}>{c.note}</span>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className={styles.muted}>No notable candlestick signals recently.</p>
              )}
            </Pane>

            <Pane {...sec("why")} caption="Why — the read">
              <ul className={styles.reasons}>
                {(a.evidence && a.evidence.length
                  ? a.evidence
                  : a.reasons.map((r) => ({ detail: r, component: "", signal: "neutral" }))
                ).map((e, i) => (
                  <li key={i} data-tone={SIGNAL_TONE[e.signal]}>
                    {e.component && <strong className={styles.evComp}>{e.component}</strong>} {e.detail}
                  </li>
                ))}
              </ul>
              <p className={styles.disclaimer}>{a.disclaimer}</p>
            </Pane>
          </div>
        </>
      )}
      {ruling && <ExitRuleDialog ticker={ticker} onClose={() => setRuling(false)} />}
    </div>
  );
}
