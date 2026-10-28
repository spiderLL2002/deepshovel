"""Release identity of this Compiler install: version plus stamped source commit."""

from __future__ import annotations

import re
from pathlib import Path

from . import __version__

SOURCE_COMMIT_FILE = "SOURCE_COMMIT"
_STAMPED_COMMIT = re.compile(r"[0-9a-f]{12}(?:-dirty)?|unknown")


def source_commit(package_directory: Path | None = None) -> str:
    """Return the commit the installer stamped into this package.

    ``scripts/install_public_service.sh`` writes the file into the staged copy
    it installs, using the same rules as the Web build stamp, so a source
    checkout reads "development".
    """

    path = (package_directory or Path(__file__).parent) / SOURCE_COMMIT_FILE
    try:
        value = path.read_text(encoding="ascii").strip()
    except FileNotFoundError:
        return "development"
    except (OSError, UnicodeDecodeError):
        return "unknown"
    return value if _STAMPED_COMMIT.fullmatch(value) else "unknown"


def compiler_release(package_directory: Path | None = None) -> str:
    """For example ``0.3.0+0123456789ab``; reported by ``/health``."""

    return f"{__version__}+{source_commit(package_directory)}"


__all__ = ["SOURCE_COMMIT_FILE", "compiler_release", "source_commit"]
