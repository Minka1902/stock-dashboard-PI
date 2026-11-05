"""Self-contained HTML analysis report for one ticker.

Renders everything already in the DB — analysis, trade plan, boom score
components, fundamentals, seasonality edge, tagged news — plus a static SVG
price chart (candles + MAs + S/R levels) drawn in pure Python. No external
assets, no JS: the file works offline, and the print stylesheet makes the
browser's "Save as PDF" produce a clean A4 document. Data is never invented;
sections without data say so.
"""
import html
import json
from datetime import datetime, timezone

from app import currency, db
from app.models import OHLCBar, StockAnalysis
from app.sources.technical import compute_ma

# Same fixed palette as the frontend chart (canvas/SVG can't read CSS tokens).
_C = {
    "bg": "#211f1c", "ink": "#2a2723", "text": "#b7b0a6", "faint": "#8f887e",
    "up": "#2f9e6e", "down": "#d4453c", "accent": "#b07d10", "info": "#2f6f9f",
    "grid": "#d8d3ca",
}

_MA_DEFS = [(20, _C["info"]), (50, _C["accent"]), (150, _C["faint"]), (200, _C["down"])]


def _e(v) -> str:
    return html.escape(str(v)) if v is not None else "—"


def _n(v, digits=2) -> str:
    return f"{v:,.{digits}f}" if isinstance(v, (int, float)) else "—"


def _pct(v, digits=1) -> str:
    return f"{v:+.{digits}f}%" if isinstance(v, (int, float)) else "—"


# ---------------------------- SVG chart ----------------------------

