"""Persistent record of production fallback activations for the local console."""

from __future__ import annotations

import json
import logging
import threading
from datetime import UTC, datetime
from pathlib import Path


class FallbackHistory:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()

    def record(self) -> None:
        with self._lock:
            try:
                count = 0
                if self.path.exists():
                    count = json.loads(self.path.read_text())["count"]
                self.path.parent.mkdir(parents=True, exist_ok=True)
                pending = self.path.with_suffix(".tmp")
                pending.write_text(
                    json.dumps(
                        {
                            "count": count + 1,
                            "lastActivatedAt": datetime.now(UTC).isoformat(
                                timespec="seconds"
                            ),
                        }
                    )
                    + "\n"
                )
                pending.replace(self.path)
            except (OSError, ValueError, KeyError, TypeError):
                logging.getLogger(__name__).exception(
                    "Could not record DeepSeek fallback activation"
                )
