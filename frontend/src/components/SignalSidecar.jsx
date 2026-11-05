import { formatCount } from "../lib/format";
import InfoTip from "./InfoTip";
import Term from "./Term";
import styles from "./SignalSidecar.module.css";
import Tooltip from "./Tooltip";

// BoomScore boolean flags → short human labels, matching the digest's wording.
const BULLISH = {
  golden_cross: "golden cross",
  near_52w_high: "near 52w high",
  macd_crossover: "MACD crossover",
  volume_confirmed: "volume breakout",
  insider_cluster_buy: "insider buying",
  congress_buy: "congress buying",
  analyst_upgrade: "analyst upgrade",
  short_squeeze: "squeeze setup",
  wsb_rising: "WSB momentum",
  rsi_recovery: "RSI recovery",
  fear_greed_contrarian: "fear contrarian",
  yield_uninversion: "curve normalizing",
  contracts_catalyst: "gov contract",
  seasonal_tailwind: "seasonal tailwind",
};
const BEARISH = {
  death_cross: "death cross",
  insider_cluster_sell: "insider selling",
  congress_sale: "congress selling",
  overbought_rsi: "overbought RSI",
  analyst_downgrade_cluster: "analyst downgrades",
  extreme_greed: "market euphoria",
  margin_debt_euphoria: "margin euphoria",
  aaii_bullish_euphoria: "retail euphoria",
};

function fired(obj, labels) {
  if (!obj) return [];
  return Object.entries(labels).filter(([k]) => obj[k]).map(([, v]) => v);
}

function num(v, d = 2) {
  return v == null ? "—" : Number(v).toFixed(d);
}

function pct(v, d = 1) {
  return v == null ? "—" : `${(v * 100).toFixed(d)}%`;
}

function Block({ title, hint, children }) {
  return (
    <section className={styles.block}>
      <div className={styles.blockHead}>
        <h3 className={styles.blockTitle}>{title}</h3>
        {hint && <span className={styles.blockHint}>{hint}</span>}
      </div>
      {children}
    </section>
  );
}

function Row({ label, value, tone }) {
  return (
    <div className={styles.row}>
      <span className={styles.rowLabel}>{label}</span>
      <Tooltip truncate><span className={styles.rowValue} data-tone={tone}>{value}</span></Tooltip>
    </div>
  );
}

function Missing({ what }) {
  return <p className={styles.missing}>No {what} stored for this ticker.</p>;
}

/**
 * The column beside the chart: the app's per-stock signals in one place —
 * composite score and what drove it, technicals, the analyst read, positioning,
 * and the trade plan. Every value is stored data; anything absent says so
 * rather than rendering a zero.
 */
