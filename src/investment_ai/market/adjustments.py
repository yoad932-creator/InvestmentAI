"""Pure, causal split normalization; cash dividends remain separate facts."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from fractions import Fraction
from zoneinfo import ZoneInfo

from investment_ai.domain.actions import CorporateAction, Split
from investment_ai.domain.bars import DailyBar
from investment_ai.domain.provenance import require, require_utc


@dataclass(frozen=True)
class AdjustedBar:
    session: date
    open: float
    high: float
    low: float
    close: float
    volume: float


def _known_splits(actions: Sequence[CorporateAction], as_of: datetime) -> tuple[Split, ...]:
    require_utc(as_of, "as_of")
    analysis_date = as_of.astimezone(ZoneInfo("America/New_York")).date()
    return tuple(sorted((a for a in actions if isinstance(a, Split)
                         and a.effective_date <= analysis_date
                         and a.provenance.available_at <= as_of),
                        key=lambda a: a.effective_date))


def _split_factor(bar: DailyBar, splits: Sequence[Split]) -> Fraction:
    factor = Fraction(1)
    for action in splits:
        if action.instrument_id == bar.instrument_id and bar.session < action.effective_date:
            factor *= Fraction(action.split_from) / Fraction(action.split_to)
    return factor


def adjust_splits(bars: Sequence[DailyBar], actions: Sequence[CorporateAction],
                  as_of: datetime) -> tuple[AdjustedBar, ...]:
    """Normalize supplied raw bars to effective, known splits at the UTC cutoff.

    Effective dates use New York's date, never the computer's local timezone.
    The caller supplies its cutoff-bounded history; this function preserves its
    order and gaps. Snapshot membership/observation selection belongs upstream.
    Exact rational factors avoid Decimal-context rounding between split events.
    """
    splits = _known_splits(actions, as_of)
    adjusted = []
    for bar in bars:
        require(bar.adjustment == "raw", "raw adjustment basis")
        factor = _split_factor(bar, splits)
        adjusted.append(AdjustedBar(
            bar.session, *(float(Fraction(value) * factor)
                           for value in (bar.open, bar.high, bar.low, bar.close)),
            float(Fraction(bar.volume) / factor)))
    return tuple(adjusted)
