"""Which version of the scoring model made a call (STC-19).

The version is a short hash of everything that decides a suggestion or a
Boom Score: the component weights, the congress amount weights and the
decision thresholds. Every suggestion and score row is stamped with it, and
the track record never mixes versions — a reweighted model starts a new
record instead of inheriting the old one's.

Signal mutes (STC-29) deliberately don't change it; the mute set is stored on
each row instead.
"""
import hashlib
import json
from functools import cache

from app import analysis, suggestions
from app.sources import boom_score

LEGACY = "legacy"   # rows recorded before versioning existed


def fingerprint() -> dict:
    """Everything that changes what the model says."""
    return {
        "weights": boom_score.WEIGHTS,
        "amount_weights": boom_score._AMOUNT_WEIGHTS,
        "analysis": {
            "conviction_accumulate": analysis.CONVICTION_ACCUMULATE,
            "conviction_avoid": analysis.CONVICTION_AVOID,
            "conviction_reduce": analysis.CONVICTION_REDUCE,
            "rr_threshold": analysis.RR_THRESHOLD,
            "atr_stop_mult": analysis._ATR_STOP_MULT,
            "struct_stop_atr_buffer": analysis._STRUCT_STOP_ATR_BUFFER,
        },
        "suggestions": {
            "earnings_risk_days": suggestions.EARNINGS_RISK_DAYS,
            "hot_boom_score": suggestions.HOT_BOOM_SCORE,
        },
    }


def compute(fp: dict) -> str:
    raw = json.dumps(fp, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()[:8]


@cache
def current() -> str:
    """This process's model version (fixed for its lifetime)."""
    return compute(fingerprint())
