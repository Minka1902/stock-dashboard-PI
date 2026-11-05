"""Contractor company name → ticker lookup table.

Applied to `contracts.ticker` column when new contracts are upserted.
Matches on lowercase fragments of recipient_name.
"""

CONTRACTOR_MAP: dict[str, str] = {
    "raytheon": "RTX",
    "lockheed martin": "LMT",
    "boeing": "BA",
    "general dynamics": "GD",
    "northrop grumman": "NOC",
    "l3harris": "LHX",
    "l3 harris": "LHX",
    "booz allen": "BAH",
    "leidos": "LDOS",
    "saic": "SAIC",
    "bae systems": "BAESY",
    "huntington ingalls": "HII",
    "textron": "TXT",
    "oshkosh": "OSK",
    "mantech": "MAN",
    "caci": "CACI",
    "palantir": "PLTR",
    "amazon": "AMZN",
    "microsoft": "MSFT",
    "google": "GOOGL",
    "alphabet": "GOOGL",
    "ibm": "IBM",
    "accenture": "ACN",
    "science applications": "SAIC",
    "general atomics": "GD",
    "peraton": "PRTN",
    "maximus": "MMS",
    "vectrus": "V2X",
    "v2x": "V2X",
    "amentum": "AMTM",
    "fluor": "FLR",
    "parsons": "PSN",
    "kbr": "KBR",
    "harris corporation": "LHX",
    "dxc technology": "DXC",
    "gartner": "IT",
    "oracle": "ORCL",
    "dell": "DELL",
    "hp inc": "HPQ",
    "hewlett packard": "HPQ",
    "unison": "UNS",
    "serco": "SCGLY",
    "dynex": "DX",
    "cube defense": "CUBE",
    "moog": "MOG",
    "heico": "HEI",
    "transdigm": "TDG",
    "curtiss-wright": "CW",
    "spirit aerosystems": "SPR",
    "triumph group": "TGI",
    "ducommun": "DCO",
    "kratos": "KTOS",
    "aerojet": "AJRD",
}


def match_ticker(company_name: str) -> str | None:
    name_lower = company_name.lower()
    for fragment, ticker in CONTRACTOR_MAP.items():
        if fragment in name_lower:
            return ticker
    return None
