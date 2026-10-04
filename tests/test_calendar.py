"""Offline, independently specified exchange dates and UTC cutoffs."""

from datetime import date, datetime, timedelta, timezone

import pytest

from investment_ai.errors import DataError
from investment_ai.market.calendar import Session, SessionCalendar, calendar_name
from support import FrozenClock, daily_bar


def utc(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


@pytest.fixture
def clock():
    return FrozenClock(utc("2024-07-06T11:59:59Z"))


def test_holiday_and_half_day():
    sessions = SessionCalendar().sessions(date(2024, 7, 3), date(2024, 7, 5))
    assert tuple(s.label for s in sessions) == (date(2024, 7, 3), date(2024, 7, 5))
    assert sessions[0].close_at == utc("2024-07-03T17:00:00Z")
    assert sessions[1].close_at == utc("2024-07-05T20:00:00Z")


@pytest.mark.parametrize("day,opening,ready", [
    ("2024-03-08", "2024-03-08T14:30:00Z", "2024-03-09T13:00:00Z"),
    ("2024-03-11", "2024-03-11T13:30:00Z", "2024-03-12T12:00:00Z"),
    ("2024-11-01", "2024-11-01T13:30:00Z", "2024-11-02T12:00:00Z"),
    ("2024-11-04", "2024-11-04T14:30:00Z", "2024-11-05T13:00:00Z"),
])
def test_dst(day, opening, ready):
    calendar = SessionCalendar()
    label = date.fromisoformat(day)
    assert calendar.sessions(label, label)[0].open_at == utc(opening)
    assert calendar.ready_at(label) == utc(ready)


def test_ready_cutoff(clock):
    calendar = SessionCalendar()
    assert calendar.ready_at(date(2024, 7, 5)) == utc("2024-07-06T12:00:00Z")
    assert calendar.expected_session(clock.now()) == date(2024, 7, 3)
    clock.instant = utc("2024-07-06T12:00:00Z")
    assert calendar.expected_session(clock.now()) == date(2024, 7, 5)
    assert calendar.expected_session(utc("2024-07-08T11:59:59Z")) == date(2024, 7, 5)
    assert calendar.expected_session(utc("2024-07-08T20:00:00Z")) == date(2024, 7, 5)
    assert calendar.expected_session(utc("2024-07-09T12:00:00Z")) == date(2024, 7, 8)


def test_frozen_schedule_replay(monkeypatch):
    original = SessionCalendar().export(date(2024, 7, 1), date(2024, 7, 8))
    assert len(original) == 5
    import exchange_calendars

    def forbidden(*args, **kwargs):
        pytest.fail("Frozen replay consulted the installed exchange calendar")

    monkeypatch.setattr(exchange_calendars, "get_calendar", forbidden)
    replay = SessionCalendar(frozen_sessions=original)
    assert replay.export(date(2024, 7, 1), date(2024, 7, 8)) == original
    assert replay.ready_at(date(2024, 7, 3)) == utc("2024-07-04T12:00:00Z")
    assert replay.expected_session(utc("2024-07-06T12:00:00Z")) == date(2024, 7, 5)
    with pytest.raises(DataError):
        replay.ready_at(date(2024, 7, 9))


def test_daily_boundary_not_exchange_open():
    bar = daily_bar()
    session = SessionCalendar().sessions(bar.session, bar.session)[0]
    assert session.open_at == utc("2025-01-02T14:30:00Z")
    assert bar.source_start == utc("2025-01-02T05:00:00Z")
    assert bar.source_end == utc("2025-01-03T05:00:00Z")


@pytest.mark.parametrize("host_timezone", ["UTC", "Asia/Jerusalem", "Pacific/Honolulu"])
def test_host_timezone_is_irrelevant(monkeypatch, host_timezone):
    monkeypatch.setenv("TZ", host_timezone)
    assert SessionCalendar().ready_at(date(2024, 3, 11)) == utc("2024-03-12T12:00:00Z")


@pytest.mark.parametrize("exchange", ["XNYS", "XNAS", "ARCX", "NYSE", "NASDAQ", "NYSE Arca"])
def test_supported_exchange_mapping(exchange):
    assert calendar_name(exchange) == "XNYS"


def test_unknown_exchange_rejected():
    with pytest.raises(DataError):
        calendar_name("XLON")


def test_exceptional_closure():
    assert SessionCalendar().sessions(date(2012, 10, 29), date(2012, 10, 30)) == ()


@pytest.mark.parametrize("instant", [datetime(2024, 7, 6, 12),
    datetime(2024, 7, 6, 15, tzinfo=timezone(timedelta(hours=3)))])
def test_non_utc_cutoff_rejected(instant):
    with pytest.raises(DataError):
        SessionCalendar().expected_session(instant)


@pytest.mark.parametrize("hour", [-1, 24, True, 8.5])
def test_invalid_ready_hour(hour):
    with pytest.raises(DataError):
        SessionCalendar(ready_hour_et=hour)


def test_unknown_session_and_invalid_range():
    calendar = SessionCalendar()
    with pytest.raises(DataError):
        calendar.ready_at(date(2024, 7, 4))
    with pytest.raises(DataError):
        calendar.sessions(date(2024, 7, 5), date(2024, 7, 3))


def test_frozen_empty_has_no_expected_session():
    with pytest.raises(DataError):
        SessionCalendar(frozen_sessions=()).expected_session(utc("2024-07-06T12:00:00Z"))


def test_frozen_never_ready_before_close():
    session = Session(date(2024, 7, 5), utc("2024-07-05T13:30:00Z"),
                      utc("2024-07-06T14:00:00Z"))
    assert SessionCalendar(frozen_sessions=(session,)).ready_at(session.label) == session.close_at


def test_frozen_invalid_records_rejected():
    with pytest.raises(DataError):
        Session(date(2024, 7, 5), datetime(2024, 7, 5, 13), utc("2024-07-05T20:00:00Z"))
    with pytest.raises(DataError):
        Session(date(2024, 7, 5), utc("2024-07-05T20:00:00Z"), utc("2024-07-05T13:30:00Z"))
    session = Session(date(2024, 7, 5), utc("2024-07-05T13:30:00Z"), utc("2024-07-05T20:00:00Z"))
    with pytest.raises(DataError):
        SessionCalendar(frozen_sessions=(session, session))


def test_no_available_session_before_frozen_history():
    session = Session(date(2024, 7, 5), utc("2024-07-05T13:30:00Z"), utc("2024-07-05T20:00:00Z"))
    with pytest.raises(DataError):
        SessionCalendar(frozen_sessions=(session,)).expected_session(utc("2024-07-06T11:59:59Z"))