export default function SignalSidecar({ signals, analysis }) {
  const boom = signals?.boom || null;
  const tech = signals?.technical || null;
  const analyst = signals?.analyst || null;
  const social = signals?.social || null;
  const short = signals?.short_interest || null;

  const bull = fired(boom, BULLISH);
  const bear = fired(boom, BEARISH);
  const scoreTone = boom == null ? "" : boom.score >= 40 ? "pos" : boom.score <= -20 ? "neg" : "";

  return (
    <aside className={styles.sidecar} aria-label="Signals">
      <Block title={<>Boom score <InfoTip term="boom_score" size={14} /></>} hint="composite">
        {boom ? (
          <>
            <div className={styles.score} data-tone={scoreTone}>
              {boom.score}
              <span className={styles.scoreScale}>/ 100</span>
            </div>
            {boom.mixed_signals && (
              <p className={styles.mixed}>Mixed — bullish and bearish signals both firing.</p>
            )}
            <div className={styles.chips}>
              {bull.map((s) => <span key={s} className={styles.chip} data-tone="pos">{s}</span>)}
              {bear.map((s) => <span key={s} className={styles.chip} data-tone="neg">{s}</span>)}
              {bull.length === 0 && bear.length === 0 && (
                <span className={styles.chip}>no components firing</span>
              )}
            </div>
          </>
        ) : <Missing what="boom score" />}
      </Block>

      <Block title="Technicals">
        {tech ? (
          <div className={styles.rows}>
            <Row label="Price" value={num(tech.price)} />
            <Row
              label="Change"
              value={tech.change_pct == null ? "—" : `${tech.change_pct >= 0 ? "+" : ""}${tech.change_pct.toFixed(2)}%`}
              tone={tech.change_pct == null ? "" : tech.change_pct >= 0 ? "pos" : "neg"}
            />
            <Row label={<Term term="rsi" tip="Computed over 14 days.">RSI 14</Term>} value={num(tech.rsi14, 1)}
                 tone={tech.rsi14 == null ? "" : tech.rsi14 >= 70 ? "neg" : tech.rsi14 <= 30 ? "pos" : ""} />
            <Row label={<Term term="macd">MACD</Term>} value={num(tech.macd, 3)}
                 tone={tech.macd_crossover ? "pos" : ""} />
            <Row label={<Term term="moving_average">MA 50 / 200</Term>} value={`${num(tech.ma50)} / ${num(tech.ma200)}`}
                 tone={tech.golden_cross ? "pos" : ""} />
            <Row label="52w range" value={`${num(tech.low_52w)} – ${num(tech.high_52w)}`} />
            <Row label={<Term term="relative_volume">Rel. volume</Term>} value={tech.rel_volume == null ? "—" : `${tech.rel_volume.toFixed(2)}×`} />
          </div>
        ) : <Missing what="technicals" />}
      </Block>

      <Block title="Analyst">
        {analyst ? (
          <div className={styles.rows}>
            <Row label="Strong buy / buy" value={`${analyst.rec_strong_buy ?? 0} / ${analyst.rec_buy ?? 0}`} tone="pos" />
            <Row label="Hold / sell" value={`${analyst.rec_hold ?? 0} / ${analyst.rec_sell ?? 0}`}
                 tone={(analyst.rec_sell ?? 0) > 0 ? "neg" : ""} />
            <Row label="Upgrades (recent)" value={analyst.recent_upgrades ?? 0} tone="pos" />
            <Row label="Downgrades (recent)" value={analyst.recent_downgrades ?? 0} tone="neg" />
            <Row label="Next earnings" value={analyst.next_earnings || "—"} />
            {analyst.latest_firm && (
              <Row
                label="Latest action"
                value={`${analyst.latest_firm}${analyst.latest_to_grade ? ` → ${analyst.latest_to_grade}` : ""}`}
              />
            )}
          </div>
        ) : <Missing what="analyst coverage" />}
      </Block>

      <Block title="Positioning" hint="short interest · social">
        {short || social ? (
          <div className={styles.rows}>
            <Row label={<Term term="short_interest">Short % float</Term>} value={pct(short?.short_pct_float)}
                 tone={short?.squeeze_flag ? "pos" : ""} />
            <Row label={<Term term="days_to_cover">Days to cover</Term>} value={num(short?.days_to_cover, 2)} />
            <Row label="Shares short" value={short?.shares_short != null ? formatCount(short.shares_short) : "—"} />
            <Row label="WSB mentions" value={social?.mentions != null ? formatCount(social.mentions) : "—"} />
            <Row label={<Term term="wsb_rank">WSB rank</Term>} value={social?.rank != null ? `#${social.rank}` : "—"} />
          </div>
        ) : <Missing what="short interest or social data" />}
      </Block>

      <Block title="Trade plan"
             hint={analysis?.rr != null ? <Term term="r_multiple">R/R {analysis.rr}</Term> : undefined}>
        {analysis && analysis.entry != null ? (
          <div className={styles.rows}>
            <Row label="Entry" value={num(analysis.entry)} />
            <Row label="Stop" value={num(analysis.stop)} tone="neg" />
            <Row label="Target" value={num(analysis.target)} tone="pos" />
            <Row label={<Term tip="Entry minus stop: what one share loses if the stop is hit. This is one R.">Risk / share</Term>}
                 value={analysis.entry != null && analysis.stop != null
                   ? num(analysis.entry - analysis.stop) : "—"} />
            {analysis.shares != null && <Row label="Shares (sized)" value={formatCount(analysis.shares)} />}
          </div>
        ) : <Missing what="trade plan — no valid stop below price yet" />}
      </Block>
    </aside>
  );
}
