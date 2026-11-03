"""Only trusted server-side bid implementations are registered."""

from .almindelige import Almindelige
from .bordlaegger import Bordlaegger
from .gode import Gode
from .halve import Halve
from .paskrig import Paskrig
from .ren_sol import RenSol
from .sans import Sans
from .sol import Sol
from .vip import VIP

_vip = VIP()
_BIDS = {
    bid.key: bid
    for bid in (
        Almindelige(),
        Halve(),
        Sans(),
        Gode(),
        Sol(),
        RenSol(),
        Bordlaegger(),
        Paskrig(),
    )
}
_BIDS.update({f"vip{level}": _vip for level in (1, 2, 3)})


def get_bid(key):
    return _BIDS[key]


def registered_bids():
    return dict(_BIDS)
