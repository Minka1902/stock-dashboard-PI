/**
 * Guided-tour step definitions, keyed by view. Each step:
 *   { target: CSS selector | null, title, body, placement? }
 * target null = centered intro card. Steps whose target isn't on screen are
 * skipped automatically, so it's safe to reference optional elements.
 */
export const TOURS = {
  sentiment: [
    {
      target: null,
      title: "Market Sentiment",
      body: "Your landing view: a composite read of the market's mood from Fear & Greed, VIX, AAII surveys, put/call ratios and margin debt. Signals, not predictions — every number links back to its public source.",
    },
    {
      target: '[data-tour="sentiment-hero"]',
      title: "The composite read",
      body: "The gauge and the lean word summarize all indicators into one contrarian stance: extreme fear historically rewards buyers, extreme greed rewards caution.",
      placement: "bottom",
    },
    {
      target: '[data-tour="nav"]',
      title: "Six modules",
      body: "Everything lives in these numbered modules. Sentiment for the mood, Suggestions for what to do about it, Portfolio and Watchlist for your names, Trades and News for the tape.",
      placement: "right",
    },
    {
      target: '[data-tour="palette"]',
      title: "Search any stock",
      body: "Press Ctrl/Cmd+K and type any ticker or company name — even one you don't watch — to open its full analysis, chart and report.",
      placement: "bottom",
    },
    {
      target: '[data-tour="refresh"]',
      title: "Refresh",
      body: "Data auto-refreshes every few minutes; this forces every source to re-fetch now. Each source shows its own status and last-refresh time.",
      placement: "bottom",
    },
  ],

  suggestions: [
    {
      target: null,
      title: "Suggestions",
      body: "The daily digest: alerts on your holdings, fresh opportunities ranked by Boom Score, and seasonal edges — the same content that can be emailed or texted to you before the market opens.",
    },
    {
      target: "#suggestions",
      title: "Read the reasoning",
      body: "Every suggestion lists the exact signals that fired (insider buys, technicals, seasonality…). Nothing is a black box — if a data source failed, it says so instead of inventing numbers.",
      placement: "top",
    },
    {
      target: '[data-tour="alerts"]',
      title: "Alerts bell",
      body: "High-signal transitions (a Boom Score crossing its threshold, a golden cross) land here exactly once, so you don't re-read the same event.",
      placement: "bottom",
    },
  ],

  portfolio: [
    {
      target: null,
      title: "Portfolio",
      body: "Your actual book. Positions get live P/L, a directive (Accumulate / Hold / Reduce / Avoid) with conviction, and a full trade plan sized to your risk settings.",
    },
    {
      target: '[data-tour="add-form"]',
      title: "Add a holding",
      body: "Ticker, share count and average cost. Once added, the daily deep-analysis run keeps two years of price history and a technical read for it.",
      placement: "bottom",
    },
    {
      target: "#portfolio",
      title: "Click any row",
      body: "Opens the full analysis: chart with levels, entry/stop/targets with risk:reward, structure, patterns, and a downloadable HTML/PDF report — now including where the stock stood on this day in past years.",
      placement: "top",
    },
  ],

  trades: [
    {
      target: null,
      title: "Insider Trades",
      body: "SEC Form 4 filings — what executives and directors actually did with their own money, straight from EDGAR.",
    },
    {
      target: "#trades",
      title: "Buys vs sells",
      body: "Clusters of insider buying at one company are one of the strongest public signals there is; the Boom Score weighs them accordingly.",
      placement: "top",
    },
  ],

  news: [
    {
      target: null,
      title: "News",
      body: "Macro headlines plus per-ticker news for everything you hold or watch, pulled from GDELT's global index.",
    },
    {
      target: "#news",
      title: "Tagged to your names",
      body: "Articles matched to a ticker you follow are tagged with it, so you can scan what moved your book first.",
      placement: "top",
    },
  ],

  watchlist: [
    {
      target: null,
      title: "Watchlist",
      body: "The radar. Every ticker here is tracked by all signal sources — technicals, fundamentals, short interest, social sentiment, analyst ratings, seasonality and the composite Boom Score.",
    },
    {
      target: '[data-tour="add-form"]',
      title: "Add tickers",
      body: "Type a symbol and an optional note. Or press Ctrl/Cmd+K to search the whole market by name first and add it from the analysis page.",
      placement: "bottom",
    },
    {
      target: "#watchlist",
      title: "Live quotes, click through",
      body: "Rows show live prices and click through to the full per-stock analysis with chart, trade plan and report.",
      placement: "top",
    },
  ],

  settings: [
    {
      target: null,
      title: "Settings",
      body: "Everything configurable lives here: notification channels, trading risk (account size and %-risk per trade, which size every trade plan), the daily analysis schedule, display and accessibility modes.",
    },
    {
      target: "#settings",
      title: "Make it yours",
      body: "Seasonality windows and lookback, dyslexia-friendly font, reduced motion, focus mode — plus the in-app module guide if you want a deeper reference than this tour.",
      placement: "top",
    },
  ],

  seasonality: [
    {
      target: null,
      title: "Seasonality",
      body: "How each watched stock historically behaved around today's date — average move, win rate and per-year bars over the coming day, week and month.",
    },
    {
      target: "#seasonality",
      title: "This day in past years",
      body: "The chips under each row show the actual closing price on this day 1, 2 and 5 years ago (and the earliest on record) with the move from then to now.",
      placement: "top",
    },
  ],
};
