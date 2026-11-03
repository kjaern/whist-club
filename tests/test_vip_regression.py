"""Permanent, independently specified VIP examples (per opponent)."""
from fractions import Fraction
import pytest
from whistapp.bids import get_bid


@pytest.mark.parametrize("key,bid,actual,expected", [
    ("vip3", 10, 10, Fraction(16)),
    ("vip3", 10, 11, Fraction(20)),
    ("vip2", 9, 10, Fraction(10)),
    ("vip1", 9, 10, Fraction(8)),
])
def test_vip_examples(key, bid, actual, expected):
    result = get_bid(key).score({}, active=[1, 2, 3, 4],
                                declarer=1, partner=2, target=bid, actual=actual, level=int(key[-1]))
    assert result.won is True
    assert result.contract_points == expected
    assert result.scores == {1: expected, 2: expected, 3: -expected, 4: -expected}


@pytest.mark.parametrize("level,surcharge", [(1, Fraction(1, 2)), (2, Fraction(1)), (3, Fraction(2))])
def test_vip_surcharges(level, surcharge):
    prices = get_bid(f"vip{level}").configuration(level=level)["values"]
    assert prices == [Fraction(n, 2) + surcharge for n in range(2, 8)]
