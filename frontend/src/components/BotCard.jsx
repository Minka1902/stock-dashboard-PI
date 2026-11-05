import { useState } from "react";
import BotActivity from "./BotActivity";
import Button from "./Button";
import CollapseToggle from "./CollapseToggle";
import TickerLabel from "./TickerLabel";
import { openTickerTab } from "../lib/nav";
import { formatMoney, formatMoneySigned, formatPercentSigned, formatRelativeTime } from "../lib/format";
import styles from "./BotCard.module.css";

const STATUS_LABEL = { active: "Running", paused: "Paused", stopped: "Stopped" };
const UNIVERSE_SHOWN = 8;

function levelsOf(p, bot, ccy) {
  return [
    p.stop != null && `stop ${formatMoney(p.stop, ccy)}`,
    p.target != null && `target ${formatMoney(p.target, ccy)}`,
    p.high != null && bot.strategy === "breakout" && `high ${formatMoney(p.high, ccy)}`,
  ].filter(Boolean).join(" · ");
}

function emptyUniverse(bot) {
  if (bot.tickers.length) return `nothing — none of its tickers trade in ${bot.currency}`;
  if (bot.watchlist_id != null) return `nothing — that watchlist has no ${bot.currency} listings`;
  return "nothing yet — the Suggestions pool is empty right now";
}

function paramSummary(spec, params) {
  if (!spec) return "";
  return spec.params
    .map((p) => `${p.label} ${params[p.key]}${p.unit?.startsWith("%") ? "%" : p.unit ? ` ${p.unit.split(" ")[0]}` : ""}`)
    .join(" · ");
}

/**
 * One demo bot: status, allocation vs equity and P&L, its open positions
 * (with the stop/target it will act on), what it watches, and its activity.
 * Props: bot (GET /api/bots item), spec (its strategy from the catalog),
 * onPause/onResume/onStop(bot), busy.
 */
export default function BotCard({ bot, spec, onPause, onResume, onStop, busy = false }) {
  const [showLog, setShowLog] = useState(false);
  const ccy = bot.currency;
  const tone = bot.pnl > 0 ? "pos" : bot.pnl < 0 ? "neg" : "flat";
  const stopped = bot.status === "stopped";
  const universe = bot.universe || [];

  return (
    <article className={styles.card} data-status={bot.status}>
      <header className={styles.head}>
        <div className={styles.identity}>
          <h3 className={styles.name}>{bot.name}</h3>
          <span className={styles.meta}>
            {bot.strategy_label} · started {formatRelativeTime(bot.created_at)}
            {stopped && bot.stopped_at ? ` · stopped ${formatRelativeTime(bot.stopped_at)}` : ""}
          </span>
        </div>
        <span className={styles.status} data-status={bot.status}>{STATUS_LABEL[bot.status]}</span>
      </header>

      <dl className={styles.stats}>
        <div>
          <dt>Allocated</dt>
          <dd>{formatMoney(bot.allocated, ccy)}</dd>
        </div>
        <div>
          <dt>{stopped ? "Returned" : "Equity"}</dt>
          <dd>{formatMoney(bot.equity, ccy)}{!stopped && !bot.equity_priced && <span className={styles.note}> (some at cost)</span>}</dd>
        </div>
        <div>
          <dt>P&amp;L</dt>
          <dd data-tone={tone}>
            {formatMoneySigned(bot.pnl, ccy)}
            {bot.pnl_pct != null && <em> {formatPercentSigned(bot.pnl_pct / 100, 2)}</em>}
          </dd>
        </div>
        {!stopped && (
          <div>
            <dt>Cash</dt>
            <dd>{formatMoney(bot.cash, ccy)}</dd>
          </div>
        )}
      </dl>

      {!stopped && (
        <p className={styles.params}>{paramSummary(spec, bot.params)}</p>
      )}

      {bot.positions.length > 0 && (
        <ul className={styles.positions}>
          {bot.positions.map((p) => (
            <li key={p.ticker} className={styles.position}>
              <button type="button" className={styles.ticker} onClick={() => openTickerTab(p.ticker)}
                      aria-label={`Analyze ${p.ticker} in a new tab`}>
                <TickerLabel ticker={p.ticker} />
              </button>
              <span className={styles.posQty}>{p.shares} @ {formatMoney(p.avg_cost, ccy)}</span>
              <span className={styles.posPl} data-tone={p.pl_pct == null ? "flat" : p.pl_pct >= 0 ? "pos" : "neg"}>
                {p.price != null ? formatMoney(p.price, ccy) : "no price"}
                {p.pl_pct != null && ` ${formatPercentSigned(p.pl_pct / 100, 1)}`}
              </span>
              <span className={styles.levels}>{levelsOf(p, bot, ccy)}</span>
            </li>
          ))}
        </ul>
      )}

      {!stopped && (
        <p className={styles.universe}>
          <span className="caption">Watches</span>{" "}
          {universe.length
            ? <>{universe.slice(0, UNIVERSE_SHOWN).join(", ")}{universe.length > UNIVERSE_SHOWN ? ` +${universe.length - UNIVERSE_SHOWN} more` : ""}</>
            : emptyUniverse(bot)}
        </p>
      )}

      <footer className={styles.foot}>
        <span className={styles.logToggle}>
          <CollapseToggle collapsed={!showLog} onClick={() => setShowLog((s) => !s)}
                          label={`activity of ${bot.name}`} />
          Activity ({bot.events.length}{bot.events.length >= 20 ? "+" : ""})
        </span>
        {!stopped && (
          <span className={styles.actions}>
            {bot.status === "active"
              ? <Button size="sm" onClick={() => onPause(bot)} disabled={busy}>Pause</Button>
              : <Button size="sm" onClick={() => onResume(bot)} disabled={busy}>Resume</Button>}
            <Button size="sm" variant="danger" onClick={() => onStop(bot)} disabled={busy}>Stop</Button>
          </span>
        )}
      </footer>
      {showLog && <BotActivity events={bot.events} currency={ccy} />}
    </article>
  );
}
