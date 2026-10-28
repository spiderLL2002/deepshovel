from __future__ import annotations

import pytest

from compiler_service import __version__
from compiler_service.release import (
    SOURCE_COMMIT_FILE,
    compiler_release,
    source_commit,
)


def test_unstamped_package_reads_development(tmp_path) -> None:
    assert source_commit(tmp_path) == "development"
    assert compiler_release(tmp_path) == f"{__version__}+development"


@pytest.mark.parametrize(
    "stamp", ["0123456789ab", "0123456789ab-dirty", "unknown"]
)
def test_installer_stamp_is_reported(tmp_path, stamp) -> None:
    (tmp_path / SOURCE_COMMIT_FILE).write_text(stamp + "\n", encoding="ascii")
    assert compiler_release(tmp_path) == f"{__version__}+{stamp}"


@pytest.mark.parametrize(
    "content",
    [b"", b"0123456789", b"0123456789AB", b"main\n", b"\xff\xfe"],
)
def test_malformed_stamp_reads_unknown(tmp_path, content) -> None:
    (tmp_path / SOURCE_COMMIT_FILE).write_bytes(content)
    assert source_commit(tmp_path) == "unknown"
