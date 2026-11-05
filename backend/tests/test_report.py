"""Tests for the standalone HTML analysis report (app/report.py)."""
import json

from app import analysis as analysis_engine
from app import db, report
from app.models import OHLCBar, OHLCSeries


def _bars(n=120):
    out = []
    px = 100.0
    for i in range(n):
        px *= 1.003 if (i // 8) % 3 != 2 else 0.998
        date = f"2026-{1 + i // 28:02d}-{1 + i % 28:02d}"
        out.append(OHLCBar(date=date, open=px * 0.995, high=px * 1.01,
                           low=px * 0.985, close=px, volume=1_000_000))
    return out


def _seed(conn, ticker="NOC"):
    bars = _bars()
    db.upsert_ohlc(conn, [OHLCSeries(
        ticker=ticker, interval="daily",
        bars_json=json.dumps([b.model_dump() for b in bars]),
        fetched_at="2026-07-06T00:00:00+00:00",
    )])
    a = analysis_engine.build(ticker, bars, bars[-1].close, account_size=50_000, risk_pct=1.0)
    db.upsert_analyses(conn, [a])
    return a


def test_build_report_renders_core_sections(conn):
    a = _seed(conn)
    html_doc = report.build_report(conn, "noc")
    assert html_doc is not None
    assert html_doc.startswith("<!DOCTYPE html>")
    assert "NOC" in html_doc
    assert a.directive in html_doc
    assert "Trade plan" in html_doc
    assert "Signals, not predictions." in html_doc
    assert "<svg" in html_doc            # embedded price chart
    assert "window.print" not in html_doc  # download variant has no auto-print


def test_build_report_print_mode_adds_autoprint(conn):
    _seed(conn)
    html_doc = report.build_report(conn, "NOC", print_mode=True)
    assert "window.print" in html_doc


def test_build_report_none_without_analysis(conn):
    assert report.build_report(conn, "ZZZQ") is None


def test_render_svg_chart_handles_thin_history():
    assert "Not enough" in report.render_svg_chart([], None)
    assert "Not enough" in report.render_svg_chart(_bars(1), None)
    svg = report.render_svg_chart(_bars(40), None)
    assert svg.startswith("<svg")


def test_build_report_renders_anchor_table(conn):
    _seed(conn)
    anchors = [
        {"years_ago": 1, "date": "2025-07-07", "close": 100.0},
        {"years_ago": "max", "date": "2015-01-02", "close": 20.0},
    ]
    html_doc = report.build_report(conn, "NOC", anchors_override=anchors)
    assert "On this day in past years" in html_doc
    assert "2025-07-07" in html_doc
    assert "earliest on record" in html_doc
    assert "since" in html_doc  # delta vs current price rendered


def test_build_report_anchor_section_honest_when_empty(conn):
    _seed(conn)
    html_doc = report.build_report(conn, "NOC", anchors_override=[])
    assert "On this day in past years" in html_doc
    assert "No deep price history available." in html_doc
