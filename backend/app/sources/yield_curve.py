"""US Treasury yield curve data — no API key required."""
import xml.etree.ElementTree as ET
from datetime import date, timedelta

import httpx

from app.models import YieldPoint

_BASE_URL = (
    "https://home.treasury.gov/resource-center/data-chart-center/"
    "interest-rates/pages/xml"
)
_NS_D = "http://schemas.microsoft.com/ado/2007/08/dataservices"
_NS_M = "http://schemas.microsoft.com/ado/2007/08/dataservices/metadata"


def _float_or_none(elem) -> float | None:
    if elem is None or not elem.text:
        return None
    try:
        return float(elem.text)
    except ValueError:
        return None


def parse_response(xml_text: str) -> list[YieldPoint]:
    root = ET.fromstring(xml_text)
    points: list[YieldPoint] = []
    for props in root.iter(f"{{{_NS_M}}}properties"):
        date_elem = props.find(f"{{{_NS_D}}}NEW_DATE")
        if date_elem is None or not date_elem.text:
            continue
        iso_date = date_elem.text[:10]  # drop T00:00:00
        yr3m = _float_or_none(props.find(f"{{{_NS_D}}}BC_3MONTH"))
        yr2 = _float_or_none(props.find(f"{{{_NS_D}}}BC_2YEAR"))
        yr10 = _float_or_none(props.find(f"{{{_NS_D}}}BC_10YEAR"))
        yr30 = _float_or_none(props.find(f"{{{_NS_D}}}BC_30YEAR"))
        spread = round(yr10 - yr2, 4) if yr10 is not None and yr2 is not None else None
        points.append(YieldPoint(date=iso_date, yr3m=yr3m, yr2=yr2, yr10=yr10, yr30=yr30, spread=spread))
    return points


def fetch(months: int = 3) -> list[YieldPoint]:
    today = date.today()
    month_strs: list[str] = []
    for i in range(months):
        d = (today.replace(day=1) - timedelta(days=i * 28)).replace(day=1)
        month_strs.append(d.strftime("%Y%m"))

    seen: set[str] = set()
    all_points: list[YieldPoint] = []
    with httpx.Client(timeout=30.0) as client:
        for ym in month_strs:
            resp = client.get(
                _BASE_URL,
                params={"data": "daily_treasury_yield_curve", "field_tdr_date_value_month": ym},
            )
            resp.raise_for_status()
            for pt in parse_response(resp.text):
                if pt.date not in seen:
                    seen.add(pt.date)
                    all_points.append(pt)

    return sorted(all_points, key=lambda p: p.date)
