"""History quality uses expected sessions, never manufactured market bars."""

from dataclasses import asdict, replace
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from investment_ai.errors import DataError
from investment_ai.market.calendar import SessionCalendar
from investment_ai.market.quality import validate_history
from test_adjustments import AS_OF, bar, split


@pytest.fixture
def expected():
    return SessionCalendar().sessions(date(2025, 1, 2), date(2025, 1, 7))


def history():
    return tuple(bar(day=day) for day in (2, 3, 6, 7))


def codes(report):
    assert report is not None
    return {issue.code for issue in report.issues}


def test_valid_history(expected):
    report = validate_history(history(), (), expected, AS_OF, actions_complete=True)
    assert report is not None
    assert report.reliable
    assert report.issues == ()


@pytest.mark.parametrize("changes", [
    {"high": "99"}, {"low": "101"}, {"open": "105"}, {"close": "95"},
    {"open": "0"}, {"high": "0"}, {"low": "0"}, {"close": "0"},
    {"open": "-1"}, {"volume": "-1"},
])
def test_quality_ohlc_volume(expected, changes):
    bad = replace(bar(), **{k: Decimal(v) for k, v in changes.items()})
    report = validate_history((bad,) + history()[1:], (), expected, AS_OF, True)
    assert codes(report) == {"invalid_ohlcv"}
    assert not report.reliable
    issue = report.issues[0]
    assert (issue.instrument_id, issue.session, issue.severity) == (
        "fixture-instrument-1", date(2025, 1, 2), "error")


def test_known_zero_volume_is_not_missing(expected):
    report = validate_history((bar(volume="0"),) + history()[1:], (), expected, AS_OF, True)
    assert report is not None
    assert report.reliable
    assert report.issues == ()


def test_missing_session_not_filled(expected):
    bars = (bar(), bar(day=6), bar(day=7))
    before = tuple(asdict(b) for b in bars)
    report = validate_history(bars, (), expected, AS_OF, True)
    assert codes(report) == {"missing_session"}
    assert not report.reliable
    assert tuple(i.session for i in report.issues) == (date(2025, 1, 3),)
    assert tuple(asdict(b) for b in bars) == before


def test_holiday_is_not_missing():
    expected = SessionCalendar().sessions(date(2025, 1, 1), date(2025, 1, 2))
    report = validate_history((bar(),), (), expected, AS_OF, True)
    assert report is not None
    assert report.reliable
    assert report.issues == ()


def test_conflicting_duplicate(expected):
    report = validate_history(history() + (bar(price="101"),), (), expected, AS_OF, True)
    assert codes(report) == {"conflicting_duplicate"}
    assert not report.reliable
    assert report.issues[0].session == date(2025, 1, 2)


def test_identical_duplicate_is_not_conflicting(expected):
    report = validate_history(history() + (bar(),), (), expected, AS_OF, True)
    assert report is not None
    assert report.reliable
    assert report.issues == ()


@pytest.mark.parametrize("price", ["151", "49"])
def test_unexplained_jump(expected, price):
    bars = (bar(),) + tuple(bar(day=d, price=price) for d in (3, 6, 7))
    report = validate_history(bars, (), expected, AS_OF, True)
    assert codes(report) == {"unexplained_jump"}
    assert not report.reliable
    assert report.issues[0].session == date(2025, 1, 3)


@pytest.mark.parametrize("price", ["150", "50"])
def test_jump_threshold_equality_allowed(expected, price):
    bars = (bar(),) + tuple(bar(day=d, price=price) for d in (3, 6, 7))
    report = validate_history(bars, (), expected, AS_OF, True)
    assert report is not None
    assert report.reliable


@pytest.mark.parametrize("price,before,after", [("25", "1", "4"), ("400", "4", "1")])
def test_explained_split_is_not_jump(expected, price, before, after):
    bars = (bar(),) + tuple(bar(day=d, price=price) for d in (3, 6, 7))
    report = validate_history(bars, (split(before=before, after=after),), expected, AS_OF, True)
    assert report is not None
    assert report.reliable
    assert report.issues == ()


@pytest.mark.parametrize("action", [
    split(day=9, after="4"),
    split(after="4", available_at=datetime(2025, 1, 9, 13, tzinfo=timezone.utc)),
])
def test_future_or_late_known_split_cannot_explain_jump(expected, action):
    bars = (bar(),) + tuple(bar(day=d, price="25") for d in (3, 6, 7))
    assert codes(validate_history(bars, (action,), expected, AS_OF, True)) == {"unexplained_jump"}


def test_actions_fetch_failure_not_empty_history(expected):
    report = validate_history(history(), (), expected, AS_OF, False)
    assert codes(report) == {"incomplete_actions"}
    assert not report.reliable
    assert report.issues[0].session is None
    assert validate_history(history(), (), expected, AS_OF, True).reliable


def test_empty_history_is_unreliable(expected):
    report = validate_history((), (), expected, AS_OF, True)
    assert codes(report) == {"missing_session"}
    assert not report.reliable
    assert {i.session for i in report.issues} == {s.label for s in expected}


def test_unordered_history_has_same_report(expected):
    bars = (bar(), bar(day=6, price="10"), bar(day=7, price="10"))
    first = validate_history(bars, (), expected, AS_OF, False)
    assert codes(first) == {"missing_session", "incomplete_actions"}
    assert validate_history(tuple(reversed(bars)), (), expected, AS_OF, False) == first


def test_quality_rejects_naive_cutoff(expected):
    with pytest.raises(DataError):
        validate_history(history(), (), expected, datetime(2025, 1, 8), True)
