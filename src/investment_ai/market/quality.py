"""Deterministic quality findings without filling gaps or guessing their cause."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from fractions import Fraction

from investment_ai.domain.actions import CorporateAction
from investment_ai.domain.bars import DailyBar
from investment_ai.domain.provenance import require, require_utc
from investment_ai.market.adjustments import _known_splits, _split_factor
from investment_ai.market.calendar import Session


@dataclass(frozen=True)
class QualityIssue:
    code: str
    instrument_id: str
    session: date | None
    severity: str


@dataclass(frozen=True)
class QualityReport:
    issues: tuple[QualityIssue, ...]
    reliable: bool


def _valid_ohlcv(bar: DailyBar) -> bool:
    return (all(v > 0 for v in (bar.open, bar.high, bar.low, bar.close))
            and bar.low <= min(bar.open, bar.close)
            and bar.high >= max(bar.open, bar.close)
            and bar.volume >= 0)


def _observation(bar: DailyBar) -> tuple:
    # Re-observation provenance alone does not make identical market data conflict.
    return (bar.open, bar.high, bar.low, bar.close, bar.volume, bar.currency,
            bar.adjustment, bar.coverage_profile, bar.source_start, bar.source_end)


def validate_history(bars: Sequence[DailyBar], actions: Sequence[CorporateAction],
                     expected: Sequence[Session], as_of: datetime,
                     actions_complete: bool) -> QualityReport:
    """Check the caller's cutoff-bounded bars and expected schedule.

    Expected sessions are authoritative (including frozen replay schedules).
    Missing observations have unknown cause. An empty batch uses an empty
    instrument ID when neither bars nor actions identify the instrument.
    """
    require_utc(as_of, "as_of")
    require(type(actions_complete) is bool, "actions_complete")
    splits = _known_splits(actions, as_of)
    labels = sorted({s.label for s in expected})
    instruments = {b.instrument_id for b in bars}
    if not instruments:
        instruments = {a.instrument_id for a in actions} or {""}
    issues = set()

    def add(code: str, instrument: str, session: date | None = None) -> None:
        issues.add(QualityIssue(code, instrument, session, "error"))

    for instrument in sorted(instruments):
        if not actions_complete:
            add("incomplete_actions", instrument)
        by_session = {}
        invalid = set()
        for bar in bars:
            if bar.instrument_id != instrument:
                continue
            if not _valid_ohlcv(bar):
                add("invalid_ohlcv", instrument, bar.session)
                invalid.add(bar.session)
            previous = by_session.get(bar.session)
            if previous is not None and _observation(previous) != _observation(bar):
                add("conflicting_duplicate", instrument, bar.session)
                invalid.add(bar.session)
            by_session[bar.session] = bar
        for label in labels:
            if label not in by_session:
                add("missing_session", instrument, label)

        # Only compare adjacent expected sessions; do not compress across gaps,
        # invalid prices, or ambiguous duplicate observations.
        for earlier, later in zip(labels, labels[1:]):
            if any(d not in by_session or d in invalid for d in (earlier, later)):
                continue
            left, right = by_session[earlier], by_session[later]
            left_close = Fraction(left.close) * _split_factor(left, splits)
            right_close = Fraction(right.close) * _split_factor(right, splits)
            if abs(right_close / left_close - 1) > Fraction(1, 2):
                add("unexplained_jump", instrument, later)
    ordered = tuple(sorted(issues, key=lambda i: (i.instrument_id, i.session or date.min, i.code)))
    return QualityReport(ordered, reliable=not ordered)
