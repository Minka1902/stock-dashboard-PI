import { useEffect, useRef, useState } from "react";
import Button from "./Button";
import Dialog from "./Dialog";
import Field from "./Field";
import Segmented from "./Segmented";
import TimeMachineOdds from "./TimeMachineOdds";
import { getTicketDefaults, placeBrokerOrder, placePaperOrder } from "../api";
import { formatMoney } from "../lib/format";
import styles from "./OrderTicket.module.css";

const SIDES = [{ value: "BUY", label: "Buy" }, { value: "SELL", label: "Sell" }];
const TYPES = [
  { value: "market", label: "Market", title: "Fills at the next live quote, moved against you by the cost model" },
  { value: "limit", label: "Limit", title: "Fills only at your limit or better" },
  { value: "stop", label: "Stop", title: "Becomes a market order once the price crosses your stop" },
];
const TIFS = [
  { value: "day", label: "Day", title: "Cancelled if not filled in its session" },
  { value: "gtc", label: "GTC", title: "Stays open until filled or cancelled" },
];

const ACCOUNTS = {
  paper: {
    label: "paper", caption: "Paper order · simulated, no real money", badge: "PAPER",
    note: "Fills are simulated at live quotes ± costs.", confirm: "Confirm paper", place: placePaperOrder,
    placed: "Placed. It fills once a live quote crosses it — see the Paper tab.",
  },
  alpaca: {
    label: "Alpaca", caption: "Alpaca paper account · sent to the broker", badge: "ALPACA PAPER",
    note: "Sent to your Alpaca paper account; fills sync back within a minute. A buy with a plan stop and target goes as a bracket.",
    confirm: "Send to Alpaca:", place: placeBrokerOrder,
    placed: "Sent to Alpaca. Its fills sync back within a minute — see the Broker tab.",
  },
};

const num = (v) => (v === "" || v == null ? null : Number(v));
const str = (v) => (v == null ? "" : String(+Number(v).toFixed(4)));

// The review step's numbers: what it costs, what it risks to the stop, and
// the reward/risk to the target. null where an input is missing — never 0.
function review({ side, qty, price, stop, target }) {
  const notional = qty && price ? qty * price : null;
  const riskPer = side === "BUY" && stop != null && price ? price - stop : null;
  const atRisk = riskPer != null && riskPer > 0 ? riskPer * qty : null;
  const r = atRisk != null && target != null ? (target - price) / riskPer : null;
  return { notional, atRisk, r };
}

/**
 * A paper order ticket: pre-filled from the stored analysis (size from your
 * risk %, stop from your exit rule or the trade plan), reviewed — notional,
 * money at risk to the stop and R — then confirmed. Keyboard-first: Enter
 * reviews, Enter confirms, Esc cancels. Paper by default; the Alpaca account
 * sends to its paper endpoint (live trading is refused server-side).
 *
 * Props: ticker (prefill; editable when empty), side ("BUY"), qty (prefill),
 * account ("paper" | "alpaca"), suggestionForDate
 * (links the order to the suggestion it came from), onPlaced(order), onClose.
 */
