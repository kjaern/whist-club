from fractions import Fraction
from whistapp.rules import distribute, distribute_special

def test_partner():
    assert distribute(Fraction(2),1,[1,2,3,4],2)=={1:2,2:2,3:-2,4:-2}
def test_self_partner():
    assert distribute(Fraction(2),1,[1,2,3,4],1)=={1:6,2:-2,3:-2,4:-2}
def test_independent_specials():
    assert distribute_special({1:(True,Fraction(9)),2:(False,Fraction(9))},[1,2,3,4])=={1:9,2:-9,3:0,4:0}
