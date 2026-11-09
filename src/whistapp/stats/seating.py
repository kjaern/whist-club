"""Where everyone sat: shared helpers for the statistics about the table.

The next seat in seat order is to a player's left: the deal passes that way (see
`web.next_dealer`), so that player plays right after them. Only a night's last seating is
stored, and imported nights use the order of the columns on the sheet.
"""

GUEST = "Gæst"


def table_order(data, hand):
    """The hand's players in seat order; whoever sat the hand out is skipped."""
    return [pid for pid in data.seats.get(hand.session_id, ()) if pid in hand.participants]


def offset(order, a, b):
    """How many seats to the left of `a` that `b` sits: 1 plays right after `a`."""
    return (order.index(b) - order.index(a)) % len(order)


def danish_date(iso):
    year, month, day = iso.split("-")
    return f"{int(day)}/{int(month)}/{year}"
