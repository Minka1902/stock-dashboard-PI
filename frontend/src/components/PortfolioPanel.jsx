import { useCallback, useMemo, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import Icon from "./Icon";
import AnimatedNumber from "./AnimatedNumber";
import CashBalances from "./CashBalances";
import ExitRuleDialog from "./ExitRuleDialog";
import ExtHoursBadge from "./ExtHoursBadge";
import InfoTip from "./InfoTip";
import MenuButton, { MenuDivider, MenuItem, MenuLabel } from "./MenuButton";
import Segmented from "./Segmented";
import SelectMenu from "./SelectMenu";
import SellDialog from "./SellDialog";
import Sparkline from "./Sparkline";
import SparkRange from "./SparkRange";
import Term from "./Term";
import TickerLabel from "./TickerLabel";
import Tooltip from "./Tooltip";
import { useFxRates } from "../hooks/useFxRates";
import { useProfile } from "../hooks/useProfile";
import { useSparklines } from "../hooks/useSparklines";
import { navigateTo, openTickerTab, routeToPath } from "../lib/nav";
import { fadeRise, prefersReducedMotion, staggerContainer, staggerItem } from "../lib/motionConfig";
import {
  currencyForSymbol, currencySymbol, formatMoney, formatMoneySigned, marketForSymbol,
} from "../lib/format";
import { convertAmount, convertCash, summarizeByCurrency } from "../lib/portfolioMath";
import styles from "./PortfolioPanel.module.css";

const DIRECTIVE_TONE = { Accumulate: "buy", Hold: "hold", Reduce: "warn", Avoid: "sell" };
const ANALYTICS_ROUTE = { kind: "view", view: "trading-bot", tab: "performance" };
const LOTS_ROUTE = { kind: "view", view: "trading-bot", tab: "lots" };

// Mirrors backend/app/themes.py THEMES (validated server-side too).
const THEMES = [
  "AI", "Semiconductors", "Medicine", "Space", "Defense", "Energy",
  "Finance", "Crypto", "Consumer", "Tech", "Other",
];

// Mirrors backend currency.BASE_CURRENCIES.
const BASE_OPTIONS = [
  { value: "USD", label: "$ USD", title: "Total the portfolio in US dollars" },
  { value: "ILS", label: "₪ ILS", title: "Total the portfolio in Israeli shekels" },
];
// Currencies offered when adding/editing a position. The backend accepts any
// code in currency.ISO_CURRENCIES; these are the ones people actually hold.
const HOLDING_CURRENCIES = ["USD", "ILS", "EUR", "GBP"];
const CURRENCY_NAMES = { USD: "US dollars", ILS: "Israeli shekels", EUR: "euros", GBP: "British pounds" };

const signedPct = (v, d = 1) => `${v >= 0 ? "+" : ""}${v.toFixed(d)}%`;
const compact = (v, ccy) => formatMoney(v, ccy, { compact: true });

function ccyTip(ccy, ticker) {
  const name = CURRENCY_NAMES[ccy] || ccy;
  const tase = marketForSymbol(ticker) === "TASE";
  return (
    <>
      <strong>Held in {name}</strong>
      <p>Price, average cost, value and P/L on this row are in {ccy}, unconverted.
        Only the totals are converted into your base currency.</p>
      {tase && <p>A Tel Aviv (TASE) listing. Yahoo quotes it in agorot; the dashboard divides by 100 to show shekels.</p>}
    </>
  );
}

export default function PortfolioPanel({
  portfolio, cash = [], signals, quotes = {}, analyses = [],
  onAdd, onEdit, onSetCategory, onRemove, onSell, onMoveCash, onSetCash,
}) {
  const [ticker, setTicker] = useState("");
  const [shares, setShares] = useState("");
  const [avgCost, setAvgCost] = useState("");
  const [addCcy, setAddCcy] = useState("auto");
  // null = follow the default (on when that currency already has cash).
  const [payCash, setPayCash] = useState(null);
  const [selling, setSelling] = useState(null);
  const [ruling, setRuling] = useState(null);
  const [error, setError] = useState(null);
  const [pending, setPending] = useState(false);
  const [mergeNote, setMergeNote] = useState(null);
  const [note, setNote] = useState(null);

  // Inline edit: which ticker is being edited + its draft values.
  const [editing, setEditing] = useState(null);
  const [editShares, setEditShares] = useState("");
  const [editCost, setEditCost] = useState("");
  const [editCcy, setEditCcy] = useState("USD");
  const [range, setRange] = useState("1m");
  const { series: sparks } = useSparklines(portfolio.map((h) => h.ticker), range);

  const { profile, update: updateProfile, save: saveProfile } = useProfile();
  const base = profile.base_currency || "USD";

  const analysisByTicker = Object.fromEntries(analyses.map((a) => [a.ticker, a]));

  // Quote currency can differ from the holding's (a user override), so rates
  // cover both sets.
  const quoteCcy = (t) => quotes[t]?.currency || currencyForSymbol(t);
  const neededCcys = [
    ...portfolio.map((h) => h.currency || "USD"),
    ...portfolio.map((h) => quoteCcy(h.ticker)),
    ...cash.map((c) => c.currency),
  ].filter(Boolean);
  const fx = useFxRates(base, neededCcys);

  // Per-position numbers, all in the holding's native currency.
  const positionOf = (h) => {
    const hc = h.currency || "USD";
    const q = quotes[h.ticker];
    let raw = q?.price;
    if (raw == null) {
      const sig = signals.find((s) => s.ticker === h.ticker);
      raw = sig?.price ?? null;
    }
    const qc = quoteCcy(h.ticker) || hc;
    const price = raw == null ? null : convertAmount(raw, qc, hc, fx.rates);
    const prev = q?.previous_close != null ? convertAmount(q.previous_close, qc, hc, fx.rates) : null;
    const value = price != null ? price * h.shares : null;
    return {
      currency: hc,
      price,
      priceMismatch: raw != null && price == null, // needs FX we don't have
      value,
      cost: h.avg_cost * h.shares,
      day: price != null && prev != null ? (price - prev) * h.shares : null,
    };
  };
  const positions = Object.fromEntries(portfolio.map((h) => [h.ticker, positionOf(h)]));
  const summary = summarizeByCurrency(Object.values(positions), base, fx.rates);
  const { total } = summary;
  const totalPl = total.cost > 0 ? ((total.value - total.cost) / total.cost) * 100 : null;
  const hasTotals = total.converted > 0;
  const cashConv = convertCash(cash, base, fx.rates);
  const hasCash = cash.some((c) => c.balance);
  const cashOf = (ccy) => cash.find((c) => c.currency === ccy)?.balance || 0;

  // Group rows by theme category, subtotals converted into the base currency.
  const groups = useMemo(() => {
    const byCat = new Map();
    for (const h of portfolio) {
      const cat = h.category || "Other";
      if (!byCat.has(cat)) byCat.set(cat, []);
      byCat.get(cat).push(h);
    }
    const order = [...THEMES, ...[...byCat.keys()].filter((c) => !THEMES.includes(c))];
    return order
      .filter((c) => byCat.has(c))
      .map((cat) => {
        const rows = byCat.get(cat);
        const sub = summarizeByCurrency(rows.map((h) => positions[h.ticker]), base, fx.rates);
        const plPct = sub.total.cost > 0
          ? ((sub.total.value - sub.total.cost) / sub.total.cost) * 100 : null;
        return {
          cat, rows,
          value: sub.total.converted > 0 ? sub.total.value : null,
          plPct: sub.total.converted > 0 ? plPct : null,
          mixed: sub.byCurrency.length > 1,
          excluded: sub.excluded,
        };
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [portfolio, quotes, signals, base, fx.rates]);

  // Stable formatters so AnimatedNumber only re-tweens when the value or the
  // base currency actually changes.
  const fmtBase = useCallback((v) => compact(v, base), [base]);
  const fmtBaseSigned = useCallback((v) => formatMoneySigned(v, base, { compact: true }), [base]);

  async function changeBase(next) {
    if (next === base) return;
    updateProfile({ base_currency: next }); // optimistic
    try {
      const saved = await saveProfile({ base_currency: next });
      setNote(saved?.note || null);
    } catch (err) {
      updateProfile({ base_currency: base });
      setNote(err.message || "Could not change the base currency");
    }
  }

  const detected = currencyForSymbol(ticker.trim());
  const addTicker = ticker.trim().toUpperCase();
  const tradeCcy = portfolio.find((h) => h.ticker === addTicker)?.currency
    || (addCcy === "auto" ? detected : addCcy);
  const payFromCash = payCash ?? (tradeCcy ? cashOf(tradeCcy) > 0 : false);
  const addCcyOptions = [
    { value: "auto", label: detected ? `Auto · ${detected}` : "Auto · detect" },
    ...HOLDING_CURRENCIES.map((c) => ({ value: c, label: `${currencySymbol(c).trim()} ${c}` })),
  ];

  async function submit(e) {
    e.preventDefault();
    const t = ticker.trim().toUpperCase();
    const sh = parseFloat(shares);
    const ac = parseFloat(avgCost);
    if (!t || !(sh > 0) || !(ac >= 0)) {
      setError("Enter a ticker, positive shares, and a non-negative average cost.");
      return;
    }
    setPending(true);
    const existing = portfolio.find((h) => h.ticker === t);
    try {
      const updated = await onAdd(t, sh, ac, addCcy === "auto" ? null : addCcy, { payFromCash });
      // onAdd stores the returned list in the hook; also read it here for the merge note.
      const after = Array.isArray(updated) ? updated.find((h) => h.ticker === t) : null;
      if (existing && after) {
        setMergeNote({ ticker: t, text: `Merged into existing ${t} — now ${after.shares} sh @ ${formatMoney(after.avg_cost, after.currency)}` });
      } else if (after) {
        setMergeNote({ ticker: t, text: `Added ${t} in ${after.currency}${addCcy === "auto" ? " (detected)" : ""}.` });
      } else {
        setMergeNote(null);
      }
      setTicker(""); setShares(""); setAvgCost(""); setAddCcy("auto"); setPayCash(null);
      setError(null);
    } catch (err) {
      setError(err.message);
    } finally {
      setPending(false);
    }
  }

  function startEdit(h) {
    setEditing(h.ticker);
    setEditShares(String(h.shares));
    setEditCost(String(h.avg_cost));
    setEditCcy(h.currency || "USD");
  }
  async function saveEdit(h) {
    const sh = parseFloat(editShares);
    const ac = parseFloat(editCost);
    if (!(sh > 0) || !(ac >= 0)) return;
    await onEdit(h.ticker, sh, ac, editCcy !== (h.currency || "USD") ? editCcy : null);
    setEditing(null);
  }

  async function sell(h, order) {
    const out = await onSell(h.ticker, order);
    setMergeNote(null);
    setNote(`Sold ${order.shares} ${h.ticker} — realized ${formatMoneySigned(out.realized_pnl, out.currency)}.`);
  }

  const showEmpty = portfolio.length === 0;
  const reduced = prefersReducedMotion();
  const cards = [
    { key: "value", label: hasCash ? "Equity" : "Total value",
      value: hasTotals || hasCash ? total.value + cashConv.total : null, tone: "flat", fmt: fmtBase,
      sub: [
        ...summary.byCurrency.map((s) => ({ ccy: s.currency, v: compact(s.value, s.currency) })),
        ...(hasCash ? [{ ccy: "cash", v: compact(cashConv.total, base) }] : []),
      ] },
    { key: "day", label: "Day P/L", value: hasTotals && total.dayKnown ? total.day : null,
      tone: total.day >= 0 ? "pos" : "neg", fmt: fmtBaseSigned,
      sub: summary.byCurrency.filter((s) => s.dayKnown)
        .map((s) => ({ ccy: s.currency, v: formatMoneySigned(s.day, s.currency, { compact: true }) })) },
    { key: "pl", label: "Total P/L", value: totalPl != null ? total.value - total.cost : null,
      tone: total.value - total.cost >= 0 ? "pos" : "neg", fmt: fmtBaseSigned, pct: totalPl,
      sub: summary.byCurrency
        .map((s) => ({ ccy: s.currency, v: formatMoneySigned(s.value - s.cost, s.currency, { compact: true }) })) },
  ];
  const foreign = summary.byCurrency.filter((s) => s.currency !== base);
  // Until the first live rates arrive, a converted total would leave the
  // foreign money out and then jump; show it as pending instead.
  const fxPending = fx.loading && foreign.length > 0;
  const excludedAmounts = summary.byCurrency.filter((s) => summary.excluded.includes(s.currency));

  return (
    <section className={styles.panel} id="portfolio">
      <header className={styles.head}>
        <div>
          <h2 className={styles.title}>Portfolio</h2>
          <p className={styles.subtitle}>
            Holdings the daily suggestions are tailored to. Each row is in its own currency;
            totals are converted into your base currency at live rates. P/L uses live quotes
            (incl. pre/post-market) when available, otherwise the latest signal price.
          </p>
        </div>
        <div className={styles.headControls}>
          <span className={styles.baseSwitch} data-tour="base-currency">
            <span className={styles.baseLabel}>
              Base <InfoTip term="base_currency" size={14} side="bottom" />
            </span>
            <Segmented
              ariaLabel="Base currency for totals"
              value={base}
              onChange={changeBase}
              options={BASE_OPTIONS}
            />
          </span>
          {!showEmpty && <SparkRange value={range} onChange={setRange} />}
          {!showEmpty && (
            <a className={styles.analyticsLink} href={routeToPath(ANALYTICS_ROUTE)}
               onClick={(e) => { e.preventDefault(); navigateTo(ANALYTICS_ROUTE); }}>
              Analytics →
            </a>
          )}
          {!showEmpty && (
            <a className={styles.analyticsLink} href={routeToPath(LOTS_ROUTE)}
               onClick={(e) => { e.preventDefault(); navigateTo(LOTS_ROUTE); }}>
              Lots &amp; rebalance →
            </a>
          )}
        </div>
      </header>

      <AnimatePresence initial={false}>
        {note && (
          <motion.p
            key="note"
            className={styles.mergeNote}
            initial={reduced ? false : { opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: "auto" }}
            exit={reduced ? { opacity: 0 } : { opacity: 0, height: 0 }}
          >
            <Icon name="info" size={13} /> {note}
            <button type="button" className={styles.noteClose} onClick={() => setNote(null)}
                    aria-label="Dismiss note">×</button>
          </motion.p>
        )}
      </AnimatePresence>

      {(!showEmpty || hasCash) && (
        <>
          <motion.div
            className={styles.summary}
            variants={staggerContainer}
            initial={reduced ? false : "hidden"}
            animate="visible"
          >
            {cards.map((c) => (
              <motion.div key={c.key} className={styles.card} data-tone={c.tone}
                          variants={staggerItem} layout={!reduced}>
                <span className={styles.cardLabel}>
                  {c.label} <span className={styles.cardBase}>in {base}</span>
                </span>
                {fxPending ? (
                  <span className={styles.cardValue} aria-busy="true">…</span>
                ) : c.value == null ? (
                  <span className={styles.cardValue}>—</span>
                ) : (
                  <span className={styles.cardValue}>
                    <AnimatedNumber value={c.value} format={c.fmt} />
                    {c.pct != null && <em className={styles.cardPct}>{signedPct(c.pct)}</em>}
                  </span>
                )}
                {c.sub.length > 1 || (c.sub.length === 1 && c.sub[0].ccy !== base) ? (
                  <span className={styles.cardSub}>
                    {c.sub.map((s, i) => (
                      <span key={s.ccy}>
                        {i > 0 && <span className={styles.dot} aria-hidden="true"> · </span>}
                        <span className={styles.subCcy}>{s.ccy}</span> {s.v}
                        {!fxPending && summary.excluded.includes(s.ccy) && <span className={styles.subExcl}> (not in total)</span>}
                      </span>
                    ))}
                  </span>
                ) : null}
              </motion.div>
            ))}
          </motion.div>
          <AnimatePresence initial={false} mode="popLayout">
            {(foreign.length > 0 || excludedAmounts.length > 0) && (
              <motion.div
                key={`fx-${base}`}
                className={styles.fxLine}
                variants={fadeRise}
                initial={reduced ? false : "hidden"}
                animate="visible"
                exit={{ opacity: 0 }}
              >
                {foreign.filter((s) => fx.rates[s.currency]).map((s) => (
                  <span key={s.currency} className={styles.fxRate}>
                    <Term term="fx_pair">1 {s.currency}</Term> = {formatMoney(fx.rates[s.currency], base, { digits: 4 })}
                  </span>
                ))}
                {foreign.some((s) => fx.rates[s.currency]) && (
                  <span className={styles.fxMeta}>
                    live Yahoo FX{fx.asOf ? ` · ${new Date(fx.asOf).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}` : ""}
                  </span>
                )}
                {fx.loading && foreign.length > 0 && <span className={styles.fxMeta}>fetching rates…</span>}
                {!fx.loading && excludedAmounts.map((s) => (
                  <span key={s.currency} className={styles.fxMissing} role="status">
                    <Icon name="info" size={12} /> FX unavailable for {s.currency} → {base}:{" "}
                    {compact(s.value, s.currency)} left out of the converted totals
                  </span>
                ))}
              </motion.div>
            )}
          </AnimatePresence>
        </>
      )}

      <CashBalances
        balances={cash}
        base={base}
        rates={fx.rates}
        currencies={HOLDING_CURRENCIES}
        onMove={onMoveCash}
        onSet={onSetCash}
      />

      <form className={styles.form} onSubmit={submit} data-tour="add-form">
        <input className={styles.ticker} placeholder="Ticker" value={ticker} maxLength={12}
               aria-label="Ticker (e.g. AAPL or TEVA.TA)"
               onChange={(e) => setTicker(e.target.value.toUpperCase())} />
        <input className={styles.num} placeholder="Shares" value={shares} type="number" min="0" step="any"
               aria-label="Shares"
               onChange={(e) => setShares(e.target.value)} />
        <input className={styles.num}
               placeholder={`Cost${(addCcy === "auto" ? detected : addCcy) ? ` ${currencySymbol(addCcy === "auto" ? detected : addCcy).trim()}` : ""}`}
               aria-label="Average cost per share, in the position's currency"
               value={avgCost} type="number" min="0" step="any"
               onChange={(e) => setAvgCost(e.target.value)} />
        <Tooltip content="The position's currency. Auto: .TA tickers are shekels, US symbols dollars, anything else is asked from Yahoo.">
          <span className={styles.ccyPick}>
            <SelectMenu label="Currency" value={addCcy} options={addCcyOptions} onChange={setAddCcy} />
          </span>
        </Tooltip>
        <Tooltip content={tradeCcy
          ? `Debit the cost (plus fees) from your ${tradeCcy} cash. Off: the money came from outside this portfolio.`
          : "Pick or type a ticker first"}>
          <label className={styles.payCash}>
            <input type="checkbox" checked={payFromCash} disabled={!tradeCcy}
                   onChange={(e) => setPayCash(e.target.checked)} />
            Pay from cash
          </label>
        </Tooltip>
        <button className={styles.add} disabled={pending || !ticker.trim()}>
          {pending ? "Adding…" : "Add"}
        </button>
      </form>
      {error && <p className={styles.error}>{error}</p>}
      {/* Only while that holding is still here — removing it retires the note. */}
      {mergeNote && portfolio.some((h) => h.ticker === mergeNote.ticker) && (
        <p className={styles.mergeNote} role="status">
          <Icon name="info" size={13} /> {mergeNote.text}
          <button type="button" className={styles.noteClose} onClick={() => setMergeNote(null)}
                  aria-label="Dismiss note">×</button>
        </p>
      )}

      {showEmpty ? (
        <div className={styles.empty}>
          <span className={styles.emptyIcon}><Icon name="wallet" size={24} /></span>
          <p className={styles.emptyTitle}>No holdings yet</p>
          <p className={styles.emptyText}>
            Add a position above so suggestions can account for it. US symbols (AAPL) and
            Tel Aviv listings (TEVA.TA) can sit side by side.
          </p>
        </div>
      ) : (
        <div className={styles.tableWrap}>
          <table className={styles.table}>
            <thead>
              <tr>
                <th>Ticker</th>
                <th className={styles.numCol}>Shares</th>
                <th className={styles.numCol}>Avg Cost</th>
                <th className={styles.numCol}>Price</th>
                <th className={styles.numCol}>Day</th>
                <th className={styles.numCol}>
                  <Term tip="Shares × price, in the position's own currency. Group and total rows are in your base currency.">Mkt Value</Term>
                </th>
                <th className={styles.numCol}><Term term="pl_pct">P/L</Term></th>
                <th>Advice</th>
                <th className={styles.numCol}>Trend</th>
                <th><span className={styles.srOnly}>Actions</span></th>
              </tr>
            </thead>
            {groups.map((g) => (
              <motion.tbody
                key={g.cat}
                variants={staggerContainer}
                initial={reduced ? false : "hidden"}
                animate="visible"
              >
                <tr className={styles.groupRow}>
                  <td colSpan={5}><span className={styles.groupName}>{g.cat}</span></td>
                  <td className={styles.numCol}>
                    {g.value != null ? (
                      <Tooltip content={g.mixed
                        ? `Converted into ${base} — this group holds more than one currency${g.excluded.length ? ` (${g.excluded.join(", ")} left out: FX unavailable)` : ""}`
                        : `In ${base}`}>
                        <span tabIndex={0}>{compact(g.value, base)}</span>
                      </Tooltip>
                    ) : "—"}
                  </td>
                  <td className={styles.numCol}>
                    {g.plPct != null && (
                      <span className={styles.pl} data-tone={g.plPct >= 0 ? "pos" : "neg"}>{signedPct(g.plPct)}</span>
                    )}
                  </td>
                  <td colSpan={3}></td>
                </tr>
                <AnimatePresence initial={false}>
                  {g.rows.map((h) => {
                    const pos = positions[h.ticker];
                    const ccy = pos.currency;
                    const q = quotes[h.ticker];
                    const price = pos.price;
                    const plPct = price != null && h.avg_cost > 0
                      ? (price - h.avg_cost) / h.avg_cost * 100 : null;
                    const dayPct = q && q.change_pct != null ? q.change_pct : null;
                    const tone = plPct == null ? "flat" : plPct >= 0 ? "pos" : "neg";
                    const dayTone = dayPct == null ? "flat" : dayPct >= 0 ? "pos" : "neg";
                    const an = analysisByTicker[h.ticker];
                    const isEditing = editing === h.ticker;
                    return (
                      <motion.tr
                        key={h.ticker}
                        className={styles.row}
                        variants={staggerItem}
                        exit={reduced ? { opacity: 0 } : { opacity: 0, x: -12 }}
                        layout={!reduced}
                      >
                        <td data-col="ticker">
                          <span className={styles.tickerLine}>
                            <Tooltip content={`Analyze ${h.ticker} in a new tab`}>
                              <button className={styles.symbolBtn} onClick={() => openTickerTab(h.ticker)}
                                      aria-label={`Analyze ${h.ticker} in a new tab`}>
                                <TickerLabel ticker={h.ticker} className={styles.symbol} />
                              </button>
                            </Tooltip>
                            <Tooltip content={ccyTip(ccy, h.ticker)}>
                              <span className={styles.ccyBadge} data-ccy={ccy} tabIndex={0}
                                    aria-label={`Held in ${CURRENCY_NAMES[ccy] || ccy}`}>
                                {currencySymbol(ccy).trim()} {ccy}
                              </span>
                            </Tooltip>
                          </span>
                          <CategoryCell
                            ticker={h.ticker}
                            category={h.category || "Other"}
                            source={h.category_source}
                            editing={isEditing}
                            onSetCategory={onSetCategory}
                          />
                        </td>
                        {isEditing ? (
                          <>
                            <td className={styles.numCol} data-col="shares">
                              <input className={styles.editInput} type="number" min="0" step="any"
                                     aria-label={`Shares of ${h.ticker}`}
                                     value={editShares} onChange={(e) => setEditShares(e.target.value)} autoFocus />
                            </td>
                            <td className={styles.numCol} data-col="cost">
                              <span className={styles.editCost}>
                                <input className={styles.editInput} type="number" min="0" step="any"
                                       aria-label={`Average cost of ${h.ticker} in ${editCcy}`}
                                       value={editCost} onChange={(e) => setEditCost(e.target.value)}
                                       onKeyDown={(e) => { if (e.key === "Escape") setEditing(null); if (e.key === "Enter") saveEdit(h); }} />
                                <SelectMenu
                                  label={`Currency of ${h.ticker}`}
                                  value={editCcy}
                                  options={HOLDING_CURRENCIES.map((c) => ({ value: c, label: c }))}
                                  onChange={setEditCcy}
                                  className={styles.editCcy}
                                />
                              </span>
                            </td>
                          </>
                        ) : (
                          <>
                            <td className={styles.numCol} data-col="shares" data-label="Shares">{h.shares}</td>
                            <td className={styles.numCol} data-col="cost" data-label="Avg cost">{formatMoney(h.avg_cost, ccy)}</td>
                          </>
                        )}
                        <td className={styles.numCol} data-col="price" data-label="Price">
                          {price != null ? (
                            <span className={styles.priceCell}>
                              <span key={price} className={styles.tick} data-tone={dayTone}>{formatMoney(price, ccy)}</span>
                              <ExtHoursBadge quote={q} />
                            </span>
                          ) : pos.priceMismatch ? (
                            <Tooltip content={`Quoted in ${quoteCcy(h.ticker)} but held in ${ccy}, and no ${quoteCcy(h.ticker)}→${ccy} rate is available right now.`}>
                              <span className={styles.fxMissingCell} tabIndex={0}>FX unavailable</span>
                            </Tooltip>
                          ) : "—"}
                        </td>
                        <td className={styles.numCol} data-col="day" data-label="Day">
                          <span className={styles.pl} data-tone={dayTone}>
                            {dayPct != null ? signedPct(dayPct, 2) : "—"}
                          </span>
                        </td>
                        <td className={styles.numCol} data-col="value">{pos.value != null ? compact(pos.value, ccy) : "—"}</td>
                        <td className={styles.numCol} data-col="pl" data-label="P/L">
                          <span className={styles.pl} data-tone={tone}>
                            {plPct != null ? signedPct(plPct) : "—"}
                          </span>
                        </td>
                        <td data-col="advice">
                          {an ? (
                            <span className={styles.advice} data-tone={DIRECTIVE_TONE[an.directive]}>
                              {an.directive}
                              <em className={styles.conv}>{an.conviction > 0 ? "+" : ""}{an.conviction}</em>
                            </span>
                          ) : (
                            <span className={styles.advicePending}>analyzing…</span>
                          )}
                        </td>
                        <td className={styles.numCol} data-col="trend">
                          <Sparkline
                            closes={sparks[h.ticker]?.closes}
                            changePct={sparks[h.ticker]?.change_pct}
                            error={sparks[h.ticker]?.error}
                            loading={!sparks[h.ticker]}
                            range={range}
                            width={80}
                          />
                        </td>
                        <td className={styles.actionsCol} data-col="actions">
                          {isEditing ? (
                            <span className={styles.editActions}>
                              <Tooltip content={`Save ${h.ticker}`}>
                                <button className={styles.iconAction} onClick={() => saveEdit(h)}
                                        aria-label={`Save ${h.ticker}`}>✓</button>
                              </Tooltip>
                              <Tooltip content="Cancel edit">
                                <button className={styles.iconAction} onClick={() => setEditing(null)}
                                        aria-label="Cancel edit">×</button>
                              </Tooltip>
                            </span>
                          ) : (
                            <span className={styles.rowActions}>
                              <Tooltip content={`Record a sale of ${h.ticker}`}>
                                <button className={styles.textAction} onClick={() => setSelling(h)}
                                        aria-label={`Sell ${h.ticker}`}>Sell</button>
                              </Tooltip>
                              <Tooltip content={`Exit rule for ${h.ticker}: stop, take-profit, trailing stop (alerts only)`}>
                                <button className={styles.iconAction} onClick={() => setRuling(h.ticker)}
                                        aria-label={`Exit rule for ${h.ticker}`}><Icon name="shield" size={14} /></button>
                              </Tooltip>
                              <Tooltip content={`Correct ${h.ticker} (recorded as an adjustment)`}>
                                <button className={styles.iconAction} onClick={() => startEdit(h)}
                                        aria-label={`Edit ${h.ticker}`}>✎</button>
                              </Tooltip>
                              <Tooltip content={`Remove ${h.ticker} without a sale (for a mistaken entry)`}>
                                <button className={styles.remove} onClick={() => onRemove(h.ticker)}
                                        aria-label={`Remove ${h.ticker}`}>×</button>
                              </Tooltip>
                            </span>
                          )}
                        </td>
                      </motion.tr>
                    );
                  })}
                </AnimatePresence>
              </motion.tbody>
            ))}
            <tfoot>
              <tr className={styles.totals}>
                <td>
                  <Tooltip content={summary.excluded.length
                    ? `Converted into ${base}. Left out (FX unavailable): ${summary.excluded.join(", ")}`
                    : `All positions converted into ${base} at live rates`}>
                    <span tabIndex={0}>Total <span className={styles.cardBase}>{base}</span></span>
                  </Tooltip>
                </td>
                <td className={styles.numCol}></td>
                <td className={styles.numCol}></td>
                <td className={styles.numCol}></td>
                <td className={styles.numCol}>
                  {hasTotals && total.dayKnown ? (
                    <span className={styles.pl} data-tone={total.day >= 0 ? "pos" : "neg"}>{fmtBaseSigned(total.day)}</span>
                  ) : "—"}
                </td>
                <td className={styles.numCol}>{hasTotals && total.value > 0 ? fmtBase(total.value) : "—"}</td>
                <td className={styles.numCol}>
                  {totalPl != null ? (
                    <span className={styles.pl} data-tone={totalPl >= 0 ? "pos" : "neg"}>{signedPct(totalPl)}</span>
                  ) : "—"}
                </td>
                <td></td>
                <td></td>
                <td></td>
              </tr>
            </tfoot>
          </table>
        </div>
      )}
      {selling && (
        <SellDialog
          holding={selling}
          price={positions[selling.ticker]?.price ?? null}
          hasCash={cashOf(selling.currency || "USD") > 0}
          onSubmit={(order) => sell(selling, order)}
          onClose={() => setSelling(null)}
        />
      )}
      {ruling && <ExitRuleDialog ticker={ruling} onClose={() => setRuling(null)} />}
    </section>
  );
}

/**
 * The holding's theme: a read-only chip normally, a picker while the row is
 * being edited.
 *
 * It used to be a live <select> rendered outside edit mode, so a mis-click on
 * a scrolling table silently rewrote a holding's theme. Gating it behind the
 * pencil matches shares and avg-cost, which were always edit-only.
 *
 * The picker is MenuButton rather than a native <select> because a native
 * dropdown's options are OS chrome: they ignore data-theme and cannot be
 * styled cross-browser, so the list sat there in system colours while the
 * rest of the app was in Iris Dusk. MenuButton renders real DOM and inherits
 * the tokens — and, since it is portaled, is not clipped by the table.
 */
function CategoryCell({ ticker, category, source, editing, onSetCategory }) {
  const isManual = source === "manual";
  const label = isManual ? category : `Auto · ${category}`;

  if (!editing) {
    return (
      <span className={styles.catWrap}>
        <Tooltip content={isManual ? "Manual theme override" : `Automatically classified as ${category}`}>
          <span className={styles.catChip} data-source={source} tabIndex={0}>
            {label}
          </span>
        </Tooltip>
      </span>
    );
  }

  return (
    <span className={styles.catWrap}>
      <MenuButton
        label={`Theme for ${ticker}`}
        glyph={label}
        align="start"
        className={styles.catMenu}
      >
        {(close) => (
          <>
            <MenuLabel>Theme</MenuLabel>
            <MenuItem onSelect={() => { onSetCategory(ticker, null); close(); }}>
              {`Auto${isManual ? "" : " ·"} ${isManual ? "" : category}`.trim()}
            </MenuItem>
            <MenuDivider />
            {THEMES.map((t) => (
              <MenuItem
                key={t}
                tone={isManual && t === category ? "active" : undefined}
                onSelect={() => { onSetCategory(ticker, t); close(); }}
              >
                {t}
              </MenuItem>
            ))}
          </>
        )}
      </MenuButton>
    </span>
  );
}
