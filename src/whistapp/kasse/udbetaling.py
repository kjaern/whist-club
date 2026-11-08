"""Udbetaling: the club pays money out of its cash to a member."""

from .base import Effect, EntryType


class Udbetaling(EntryType):
    key = "udbetaling"
    label = "Udbetaling"
    description = "Klubkassen betaler et medlem. Kassen har pengene mindre, og medlemmets saldo falder tilsvarende."
    target = "member"

    def effect(self, amount, member_id, bucket_id):
        return Effect(members={member_id: -amount}, cash=-amount)
