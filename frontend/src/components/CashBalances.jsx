import { useState } from "react";
import Button from "./Button";
import Segmented from "./Segmented";
import SelectMenu from "./SelectMenu";
import Tooltip from "./Tooltip";
import { currencySymbol, formatMoney } from "../lib/format";
import { convertCash } from "../lib/portfolioMath";
import styles from "./CashBalances.module.css";

const MODES = [
  { value: "deposit", label: "Deposit" },
  { value: "withdraw", label: "Withdraw" },
  { value: "set", label: "Set to" },
];

/**
 * Cash held per currency, with deposit / withdraw / set-balance controls.
 * Every change is a ledger row (a "set" records the gap as a deposit or a
 * withdrawal), so the balance always reconciles with the history.
 *
 * Props: balances [{currency, balance}], base, rates (into base), currencies
 * offered for a new balance, onMove(ccy, amount, side), onSet(ccy, balance),
 * readOnly (hide the controls).
 */
export default function CashBalances({
  balances, base, rates, currencies, onMove, onSet, readOnly = false,
}) {
  const [form, setForm] = useState(null); // { currency, mode, amount, fresh }
  const [error, setError] = useState(null);
  const [pending, setPending] = useState(false);

  const shown = balances.filter((b) => b.balance);
  const { total, excluded } = convertCash(shown, base, rates);
  const unused = currencies.filter((c) => !shown.some((b) => b.currency === c));

  const open = (currency, mode, fresh = false) => {
    setForm({ currency, mode, amount: "", fresh });
    setError(null);
  };

  async function submit(e) {
    e.preventDefault();
    const amount = parseFloat(form.amount);
    if (!(amount >= 0) || (form.mode !== "set" && !(amount > 0))) {
      setError(form.mode === "set" ? "Enter a balance of 0 or more." : "Enter an amount above 0.");
      return;
    }
    setPending(true);
    try {
      if (form.mode === "set") await onSet(form.currency, amount);
      else await onMove(form.currency, amount, form.mode);
      setForm(null);
      setError(null);
    } catch (err) {
      setError(err.message);
    } finally {
      setPending(false);
    }
  }

  return (
    <div className={styles.cash}>
      <div className={styles.head}>
        <span className="caption">Cash</span>
        <span className={styles.total}>
          {shown.length ? formatMoney(total, base) : "—"}
          <span className={styles.base}> {base}</span>
        </span>
        {excluded.length > 0 && (
          <span className={styles.excluded} role="status">
            {excluded.join(", ")} left out of the total: FX unavailable
          </span>
        )}
        {!readOnly && unused.length > 0 && !form?.fresh && (
          <Button size="sm" onClick={() => open(unused.includes(base) ? base : unused[0], "deposit", true)}>
            + Cash
          </Button>
        )}
      </div>

      {shown.length > 0 && (
        <ul className={styles.list}>
          {shown.map((b) => (
            <li key={b.currency} className={styles.row}>
              <span className={styles.ccy}>{currencySymbol(b.currency).trim()} {b.currency}</span>
              <span className={styles.amount}>{formatMoney(b.balance, b.currency)}</span>
              {!readOnly && (
                <span className={styles.actions}>
                  {MODES.map((m) => (
                    <Button key={m.value} size="sm" variant="quiet"
                            onClick={() => open(b.currency, m.value)}
                            aria-label={`${m.label} ${b.currency} cash`}>
                      {m.label}
                    </Button>
                  ))}
                </span>
              )}
            </li>
          ))}
        </ul>
      )}

      {form && (
        <form className={styles.form} onSubmit={submit}>
          {form.fresh ? (
            <SelectMenu label="Cash currency" value={form.currency}
                        options={unused.map((c) => ({ value: c, label: `${currencySymbol(c).trim()} ${c}` }))}
                        onChange={(c) => setForm({ ...form, currency: c })} />
          ) : (
            <span className={styles.ccy}>{form.currency}</span>
          )}
          <Segmented ariaLabel="Cash change" value={form.mode} options={MODES}
                     onChange={(mode) => setForm({ ...form, mode })} />
          <Tooltip content={form.mode === "set"
            ? "The balance becomes exactly this; the difference is recorded as a deposit or withdrawal."
            : "Recorded in the transaction history."}>
            <input className={styles.input} type="number" min="0" step="any" autoFocus
                   placeholder={`Amount ${currencySymbol(form.currency).trim()}`}
                   aria-label={`Amount in ${form.currency}`} value={form.amount}
                   onChange={(e) => setForm({ ...form, amount: e.target.value })}
                   onKeyDown={(e) => { if (e.key === "Escape") setForm(null); }} />
          </Tooltip>
          <Button type="submit" variant="primary" size="sm" disabled={pending}>
            {pending ? "Saving…" : "Save"}
          </Button>
          <Button size="sm" variant="quiet" onClick={() => setForm(null)}>Cancel</Button>
        </form>
      )}
      {error && <p className={styles.error} role="alert">{error}</p>}
    </div>
  );
}
