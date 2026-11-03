from fractions import Fraction
from whistapp.rules import load_rules,calculate,distribute,special_outcome,distribute_special,distribute_paskrig
r=load_rules()['bids']
def test_win():
    assert calculate(r['sans'],8,9)==(True,Fraction(6))
def test_loss_example():
    assert calculate(r['gode'],9,8)==(False,Fraction(-6))
def test_four_player_distribution():
    assert distribute(Fraction(6),1,[1,2,3,4])=={1:18,2:-6,3:-6,4:-6}
def test_special():
    assert special_outcome(r['sol'],1)==(True,Fraction(9))
    assert special_outcome(r['sol'],2)==(False,Fraction(9))
    assert special_outcome(r['ren_sol'],0)==(True,Fraction(18))
    assert special_outcome(r['ren_sol'],1)==(False,Fraction(18))
    assert special_outcome(r['bordlaegger'],1)==(False,Fraction(36))
    assert distribute_special({1:(False,Fraction(9))},[1,2,3,4])=={1:-9,2:3,3:3,4:3}
    assert distribute_special({1:(False,Fraction(9)),2:(False,Fraction(9))},[1,2,3,4])=={1:Fraction(-9,2),2:Fraction(-9,2),3:Fraction(9,2),4:Fraction(9,2)}
    assert distribute_special({1:(True,Fraction(9))},[1,2,3,4])=={1:9,2:-3,3:-3,4:-3}
    assert distribute_special({1:(True,Fraction(9)),2:(False,Fraction(9))},[1,2,3,4])=={1:9,2:-9,3:0,4:0}
    assert distribute_special({1:(True,Fraction(9)),2:(True,Fraction(9))},[1,2,3,4])=={1:Fraction(9,2),2:Fraction(9,2),3:Fraction(-9,2),4:Fraction(-9,2)}
def test_paskrig():
    assert distribute_paskrig({1:9,2:7,3:8,4:6},[1,2,3,4])=={1:-9,2:3,3:3,4:3}
def test_paskrig_tied():
    assert distribute_paskrig({1:9,2:9,3:7,4:6},[1,2,3,4])=={1:Fraction(-9,2),2:Fraction(-9,2),3:Fraction(9,2),4:Fraction(9,2)}


def test_partnership_not_doubled():
    won, amount = calculate(r["gode"], 9, 10)
    assert won and amount == 12
    assert distribute(amount, 1, [1,2,3,4], partner=2) == {1:12,2:12,3:-12,4:-12}

def test_self_partner_solo():
    assert distribute(Fraction(12), 1, [1,2,3,4], partner=1) == {1:36,2:-12,3:-12,4:-12}
