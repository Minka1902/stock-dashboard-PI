"""A curated large-cap universe for the earnings calendar.

"Which big companies report, and when" needs a definition of "big". This is a
configuration choice — the same kind as X_ACCOUNTS or the contractors list —
not derived data, and it is stated here rather than inferred from a market-cap
screen the app doesn't have. Override with STOCKS_EARNINGS_UNIVERSE.

Roughly the S&P 100 by weight: the names whose prints move indices and sectors.
"""

MAJORS = [
    # mega-cap tech
    "AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "AVGO", "TSLA", "ORCL",
    "AMD", "CRM", "ADBE", "CSCO", "ACN", "INTC", "QCOM", "TXN", "IBM", "NOW",
    "INTU", "AMAT", "MU", "ADI", "LRCX", "PANW", "KLAC", "SNPS", "CDNS",
    # financials
    "BRK-B", "JPM", "V", "MA", "BAC", "WFC", "GS", "MS", "SPGI", "AXP", "BLK",
    "C", "SCHW", "CB", "PGR", "COF",
    # healthcare
    "LLY", "UNH", "JNJ", "ABBV", "MRK", "TMO", "ABT", "PFE", "DHR", "AMGN",
    "BMY", "GILD", "CVS", "MDT", "ISRG", "VRTX", "REGN",
    # consumer
    "WMT", "PG", "HD", "COST", "KO", "PEP", "MCD", "NKE", "LOW", "SBUX",
    "TGT", "CL", "MDLZ", "MO", "PM", "TJX", "BKNG",
    # industrials, energy, utilities, materials, telecom
    "XOM", "CVX", "COP", "SLB", "GE", "CAT", "BA", "HON", "UNP", "RTX", "LMT",
    "DE", "UPS", "MMM", "NOC", "GD", "NEE", "DUK", "SO", "LIN", "APD", "SHW",
    "T", "VZ", "CMCSA", "DIS", "NFLX",
]
