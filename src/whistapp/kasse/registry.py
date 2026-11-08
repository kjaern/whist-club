"""Only these server-side entry types exist; their files are what Vis regel shows."""

import inspect
import sys

from . import aftener, base
from .indbetaling import Indbetaling
from .korrektion import Korrektion
from .udbetaling import Udbetaling
from .udgift import Udgift

ENTRY_TYPES = {t.key: t for t in (Indbetaling(), Udbetaling(), Udgift(), Korrektion())}

# Rules a member can read: each entry type, and what nights charge.
RULE_TITLES = {"aftener": "Spilaftener", **{k: t.label for k, t in ENTRY_TYPES.items()}}


def get_type(key):
    return ENTRY_TYPES[key]


def rule_sources(key):
    """(file name, source) for one rule: its own file, then base.py it builds on."""
    modules = [aftener] if key == "aftener" else [sys.modules[type(ENTRY_TYPES[key]).__module__], base]
    return [(m.__name__.rsplit(".", 1)[-1] + ".py", inspect.getsource(m)) for m in modules]
