"""cherrypick.core.calendar — one shared market calendar for the whole suite.

Replaces MEICAgent's hand-maintained ``*_2026`` config lists (which drift and can be wrong — the
2026 triple-witching list had 2026-06-18, a *Thursday*, instead of the real 3rd Friday 2026-06-19)
and unifies with EarningsAgent's live calendar. Everything that can be computed from rules is computed
(NYSE holidays, quarterly expiries, triple-witching); the only curated data is the FOMC schedule, which
the Fed announces rather than derives, so it is bundled per year and extended as new years are set.

All functions take/return ``datetime.date``. Weekday convention is Python's: Monday=0 … Sunday=6.
"""

from __future__ import annotations

from datetime import date, timedelta

# Weekday constants (Python convention)
MON, TUE, WED, THU, FRI, SAT, SUN = range(7)

# Curated FOMC announcement days (the second, decision day of each meeting). The Fed *announces*
# these, so they cannot be computed — bundle known years and extend as the Fed publishes new schedules.
_FOMC_DATES: dict[int, tuple[str, ...]] = {
    2025: (
        "2025-01-29", "2025-03-19", "2025-05-07", "2025-06-18",
        "2025-07-30", "2025-09-17", "2025-10-29", "2025-12-10",
    ),
    2026: (
        "2026-01-28", "2026-03-18", "2026-04-29", "2026-06-10",
        "2026-07-29", "2026-09-16", "2026-10-28", "2026-12-16",
    ),
}


# --------------------------------------------------------------------------- date helpers
def nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """The ``n``-th ``weekday`` of ``month`` (n starts at 1). E.g. 3rd Friday = nth_weekday(y,m,FRI,3)."""
    first = date(year, month, 1)
    offset = (weekday - first.weekday()) % 7
    return first + timedelta(days=offset + 7 * (n - 1))


def last_weekday(year: int, month: int, weekday: int) -> date:
    """The last ``weekday`` of ``month`` (e.g. last Monday of May)."""
    if month == 12:
        last = date(year, 12, 31)
    else:
        last = date(year, month + 1, 1) - timedelta(days=1)
    offset = (last.weekday() - weekday) % 7
    return last - timedelta(days=offset)


def easter(year: int) -> date:
    """Gregorian Easter Sunday (Anonymous / Meeus–Jones–Butcher algorithm)."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    lval = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * lval) // 451
    month = (h + lval - 7 * m + 114) // 31
    day = ((h + lval - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def _observed(d: date) -> date:
    """NYSE weekend observance: Saturday -> preceding Friday, Sunday -> following Monday."""
    if d.weekday() == SAT:
        return d - timedelta(days=1)
    if d.weekday() == SUN:
        return d + timedelta(days=1)
    return d


# --------------------------------------------------------------------------- NYSE holidays
def nyse_holidays(year: int) -> set[date]:
    """The set of NYSE holidays observed within ``year`` (computed from the standard rules)."""
    hols: set[date] = set()

    # New Year's Day. Sunday -> Monday; a Saturday New Year is observed on the *prior* Dec 31, which
    # belongs to the previous year, so it is not added here.
    new_year = _observed(date(year, 1, 1))
    if new_year.year == year:
        hols.add(new_year)

    hols.add(nth_weekday(year, 1, MON, 3))          # MLK Jr. Day — 3rd Monday of January
    hols.add(nth_weekday(year, 2, MON, 3))          # Washington's Birthday — 3rd Monday of February
    hols.add(easter(year) - timedelta(days=2))      # Good Friday — Friday before Easter
    hols.add(last_weekday(year, 5, MON))            # Memorial Day — last Monday of May
    if year >= 2022:                                 # Juneteenth — NYSE holiday since 2022
        hols.add(_observed(date(year, 6, 19)))
    hols.add(_observed(date(year, 7, 4)))           # Independence Day
    hols.add(nth_weekday(year, 9, MON, 1))          # Labor Day — 1st Monday of September
    hols.add(nth_weekday(year, 11, THU, 4))         # Thanksgiving — 4th Thursday of November
    hols.add(_observed(date(year, 12, 25)))         # Christmas Day
    return hols


def is_holiday(d: date) -> bool:
    return d in nyse_holidays(d.year)


def is_trading_day(d: date) -> bool:
    """A weekday that is not an NYSE holiday. (Half-days are still trading days.)"""
    return d.weekday() < SAT and not is_holiday(d)


def next_trading_day(d: date) -> date:
    nxt = d + timedelta(days=1)
    while not is_trading_day(nxt):
        nxt += timedelta(days=1)
    return nxt


def previous_trading_day(d: date) -> date:
    prev = d - timedelta(days=1)
    while not is_trading_day(prev):
        prev -= timedelta(days=1)
    return prev


# --------------------------------------------------------------------------- expiries
def triple_witching_dates(year: int) -> list[date]:
    """3rd Friday of March, June, September, December (simultaneous expiry of stock options, index
    futures, and index options)."""
    return [nth_weekday(year, m, FRI, 3) for m in (3, 6, 9, 12)]


def is_triple_witching(d: date) -> bool:
    return d in triple_witching_dates(d.year)


def quarterly_expiry_dates(year: int) -> list[date]:
    """Last *trading* day of each calendar quarter (adjusted back over weekends/holidays)."""
    out: list[date] = []
    for end_month in (3, 6, 9, 12):
        if end_month == 12:
            d = date(year, 12, 31)
        else:
            d = date(year, end_month + 1, 1) - timedelta(days=1)
        while not is_trading_day(d):
            d -= timedelta(days=1)
        out.append(d)
    return out


def is_quarterly_expiry(d: date) -> bool:
    return d in quarterly_expiry_dates(d.year)


# --------------------------------------------------------------------------- FOMC (curated)
def fomc_year_known(year: int) -> bool:
    return year in _FOMC_DATES


def fomc_dates(year: int) -> list[date]:
    """FOMC announcement days for ``year``. Empty list for years not yet bundled — callers that gate
    on FOMC should check :func:`fomc_year_known` and fail safe (treat unknown years conservatively)."""
    return [date.fromisoformat(s) for s in _FOMC_DATES.get(year, ())]


def is_fomc_day(d: date) -> bool:
    return d in set(fomc_dates(d.year))
