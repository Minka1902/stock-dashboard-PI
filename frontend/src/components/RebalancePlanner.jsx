import { useEffect, useState } from "react";
import Button from "./Button";
import Field from "./Field";
import PaperTradeButton from "./PaperTradeButton";
import ScrollHint from "./ScrollHint";
import { getRebalance, getTargets, saveTargets } from "../api";
import { formatMoney } from "../lib/format";
import styles from "./RebalancePlanner.module.css";

const blankRow = () => ({ ticker: "", weight: "" });

/**
 * Target weights for an account and the plan back to them: the drift table
 * and the proposed trades (sells first, whole shares, buys scaled to the
 * cash there is). Proposals are never placed; on the paper account each one
 * opens the order ticket, guardrails and all. Props: account, onPlaced.
 */
export default function RebalancePlanner({ account, onPlaced }) {
  const [rows, setRows] = useState([blankRow()]);
  const [band, setBand] = useState("5");
  const [minTrade, setMinTrade] = useState("50");
  const [plan, setPlan] = useState(null);
  const [errors, setErrors] = useState({});
  const [message, setMessage] = useState(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let live = true;
    getTargets(account).then((d) => {
      if (!live) return;
      const r = Object.entries(d.targets).map(([ticker, weight]) => ({ ticker, weight: String(weight) }));
      setRows(r.length ? r : [blankRow()]);
      setPlan(null);
    }).catch(() => {});
    return () => { live = false; };
  }, [account]);

  const total = rows.reduce((s, r) => s + (Number(r.weight) || 0), 0);
  const set = (i, patch) => setRows(rows.map((r, j) => (j === i ? { ...r, ...patch } : r)));

  async function act(fn) {
    setBusy(true);
    setErrors({});
    setMessage(null);
    try {
      await fn();
    } catch (e) {
      setErrors(e.fields || {});
      setMessage(e.fields ? Object.values(e.fields).join("; ") : e.message);
    } finally {
      setBusy(false);
    }
  }

  const save = () => act(async () => {
    const targets = Object.fromEntries(rows.filter((r) => r.ticker && r.weight !== "").map((r) => [r.ticker, Number(r.weight)]));
    await saveTargets(account, targets);
    setMessage("Targets saved.");
  });
  const run = () => act(async () => setPlan(await getRebalance(account, Number(band), Number(minTrade))));
  const m = (v) => formatMoney(v, plan?.base_currency || "USD");

  return (
    <div className={styles.root}>
      <div className={styles.targets}>
        {rows.map((r, i) => (
          <div key={i} className={styles.targetRow}>
            <Field label={i === 0 ? "Ticker" : null} error={errors[r.ticker]}>
              {(a) => <input {...a} aria-label={`Target ${i + 1} ticker`} value={r.ticker} size={8}
                             onChange={(e) => set(i, { ticker: e.target.value.toUpperCase().trim() })} />}
            </Field>
            <Field label={i === 0 ? "Target" : null} suffix="%">
              {(a) => <input {...a} aria-label={`Target ${i + 1} weight`} type="number" min="0" max="100" step="any"
                             value={r.weight} onChange={(e) => set(i, { weight: e.target.value })} />}
            </Field>
            <Button size="sm" variant="quiet" aria-label={`Remove target ${i + 1}`}
                    onClick={() => setRows(rows.length > 1 ? rows.filter((_, j) => j !== i) : [blankRow()])}>×</Button>
          </div>
        ))}
        <div className={styles.actions}>
          <Button size="sm" variant="quiet" onClick={() => setRows([...rows, blankRow()])}>+ Ticker</Button>
          <span className={total > 100 ? styles.error : styles.faint}>{total.toFixed(1)}% targeted · {(100 - total).toFixed(1)}% stays cash</span>
          <Button size="sm" variant="ghost" onClick={save} disabled={busy}>Save targets</Button>
        </div>
      </div>
      <div className={styles.actions}>
        <Field label="Band" suffix="points" inline>
          {(a) => <input {...a} type="number" min="0.5" max="50" step="0.5" size={4} value={band} onChange={(e) => setBand(e.target.value)} />}
        </Field>
        <Field label="Smallest trade" suffix="base ccy" inline>
          {(a) => <input {...a} type="number" min="0" step="any" size={6} value={minTrade} onChange={(e) => setMinTrade(e.target.value)} />}
        </Field>
        <Button size="sm" variant="primary" onClick={run} disabled={busy}>Plan the rebalance</Button>
      </div>
      {message && <p className={Object.keys(errors).length || !message.startsWith("Targets") ? styles.error : styles.ok} role="status">{message}</p>}
      {plan && (
        <>
          <p className={styles.faint}>
            Account {m(plan.account_value)} · cash {m(plan.cash)} → {m(plan.cash_after)} after the trades · band {plan.band} points
          </p>
          <ScrollHint className={styles.wrap}>
            <table className={styles.table}>
              <thead>
                <tr><th>Ticker</th><th className={styles.num}>Now</th><th className={styles.num}>Target</th><th className={styles.num}>Drift</th><th>Action</th></tr>
              </thead>
              <tbody>
                {plan.rows.map((r) => (
                  <tr key={r.ticker}>
                    <td className={styles.ticker}>{r.ticker}</td>
                    <td className={styles.num}>{r.current_pct.toFixed(1)}%</td>
                    <td className={styles.num}>{r.target_pct == null ? "—" : `${r.target_pct}%`}</td>
                    <td className={styles.num}>{r.drift == null ? "—" : `${r.drift > 0 ? "+" : ""}${r.drift.toFixed(1)}`}</td>
                    <td className={styles.faint}>{r.action}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </ScrollHint>
          {plan.orders.length === 0 ? <p className={styles.faint}>Nothing is outside the band.</p> : (
            <ul className={styles.orders}>
              {plan.orders.map((o) => (
                <li key={o.ticker}>
                  <strong data-side={o.side}>{o.side === "BUY" ? "Buy" : "Sell"} {o.qty} {o.ticker}</strong>
                  <span className={styles.faint}>≈ {m(o.value)} · {o.why}</span>
                  {account === "paper" && (
                    <PaperTradeButton ticker={o.ticker} side={o.side} qty={o.qty} className={styles.link}
                                      label="Open in the ticket" onPlaced={onPlaced} />
                  )}
                </li>
              ))}
            </ul>
          )}
          {plan.notes.map((n) => <p key={n} className={styles.faint}>{n}</p>)}
          {account !== "paper" && plan.orders.length > 0 && (
            <p className={styles.faint}>Your manual account is tracked by hand: place these at your broker, then record the fills.</p>
          )}
        </>
      )}
    </div>
  );
}
