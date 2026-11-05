import { useEffect, useState } from "react";
import BotCard from "./BotCard";
import Button from "./Button";
import ConfirmDialog from "./ConfirmDialog";
import Icon from "./Icon";
import NewBotDialog from "./NewBotDialog";
import { useBots } from "../hooks/useBots";
import { getWatchlists } from "../api";
import { formatMoney, formatMoneySigned, formatPercentSigned } from "../lib/format";
import styles from "./BotsTab.module.css";

// Running (and paused) bots, totalled per currency — bots hold one currency
// each, and nothing here converts between them.
function totalsByCurrency(bots) {
  const map = new Map();
  for (const b of bots) {
    const t = map.get(b.currency) || { currency: b.currency, allocated: 0, equity: 0, count: 0 };
    t.allocated += b.allocated;
    t.equity += b.equity;
    t.count += 1;
    map.set(b.currency, t);
  }
  return [...map.values()];
}

/**
 * The Bots tab of the Trading Bot page: demo bots, each running one preset
 * strategy with cash set aside from the Portfolio. Props: cash (Portfolio cash
 * balances), onCashChanged (reload them after a bot takes or returns cash).
 */
export default function BotsTab({ cash = [], onCashChanged }) {
  const { bots, strategies, costBps, loading, error, create, pause, resume, stop, stopAll } =
    useBots(onCashChanged);
  const [watchlists, setWatchlists] = useState([]);
  const [newOpen, setNewOpen] = useState(false);
  const [stopping, setStopping] = useState(null); // a bot, or "all"
  const [busyId, setBusyId] = useState(null);
  const [notice, setNotice] = useState(null);

  useEffect(() => {
    getWatchlists().then(setWatchlists).catch(() => {});
  }, []);

  const running = bots.filter((b) => b.status !== "stopped");
  const stopped = bots.filter((b) => b.status === "stopped");
  const totals = totalsByCurrency(running);
  const specOf = (key) => strategies.find((s) => s.key === key);

  async function withBusy(bot, fn) {
    setBusyId(bot.id);
    try {
      await fn(bot.id);
      setNotice(null);
    } catch (e) {
      setNotice(e.message);
    } finally {
      setBusyId(null);
    }
  }

  async function confirmStop() {
    if (stopping === "all") {
      const out = await stopAll();
      const failed = Object.entries(out.failed || {});
      setNotice(failed.length ? `Not stopped: ${failed.map(([n, why]) => `${n} (${why})`).join("; ")}` : null);
    } else {
      await stop(stopping.id);
    }
  }

  return (
    <div className={styles.root}>
      <div className={styles.bar}>
        <p className={styles.demo} role="note">
          <Icon name="info" size={14} />
          <span>
            <strong>DEMO</strong> — simulated fills at the live price
            {costBps != null ? ` ± ${costBps} bps` : ""}, only while the stock's market is open.
            No real orders are ever placed.
          </span>
        </p>
        <div className={styles.barActions}>
          {running.length > 0 && (
            <Button variant="danger" onClick={() => setStopping("all")}>Stop all</Button>
          )}
          <Button variant="primary" onClick={() => setNewOpen(true)} disabled={!strategies.length}>
            + New bot
          </Button>
        </div>
      </div>

      {totals.length > 0 && (
        <dl className={styles.totals}>
          {totals.map((t) => {
            const pnl = t.equity - t.allocated;
            return (
              <div key={t.currency} className={styles.total}>
                <dt>{t.count} bot{t.count === 1 ? "" : "s"} · {t.currency}</dt>
                <dd>
                  {formatMoney(t.equity, t.currency)}
                  <span className={styles.totalSub}> of {formatMoney(t.allocated, t.currency)} allocated</span>
                </dd>
                <dd data-tone={pnl > 0 ? "pos" : pnl < 0 ? "neg" : "flat"}>
                  {formatMoneySigned(pnl, t.currency)} {formatPercentSigned(t.allocated ? pnl / t.allocated : 0, 2)}
                </dd>
              </div>
            );
          })}
        </dl>
      )}

      {(error || notice) && <p className={styles.error} role="alert">{notice || error}</p>}

      {loading ? (
        <p className={styles.placeholder}>Loading bots…</p>
      ) : running.length === 0 ? (
        <div className={styles.empty}>
          <span className={styles.emptyIcon}><Icon name="bot" size={24} /></span>
          <p className={styles.emptyTitle}>No bots running</p>
          <p className={styles.emptyText}>
            Start one with part of your Portfolio cash: follow the app's suggestions, ride a
            trend, buy dips, or catch breakouts. Stop it any time to get the cash back.
          </p>
          {cash.some((c) => c.balance > 0)
            ? <Button variant="primary" onClick={() => setNewOpen(true)} disabled={!strategies.length}>+ New bot</Button>
            : <p className={styles.emptyText}>First add cash on the Portfolio page.</p>}
        </div>
      ) : (
        <div className={styles.grid}>
          {running.map((b) => (
            <BotCard key={b.id} bot={b} spec={specOf(b.strategy)} busy={busyId === b.id}
                     onPause={(bot) => withBusy(bot, pause)}
                     onResume={(bot) => withBusy(bot, resume)}
                     onStop={setStopping} />
          ))}
        </div>
      )}

      {stopped.length > 0 && (
        <details className={styles.history}>
          <summary>Stopped bots ({stopped.length})</summary>
          <div className={styles.grid}>
            {stopped.map((b) => <BotCard key={b.id} bot={b} spec={specOf(b.strategy)} />)}
          </div>
        </details>
      )}

      {newOpen && (
        <NewBotDialog open strategies={strategies} cash={cash} watchlists={watchlists}
                      onCreate={create} onClose={() => setNewOpen(false)} />
      )}
      <ConfirmDialog
        open={stopping != null}
        tone="negative"
        caption="Demo bot"
        title={stopping === "all" ? "Stop every bot?" : `Stop ${stopping?.name}?`}
        confirmLabel={stopping === "all" ? "Stop all bots" : "Stop bot"}
        onConfirm={confirmStop}
        onClose={() => setStopping(null)}
      >
        <p>
          {stopping === "all" ? "Each bot's" : "Its"} open positions are closed at the live price
          (or the last close if there's no quote), and all of {stopping === "all" ? "their" : "its"} cash
          goes back to your Portfolio. This can't be undone; you can start a new bot afterwards.
        </p>
      </ConfirmDialog>
    </div>
  );
}