def render_svg_chart(bars: list[OHLCBar], analysis: StockAnalysis | None,
                     width: int = 860, height: int = 340, n_bars: int = 130) -> str:
    """Candles + MA lines + S/R and plan levels for the last `n_bars` days."""
    bars = bars[-n_bars:]
    if len(bars) < 2:
        return "<p class='muted'>Not enough price history for a chart.</p>"

    pad_l, pad_r, pad_t, pad_b = 8, 56, 10, 22
    plot_w, plot_h = width - pad_l - pad_r, height - pad_t - pad_b

    levels = []
    if analysis:
        levels = [l.price for l in analysis.support[:3] + analysis.resistance[:3]]
        levels += [p for p in (analysis.entry, analysis.stop, analysis.target) if p]
    lo = min(min(b.low for b in bars), *levels) if levels else min(b.low for b in bars)
    hi = max(max(b.high for b in bars), *levels) if levels else max(b.high for b in bars)
    span = (hi - lo) or 1.0
    lo -= span * 0.04
    hi += span * 0.04
    span = hi - lo

    def x(i: int) -> float:
        return pad_l + (i + 0.5) * plot_w / len(bars)

    def y(price: float) -> float:
        return pad_t + (hi - price) / span * plot_h

    cw = max(1.5, plot_w / len(bars) * 0.6)  # candle body width
    parts = [
        f'<svg viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg" '
        f'role="img" aria-label="Daily price chart" font-family="monospace" font-size="10">'
    ]

    # horizontal grid + price labels
    for k in range(5):
        gy = pad_t + k * plot_h / 4
        price = hi - k * span / 4
        parts.append(f'<line x1="{pad_l}" y1="{gy:.1f}" x2="{width - pad_r}" y2="{gy:.1f}" '
                     f'stroke="{_C["grid"]}" stroke-width="0.6"/>')
        parts.append(f'<text x="{width - pad_r + 4}" y="{gy + 3:.1f}" fill="{_C["faint"]}">{price:,.2f}</text>')

    # date labels (first / middle / last)
    for i in (0, len(bars) // 2, len(bars) - 1):
        anchor = "start" if i == 0 else "end" if i == len(bars) - 1 else "middle"
        parts.append(f'<text x="{x(i):.1f}" y="{height - 6}" fill="{_C["faint"]}" '
                     f'text-anchor="{anchor}">{bars[i].date}</text>')

    # candles
    for i, b in enumerate(bars):
        color = _C["up"] if b.close >= b.open else _C["down"]
        cx = x(i)
        parts.append(f'<line x1="{cx:.1f}" y1="{y(b.high):.1f}" x2="{cx:.1f}" y2="{y(b.low):.1f}" '
                     f'stroke="{color}" stroke-width="0.8"/>')
        top, bot = y(max(b.open, b.close)), y(min(b.open, b.close))
        parts.append(f'<rect x="{cx - cw / 2:.1f}" y="{top:.1f}" width="{cw:.1f}" '
                     f'height="{max(bot - top, 0.8):.1f}" fill="{color}"/>')

    # moving averages (window limited to visible bars)
    closes = [b.close for b in bars]
    for period, color in _MA_DEFS:
        pts = []
        for i in range(len(bars)):
            ma = compute_ma(closes[: i + 1], period)
            if ma is not None:
                pts.append(f"{x(i):.1f},{y(ma):.1f}")
        if len(pts) >= 2:
            parts.append(f'<polyline points="{" ".join(pts)}" fill="none" '
                         f'stroke="{color}" stroke-width="1.1"/>')

    # S/R + plan levels
    def hline(price, color, dash, label):
        parts.append(f'<line x1="{pad_l}" y1="{y(price):.1f}" x2="{width - pad_r}" y2="{y(price):.1f}" '
                     f'stroke="{color}" stroke-width="1" stroke-dasharray="{dash}"/>')
        parts.append(f'<text x="{pad_l + 2}" y="{y(price) - 3:.1f}" fill="{color}">{label}</text>')

    if analysis:
        for l in analysis.support[:3]:
            hline(l.price, _C["up"], "4 3", f"S {l.touches}x")
        for l in analysis.resistance[:3]:
            hline(l.price, _C["down"], "4 3", f"R {l.touches}x")
        if analysis.entry:
            hline(analysis.entry, _C["accent"], "1 0", "entry")
        if analysis.stop:
            hline(analysis.stop, _C["down"], "1 0", "stop")
        if analysis.target:
            hline(analysis.target, _C["up"], "1 0", "3R target")

    parts.append(f'<text x="{pad_l + 2}" y="{pad_t + 2}" fill="{_C["faint"]}">'
                 f'MA20/50/150/200 · last {len(bars)} sessions</text>')
    parts.append("</svg>")
    return "".join(parts)


# ---------------------------- sections ----------------------------

def _section(title: str, body: str) -> str:
    return f"<section><h2>{_e(title)}</h2>{body}</section>"


def _table(headers: list[str], rows: list[list[str]]) -> str:
    if not rows:
        return "<p class='muted'>No data.</p>"
    head = "".join(f"<th>{_e(h)}</th>" for h in headers)
    body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in row) + "</tr>" for row in rows)
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def _plan_section(a: StockAnalysis) -> str:
    if a.stop is None:
        return "<p class='muted'>No valid stop below price yet — plan pending.</p>"
    sym = _e(currency.symbol_for(a.currency or currency.currency_for_symbol(a.ticker)))
    stats = _table(
        ["Entry", f"Stop ({_e(a.stop_basis)})", "Target 3R", "Risk/share", "Reward/share", "R:R", "Shares"],
        [[f"{sym}{_n(a.entry)}", f"{sym}{_n(a.stop)}", f"{sym}{_n(a.target)}",
          f"{sym}{_n(a.risk_per_share)}", f"{sym}{_n(a.reward_per_share)}",
          _n(a.rr), _e(a.suggested_shares) if a.suggested_shares is not None else "—"]],
    )
    ladder = _table(
        ["R", "Price", "Feasibility", "Why"],
        [[f"{t.r}:1", f"{sym}{_n(t.price)}", _e(t.feasibility), _e(t.why)] for t in a.targets],
    )
    sizing = ""
    if a.account_size:
        sizing = (f"<p class='muted'>Sized to {_e(a.risk_pct)}% of a "
                  f"{_e(currency.symbol_for(a.account_currency))}{a.account_size:,.0f} account."
                  + (f" {_e(a.sizing_note)}" if a.sizing_note else "") + "</p>")
    return stats + "<h3>Target ladder</h3>" + ladder + sizing


