"""User-built trading rules (STC-63): a JSON schema of conditions over price
and volume indicators, and their vectorized evaluation over daily bars.

Only point-in-time inputs are allowed: each indicator value on bar t is
computed from bars up to and including t, never later ones. Fundamentals,
analyst ratings, congress or insider snapshots, the Boom Score and the like
are refused — the app stores only their *latest* values, so testing them
against old prices would leak today into the past.
"""
import hashlib
import json
from typing import Literal, Union

import numpy as np
from pydantic import BaseModel, Field, field_validator, model_validator

from app.models import OHLCBar

# name -> (default period or None for no period, (min, max) period)
INDICATORS: dict[str, tuple[int | None, tuple[int, int] | None]] = {
    "close": (None, None), "open": (None, None), "high": (None, None), "low": (None, None),
    "volume": (None, None),
    "sma": (50, (2, 400)), "ema": (20, (2, 400)), "rsi": (14, (2, 100)), "atr": (14, (2, 100)),
    "macd": (None, None), "macd_signal": (None, None), "macd_hist": (None, None),
    "bb_upper": (20, (5, 200)), "bb_lower": (20, (5, 200)),
    "rvol": (20, (2, 200)), "high_n": (20, (2, 400)), "low_n": (20, (2, 400)),
    "gap_pct": (None, None), "change_pct": (None, None),
}
LABELS = {
    "close": "Close", "open": "Open", "high": "High", "low": "Low", "volume": "Volume",
    "sma": "SMA", "ema": "EMA", "rsi": "RSI", "atr": "ATR", "macd": "MACD", "macd_signal": "MACD signal",
    "macd_hist": "MACD histogram", "bb_upper": "Bollinger upper", "bb_lower": "Bollinger lower",
    "rvol": "Relative volume", "high_n": "N-day high (before today)", "low_n": "N-day low (before today)",
    "gap_pct": "Gap % (open vs previous close)", "change_pct": "Change % (vs previous close)",
}
# Stored as latest snapshots only, so never point-in-time.
NOT_POINT_IN_TIME = {
    "pe_ratio", "forward_pe", "peg_ratio", "pb_ratio", "revenue_growth", "profit_margin", "market_cap",
    "sector", "fundamentals", "analyst", "analyst_rating", "analyst_upgrades", "analyst_downgrades",
    "congress", "congress_buys", "insider", "insider_buys", "short_interest", "wsb", "boom_score",
    "sentiment", "news",
}
OPS = (">", "<", ">=", "<=", "crosses_above", "crosses_below")
MAX_CONDITIONS = 12


class Operand(BaseModel):
    kind: Literal["indicator", "value"] = "indicator"
    name: str | None = None
    period: int | None = None
    value: float | None = None

    @model_validator(mode="after")
    def _check(self):
        if self.kind == "value":
            if self.value is None:
                raise ValueError("a fixed value needs a number")
            return self
        if self.name in NOT_POINT_IN_TIME:
            raise ValueError(f"'{self.name}' isn't point-in-time: only its latest value is stored, "
                             f"so testing it against past prices would use today's data")
        if self.name not in INDICATORS:
            raise ValueError(f"unknown indicator '{self.name}'")
        default, bounds = INDICATORS[self.name]
        if bounds is None:
            self.period = None
        else:
            self.period = self.period or default
            if not bounds[0] <= self.period <= bounds[1]:
                raise ValueError(f"{LABELS[self.name]} period must be {bounds[0]}–{bounds[1]}")
        return self


class Condition(BaseModel):
    left: Operand
    op: Literal[">", "<", ">=", "<=", "crosses_above", "crosses_below"]
    right: Operand


class Group(BaseModel):
    mode: Literal["all", "any"] = "all"
    conditions: list[Union[Condition, "Group"]] = Field(min_length=1)


