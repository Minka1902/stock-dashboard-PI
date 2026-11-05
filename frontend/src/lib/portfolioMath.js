// Multi-currency portfolio arithmetic. Pure, so the rules are in one place:
// positions keep their native currency; converted totals only ever use a
// live rate, and money with no rate is excluded (and reported), never guessed.

/**
 * `amount` in `from` expressed in `to`, using `rates` (multipliers into one
 * common base, e.g. { USD: 1, ILS: 0.27 }). null when either rate is missing.
 */
export function convertAmount(amount, from, to, rates) {
  if (amount == null || Number.isNaN(amount)) return null;
  if (!from || !to || from === to) return amount;
  const rf = rates?.[from];
  const rt = rates?.[to];
  if (!rf || !rt) return null;
  return (amount * rf) / rt;
}

/**
 * Totals per currency plus one converted total in `base`.
 *
 * @param positions [{ currency, value, cost, day }] native amounts; value/cost
 *   null when the position has no price yet (skipped), day null when unknown.
 * @returns {
 *   byCurrency: [{ currency, value, cost, day, dayKnown, count }],
 *   total: { value, cost, day, dayKnown } in base (null values when nothing converts),
 *   excluded: [currency…]  — had value but no FX rate into base,
 * }
 */
export function summarizeByCurrency(positions, base, rates) {
  const map = new Map();
  for (const p of positions) {
    if (p.value == null) continue;
    const c = p.currency || "USD";
    if (!map.has(c)) map.set(c, { currency: c, value: 0, cost: 0, day: 0, dayKnown: false, count: 0 });
    const s = map.get(c);
    s.value += p.value;
    s.cost += p.cost ?? 0;
    s.count += 1;
    if (p.day != null) { s.day += p.day; s.dayKnown = true; }
  }
  // Base currency first, then alphabetical: the one you total in leads.
  const byCurrency = [...map.values()].sort((a, b) =>
    (a.currency === base ? -1 : b.currency === base ? 1 : a.currency.localeCompare(b.currency)));

  const total = { value: 0, cost: 0, day: 0, dayKnown: false, converted: 0 };
  const excluded = [];
  for (const s of byCurrency) {
    const rate = s.currency === base ? 1 : rates?.[s.currency];
    if (!rate) { excluded.push(s.currency); continue; }
    total.value += s.value * rate;
    total.cost += s.cost * rate;
    if (s.dayKnown) { total.day += s.day * rate; total.dayKnown = true; }
    total.converted += 1;
  }
  return { byCurrency, total, excluded };
}

/**
 * Cash balances converted into `base`. Same rule as positions: a currency
 * with no live rate is excluded and reported, never guessed.
 *
 * @param balances [{ currency, balance }]
 * @returns { total, excluded: [currency…] }
 */
export function convertCash(balances, base, rates) {
  let total = 0;
  const excluded = [];
  for (const { currency, balance } of balances) {
    if (!balance) continue;
    const rate = currency === base ? 1 : rates?.[currency];
    if (!rate) { excluded.push(currency); continue; }
    total += balance * rate;
  }
  return { total, excluded };
}
