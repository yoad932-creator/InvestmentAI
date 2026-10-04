"""Pinned U.S. equity sessions and explicitly assumed daily-bar readiness.

Exported UTC records are the schedule authority during snapshot replay.
Readiness is a conservative policy assumption, not vendor finality evidence.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from investment_ai.domain.provenance import require, require_date, require_utc
from investment_ai.errors import DataError


@dataclass(frozen=True)
class Session:
    label: date
    open_at: datetime
    close_at: datetime

    def __post_init__(self) -> None:
        require_date(self.label, "session label")
        require_utc(self.open_at, "session open")
        require_utc(self.close_at, "session close")
        require(self.open_at < self.close_at, "session interval")


def calendar_name(exchange: str) -> str:
    """Explicit supported daily-equity policy mapping; unknown venues fail."""
    if exchange not in ("XNYS", "XNAS", "ARCX", "NYSE", "NASDAQ", "NYSE Arca"):
        raise DataError("unsupported_exchange", "No supported daily exchange schedule.")
    return "XNYS"


class SessionCalendar:
    def __init__(self, ready_hour_et: int = 8,
                 frozen_sessions: Sequence[Session] | None = None):
        require(type(ready_hour_et) is int and 0 <= ready_hour_et <= 23, "ready hour")
        self._ready_hour = ready_hour_et
        self._new_york = ZoneInfo("America/New_York")
        self._frozen = None if frozen_sessions is None else tuple(frozen_sessions)
        if self._frozen is not None:
            require(all(isinstance(s, Session) for s in self._frozen), "frozen sessions")
            require(len({s.label for s in self._frozen}) == len(self._frozen),
                    "duplicate frozen sessions")
            self._frozen = tuple(sorted(self._frozen, key=lambda s: s.label))

    def sessions(self, start: date, end: date) -> tuple[Session, ...]:
        require_date(start, "session range start")
        require_date(end, "session range end")
        require(start <= end, "session range")
        if self._frozen is not None:
            return tuple(s for s in self._frozen if start <= s.label <= end)
        # Explicit bounds avoid the library's clock-dependent default range.
        # Padding allows an entirely closed requested interval to return ().
        import exchange_calendars

        try:
            calendar = exchange_calendars.get_calendar(
                "XNYS", start=start - timedelta(days=370), end=end + timedelta(days=370))
            schedule = calendar.schedule.loc[start.isoformat():end.isoformat()]
        except (ValueError, OverflowError) as exc:
            raise DataError("calendar_coverage", "Exchange schedule unavailable for range.") from exc
        return tuple(Session(label.date(), row["open"].to_pydatetime(),
                             row["close"].to_pydatetime())
                     for label, row in schedule.iterrows())

    def export(self, start: date, end: date) -> tuple[Session, ...]:
        """Freeze the exact UTC schedule used, including early/exceptional closes."""
        return self.sessions(start, end)

    def ready_at(self, session: date) -> datetime:
        require_date(session, "session")
        matches = self.sessions(session, session)
        if not matches:
            raise DataError("unknown_session", "Session absent from exchange schedule.")
        assumed = datetime.combine(session + timedelta(days=1), time(self._ready_hour),
                                   tzinfo=self._new_york).astimezone(timezone.utc)
        return max(assumed, matches[0].close_at)

    def expected_session(self, as_of: datetime) -> date:
        """Latest scheduled session whose conservative readiness has elapsed."""
        require_utc(as_of, "as_of")
        local_date = as_of.astimezone(self._new_york).date()
        sessions = (self._frozen if self._frozen is not None else
                    self.sessions(local_date - timedelta(days=370), local_date))
        for session in reversed(sessions):
            if session.label <= local_date and self.ready_at(session.label) <= as_of:
                return session.label
        raise DataError("calendar_coverage", "No available session in exchange schedule.")
