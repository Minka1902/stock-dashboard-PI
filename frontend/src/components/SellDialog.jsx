import { useState } from "react";
import Button from "./Button";
import Dialog from "./Dialog";
import Field from "./Field";
import { formatMoney, formatMoneySigned } from "../lib/format";
import styles from "./SellDialog.module.css";

/**
 * Record a (partial) sale of a held position. Shares default to the whole
 * position and price to the live quote; the realized P&L preview uses the
 * position's average cost, exactly as the ledger will.
 *
 * Props: holding {ticker, shares, avg_cost, currency}, price (live, in the
 * holding's currency, may be null), hasCash (default "credit to cash" on),
 * onSubmit({shares, price, fees, toCash}), onClose.
 */
export default function SellDialog({ holding, price, hasCash, onSubmit, onClose }) {
  const [shares, setShares] = useState(String(holding.shares));
  const [px, setPx] = useState(price != null ? String(+price.toFixed(4)) : "");
  const [fees, setFees] = useState("");
  const [toCash, setToCash] = useState(hasCash);
  const [error, setError] = useState(null);
  const [pending, setPending] = useState(false);

  const ccy = holding.currency || "USD";
  const qty = parseFloat(shares);
  const p = parseFloat(px);
  const f = parseFloat(fees) || 0;
  const valid = qty > 0 && qty <= holding.shares + 1e-9 && p >= 0 && f >= 0;
  const realized = valid ? (p - holding.avg_cost) * qty - f : null;

  async function submit(e) {
    e.preventDefault();
    if (!valid) {
      setError(qty > holding.shares
        ? `You hold ${holding.shares} ${holding.ticker}.`
        : "Enter positive shares and a price of 0 or more.");
      return;
    }
    setPending(true);
    try {
      await onSubmit({ shares: qty, price: p, fees: f, toCash });
      onClose();
    } catch (err) {
      setError(err.message);
      setPending(false);
    }
  }

  return (
    <Dialog
      open
      size="sm"
      caption="Record a sale"
      title={`Sell ${holding.ticker}`}
      onClose={onClose}
      footer={(
        <>
          <Button variant="quiet" onClick={onClose}>Cancel</Button>
          <Button variant="primary" type="submit" form="sell-form" disabled={pending}>
            {pending ? "Recording…" : "Record sale"}
          </Button>
        </>
      )}
    >
      <form id="sell-form" className={styles.form} onSubmit={submit}>
        <Field label="Shares" hint={`of ${holding.shares} held`}>
          {(a) => <input {...a} type="number" min="0" step="any" value={shares}
                         onChange={(e) => setShares(e.target.value)} />}
        </Field>
        <Field label={`Price (${ccy})`} hint={price == null ? "no live quote — enter your fill" : "live quote; change it to your fill"}>
          {(a) => <input {...a} type="number" min="0" step="any" value={px}
                         onChange={(e) => setPx(e.target.value)} />}
        </Field>
        <Field label={`Fees (${ccy})`}>
          {(a) => <input {...a} type="number" min="0" step="any" value={fees} placeholder="0"
                         onChange={(e) => setFees(e.target.value)} />}
        </Field>
        <label className={styles.check}>
          <input type="checkbox" checked={toCash} onChange={(e) => setToCash(e.target.checked)} />
          Credit the proceeds to {ccy} cash
        </label>
        <p className={styles.preview} data-tone={realized == null ? "flat" : realized >= 0 ? "pos" : "neg"}>
          Realized P/L {realized == null ? "—" : formatMoneySigned(realized, ccy)}
          <span className={styles.basis}> vs avg cost {formatMoney(holding.avg_cost, ccy)}</span>
        </p>
        {error && <p className={styles.error} role="alert">{error}</p>}
      </form>
    </Dialog>
  );
}
