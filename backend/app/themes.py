"""Pure theme classifier for portfolio holdings (Task 11).

Maps a ticker + its Yahoo sector/industry onto a small set of investor-facing
themes. No I/O — callers pass in the fundamentals they already have. When
nothing is known the theme is "Other"; a theme is never invented.
"""

# The canonical theme vocabulary, most-specific first (used to validate the
# manual override too).
THEMES = [
    "AI", "Semiconductors", "Medicine", "Space", "Defense", "Energy",
    "Finance", "Crypto", "Consumer", "Tech", "Other",
]

# Curated overrides that sector/industry alone would miss or misfile. Checked
# before the keyword rules. Kept intentionally small and high-confidence.
TICKER_THEMES = {
    # AI / accelerated compute & AI-first platforms
    "NVDA": "AI", "AMD": "AI", "PLTR": "AI", "SMCI": "AI", "AI": "AI",
    "SNOW": "AI", "PATH": "AI",
    # Semiconductors (non-AI-branded)
    "INTC": "Semiconductors", "MU": "Semiconductors", "TSM": "Semiconductors",
    "AVGO": "Semiconductors", "QCOM": "Semiconductors", "ASML": "Semiconductors",
    "ARM": "Semiconductors", "LRCX": "Semiconductors", "AMAT": "Semiconductors",
    # Space
    "RKLB": "Space", "LUNR": "Space", "ASTS": "Space", "RDW": "Space",
    "SPCE": "Space",
    # Defense
    "LMT": "Defense", "RTX": "Defense", "NOC": "Defense", "GD": "Defense",
    "LHX": "Defense", "PLTR_DEF": "Defense",
    # Crypto
    "COIN": "Crypto", "MSTR": "Crypto", "HOOD": "Crypto", "MARA": "Crypto",
    "RIOT": "Crypto", "CLSK": "Crypto", "BITO": "Crypto",
    # Medicine / biotech
    "MRNA": "Medicine", "PFE": "Medicine", "LLY": "Medicine", "CRSP": "Medicine",
    "NVAX": "Medicine",
    # Energy
    "XOM": "Energy", "CVX": "Energy", "ENPH": "Energy", "FSLR": "Energy",
    # Consumer
    "TSLA": "Consumer", "NKE": "Consumer", "SBUX": "Consumer", "MCD": "Consumer",
}

# (keyword, theme) rules applied against industry then sector, case-insensitive.
# Order matters: earlier rules win.
_KEYWORD_RULES = [
    ("semiconductor", "Semiconductors"),
    ("biotech", "Medicine"),
    ("pharma", "Medicine"),
    ("drug", "Medicine"),
    ("medical", "Medicine"),
    ("health", "Medicine"),
    ("aerospace", "Defense"),
    ("defense", "Defense"),
    ("oil", "Energy"),
    ("gas", "Energy"),
    ("solar", "Energy"),
    ("energy", "Energy"),
    ("utilit", "Energy"),
    ("bank", "Finance"),
    ("insur", "Finance"),
    ("capital market", "Finance"),
    ("financ", "Finance"),
    ("software", "Tech"),
    ("information technology", "Tech"),
    ("internet", "Tech"),
    ("semiconductors & semiconductor equipment", "Semiconductors"),
    ("consumer", "Consumer"),
    ("retail", "Consumer"),
]


def classify(ticker: str, sector: str | None, industry: str | None) -> str:
    """Return the best theme for a ticker. Never invents data → 'Other' when
    nothing matches or fundamentals are missing."""
    t = (ticker or "").upper()
    if t in TICKER_THEMES:
        return TICKER_THEMES[t]

    haystack = f"{industry or ''} \n {sector or ''}".lower()
    for keyword, theme in _KEYWORD_RULES:
        if keyword in haystack:
            return theme
    return "Other"
