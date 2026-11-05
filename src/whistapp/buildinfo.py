"""Server version and commit of the running build, recorded on each night it creates."""

import os
import subprocess
from functools import lru_cache
from importlib.metadata import PackageNotFoundError, version as package_version
from pathlib import Path

ROOT = Path(__file__).parent


def application_version():
    try:
        return package_version("whistapp")
    except PackageNotFoundError:
        return "dev"


def application_commit():
    commit = os.environ.get("WHIST_GIT_COMMIT", "").strip()
    if not commit:
        try:
            commit = subprocess.check_output(
                ["git", "rev-parse", "--short=7", "HEAD"],
                cwd=ROOT.parent,
                stderr=subprocess.DEVNULL,
                timeout=2,
                text=True,
            ).strip()
        except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
            return "unknown"
    return commit[:7]


@lru_cache(maxsize=1)
def build_stamp():
    """(server_version, server_commit) stored on new nights; an unknown commit is stored as NULL."""
    commit = application_commit()
    return application_version(), (None if commit == "unknown" else commit)