def build_report(conn, ticker: str, *, print_mode: bool = False, profile=None,
                 analysis_override: StockAnalysis | None = None,
                 daily_override: list[OHLCBar] | None = None,
                 anchors_override: list[dict] | None = None) -> str | None:
    """Full standalone HTML document, or None when no analysis exists.

    `profile` (the requesting user's NotifyProfile) personalizes position
    sizing; stored analyses are unsized and shared across users. The overrides
    let the on-demand analyzer render reports for never-stored tickers.
    """
    from app.analysis import apply_sizing  # local import avoids a cycle

    ticker = ticker.strip().upper()
    a = analysis_override or db.get_analysis(conn, ticker)
    if a is None:
        return None
    if profile is not None:
        a = apply_sizing(a, profile.account_size, profile.risk_pct,
                         account_currency=profile.base_currency)

    # Prices in the listing's own currency: ₪ for TASE, $ for US listings.
    sym = _e(currency.symbol_for(a.currency or currency.currency_for_symbol(ticker)))
    daily = daily_override if daily_override is not None else db.get_ohlc(conn, ticker, "daily")
    boom = next((b for b in db.get_boom_scores(conn) if b.ticker == ticker), None)
    fund = db.get_fundamentals_for(conn, ticker)
    season = db.get_seasonality_for(conn, ticker)
    news = [n for n in db.get_news(conn, limit=300) if n.ticker == ticker][:10]
    generated = datetime.now(timezone.utc).isoformat(timespec="seconds")

    dir_color = {"Accumulate": _C["up"], "Hold": _C["accent"],
                 "Reduce": _C["accent"], "Avoid": _C["down"]}.get(a.directive, _C["text"])

    # --- sections ---
    chart = render_svg_chart(daily, a)

    structure_rows = [
        ["Trend", _e(a.trend)], ["MA alignment", _e(a.ma_alignment)],
        ["MA20 / MA50", f"{_n(a.ma20)} / {_n(a.ma50)}"],
        ["MA150 / MA200", f"{_n(a.ma150)} / {_n(a.ma200)}"],
        ["ATR(14)", f"{sym}{_n(a.atr14)}" + (f" ({_n(a.atr_pct)}%)" if a.atr_pct else "")],
    ]
    structure = _table(["Metric", "Value"], structure_rows)

    sr = _table(
        ["Kind", "Price", "Touches", "Last touch"],
        [[_e(l.kind), f"{sym}{_n(l.price)}", str(l.touches), _e(l.last_touch)]
         for l in a.resistance[:5] + a.support[:5]],
    )

    patterns = _table(
        ["Pattern", "Direction", "Confidence", "Measured move", "Pivots", "Note"],
        [[_e(p.label), _e(p.direction), f"{p.confidence * 100:.0f}%",
          f"{sym}{_n(p.measured_move)}" if p.measured_move else "—",
          "; ".join(f"{_e(pv.get('role'))} {sym}{_n(pv.get('price'))}" for pv in p.pivots),
          _e(p.note)] for p in a.patterns],
    ) if a.patterns else "<p class='muted'>No classical pattern reads clearly right now.</p>"

    open_gaps = [g for g in a.gaps if not g.filled]
    gaps = _table(
        ["Date", "Kind", "Size", "From", "To"],
        [[_e(g.date), _e(g.kind), _pct(g.pct), f"{sym}{_n(g.from_price)}", f"{sym}{_n(g.to_price)}"]
         for g in open_gaps],
    ) if open_gaps else "<p class='muted'>No unfilled gaps.</p>"

    reasons = "<ol>" + "".join(f"<li>{_e(r)}</li>" for r in a.reasons) + "</ol>" \
        if a.reasons else "<p class='muted'>No signals fired.</p>"

    if boom:
        try:
            components = json.loads(boom.components)
        except (json.JSONDecodeError, TypeError):
            components = {}
        boom_html = (
            f"<p>Composite score: <strong>{boom.score:+d}</strong> "
            f"<span class='muted'>(computed {_e(boom.computed_at)})</span></p>"
            + _table(["Component", "Points"],
                     [[_e(k.replace('_', ' ')), f"{v:+d}"]
                      for k, v in sorted(components.items(), key=lambda kv: -abs(kv[1]))])
            + (f"<p class='muted'>{_e(boom.score_note)}</p>" if boom.score_note else "")
        )
    else:
        boom_html = "<p class='muted'>Not on the watchlist — no Boom Score computed.</p>"

    if fund:
        fund_html = _table(["Metric", "Value"], [
            ["Sector / industry", f"{_e(fund.sector)} / {_e(fund.industry)}"],
            ["P/E (ttm / fwd)", f"{_n(fund.pe_ratio)} / {_n(fund.forward_pe)}"],
            ["PEG / P/B", f"{_n(fund.peg_ratio)} / {_n(fund.pb_ratio)}"],
            ["Revenue growth", _pct(fund.revenue_growth * 100) if fund.revenue_growth is not None else "—"],
            ["Profit margin", _pct(fund.profit_margin * 100) if fund.profit_margin is not None else "—"],
            ["Market cap", f"{sym}{fund.market_cap:,.0f}" if fund.market_cap else "—"],
        ])
    else:
        fund_html = "<p class='muted'>No fundamentals stored.</p>"

    # "On this day in past years" — close then vs the current price.
    anchors = anchors_override
    if anchors is None and season:
        try:
            anchors = json.loads(season.anchors_json)
        except (json.JSONDecodeError, TypeError):
            anchors = []
    anchors = anchors or []
    if anchors:
        anchor_rows = []
        for an in anchors:
            label = "earliest on record" if an.get("years_ago") == "max" \
                else f"{an.get('years_ago')}y ago"
            close = an.get("close")
            delta = None
            if isinstance(close, (int, float)) and close and isinstance(a.price, (int, float)):
                delta = (a.price / close - 1.0) * 100
            anchor_rows.append([
                _e(label), _e(an.get("date")), f"{sym}{_n(close)}",
                _pct(delta) + (" since" if delta is not None else ""),
            ])
        anchors_html = ("<h3>On this day in past years</h3>"
                        + _table(["When", "Date", "Close", "Move to today"], anchor_rows))
    else:
        anchors_html = ("<h3>On this day in past years</h3>"
                        "<p class='muted'>No deep price history available.</p>")

    if season:
        try:
            windows = json.loads(season.windows_json)
        except (json.JSONDecodeError, TypeError):
            windows = []
        rows = []
        for w in windows:
            per_year = w.get("per_year", [])
            if not per_year:
                continue
            returns = [e["return"] for e in per_year]
            avg = sum(returns) / len(returns) * 100
            win = sum(1 for r in returns if r > 0) / len(returns) * 100
            rows.append([_e(w.get("label", w.get("key"))), _pct(avg),
                         f"{win:.0f}%", str(len(returns))])
        season_html = anchors_html + _table(["Window", "Avg move", "Win rate", "Years"], rows)
    else:
        season_html = anchors_html + "<p class='muted'>No seasonality window stats stored.</p>"

    news_html = _table(
        ["Date", "Headline", "Source"],
        [[_e(n.seendate[:10]),
          f'<a href="{_e(n.url)}">{_e(n.title)}</a>', _e(n.domain)] for n in news],
    ) if news else "<p class='muted'>No tagged news for this ticker yet.</p>"

    auto_print = "<script>window.addEventListener('load',()=>window.print());</script>" \
        if print_mode else ""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_e(ticker)} — analysis report {generated[:10]}</title>
