"""金额年度、期间和时点分别解析；只消费当前列的原始声明。"""

from dataclasses import dataclass, asdict
from datetime import date
import re
import unicodedata

TEMPORAL_VERSION = "fiscal-time-v1-explicit-column-scope"
_DATE = re.compile(
    r"(?:(?P<cn_year>20\d{2})年(?P<cn_month>\d{1,2})月(?P<cn_day>\d{1,2})日?"
    r"|(?P<iso_year>20\d{2})(?P<separator>[-/])(?P<iso_month>\d{1,2})"
    r"(?P=separator)(?P<iso_day>\d{1,2}))(?![\d月])"
)
_YEAR = re.compile(r"(20\d{2})年")
_QUARTERS = {"一": 1, "二": 2, "三": 3, "四": 4, "1": 1, "2": 2, "3": 3, "4": 4}


@dataclass(frozen=True)
class TemporalScope:
    amount_year: int | None
    period: str | None
    as_of: str | None = None
    precision: str = "year"
    reasons: tuple[str, ...] = ()
    explicit_years: tuple[int, ...] = ()
    column_literal: str = ""

    def to_dict(self):
        return asdict(self)


def resolve_temporal_scope(header, *, report_year=None, default_amount_year=None):
    literal = str(header or "")
    text = "".join(unicodedata.normalize("NFKC", literal).split()).replace("—", "-")
    years = {int(value) for value in _YEAR.findall(text)}
    observed_dates = list(_DATE.finditer(text))
    years.update(int(value) for value in re.findall(r"(20\d{2})(?=[-/]\d)", text))
    default = default_amount_year if default_amount_year is not None else report_year
    period = "上年" if "上年" in text else "年末" if "年末" in text else "年初" if "年初结转" in text else "本年"
    amount_year = next(iter(years)) if len(years) == 1 else (default - 1 if default and "上年" in text else default)
    common = {"explicit_years": tuple(sorted(years)), "column_literal": literal}
    if len(years) > 1:
        return TemporalScope(None, None, reasons=("multiple_explicit_amount_years",), **common)
    if years and default is not None and (
        "上年" in text and amount_year != default - 1 or "本年" in text and amount_year != default
    ):
        return TemporalScope(amount_year, None, reasons=("explicit_relative_amount_year_conflict",), **common)
    dates = []
    try:
        dates = [date(int(m["cn_year"] or m["iso_year"]), int(m["cn_month"] or m["iso_month"]),
                      int(m["cn_day"] or m["iso_day"])).isoformat() for m in observed_dates]
    except ValueError:
        return TemporalScope(amount_year, None, reasons=("invalid_explicit_timepoint",), **common)
    if not dates and re.search(r"20\d{2}(?:[-/]\d{1,2}[-/]\d+|年\d{1,2}月\d+日?)", text):
        return TemporalScope(amount_year, None, reasons=("invalid_explicit_timepoint_format",), **common)
    distinct_dates = list(dict.fromkeys(dates))
    if len(distinct_dates) > 1:
        if len(distinct_dates) == 2 and re.fullmatch(r"(?:~|至|到|-)", text[observed_dates[0].end():observed_dates[1].start()]) and distinct_dates[0] <= distinct_dates[1]:
            return TemporalScope(amount_year, "/".join(distinct_dates), precision="day_range", **common)
        return TemporalScope(amount_year, None, reasons=("multiple_explicit_timepoints",), **common)
    if dates:
        return TemporalScope(amount_year, "时点", as_of=dates[0], precision="day", **common)
    quarters = {_QUARTERS[m] for m in re.findall(r"第?([一二三四1-4])季度", text)}
    if len(quarters) > 1:
        return TemporalScope(amount_year, None, reasons=("multiple_explicit_quarters",), **common)
    if quarters:
        return TemporalScope(amount_year, f"第{next(iter(quarters))}季度", precision="quarter", **common)
    month_range = re.search(r"(?:20\d{2}年)?(\d{1,2})(?:-|~|至|到)(\d{1,2})月", text)
    if month_range:
        first, last = map(int, month_range.groups())
        if not 1 <= first <= last <= 12:
            return TemporalScope(amount_year, None, reasons=("invalid_explicit_month_range",), **common)
        return TemporalScope(amount_year, f"{first}-{last}月", precision="month_range", **common)
    months = {int(value) for value in re.findall(r"(?<!\d)(\d{1,2})月", text)}
    if months:
        if len(months) != 1 or not 1 <= next(iter(months)) <= 12:
            return TemporalScope(amount_year, None, reasons=("invalid_or_conflicting_explicit_months",), **common)
        month = next(iter(months))
        if "截至" in text or "截止" in text or "月末" in text:
            # 原文只到月份时不虚构具体日期；月精度与日精度不是同一身份。
            return TemporalScope(amount_year, "时点", as_of=f"{amount_year:04}-{month:02}" if amount_year else None,
                                 precision="month", **common)
        return TemporalScope(amount_year, f"{month}月", precision="month", **common)
    return TemporalScope(amount_year, period, **common)
