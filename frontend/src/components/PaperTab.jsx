import { useState } from "react";
import Button from "./Button";
import ConfirmDialog from "./ConfirmDialog";
import ExitRulesTable from "./ExitRulesTable";
import Field from "./Field";
import MetricTiles from "./MetricTiles";
import OrderTicket from "./OrderTicket";
import PaperModeBar from "./PaperModeBar";
import SelectMenu from "./SelectMenu";
import { usePaper } from "../hooks/usePaper";
import { openTickerTab } from "../lib/nav";
import { formatMoney, formatRelativeTime } from "../lib/format";
import styles from "./PaperTab.module.css";

const CURRENCIES = [{ value: "USD", label: "USD" }, { value: "ILS", label: "ILS" }];

const orderLevel = (o) => (o.type === "limit" ? ` @ ${o.limit_price}` : o.type === "stop" ? ` stop ${o.stop_price}` : "");

/**
 * The Paper tab of the Trading Bot page: a simulated account you trade by
 * hand through the order ticket — cash, positions at live prices, resting
 * orders (cancel), the fill history with measured slippage, exit rules on
 * paper positions, and funding / starting over.
 */
export default function PaperTab() {
  const { data, error, reload, reset, cancel } = usePaper();
  const [ticket, setTicket] = useState(false);
  const [amount, setAmount] = useState(null);
  const [ccy, setCcy] = useState(null);
  const [resetting, setResetting] = useState(false);
  const [notice, setNotice] = useState(null);

  if (error && !data) return <p className={styles.error} role="alert">{error}</p>;
  if (!data) return <p className={styles.placeholder}>Loading the paper account…</p>;

  const fundAmount = amount ?? String(data.defaults.amount);
  const fundCcy = ccy ?? data.defaults.currency;
  // Positions at the live price (at cost when there is none), per currency.
  const invested = data.positions.reduce((acc, p) => ({
    ...acc, [p.currency]: (acc[p.currency] || 0) + (p.value ?? p.shares * p.avg_cost) }), {});

  async function doReset() {
    await reset({ amount: Number(fundAmount), currency: fundCcy });
    setAmount(null);
  }

  async function doCancel(id) {
    try {
      await cancel(id);
      setNotice(null);
    } catch (e) {
      setNotice(e.message);
    }
  }

  const fundForm = (
    <div className={styles.fund}>
      <Field label="Amount" inline>
        {(a) => <input {...a} type="number" min="1" step="any" value={fundAmount}
                       onChange={(e) => setAmount(e.target.value)} />}
      </Field>
      <SelectMenu label="Currency" value={fundCcy} options={CURRENCIES} onChange={setCcy} className={styles.ccy} />
      <Button variant={data.funded ? "danger" : "primary"} size="sm"
              onClick={() => (data.funded ? setResetting(true) : doReset())}>
        {data.funded ? "Start over…" : "Fund the paper account"}
      </Button>
    </div>
  );

  return (
    <div className={styles.root}>
      <PaperModeBar modes={data.modes} />

      {!data.funded ? (
        <section className={styles.section}>
          <p className={styles.text}>
            Practise with simulated cash: tickets fill at live quotes moved {data.cost_bps_per_side} bps against
            you, only while the market is open. Your real portfolio is never touched.
          </p>
          {fundForm}
        </section>
      ) : (
        <>
          <section className={styles.section}>
            <div className={styles.head}>
              <MetricTiles items={[
                ...data.cash.map((c) => ({ key: `cash-${c.currency}`, label: `Cash ${c.currency}`, value: formatMoney(c.balance, c.currency) })),
                ...Object.entries(invested).map(([c, v]) => ({ key: `inv-${c}`, label: `Invested ${c}`, value: formatMoney(v, c) })),
                { key: "open", label: "Open orders", value: String(data.open_orders.length) },
                { key: "slip", label: "Median slippage", value: data.slippage.median_bps == null ? null : `${data.slippage.median_bps} bps`,
                  tip: `Fill vs the price the ticket was opened at, over ${data.slippage.n} fills. Paper fills are simulated by the cost model, so this only confirms it.` },
              ]} />
              <Button variant="primary" onClick={() => setTicket(true)}>+ New order</Button>
            </div>
            {notice && <p className={styles.error} role="alert">{notice}</p>}
          </section>

          <section className={styles.section} aria-labelledby="paper-pos">
            <h3 id="paper-pos" className={styles.title}>Positions</h3>
            {data.positions.length === 0 ? <p className={styles.text}>No positions yet.</p> : (
              <ul className={styles.list}>
                {data.positions.map((p) => (
                  <li key={p.ticker} className={styles.row}>
                    <button type="button" className={styles.symbol} onClick={() => openTickerTab(p.ticker)}>{p.ticker}</button>
                    <span>{p.shares} @ {formatMoney(p.avg_cost, p.currency)}</span>
                    <span>{p.price == null ? "no live price" : formatMoney(p.price, p.currency)}</span>
                    <span data-tone={p.pl_pct == null ? "flat" : p.pl_pct >= 0 ? "pos" : "neg"}>
                      {p.pl_pct == null ? "—" : `${p.pl_pct > 0 ? "+" : ""}${p.pl_pct}%`}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </section>

          <section className={styles.section} aria-labelledby="paper-open">
            <h3 id="paper-open" className={styles.title}>Open orders</h3>
            {data.open_orders.length === 0 ? <p className={styles.text}>Nothing resting.</p> : (
              <ul className={styles.list}>
                {data.open_orders.map((o) => (
                  <li key={o.id} className={styles.row}>
                    <span className={styles.symbol}>{o.ticker}</span>
                    <span>{o.side} {o.qty} · {o.type}{orderLevel(o)} · {o.tif.toUpperCase()}</span>
                    <span className={styles.faint}>{formatRelativeTime(o.created_at)}</span>
                    <Button size="sm" variant="quiet" onClick={() => doCancel(o.id)}>Cancel</Button>
                  </li>
                ))}
              </ul>
            )}
          </section>

          <section className={styles.section} aria-labelledby="paper-hist">
            <h3 id="paper-hist" className={styles.title}>Order history</h3>
            {data.recent_orders.filter((o) => o.status !== "submitted").length === 0
              ? <p className={styles.text}>No filled or closed orders yet.</p> : (
              <ul className={styles.list}>
                {data.recent_orders.filter((o) => o.status !== "submitted").map((o) => (
                  <li key={o.id} className={styles.row}>
                    <span className={styles.symbol}>{o.ticker}</span>
                    <span>{o.side} {o.qty} · {o.type}{orderLevel(o)}</span>
                    <span className={styles.status} data-status={o.status}>{o.status}</span>
                    <span className={styles.faint}>
                      {o.status === "filled"
                        ? `at ${formatMoney(o.fill_price, o.currency)}${o.slippage_bps != null ? ` · ${o.slippage_bps} bps` : ""}`
                        : o.reason}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </section>

          <ExitRulesTable account="paper" title="Exit rules · Paper"
                          emptyText="No paper positions yet — place an order above." />

          <section className={styles.section} aria-labelledby="paper-reset">
            <h3 id="paper-reset" className={styles.title}>Start over</h3>
            {fundForm}
          </section>
        </>
      )}

      {ticket && <OrderTicket onPlaced={reload} onClose={() => { setTicket(false); reload(); }} />}
      <ConfirmDialog open={resetting} tone="negative" caption="Paper account" title="Start the paper account over?"
                     confirmLabel="Start over" onConfirm={doReset} onClose={() => setResetting(false)}>
        <p>
          Open orders are cancelled, positions and cash are cleared, and {fundAmount} {fundCcy} is deposited.
          The history stays in the ledger.
        </p>
      </ConfirmDialog>
    </div>
  );
}
