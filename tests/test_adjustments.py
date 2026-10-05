"""Hand-calculated, offline split normalization and causal action cutoffs."""

from dataclasses import asdict, replace
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from investment_ai.domain.actions import Dividend, Split
from investment_ai.errors import DataError
from investment_ai.market.adjustments import AdjustedBar, adjust_splits
from support import daily_bar, provenance


AS_OF = datetime(2025, 1, 8, 13, tzinfo=timezone.utc)


def bar(day=2, price="100", volume="100", **changes):
    return daily_bar(session=date(2025, 1, day),
                     source_start=datetime(2025, 1, day, 5, tzinfo=timezone.utc),
                     source_end=datetime(2025, 1, day + 1, 5, tzinfo=timezone.utc),
                     open=Decimal(price), high=Decimal(price), low=Decimal(price),
                     close=Decimal(price), volume=Decimal(volume), **changes)


def split(day=3, before="1", after="2", **changes):
    return Split("fixture-instrument-1", date(2025, 1, day), Decimal(before),
                 Decimal(after), provenance(**changes))


def test_two_for_one_split():
    raw = bar()
    before = asdict(raw)
    result = adjust_splits((raw,), (split(),), AS_OF)
    assert result == (AdjustedBar(date(2025, 1, 2), 50, 50, 50, 50, 200),)
    assert result[0].close * result[0].volume == 10000
    assert asdict(raw) == before


def test_all_ohlc_fields_adjusted():
    raw = replace(bar(), open=Decimal("96"), high=Decimal("104"), low=Decimal("92"))
    assert adjust_splits((raw,), (split(),), AS_OF) == (
        AdjustedBar(date(2025, 1, 2), 48, 52, 46, 50, 200),)


def test_reverse_split():
    result = adjust_splits((bar(price="10", volume="1000"),),
                           (split(before="10", after="1"),), AS_OF)
    assert result == (AdjustedBar(date(2025, 1, 2), 100, 100, 100, 100, 100),)


def test_future_split_ignored():
    raw = bar()
    assert adjust_splits((raw,), (split(day=9),), AS_OF) == (
        AdjustedBar(raw.session, 100, 100, 100, 100, 100),)


def test_late_known_action_ignored():
    action = split(available_at=datetime(2025, 1, 9, 13, tzinfo=timezone.utc))
    assert adjust_splits((bar(),), (action,), AS_OF)[0].close == 100
    assert adjust_splits((bar(),), (action,), action.provenance.available_at)[0].close == 50


def test_effective_day_uses_new_york_date():
    # Jan 3 UTC is still Jan 2 in New York; tomorrow's split is not effective.
    cutoff = datetime(2025, 1, 3, 1, tzinfo=timezone.utc)
    action = split(available_at=datetime(2025, 1, 2, 12, tzinfo=timezone.utc))
    assert adjust_splits((bar(),), (action,), cutoff)[0].close == 100


def test_split_does_not_adjust_effective_day_or_later():
    result = adjust_splits((bar(day=3, price="50"), bar(day=6, price="55")),
                           (split(),), AS_OF)
    assert [b.close for b in result] == [50, 55]
    assert [b.volume for b in result] == [100, 100]


def test_multiple_splits_apply_only_to_earlier_bars():
    result = adjust_splits((bar(), bar(day=3, price="50")),
                           (split(day=6, before="5", after="1"), split()), AS_OF)
    assert [b.close for b in result] == [250, 250]
    assert [b.volume for b in result] == [40, 20]


def test_dividend_not_price_adjustment():
    dividend = Dividend("fixture-instrument-1", date(2025, 1, 3), Decimal("2"),
                        "USD", None, None, None, provenance())
    before = asdict(dividend)
    assert adjust_splits((bar(),), (dividend,), AS_OF) == (
        AdjustedBar(date(2025, 1, 2), 100, 100, 100, 100, 100),)
    assert asdict(dividend) == before


def test_other_instrument_action_ignored():
    action = replace(split(), instrument_id="another-stock")
    assert adjust_splits((bar(),), (action,), AS_OF)[0].close == 100


def test_adjustments_reject_naive_cutoff():
    with pytest.raises(DataError):
        adjust_splits((bar(),), (), datetime(2025, 1, 8))


def test_already_adjusted_bar_rejected():
    with pytest.raises(DataError):
        adjust_splits((bar(adjustment="split_adjusted"),), (split(),), AS_OF)


def test_adjustments_preserve_zero_volume_and_gaps():
    result = adjust_splits((bar(volume="0"), bar(day=6)), (split(),), AS_OF)
    assert tuple(b.session for b in result) == (date(2025, 1, 2), date(2025, 1, 6))
    assert result[0].volume == 0


def test_empty_adjustments():
    assert adjust_splits((), (), AS_OF) == ()