export default function OrderTicket({ ticker: initialTicker = "", side: initialSide = "BUY", qty: initialQty = null,
  account = "paper", suggestionForDate = null, onPlaced, onClose }) {
  const meta = ACCOUNTS[account];
  const [ticker, setTicker] = useState(initialTicker.toUpperCase());
  const [side, setSide] = useState(initialSide);
  const [type, setType] = useState("market");
  const [tif, setTif] = useState("day");
  const [qty, setQty] = useState(str(initialQty));
  const [limit, setLimit] = useState("");
  const [stopPx, setStopPx] = useState("");
  const [planStop, setPlanStop] = useState("");
  const [planTarget, setPlanTarget] = useState("");
  const [defaults, setDefaults] = useState(null);
  const [step, setStep] = useState("form");
  const [errors, setErrors] = useState({});
  const [formError, setFormError] = useState(null);
  const [placed, setPlaced] = useState(null);
  const [pending, setPending] = useState(false);
  const confirmRef = useRef(null);

  useEffect(() => {
    if (!ticker) return undefined;
    let live = true;
    const id = setTimeout(() => {
      getTicketDefaults(ticker, side).then((d) => {
        if (!live) return;
        setDefaults(d);
        setQty((q) => q || str(d.qty));
        setPlanStop((v) => v || str(d.stop));
        setPlanTarget((v) => v || str(d.target));
      }).catch(() => { if (live) setDefaults(null); });
    }, 250);
    return () => { live = false; clearTimeout(id); };
  }, [ticker, side]);

  const price = type === "limit" ? num(limit) ?? defaults?.price : defaults?.price;
  const ccy = defaults?.currency || "USD";
  const buying = side === "BUY";
  const r = review({ side, qty: num(qty), price, stop: buying ? num(planStop) : null,
    target: buying ? num(planTarget) : null });
  const m = (v) => (v == null ? "—" : formatMoney(v, ccy));

  function toReview(e) {
    e.preventDefault();
    setErrors({});
    setFormError(null);
    const errs = {};
    if (!ticker) errs.ticker = "Enter a ticker";
    if (!(num(qty) > 0)) errs.qty = "Enter a quantity above 0";
    if (type === "limit" && !(num(limit) > 0)) errs.limit_price = "Enter a limit price";
    if (type === "stop" && !(num(stopPx) > 0)) errs.stop_price = "Enter a stop price";
    if (buying && planStop !== "" && !(num(planStop) > 0)) errs.plan_stop = "Above 0, or leave it empty";
    if (buying && planTarget !== "" && !(num(planTarget) > 0)) errs.plan_target = "Above 0, or leave it empty";
    if (Object.keys(errs).length) { setErrors(errs); return; }
    setStep("review");
    requestAnimationFrame(() => confirmRef.current?.focus());
  }

  async function confirm() {
    setPending(true);
    try {
      const order = await meta.place({
        ticker, side, qty: num(qty), type, tif, limit_price: num(limit), stop_price: num(stopPx),
        suggestion_for_date: suggestionForDate,
        plan_stop: buying ? num(planStop) : null, plan_target: buying ? num(planTarget) : null,
      });
      setPlaced(order);
      setStep("done");
      onPlaced?.(order);
    } catch (err) {
      setErrors(err.fields || {});
      setFormError(err.fields ? null : err.message);
      setStep("form");
    } finally {
      setPending(false);
    }
  }

  const footer = step === "form" ? (
    <>
      <Button variant="quiet" onClick={onClose}>Cancel</Button>
      <Button variant="primary" type="submit" form="order-ticket">Review</Button>
    </>
  ) : step === "review" ? (
    <>
      <Button variant="quiet" onClick={() => setStep("form")}>Back</Button>
      <Button ref={confirmRef} variant="primary" onClick={confirm} disabled={pending}>
        {pending ? "Placing…" : `${meta.confirm} ${side.toLowerCase()}`}
      </Button>
    </>
  ) : (
    <Button variant="primary" onClick={onClose}>Done</Button>
  );

  return (
    <Dialog open size="md" tone="caution" caption={meta.caption}
            title={ticker ? `${side === "BUY" ? "Buy" : "Sell"} ${ticker}` : `New ${meta.label} order`}
            onClose={onClose} footer={footer}>
      <div className={styles.paper} role="note"><strong>{meta.badge}</strong> {meta.note}</div>

      {step === "form" && (
        <form id="order-ticket" className={styles.form} onSubmit={toReview}>
          {!initialTicker && (
            <Field label="Ticker" error={errors.ticker}>
              {(a) => <input {...a} value={ticker} autoCapitalize="characters"
                             onChange={(e) => setTicker(e.target.value.toUpperCase().trim())} />}
            </Field>
          )}
          <div className={styles.row}>
            <Segmented options={SIDES} value={side} onChange={setSide} ariaLabel="Side" />
            <Segmented options={TYPES} value={type} onChange={setType} ariaLabel="Order type" />
            <Segmented options={TIFS} value={tif} onChange={setTif} ariaLabel="Time in force" />
          </div>
          <div className={styles.row}>
            <Field label="Quantity" error={errors.qty}
                   hint={defaults?.qty != null && side === "BUY" ? `sized for ${defaults.risk_pct}% risk to the stop`
                     : defaults?.sizing_note || (side === "SELL" && defaults ? `${defaults.held} held` : null)}>
              {(a) => <input {...a} type="number" min="0" step="any" inputMode="decimal" value={qty}
                             onChange={(e) => setQty(e.target.value)} />}
            </Field>
            {type === "limit" && (
              <Field label="Limit price" suffix={ccy} error={errors.limit_price}>
                {(a) => <input {...a} type="number" min="0" step="any" inputMode="decimal" value={limit}
                               onChange={(e) => setLimit(e.target.value)} />}
              </Field>
            )}
            {type === "stop" && (
              <Field label="Stop price" suffix={ccy} error={errors.stop_price}>
                {(a) => <input {...a} type="number" min="0" step="any" inputMode="decimal" value={stopPx}
                               onChange={(e) => setStopPx(e.target.value)} />}
              </Field>
            )}
          </div>
          {buying && (
            <div className={styles.row}>
              <Field label="Plan stop" suffix={ccy} error={errors.plan_stop}
                     hint={defaults?.stop != null ? "from your exit rule / the trade plan" : "optional; your limits may need one"}>
                {(a) => <input {...a} type="number" min="0" step="any" inputMode="decimal" value={planStop}
                               onChange={(e) => setPlanStop(e.target.value)} />}
              </Field>
              <Field label="Plan target" suffix={ccy} error={errors.plan_target} hint="optional">
                {(a) => <input {...a} type="number" min="0" step="any" inputMode="decimal" value={planTarget}
                               onChange={(e) => setPlanTarget(e.target.value)} />}
              </Field>
            </div>
          )}
          <p className={styles.hint}>
            {defaults?.price != null
              ? `Last ${m(defaults.price)} (${defaults.price_source}).`
              : ticker ? "No price for this ticker yet." : ""}
          </p>
          {formError && <p className={styles.error} role="alert">{formError}</p>}
        </form>
      )}

      {step === "review" && (
        <dl className={styles.review}>
          <div><dt>Order</dt><dd>{side} {qty} {ticker} · {type}{type === "limit" ? ` @ ${m(num(limit))}` : ""}{type === "stop" ? ` @ ${m(num(stopPx))}` : ""} · {tif.toUpperCase()}</dd></div>
          <div><dt>Notional</dt><dd>{m(r.notional)}</dd></div>
          <div><dt>At risk to the stop</dt><dd>{r.atRisk == null ? "— (no stop below the price)" : m(r.atRisk)}</dd></div>
          <div><dt>Reward : risk</dt><dd>{r.r == null ? "—" : `${r.r.toFixed(1)}R`}</dd></div>
        </dl>
      )}
      {step === "review" && buying && num(planStop) > 0 && (
        <TimeMachineOdds ticker={ticker} entry={price ?? null} stop={num(planStop)} target={num(planTarget)}
                         currency={ccy} compact />
      )}

      {step === "done" && placed && (
        <p className={styles.done} role="status">
          {placed.status === "filled"
            ? `Filled: ${placed.side} ${placed.qty} ${placed.ticker} at ${m(placed.fill_price)}.`
            : placed.status === "rejected"
              ? `Rejected: ${placed.reason}`
              : meta.placed}
        </p>
      )}
    </Dialog>
  );
}
