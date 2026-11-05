import { useEffect, useState } from "react";
import Button from "./Button";
import Dialog from "./Dialog";
import Field from "./Field";
import Segmented from "./Segmented";
import SelectMenu from "./SelectMenu";
import { getSavedRules } from "../api";
import { formatMoney } from "../lib/format";
import styles from "./NewBotDialog.module.css";

const UNIVERSE_MODES = [
  { value: "tickers", label: "Tickers" },
  { value: "watchlist", label: "Watchlist" },
];
const SUGGESTED = { value: "suggested", label: "Suggestions pool" };

const defaultsOf = (spec) => Object.fromEntries(spec.params.map((p) => [p.key, String(p.default)]));

/**
 * Create a demo bot: pick a preset strategy, tune its parameters, choose what
 * it trades (a ticker list, a watchlist, or — Follow Suggestions only — the
 * Suggestions pool) and how much of your cash it gets. The server validates
 * everything and its field errors show beside each field.
 *
 * Props: open, strategies (GET /api/bots/strategies), cash [{currency,
 * balance}], watchlists [{id, name}], onCreate(body) (async, throws ApiError),
 * onClose.
 */
export default function NewBotDialog({ open, strategies, cash, watchlists, onCreate, onClose }) {
  const funded = cash.filter((c) => c.balance > 0);
  const [strategy, setStrategy] = useState(strategies[0]?.key || "trend");
  const spec = strategies.find((s) => s.key === strategy) || strategies[0];
  const [params, setParams] = useState(() => (spec ? defaultsOf(spec) : {}));
  const [name, setName] = useState("");
  const [mode, setMode] = useState(spec?.needs_tickers === false ? "suggested" : "tickers");
  const [tickers, setTickers] = useState("");
  const [watchlistId, setWatchlistId] = useState(watchlists[0]?.id ?? null);
  const [ccy, setCcy] = useState(funded[0]?.currency || "USD");
  const [amount, setAmount] = useState("");
  const [errors, setErrors] = useState({});
  const [formError, setFormError] = useState(null);
  const [pending, setPending] = useState(false);
  const [savedRules, setSavedRules] = useState([]);

  useEffect(() => {
    getSavedRules().then((d) => setSavedRules(d.rules)).catch(() => {});
  }, []);

  const available = cash.find((c) => c.currency === ccy)?.balance || 0;
  const modes = spec?.needs_tickers ? UNIVERSE_MODES : [SUGGESTED, ...UNIVERSE_MODES];
  const activeMode = modes.some((m) => m.value === mode) ? mode : modes[0].value;

  function pickStrategy(key) {
    const next = strategies.find((s) => s.key === key);
    setStrategy(key);
    setParams(defaultsOf(next));
    setMode(next.needs_tickers ? "tickers" : "suggested");
    setErrors({});
  }

  async function submit(e) {
    e.preventDefault();
    setPending(true);
    setFormError(null);
    try {
      await onCreate({
        strategy,
        name: name.trim() || undefined,
        params: Object.fromEntries(Object.entries(params).map(([k, v]) => [k, Number(v)])),
        tickers: activeMode === "tickers" ? tickers : [],
        watchlist_id: activeMode === "watchlist" ? watchlistId : null,
        amount: amount === "" ? null : Number(amount),
        currency: ccy,
      });
      onClose();
    } catch (err) {
      setErrors(err.fields || {});
      setFormError(err.fields ? null : err.message);
    } finally {
      setPending(false);
    }
  }

  if (!spec) return null;
  return (
    <Dialog
      open={open}
      size="lg"
      tone="accent"
      caption="Demo bot · simulated fills"
      title="New trading bot"
      onClose={onClose}
      footer={(
        <>
          <Button variant="quiet" onClick={onClose}>Cancel</Button>
          <Button variant="primary" type="submit" form="new-bot" disabled={pending || !funded.length}>
            {pending ? "Starting…" : "Start bot"}
          </Button>
        </>
      )}
    >
      <form id="new-bot" className={styles.form} onSubmit={submit}>
        <fieldset className={styles.strategies}>
          <legend className="caption">Strategy</legend>
          {strategies.map((s) => (
            <button key={s.key} type="button" className={styles.strategy}
                    aria-pressed={s.key === strategy} onClick={() => pickStrategy(s.key)}>
              <span className={styles.strategyName}>{s.label}</span>
              <span className={styles.strategySummary}>{s.summary}</span>
            </button>
          ))}
        </fieldset>
        {errors.strategy && <p className={styles.error}>{errors.strategy}</p>}

        <div className={styles.grid}>
          {spec.params.map((p) => (p.choices === "saved_rules" ? (
            <Field key={p.key} label={p.label} error={errors[`params.${p.key}`]}
                   hint={savedRules.length ? null : "Save a rule on the Rules tab first"}>
              {() => (
                <SelectMenu label={p.label} value={params[p.key]}
                            options={[{ value: "0", label: "Pick a saved rule" },
                              ...savedRules.map((r) => ({ value: String(r.id), label: r.name }))]}
                            onChange={(v) => setParams({ ...params, [p.key]: v })} />
              )}
            </Field>
          ) : (
            <Field key={p.key} label={p.label} suffix={p.unit} error={errors[`params.${p.key}`]}>
              {(a) => <input {...a} type="number" min={p.min} max={p.max} step={p.step}
                             value={params[p.key] ?? ""}
                             onChange={(e) => setParams({ ...params, [p.key]: e.target.value })} />}
            </Field>
          )))}
        </div>

        <div className={styles.universe}>
          <span className="caption">Trades</span>
          <Segmented ariaLabel="What the bot trades" value={activeMode} options={modes} onChange={setMode} />
          {activeMode === "tickers" && (
            <Field label="Tickers" hint={`Comma-separated ${ccy} listings, e.g. AAPL, MSFT`} error={errors.tickers}>
              {(a) => <input {...a} value={tickers} placeholder="AAPL, MSFT, NVDA"
                             onChange={(e) => setTickers(e.target.value.toUpperCase())} />}
            </Field>
          )}
          {activeMode === "watchlist" && (watchlists.length ? (
            <SelectMenu label="Watchlist" value={watchlistId}
                        options={watchlists.map((w) => ({ value: w.id, label: w.name }))}
                        onChange={setWatchlistId} />
          ) : <p className={styles.hint}>You have no watchlists yet.</p>)}
          {activeMode === "suggested" && (
            <p className={styles.hint}>Your watchlists plus the names the signal sources surface —
              the same pool the Suggestions page ranks.</p>
          )}
          {(errors.watchlist_id || (activeMode !== "tickers" && errors.tickers)) && (
            <p className={styles.error}>{errors.watchlist_id || errors.tickers}</p>
          )}
        </div>

        <div className={styles.grid}>
          <div className={styles.money}>
            <span className="caption">From cash</span>
            {funded.length ? (
              <SelectMenu label="Cash currency" value={ccy}
                          options={funded.map((c) => ({ value: c.currency, label: `${c.currency} · ${formatMoney(c.balance, c.currency)}` }))}
                          onChange={setCcy} />
            ) : <p className={styles.error}>Add cash on the Portfolio page first.</p>}
            {errors.currency && <p className={styles.error}>{errors.currency}</p>}
          </div>
          <Field label={`Amount (${ccy})`} hint={`${formatMoney(available, ccy)} available`} error={errors.amount}>
            {(a) => <input {...a} type="number" min="0" step="any" value={amount}
                           onChange={(e) => setAmount(e.target.value)} />}
          </Field>
          <Field label="Name" hint="optional" error={errors.name}>
            {(a) => <input {...a} value={name} maxLength={60} placeholder={spec.label}
                           onChange={(e) => setName(e.target.value)} />}
          </Field>
        </div>

        <p className={styles.disclaimer}>
          Demo only: fills are simulated at the live price plus a small cost, while the stock's
          market is open. The amount leaves your Portfolio cash until you stop the bot, then
          comes back with the bot's P&amp;L. Nothing is ever sent to a broker.
        </p>
        {formError && <p className={styles.error} role="alert">{formError}</p>}
      </form>
    </Dialog>
  );
}
