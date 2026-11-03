from fractions import Fraction
from whistapp.bids import get_bid, registered_bids
from whistapp.rules import load_rules, calculate, distribute, distribute_special, distribute_paskrig

def test_registry_matches_rules():
    assert set(registered_bids()) == set(load_rules()['bids'])

def test_ordinary_matches_previous_engine():
    rules = load_rules()['bids']
    for key in ('almindelige','halve','sans','gode','vip1','vip2','vip3'):
        for target in range(8,14):
            for actual in range(14):
                bid=rules[key]
                won, points=calculate(bid,target,actual)
                result=get_bid(key).score(bid,active=[1,2,3,4],declarer=1,partner=2,target=target,actual=actual,**({"level":int(key[-1])} if key.startswith("vip") else {}))
                assert (result.won,result.contract_points,result.scores)==(won,points,distribute(points,1,[1,2,3,4],2))

def test_special_matches_previous_engine():
    rules=load_rules()['bids']
    for key in ('sol','ren_sol','bordlaegger'):
        bid=rules[key]
        for actual in range(14):
            outcomes={1:(actual<=bid['max_allowed_tricks'],Fraction(str(bid['value'])))}
            assert get_bid(key).score(bid,active=[1,2,3,4],declarer=1,actuals={1:actual}).scores==distribute_special(outcomes,[1,2,3,4])
    bid=rules['paskrig']
    actuals={1:2,2:3,3:5,4:3}
    assert get_bid('paskrig').score(bid,active=[1,2,3,4],declarer=1,actuals=actuals).scores==distribute_paskrig(actuals,[1,2,3,4],bid['value'])
