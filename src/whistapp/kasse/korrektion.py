"""Korrektion: a signed adjustment of one member's balance, or of the club's cash.

Used for mistakes found after a night is final, starting balances and cash count differences.
No money moves between member and club; the books are only set right.
"""

from .base import Effect, EntryType


class Korrektion(EntryType):
    key = "korrektion"
    label = "Korrektion"
    description = ("Retter et medlems saldo (plus: medlemmet har mere til gode, minus: skylder mere) "
                   "eller klubkassen (plus: kassen har mere). Skal have en forklaring.")
    target = "member_or_cash"
    signed = True
    note_required = True

    def effect(self, amount, member_id, bucket_id):
        if member_id is None:
            return Effect(cash=amount)
        return Effect(members={member_id: amount})