class RuleSpec(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    entry: Group
    exit: Group | None = None
    stop_pct: float | None = Field(default=None, ge=0.5, le=50)
    target_pct: float | None = Field(default=None, ge=0.5, le=500)
    trail_pct: float | None = Field(default=None, ge=0.5, le=50)
    size_pct: float = Field(default=100, ge=1, le=100)

    @field_validator("name")
    @classmethod
    def _strip(cls, v: str) -> str:
        return v.strip()

    @model_validator(mode="after")
    def _limit(self):
        if count_conditions(self.entry) + (count_conditions(self.exit) if self.exit else 0) > MAX_CONDITIONS:
            raise ValueError(f"at most {MAX_CONDITIONS} conditions per rule")
        return self


def count_conditions(g: Group) -> int:
    return sum(count_conditions(c) if isinstance(c, Group) else 1 for c in g.conditions)


def version(spec: RuleSpec) -> str:
    """A saved rule's model version: 'rule:' + a short hash of what it decides
    on (the name doesn't change the signals, so it isn't part of it)."""
    raw = json.dumps(spec.model_dump(exclude={"name"}), sort_keys=True, separators=(",", ":"))
    return "rule:" + hashlib.sha256(raw.encode()).hexdigest()[:8]


# ---------- vectorized indicators (each value uses bars up to its own) ----------

def _sma(x: np.ndarray, n: int) -> np.ndarray:
    out = np.full(len(x), np.nan)
    if len(x) >= n:
        c = np.cumsum(np.insert(x, 0, 0.0))
        out[n - 1:] = (c[n:] - c[:-n]) / n
    return out


def _ema(x: np.ndarray, n: int) -> np.ndarray:
    out = np.full(len(x), np.nan)
    if len(x) < n:
        return out
    a = 2 / (n + 1)
    out[n - 1] = x[:n].mean()
    for i in range(n, len(x)):
        out[i] = a * x[i] + (1 - a) * out[i - 1]
    return out


def _wilder(x: np.ndarray, n: int, start: int) -> np.ndarray:
    """Wilder smoothing of x from index `start` (its first n values seed it)."""
    out = np.full(len(x), np.nan)
    if len(x) < start + n:
        return out
    out[start + n - 1] = x[start:start + n].mean()
    for i in range(start + n, len(x)):
        out[i] = (out[i - 1] * (n - 1) + x[i]) / n
    return out


def _rsi(c: np.ndarray, n: int) -> np.ndarray:
    d = np.diff(c, prepend=np.nan)
    gain, loss = np.where(d > 0, d, 0.0), np.where(d < 0, -d, 0.0)
    ag, al = _wilder(gain, n, 1), _wilder(loss, n, 1)
    with np.errstate(divide="ignore", invalid="ignore"):
        rs = ag / al
        return np.where(al == 0, np.where(ag == 0, 50.0, 100.0), 100 - 100 / (1 + rs))


def _rolling(x: np.ndarray, n: int, fn) -> np.ndarray:
    """fn over the n bars *before* each bar (today excluded)."""
    out = np.full(len(x), np.nan)
    if len(x) > n:
        win = np.lib.stride_tricks.sliding_window_view(x, n)
        out[n:] = fn(win[:-1], axis=1)
    return out


def series(name: str, period: int | None, b: dict[str, np.ndarray]) -> np.ndarray:
    o, h, l, c, v = b["open"], b["high"], b["low"], b["close"], b["volume"]
    if name in ("close", "open", "high", "low", "volume"):
        return b[name]
    if name == "sma":
        return _sma(c, period)
    if name == "ema":
        return _ema(c, period)
    if name == "rsi":
        return _rsi(c, period)
    if name == "atr":
        prev = np.roll(c, 1)
        tr = np.maximum(h - l, np.maximum(np.abs(h - prev), np.abs(l - prev)))
        tr[0] = h[0] - l[0]
        return _wilder(tr, period, 0)
    if name.startswith("macd"):
        macd = _ema(c, 12) - _ema(c, 26)
        first = np.argmax(~np.isnan(macd)) if np.any(~np.isnan(macd)) else len(macd)
        sig = np.full(len(c), np.nan)
        sig[first:] = _ema(macd[first:], 9)
        return {"macd": macd, "macd_signal": sig, "macd_hist": macd - sig}[name]
    if name in ("bb_upper", "bb_lower"):
        mean = _sma(c, period)
        sq = _sma(c * c, period)
        sd = np.sqrt(np.maximum(sq - mean * mean, 0))
        return mean + 2 * sd if name == "bb_upper" else mean - 2 * sd
    if name in ("gap_pct", "change_pct"):
        prev = np.roll(c, 1)
        prev[0] = np.nan
        with np.errstate(divide="ignore", invalid="ignore"):
            return ((o if name == "gap_pct" else c) / prev - 1) * 100
    if name == "rvol":
        avg = _sma(v, period)
        with np.errstate(divide="ignore", invalid="ignore"):
            return np.where(avg > 0, v / avg, np.nan)
    if name == "high_n":
        return _rolling(h, period, np.max)
    return _rolling(l, period, np.min)


def arrays(bars: list[OHLCBar]) -> dict[str, np.ndarray]:
    return {k: np.array([getattr(x, k) for x in bars], dtype=float)
            for k in ("open", "high", "low", "close", "volume")}


def value(name: str, period: int | None, b: dict) -> np.ndarray:
    """An indicator's values, memoized on `b` so conditions sharing one
    (several scans on one ticker) compute it once."""
    memo = b.setdefault("memo", {})
    if (name, period) not in memo:
        memo[(name, period)] = series(name, period, b)
    return memo[(name, period)]


def _operand(op: Operand, b: dict) -> np.ndarray:
    if op.kind == "value":
        return np.full(len(b["close"]), op.value)
    return value(op.name, op.period, b)


def _condition(cond: Condition, b: dict) -> np.ndarray:
    left, right = _operand(cond.left, b), _operand(cond.right, b)
    with np.errstate(invalid="ignore"):
        if cond.op in (">", "<", ">=", "<="):
            out = {">": left > right, "<": left < right, ">=": left >= right, "<=": left <= right}[cond.op]
        else:
            pl, pr = np.roll(left, 1), np.roll(right, 1)
            out = (left > right) & (pl <= pr) if cond.op == "crosses_above" else (left < right) & (pl >= pr)
            out[0] = False
    return out & ~np.isnan(left) & ~np.isnan(right)


def evaluate(group: Group, b: dict) -> np.ndarray:
    """A boolean per bar: does the group hold on that bar's close?"""
    parts = [evaluate(c, b) if isinstance(c, Group) else _condition(c, b) for c in group.conditions]
    return np.logical_and.reduce(parts) if group.mode == "all" else np.logical_or.reduce(parts)
