"""Tests for cherrypit.calendar.

The 2026 cases are golden-master checks against MEICAgent's current hardcoded config lists — the
computed calendar must reproduce them exactly *where the config is correct*. The one deliberate
exception is triple-witching June 2026: the config has 2026-06-18 (a Thursday), which is a bug; the
real 3rd Friday is 2026-06-19, and this suite pins the correct value.
"""

from datetime import date

import pytest

from cherrypit import calendar as cal

# --- MEICAgent config lists, 2026 (the golden master) -------------------------------------------
CFG_HOLIDAYS_2026 = {
    date(2026, 1, 1), date(2026, 1, 19), date(2026, 2, 16), date(2026, 4, 3), date(2026, 5, 25),
    date(2026, 6, 19), date(2026, 7, 3), date(2026, 9, 7), date(2026, 11, 26), date(2026, 12, 25),
}
CFG_QUARTERLY_2026 = [date(2026, 3, 31), date(2026, 6, 30), date(2026, 9, 30), date(2026, 12, 31)]
CFG_FOMC_2026 = [
    date(2026, 1, 28), date(2026, 3, 18), date(2026, 4, 29), date(2026, 6, 10),
    date(2026, 7, 29), date(2026, 9, 16), date(2026, 10, 28), date(2026, 12, 16),
]


def test_nyse_holidays_2026_matches_config_exactly():
    assert cal.nyse_holidays(2026) == CFG_HOLIDAYS_2026


def test_quarterly_expiry_2026_matches_config():
    assert cal.quarterly_expiry_dates(2026) == CFG_QUARTERLY_2026


def test_fomc_2026_matches_config():
    assert cal.fomc_dates(2026) == CFG_FOMC_2026


def test_triple_witching_2026_is_the_correct_third_fridays():
    # Correct 3rd Fridays — note June is the 19th, NOT the config's erroneous 18th.
    assert cal.triple_witching_dates(2026) == [
        date(2026, 3, 20), date(2026, 6, 19), date(2026, 9, 18), date(2026, 12, 18),
    ]


def test_config_triple_witching_june_bug_is_a_thursday():
    """Documents the config drift the shared calendar fixes: 2026-06-18 is a Thursday."""
    assert date(2026, 6, 18).weekday() == cal.THU
    assert cal.triple_witching_dates(2026)[1] == date(2026, 6, 19)  # the real one


# --- computation helpers -----------------------------------------------------------------------
@pytest.mark.parametrize("year,expected", [
    (2024, date(2024, 3, 31)),
    (2025, date(2025, 4, 20)),
    (2026, date(2026, 4, 5)),
])
def test_easter(year, expected):
    assert cal.easter(year) == expected


def test_good_friday_is_a_holiday():
    assert date(2026, 4, 3) in cal.nyse_holidays(2026)  # Good Friday 2026


def test_weekend_observance_independence_day_2026():
    # July 4 2026 is a Saturday -> observed Friday July 3.
    assert date(2026, 7, 3) in cal.nyse_holidays(2026)
    assert date(2026, 7, 4) not in cal.nyse_holidays(2026)


def test_juneteenth_only_from_2022():
    assert date(2021, 6, 19) not in cal.nyse_holidays(2021)
    assert cal._observed(date(2023, 6, 19)) in cal.nyse_holidays(2023)


def test_is_trading_day_weekend_and_holiday():
    assert cal.is_trading_day(date(2026, 7, 6)) is True    # a normal Monday
    assert cal.is_trading_day(date(2026, 7, 4)) is False   # Saturday
    assert cal.is_trading_day(date(2026, 12, 25)) is False # Christmas


def test_next_and_previous_trading_day_skip_holidays_and_weekends():
    # Thu 2026-12-24 -> next skips Christmas (Fri 25) and the weekend -> Mon 2026-12-28.
    assert cal.next_trading_day(date(2026, 12, 24)) == date(2026, 12, 28)
    # Back from Sat 2026-07-04 -> Fri is the Independence holiday -> Thu 2026-07-02.
    assert cal.previous_trading_day(date(2026, 7, 4)) == date(2026, 7, 2)


def test_fomc_year_known_and_unknown():
    assert cal.fomc_year_known(2026) is True
    assert cal.fomc_year_known(2099) is False
    assert cal.fomc_dates(2099) == []  # fail-safe empty for unbundled years
