// Plain-language definitions for the jargon used across the dashboard.
// `short` is for inline tooltips (InfoTip); `long` is for the guide's glossary
// section. Kept consistent with CHIP_META in BoomScorePanel.jsx and WEIGHTS in
// backend/app/sources/boom_score.py — change them together.

export const GLOSSARY = {
  boom_score: {
    label: "Boom Score",
    short: "One composite number from −90 to +100 that sums every other signal on the dashboard.",
    long: "A single, explainable score that adds up all the bullish (positive) and bearish (negative) signals for a ticker. Range is roughly −90 to +100. Higher means more independent signals are pointing up right now. It is evidence to investigate, not a prediction — every component that fired is shown so you can see why.",
  },
  golden_cross: {
    label: "Golden cross",
    short: "The 50-day average price crossing above the 200-day average — a classic medium-term uptrend signal.",
    long: "When a stock's 50-day moving average rises above its 200-day moving average. It signals that recent momentum has overtaken the longer trend, which historically marks the start of medium-term up-moves. Adds +20 to the Boom Score.",
  },
  death_cross: {
    label: "Death cross",
    short: "The 50-day average dropping below the 200-day average — a medium-term downtrend signal.",
    long: "The opposite of a golden cross: the 50-day moving average falls below the 200-day moving average, suggesting a medium-term downtrend. Subtracts 20 from the Boom Score.",
  },
  rsi: {
    label: "RSI (Relative Strength Index)",
    short: "A 0–100 momentum gauge. Below ~30 is oversold; above ~70 is overbought.",
    long: "The Relative Strength Index measures how fast and far price has moved on a 0–100 scale. A reading of 30–50 is treated as an oversold-recovery zone (adds +10); above 70 is overbought and pullback-prone (subtracts 10).",
  },
  macd: {
    label: "MACD (and crossover)",
    short: "Momentum from the gap between a 12- and 26-bar average; a cross above its 9-bar signal line flags upward momentum.",
    long: "MACD (Moving Average Convergence Divergence) compares two moving averages to track momentum. When the MACD line crosses above its signal line it flags a fresh shift to upward momentum. Adds +10 to the Boom Score.",
  },
  moving_average: {
    label: "Moving average (SMA / MA)",
    short: "The average closing price over the last N bars (e.g. MA50, MA200). Smooths day-to-day noise to show the trend.",
    long: "A simple moving average adds up the closing prices of the last N bars and divides by N. The 50-day (MA50) and 200-day (MA200) averages are the usual medium- and long-term trend references; price above a rising average is read as an uptrend. The golden and death crosses compare these two.",
  },
  ema: {
    label: "Exponential moving average (EMA)",
    short: "A moving average that weights recent bars more heavily, so it reacts faster than a simple average.",
    long: "Like a simple moving average, but each older bar counts a little less than the one after it. The chart's 9- and 21-bar EMAs follow short-term swings more closely than the 50/200 averages.",
  },
  bollinger: {
    label: "Bollinger Bands",
    short: "A 20-bar average with bands 2 standard deviations above and below. Wide bands = volatile; narrow bands = quiet.",
    long: "Bollinger Bands (20, 2) plot a 20-bar simple moving average with an upper and lower band two standard deviations away. The bands widen when prices swing more and pinch together when the market goes quiet. Price touching a band is not a signal on its own.",
  },
  vwap: {
    label: "VWAP (Volume-Weighted Average Price)",
    short: "The session's average traded price, weighted by volume at each price. Intraday only; resets every session.",
    long: "VWAP averages every trade in the session weighted by its size, so heavily traded prices count more. Traders use it as the session's 'fair' reference price. It only has meaning within a single trading day, so the chart shows it on intraday timeframes only.",
  },
  r_multiple: {
    label: "R-multiple / reward-to-risk (R/R)",
    short: "A trade measured in units of its planned risk. R = distance from entry to stop; R/R 2 means the target is twice that distance.",
    long: "One R is the amount you would lose if the stop is hit (entry price minus stop price). Reward-to-risk (R/R) divides the distance to the target by that risk, so R/R 2 means the target is two R away. Results are also reported in R — +1.5R is a gain of one and a half times the planned risk. It describes the plan's geometry, not the odds of reaching the target.",
  },
  conviction: {
    label: "Conviction tier",
    short: "A plain-language band for the Boom Score: Watching (0–25), Interesting (26–50), High Conviction (51–75), Strong Setup (76+).",
    long: "The Boom Score's band, so a list can be scanned without reading numbers: Watching 0–25, Interesting 26–50, High Conviction 51–75, Strong Setup 76 or more, and Bearish Signals below 0. A higher tier means more independent signals agree right now — it is not a forecast.",
  },
  atr: {
    label: "ATR (Average True Range)",
    short: "The average size of a day's price range over the last 14 days, including overnight gaps. A volatility yardstick in the stock's own currency.",
    long: "Average True Range measures how much a stock typically moves in a day, counting gaps from the previous close. The analysis uses it to place stops a sensible distance from price and to say how stretched price is from its 20-day average (in ATRs).",
  },
  analysis_conviction: {
    label: "Analysis conviction (conv)",
    short: "The stock analysis's evidence total, −100 to +100: each technical finding adds or subtracts its weight. 45+ reads Accumulate, −15 or less Reduce, −45 or less Avoid, otherwise Hold.",
    long: "The per-stock analysis scores each piece of technical evidence it finds — trend, chart patterns, support and trendlines, momentum — with a signed weight, and adds them up (capped at ±100). The total maps to a directive: 45 or more is Accumulate, between −15 and 45 Hold, −15 or less Reduce, −45 or less Avoid. Every contributing finding is listed with it; it is a summary of evidence, not a forecast.",
  },
  days_to_cover: {
    label: "Days to cover (short ratio)",
    short: "Shares sold short ÷ average daily volume — roughly how many days of normal trading it would take shorts to buy back.",
    long: "Divides the number of shares sold short by the average number of shares traded per day. A high value means short sellers would need many days of normal volume to close their positions, which can amplify a rise if they are forced to buy.",
  },
  form4: {
    label: "SEC Form 4",
    short: "The filing company insiders (officers, directors, 10% owners) must submit within two business days of trading their own company's stock.",
    long: "U.S. securities law requires insiders to report changes in their holdings on Form 4, generally within two business days. The dashboard reads these filings from SEC EDGAR; open-market purchases and sales are the informative ones, while grants and option exercises are often routine.",
  },
  put_call: {
    label: "Put/call ratio",
    short: "Put options traded ÷ call options traded. Above ~1 means more demand for downside protection; well below 1, more upside bets.",
    long: "Puts gain when prices fall and calls gain when they rise, so the ratio of put to call volume is a read on how defensive options traders are. Readings are usually compared with their own recent range rather than a fixed line.",
  },
  margin_debt: {
    label: "Margin debt",
    short: "Money investors have borrowed from brokers against their holdings, reported monthly by FINRA.",
    long: "FINRA publishes the total debit balances in customers' margin accounts each month. Fast growth shows leverage-fuelled optimism; sharp declines often coincide with forced selling. The figure is released with a lag of several weeks.",
  },
  aaii: {
    label: "AAII sentiment survey",
    short: "A weekly poll by the American Association of Individual Investors: the share of members bullish, neutral or bearish on the next six months.",
    long: "Each week AAII asks its members where they think the stock market will be in six months. The bull–bear spread is often read contrarily: extreme pessimism among individual investors has historically come near market lows.",
  },
  vix: {
    label: "VIX (volatility index)",
    short: "Cboe's measure of the S&P 500 volatility options traders expect over the next 30 days. Higher = more expected turbulence.",
    long: "The VIX is calculated from S&P 500 option prices and expresses the market's expected 30-day volatility as an annualised percentage. It tends to spike when stocks fall sharply, which is why it is nicknamed the fear gauge.",
  },
  extended_hours: {
    label: "Extended hours (pre-market / after-hours)",
    short: "Trading before the regular session opens or after it closes. Volume is thin, so prices can jump on little trading.",
    long: "U.S. stocks also trade roughly 4:00–9:30 a.m. (pre-market) and 4:00–8:00 p.m. ET (after-hours). Fewer participants means wider spreads and moves that may not hold into the regular session. Some exchanges, such as Tel Aviv, have no extended session at all.",
  },
  pe_ratio: {
    label: "P/E ratio",
    short: "Share price ÷ earnings per share. Trailing uses the last 12 months' reported earnings; forward uses analysts' estimates.",
    long: "The price-to-earnings ratio says how many dollars investors pay for one dollar of annual profit. It is most useful compared with the same company's history or its sector; it is not meaningful when earnings are negative.",
  },
  peg: {
    label: "PEG ratio",
    short: "P/E divided by the expected earnings growth rate. Around 1 means the valuation roughly matches expected growth.",
    long: "PEG adjusts the P/E for growth: a company growing earnings 20% a year on a P/E of 20 has a PEG of 1. It depends entirely on the growth estimate used, so treat it as a rough comparison tool.",
  },
  price_to_book: {
    label: "P/B (price-to-book)",
    short: "Share price ÷ book value per share (assets minus liabilities).",
    long: "Compares the market price with the accounting value of the company's net assets. Asset-heavy businesses such as banks are often compared on P/B; for software companies it says little.",
  },
  eps: {
    label: "EPS (earnings per share)",
    short: "Net profit divided by shares outstanding. 'est.' is analysts' consensus estimate before the report.",
    long: "Earnings per share is the profit attributable to each share. Around earnings reports the market compares the reported EPS with the consensus estimate; a large surprise either way often moves the price.",
  },
  wsb_rank: {
    label: "Reddit (WSB) mention rank",
    short: "Where a ticker ranks by mentions across Reddit's stock communities such as r/wallstreetbets (1 = most mentioned), from ApeWisdom. Measures attention, not quality.",
    long: "ApeWisdom counts ticker mentions across Reddit's investing communities, r/wallstreetbets among them, and ranks them. A climb of five or more places in 24 hours is flagged as a short-horizon signal (+10). It shows a surge of retail attention, which can reverse just as quickly.",
  },
  relative_volume: {
    label: "Relative volume",
    short: "Today's trading volume versus its average. A rising price on >1.5× volume shows real participation.",
    long: "How heavily a stock is trading compared with its typical day. A price rising on more than 1.5× average volume means the move is backed by real buying interest rather than a thin drift. Adds +10 (\"volume confirmed\").",
  },
  near_52w_high: {
    label: "Near 52-week high",
    short: "Price within ~3% of its highest level in a year — breakout territory.",
    long: "When the current price sits within about 3% of its highest point over the past 52 weeks. Stocks breaking to new highs often keep running as resistance clears. Adds +10 to the Boom Score.",
  },
  short_interest: {
    label: "Short interest",
    short: "The share of a stock's float that traders have bet against by selling borrowed shares.",
    long: "The percentage of a company's freely tradable shares that have been sold short (a bet the price will fall). Very high short interest can fuel sharp upward moves if those traders are forced to buy back — see short squeeze.",
  },
  short_squeeze: {
    label: "Short squeeze",
    short: "When a heavily shorted stock rises, forcing short sellers to buy back and push it higher.",
    long: "If a stock with high short interest starts rising, short sellers may be forced to buy shares to limit losses, which pushes the price up further in a feedback loop. The dashboard flags squeeze potential when short float is high; adds +10.",
  },
  insider_cluster: {
    label: "Insider cluster trade",
    short: "Two or more company officers/directors buying (or selling) their own stock in a short window.",
    long: "Corporate insiders (executives, directors) must disclose trades on SEC Form 4. Several of them buying around the same time is a strong conviction signal (adds +20); several selling is a warning (subtracts 20).",
  },
  congress_trade: {
    label: "Congressional trade",
    short: "A stock trade disclosed by a member of Congress, weighted by dollar size and recency.",
    long: "Members of Congress disclose their stock transactions. A purchase adds up to +15 (scaled by the reported dollar range and how recent it is); a sale subtracts 15. Treated as a slower, longer-horizon signal.",
  },
  analyst_rating: {
    label: "Analyst rating change",
    short: "Wall Street analysts upgrading or downgrading a stock's recommendation.",
    long: "Professional analysts publish buy/hold/sell ratings. A recent upgrade or initiation adds +15; a cluster of two or more downgrades subtracts 15. The dashboard also flags when earnings are within 7 days (extra event risk).",
  },
  fear_greed: {
    label: "Fear & Greed Index",
    short: "CNN's 0–100 market-mood gauge. Extreme fear (<25) often marks contrarian entry points.",
    long: "A composite of market indicators on a 0–100 scale where low is fear and high is greed. Extreme fear (below 25) historically marks good contrarian entry points and adds +10; extreme greed (above 78) signals froth and subtracts 10.",
  },
  yield_curve: {
    label: "Yield curve (un-inversion)",
    short: "When short-term Treasury yields fall back below long-term yields after being inverted.",
    long: "Normally longer-term bonds pay more than short-term ones. When that flips (an inversion) it has historically preceded recessions; the curve returning to normal (un-inverting) has historically preceded recoveries 6–18 months out. A recent un-inversion adds +15.",
  },
  seasonality: {
    label: "Seasonality",
    short: "A stock's historical tendency to rise or fall during this specific time of year.",
    long: "Some stocks show a repeatable edge in particular calendar windows. The dashboard flags a seasonal tailwind when a ticker has averaged at least +2% with a 60%+ win rate over the past ~10 years for the coming week. Adds +10.",
  },
  contracts_catalyst: {
    label: "Federal contract catalyst",
    short: "A large new U.S. government award (>$100M) — concrete, booked future revenue.",
    long: "Major federal contracts are real, disclosed future revenue rather than speculation. A new award over $100M in the last 30 days for a watchlist company adds +10 to the Boom Score.",
  },
  pl_pct: {
    label: "P/L %",
    short: "Profit or loss versus your average cost: (price − avg cost) ÷ avg cost, in the position's own currency.",
    long: "How far the current price is above (+) or below (−) what you paid on average per share. It is computed in the position's native currency, so exchange-rate moves don't distort a single holding; they only enter the converted totals.",
  },
  native_currency: {
    label: "Native currency",
    short: "The currency the position trades in. Prices, cost and value on the row are shown in it, unconverted.",
    long: "Each holding keeps the currency of the market you bought it on — dollars for US listings, shekels for Tel Aviv (.TA) listings. Yahoo quotes TASE prices in agorot (1/100 shekel); the dashboard divides them into shekels everywhere. Only the totals are converted into your base currency, using a live exchange rate.",
  },
  base_currency: {
    label: "Base currency",
    short: "The single currency your portfolio totals (and account size) are converted into, at live Yahoo exchange rates.",
    long: "Totals across markets need one currency. Every position is converted into it with the live rate shown under the cards. If a rate can't be fetched, that money is left out of the converted total and listed as \"FX unavailable\" rather than converted at a guessed rate.",
  },
  tase: {
    label: "TASE (Tel Aviv Stock Exchange)",
    short: "Israel's exchange. Tickers end in .TA and trade in shekels, Monday–Friday (Fridays close early). No pre-market or after-hours session.",
    long: "Tel Aviv listings use the .TA suffix (e.g. TEVA.TA) and are priced in shekels. Since January 2026 TASE trades Monday to Thursday until about 17:30 and Friday until about 13:50 Israel time. US-only data — SEC Form 4 insider trades, congressional trades, US federal contracts and US short interest — doesn't exist for these listings, so those signals are marked not applicable and the Boom Score is renormalized over the rest.",
  },
  fx_pair: {
    label: "Currency pair",
    short: "How many units of the second currency one unit of the first buys — USD/ILS 3.65 means $1 = ₪3.65.",
    long: "Yahoo quotes currency pairs as XXXYYY=X. They trade around the clock on weekdays, so they don't have a market-open badge. Add or reorder the pairs shown in the ticker tape in Settings.",
  },
};

export default GLOSSARY;
