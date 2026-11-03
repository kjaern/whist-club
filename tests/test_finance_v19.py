from fractions import Fraction

from whistapp.finance import fee_amount, placement_amounts, settlement_rows
from whistapp.database import connect, init_db
import whistapp.database as database

def test_club_finance_configuration():
    assert fee_amount()==40
    assert placement_amounts()==[0,25,75,115,135,150]
    assert sum(placement_amounts())==500

def test_settlement_order_and_ties(tmp_path,monkeypatch):
    monkeypatch.setattr(database,'DB_PATH',tmp_path/'test.db')
    init_db()
    with connect() as db:
        sid=db.execute("INSERT INTO sessions(title,is_club) VALUES ('A',1)").lastrowid
        for i in range(6): db.execute('INSERT INTO club_members(name) VALUES (?)',(f'P{i}',))
        # No hands: all members are tied, so the placement pot is split evenly.
        rows = settlement_rows(db, sid, placement_amounts())
        payments = [Fraction(numerator, denominator) for _, _, numerator, denominator in rows]
        assert len(rows) == 6
        assert payments == [Fraction(250, 3)] * 6
        assert sum(payments) == 500
