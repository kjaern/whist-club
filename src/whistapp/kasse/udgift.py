"""Udgift: the club spends money, usually from one of its buckets (fx en tur)."""

from .base import Effect, EntryType


class Udgift(EntryType):
    key = "udgift"
    label = "Udgift"
    description = "Klubben bruger penge. Kassen har pengene mindre; vælges en pulje, tæller beløbet som brugt fra den."
    target = "bucket"

    def effect(self, amount, member_id, bucket_id):
        return Effect(cash=-amount, buckets={bucket_id: amount} if bucket_id is not None else {})
