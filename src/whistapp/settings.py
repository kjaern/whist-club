"""Runtime paths are relative to WHIST_HOME, or the launching directory."""

import os
from configparser import ConfigParser
from pathlib import Path

HOME = Path(os.environ.get("WHIST_HOME", Path.cwd())).expanduser().resolve()
CONFIG = HOME / "config"
DATA = HOME / "data"


def settings():
    c = ConfigParser()
    if not c.read(CONFIG / "config.ini", encoding="utf-8"):
        raise RuntimeError(
            f"Missing {CONFIG / 'config.ini'}; run: python -m whistapp.cli init"
        )
    return c
