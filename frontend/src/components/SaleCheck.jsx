import { useState } from "react";
import Button from "./Button";
import Field from "./Field";
import SelectMenu from "./SelectMenu";
import { checkSale } from "../api";
import { formatDate, formatMoney } from "../lib/format";
import styles from "./SaleCheck.module.css";

const METHODS = [
  { value: "fifo", label: "First in, first out" },
  { value: "hifo", label: "Highest cost first" },
  { value: "specific", label: "Specific lots" },
];

/**
 * What a sale would realize before you sell: the lots it uses by the method
 * you pick, the gain split short / long term, and any wash-sale exposure.
 * Nothing is sold. Props: account, positions (from GET /api/portfolio/lots),
 * preset ({ticker, qty, lotIds}) to start from — e.g. a harvest candidate.
 */
export default function SaleCheck({ account, positions, preset = null }) {
  const [ticker, setTicker] = useState(preset?.ticker || positions[0]?.ticker || "");
  const [qty, setQty] = useState(preset ? String(preset.qty) : "");
  const [method, setMethod] = useState(preset?.lotIds ? "specific" : "fifo");
  const [lotIds, setLotIds] = useState(preset?.lotIds || []);
  const [result, setResult] = useState(null);
  const [errors, setErrors] = useState({});
  const [busy, setBusy] = useState(false);

  const position = positions.find((p) => p.ticker === ticker);
  const m = (v) => formatMoney(v, position?.currency || "USD");

  async function run(e) {
    e.preventDefault();
    setBusy(true);
    setErrors({});
    try {
      setResult(await checkSale({ account, ticker, qty: Number(qty), method, lot_ids: method === "specific" ? lotIds : [] }));
    } catch (err) {
      setErrors(err.fields || { qty: err.message });
      setResult(null);
    } finally {
      setBusy(false);
    }
  }

  if (!positions.length) return null;
  return (
    <form className={styles.root} onSubmit={run}>
      <div className={styles.row}>
        <Field label="Ticker">
          {() => <SelectMenu label="Ticker" value={ticker} options={positions.map((p) => ({ value: p.ticker, label: p.ticker }))}
                             onChange={(v) => { setTicker(v); setLotIds([]); setResult(null); }} />}
        </Field>
        <Field label="Shares" error={errors.qty} hint={position ? `${+position.qty.toFixed(4)} held` : null}>
          {(a) => <input {...a} type="number" min="0" step="any" inputMode="decimal" value={qty}
                         onChange={(e) => setQty(e.target.value)} />}
        </Field>
        <Field label="Lots sold">
          {() => <SelectMenu label="Lots sold" value={method} options={METHODS} onChange={setMethod} />}
        </Field>
        <Button type="submit" size="sm" variant="primary" disabled={busy || !(Number(qty) > 0)}>
          {busy ? "Checking…" : "Check the sale"}
        </Button>
      </div>
      {method === "specific" && position && (
        <fieldset className={styles.lots}>
          <legend>Lots to sell from</legend>
          {position.lots.map((l) => (
            <label key={l.id} className={styles.check}>
              <input type="checkbox" checked={lotIds.includes(l.id)}
                     onChange={(e) => setLotIds(e.target.checked ? [...lotIds, l.id] : lotIds.filter((x) => x !== l.id))} />
              {l.id} · {+l.qty.toFixed(4)} @ {m(l.per_share)} · {formatDate(l.acquired)}
            </label>
          ))}
          {errors.lot_ids && <p className={styles.error}>{errors.lot_ids}</p>}
        </fieldset>
      )}
      {result && (
        <div className={styles.result} aria-live="polite">
          <p>
            Selling {+Number(qty).toFixed(4)} {result.ticker} at {m(result.price)} realizes{" "}
            <strong data-tone={result.gain >= 0 ? "up" : "down"}>{m(result.gain)}</strong>: short term {m(result.short)},
            long term {m(result.long)}.
          </p>
          <ul className={styles.used}>
            {result.lots.map((u) => (
              <li key={u.id}>{u.id}{u.exact ? "" : " ≈"} · {+u.qty.toFixed(4)} bought {formatDate(u.acquired)} · {m(u.gain)} {u.term}</li>
            ))}
          </ul>
          {result.wash.length > 0 && (
            <p className={styles.wash}>
              Wash sale: {result.wash.map((w) => `${+w.qty.toFixed(4)} ${w.ticker} bought ${w.acquired}`).join(", ")} —{" "}
              {m(result.disallowed)} of the loss would be disallowed.
            </p>
          )}
          {result.rebuy_on_or_after && (
            <p className={styles.faint}>To keep the loss, don&apos;t buy {result.ticker} back before {result.rebuy_on_or_after}.</p>
          )}
          <p className={styles.faint}>{result.note}</p>
        </div>
      )}
    </form>
  );
}
