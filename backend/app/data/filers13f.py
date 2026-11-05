"""Well-known 13F filers and multi-class CUSIPs, from trader-copycat's
filers.json and share_classes.json in Jake Schincariol's trader-skill (MIT,
github.com/Jakeschincariol/trader-skill; THIRD_PARTY_NOTICES.md). Every CIK
was checked against SEC's submissions API on 2026-10-13.

A 13F is filed by the firm, not the person it's known for; `person` says who
that is. STOCKS_13F_FILERS narrows the list to the CIKs you name.
"""

FILERS = [
    ("0001067983", "Berkshire Hathaway", "Warren Buffett / Greg Abel"),
    ("0001336528", "Pershing Square", "Bill Ackman"),
    ("0001649339", "Scion Asset Management", "Michael Burry"),
    ("0001350694", "Bridgewater Associates", "founded by Ray Dalio"),
    ("0001697748", "ARK Investment Management", "Cathie Wood"),
    ("0001536411", "Duquesne Family Office", "Stanley Druckenmiller"),
    ("0001656456", "Appaloosa", "David Tepper"),
    ("0001061768", "Baupost Group", "Seth Klarman"),
    ("0001040273", "Third Point", "Dan Loeb"),
    ("0001709323", "Himalaya Capital", "Li Lu"),
    ("0000921669", "Icahn Carl C", "Carl Icahn"),
    ("0001029160", "Soros Fund Management", "George Soros (family office)"),
    ("0001037389", "Renaissance Technologies", "founded by Jim Simons"),
    ("0001167483", "Tiger Global", "Chase Coleman"),
    ("0001135730", "Coatue Management", "Philippe Laffont"),
    ("0001061165", "Lone Pine Capital", "founded by Stephen Mandel"),
    ("0001791786", "Elliott Investment Management", "founded by Paul Singer"),
    ("0001569205", "Fundsmith", "Terry Smith"),
    ("0001096343", "Markel Group", "Tom Gayner"),
    ("0001166559", "Gates Foundation Trust", "Bill & Melinda Gates Foundation Trust"),
    ("0001549575", "Dalal Street", "Mohnish Pabrai"),
    ("0001112520", "Akre Capital Management", "founded by Chuck Akre"),
    ("0000949509", "Oaktree Capital Management", "Howard Marks"),
]

# CUSIP -> Yahoo ticker where a company has several listed share classes and
# a name can't tell them apart (checked against OpenFIGI on 2026-10-13).
SHARE_CLASSES = {
    "02079K305": "GOOGL", "02079K107": "GOOG", "084670108": "BRK-A", "084670702": "BRK-B",
    "526057104": "LEN", "526057302": "LEN-B", "530909100": "LLYVA", "530909308": "LLYVK",
    "531229755": "FWONK", "35137L105": "FOXA", "35137L204": "FOX", "65249B109": "NWSA",
    "65249B208": "NWS", "904311107": "UAA", "904311206": "UA", "422806109": "HEI",
    "422806208": "HEI-A", "98954M101": "ZG", "98954M200": "Z", "115637100": "BF-A",
    "115637209": "BF-B",
}
