import { useState } from "react";
import Button from "./Button";
import OrderTicket from "./OrderTicket";
import ScrollHint from "./ScrollHint";
import { useBroker } from "../hooks/useBroker";
import { formatMoney, formatRelativeTime } from "../lib/format";
import styles from "./BrokerTab.module.css";

const OPEN = new Set(["new", "submitted", "partially_filled"]);

/**
 * The Broker tab of the Trading Bot page: the Alpaca paper account behind
 * the same order path, guardrails, kill switch and audit log as everything
 * else. Shows the connection checks, the ledger's mirror of positions and
 * cash (reconciled with Alpaca), open and recent orders, and a ticket.
 */
export default function BrokerTab() {
  const { data, error, reload, cancel, sync } = useBroker();
  const [ticket, setTicket] = useState(false);
  const [message, setMessage] = useState(null);
  const [busy, setBusy] = useState(false);

  async function act(fn) {
    setBusy(true);
    setMessage(null);
    try {
      setMessage(await fn());
    } catch (e) {
      setMessage(e.message);
    } finally {
      setBusy(false);
    }
  }

  if (!data) return <p className={styles.placeholder}>{error || "Loading the broker…"}</p>;
  if (!data.configured) {
    return (
      <div className={styles.placeholder}>
        <p>No broker is connected. To use Alpaca&apos;s paper account, an admin sets STOCKS_ALPACA_KEY_ID and
          STOCKS_ALPACA_SECRET_KEY (paper keys from alpaca.markets) on the server and restarts it.</p>
      </div>
    );
  }
  if (!data.yours) {
    return <p className={styles.placeholder}>The connected Alpaca account belongs to another user on this server.</p>;
  }

  const m = (v) => formatMoney(v, "USD");
  const recent = data.recent_orders.filter((o) => !OPEN.has(o.status));
  return (
    <div className={styles.root}>
      <section className={styles.section} aria-labelledby="broker-title">
        <div className={styles.head}>
          <h3 id="broker-title" className={styles.title}>Alpaca</h3>
          <span className={styles.mode} data-mode={data.mode}>{data.mode === "paper" ? "Paper" : "LIVE"}</span>
          <div className={styles.actions}>
            <Button size="sm" variant="ghost" disabled={busy}
                    onClick={() => act(async () => {
                      const out = await sync();
                      const n = out.reconcile?.diffs?.length ?? 0;
                      return `Synced: ${out.orders} order update${out.orders === 1 ? "" : "s"}, ${n} difference${n === 1 ? "" : "s"} reconciled.`;
                    })}>
              Sync now
            </Button>
            <Button size="sm" variant="primary" onClick={() => setTicket(true)}>+ New order</Button>
          </div>
        </div>
        {data.error && <p className={styles.error} role="alert">{data.error}</p>}
        {message && <p className={styles.faint} role="status">{message}</p>}
        {data.summary && (
          <dl className={styles.summary}>
            <div><dt>Cash</dt><dd>{m(data.summary.cash)}</dd></div>
            <div><dt>Equity</dt><dd>{m(data.summary.equity)}</dd></div>
            <div><dt>Alpaca buying power</dt><dd>{m(data.summary.buying_power)}</dd></div>
          </dl>
        )}
        <ul className={styles.checks}>
          {data.checks.map((c) => <li key={c.text} data-ok={c.ok ? "yes" : "no"}>{c.ok ? "✓" : "!"} {c.text}</li>)}
        </ul>
        <p className={styles.faint}>
          Orders go through the same guardrails, kill switch and audit log as paper and bots, and buy with cash only.
          Fills sync every minute; positions and cash are reconciled with Alpaca every 15 minutes.
        </p>
      </section>

      <section className={styles.section} aria-labelledby="broker-positions">
        <h3 id="broker-positions" className={styles.title}>Positions</h3>
        {data.positions.length === 0 ? <p className={styles.faint}>None.</p> : (
          <ScrollHint className={styles.wrap}>
            <table className={styles.table}>
              <thead><tr><th>Ticker</th><th className={styles.num}>Shares</th><th className={styles.num}>Avg cost</th><th className={styles.num}>Price</th></tr></thead>
              <tbody>
                {data.positions.map((p) => (
                  <tr key={p.ticker}><td className={styles.ticker}>{p.ticker}</td><td className={styles.num}>{+p.shares.toFixed(4)}</td>
                    <td className={styles.num}>{m(p.avg_cost)}</td><td className={styles.num}>{p.price == null ? "—" : m(p.price)}</td></tr>
                ))}
              </tbody>
            </table>
          </ScrollHint>
        )}
      </section>

      <section className={styles.section} aria-labelledby="broker-orders">
        <h3 id="broker-orders" className={styles.title}>Orders</h3>
        {data.open_orders.length === 0 && recent.length === 0 && <p className={styles.faint}>No orders yet.</p>}
        <ul className={styles.orders}>
          {[...data.open_orders, ...recent].map((o) => (
            <li key={o.id} data-status={o.status}>
              <strong>{o.side} {+o.qty.toFixed(4)} {o.ticker}</strong>
              <span className={styles.faint}>
                {o.type}{o.limit_price ? ` @ ${m(o.limit_price)}` : ""}{o.stop_price ? ` stop ${m(o.stop_price)}` : ""}
                {o.source === "bracket" ? " · bracket exit" : ""}
              </span>
              <span className={styles.status}>
                {o.status === "partially_filled" ? `partially filled ${+o.filled_qty.toFixed(4)}` : o.status}
                {o.fill_price ? ` · ${m(o.fill_price)}` : ""}
              </span>
              <span className={styles.faint}>{o.reason}{o.reason ? " · " : ""}{formatRelativeTime(o.created_at)}</span>
              {OPEN.has(o.status) && o.source !== "bracket" && (
                <Button size="sm" variant="quiet" disabled={busy}
                        onClick={() => act(async () => { await cancel(o.id); return "Cancel sent to Alpaca."; })}>Cancel</Button>
              )}
            </li>
          ))}
        </ul>
      </section>

      {ticket && <OrderTicket account="alpaca" onPlaced={reload} onClose={() => setTicket(false)} />}
    </div>
  );
}
