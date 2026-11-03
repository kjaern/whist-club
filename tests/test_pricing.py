from fractions import Fraction
from whistapp.rules import load_rules,calculate

def test_derived_prices():
    bids=load_rules()['bids']
    for i,base in enumerate(bids['almindelige']['values']):
        for kind in ('halve','sans','gode'):
            assert Fraction(str(bids[kind]['values'][i]))==2*Fraction(str(base))
        for level in (1,2,3):
            assert Fraction(str(bids[f'vip{level}']['values'][i]))==Fraction(str(base))+{1: Fraction(1,2), 2: Fraction(1), 3: Fraction(2)}[level]

def test_scoring_uses_derived_prices():
    bids=load_rules()['bids']
    assert calculate(bids['gode'],9,10)==(True,Fraction(12))


def test_permanent_example_8_sans_bid_9_actual_wins_6_per_opponent():
    # House-rule regression test: do not change this expected value.
    bids = load_rules()['bids']
    assert calculate(bids['sans'], 8, 9) == (True, Fraction(6))

def test_permanent_example_9_gode_bid_8_actual_loses_6_per_opponent():
    # House-rule regression test: do not change this expected value.
    bids = load_rules()['bids']
    assert calculate(bids['gode'], 9, 8) == (False, Fraction(-6))
