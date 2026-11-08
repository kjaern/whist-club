"""Indbetaling: a member pays money into the club's cash."""

from .base import Effect, EntryType


class Indbetaling(EntryType):
    key = "indbetaling"
    label = "Indbetaling"
    description = "Et medlem betaler til klubkassen. Medlemmets saldo stiger, og kassen får pengene."
    target = "member"

    def effect(self, amount, member_id, bucket_id):
        return Effect(members={member_id: amount}, cash=amount)
