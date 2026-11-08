"""The interface every kind of kasse entry implements, and how amounts are read.

A member's saldo is negative when the member owes the club and positive when the club
owes the member (penge til gode). Amounts are Fractions, never floats.
"""

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from fractions import Fraction


def parse_amount(text, signed=False, allow_zero=False):
    """'125,50' -> Fraction(251, 2), Danish style: '1.250' and '1.250,50' use '.' between thousands.
    A lone '.' followed by one or two digits ('12.5', '12.50') is read as a decimal point.
    At most two decimals; zero is an amount only with allow_zero."""
    raw = str(text or "").strip().replace(" ", "")
    if "," in raw or re.fullmatch(r"-?\d{1,3}(\.\d{3})+", raw):
        raw = raw.replace(".", "").replace(",", ".")
    try:
        value = Fraction(raw)
    except (ValueError, ZeroDivisionError):
        raise ValueError("Beløbet skal være et tal, fx 125 eller 125,50")
    if (value * 100).denominator != 1:
        raise ValueError("Beløbet må højst have to decimaler")
    if value == 0 and not allow_zero:
        raise ValueError("Beløbet må ikke være 0")
    if value < 0 and not signed:
        raise ValueError("Beløbet skal være positivt")
    return value


@dataclass(frozen=True)
class Effect:
    """What one entry changes: member balances, the club's cash and what was spent from buckets."""
    members: dict = field(default_factory=dict)   # member_id -> change in the member's saldo
    cash: Fraction = Fraction(0)                  # change in the club's cash
    buckets: dict = field(default_factory=dict)   # bucket_id -> amount spent from the bucket


class EntryType(ABC):
    key: str
    label: str
    description: str
    # Who the entry is about: "member" (one member), "bucket" (optional bucket, no member)
    # or "member_or_cash" (one member, or the club's cash when no member is chosen).
    target: str
    signed = False          # may the amount be negative?
    note_required = False

    def validate(self, *, amount, member_id, bucket_id, note):
        """Raise ValueError with a Danish message if the entry does not make sense."""
        if self.target == "member" and member_id is None:
            raise ValueError(f"{self.label} skal have et medlem")
        if self.target == "bucket" and member_id is not None:
            raise ValueError(f"{self.label} hører til klubben, ikke til et medlem")
        if self.target != "bucket" and bucket_id is not None:
            raise ValueError(f"{self.label} kan ikke høre til en pulje")
        if self.note_required and not note.strip():
            raise ValueError(f"{self.label} skal have en forklaring")

    @abstractmethod
    def effect(self, amount: Fraction, member_id: int | None, bucket_id: int | None) -> Effect:
        """What the entry does to the books. A reversal is the same entry with the amount negated."""