<style>
  :root {{ color-scheme: light; }}
  * {{ box-sizing: border-box; }}
  body {{
    font: 13px/1.55 -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    color: #262320; background: #faf8f4; margin: 0; padding: 32px 40px;
    max-width: 960px; margin-inline: auto;
  }}
  header.rpt {{ display: flex; align-items: baseline; gap: 14px; flex-wrap: wrap;
    border-bottom: 2px solid #262320; padding-bottom: 10px; }}
  h1 {{ font-size: 26px; margin: 0; letter-spacing: 0.02em; }}
  .directive {{ font-weight: 700; color: {dir_color}; font-size: 15px;
    text-transform: uppercase; letter-spacing: 0.06em; }}
  .meta {{ margin-left: auto; color: #6d675e; font-size: 11.5px; text-align: right; }}
  h2 {{ font-size: 14px; text-transform: uppercase; letter-spacing: 0.08em;
    border-bottom: 1px solid #d8d3ca; padding-bottom: 4px; margin: 26px 0 10px; }}
  h3 {{ font-size: 12.5px; margin: 14px 0 6px; }}
  table {{ border-collapse: collapse; width: 100%; font-size: 12px; }}
  th, td {{ text-align: left; padding: 5px 10px 5px 0; vertical-align: top;
    border-bottom: 1px solid #e7e2d9; }}
  th {{ color: #6d675e; font-weight: 600; font-size: 10.5px;
    text-transform: uppercase; letter-spacing: 0.05em; }}
  .muted {{ color: #6d675e; }}
  ol {{ padding-left: 20px; margin: 6px 0; }}
  li {{ margin-bottom: 3px; }}
  a {{ color: #2f6f9f; }}
  svg {{ width: 100%; height: auto; background: #fffdf9; border: 1px solid #e7e2d9; }}
  .disclaimer {{ margin-top: 28px; padding: 10px 14px; border: 1px solid #d8b45a;
    background: #faf3df; font-size: 11.5px; color: #6d5a24; }}
  @media print {{
    body {{ padding: 0; max-width: none; background: #fff; }}
    section {{ break-inside: avoid; }}
    a {{ color: inherit; text-decoration: none; }}
    @page {{ size: A4; margin: 16mm 14mm; }}
  }}
</style>
</head>
<body>
<header class="rpt">
  <h1>{_e(ticker)}</h1>
  <span class="directive">{_e(a.directive)}</span>
  <span>conviction {a.conviction:+d}</span>
  <span>price {sym}{_n(a.price)}</span>
  <span class="meta">Stock Signal Dashboard<br>
    analysis {_e(a.computed_at)} · report {_e(generated)}</span>
</header>
{_section("Price chart — daily", chart)}
{_section("Trade plan", _plan_section(a))}
{_section("Why — the reasoning", reasons)}
{_section("Trend & structure", structure)}
{_section("Support & resistance", sr)}
{_section("Patterns", patterns)}
{_section("Unfilled gaps", gaps)}
{_section("Boom Score components", boom_html)}
{_section("Fundamentals", fund_html)}
{_section("Seasonality", season_html)}
{_section("Recent news", news_html)}
<p class="disclaimer"><strong>Signals, not predictions.</strong> {_e(a.disclaimer)}
Every figure above is derived from the public data sources shown in the dashboard,
computed at the timestamps indicated. Nothing here is investment advice.</p>
{auto_print}
</body>
</html>"""
