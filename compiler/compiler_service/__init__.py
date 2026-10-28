"""Independent document compiler service for DeepShovel."""

from __future__ import annotations

import os
from typing import Any

# ONNX Runtime's non-Windows telemetry uploader is initialized by native code.
# This must be fixed before any package import can reach FastEmbed/ONNX Runtime;
# setting it at an application factory or request boundary is already too late.
os.environ["ORT_DISABLE_TELEMETRY"] = "1"

__version__ = "0.3.1"

__all__ = ["create_app"]


def __getattr__(name: str) -> Any:
    if name != "create_app":
        raise AttributeError(name)
    from .api import create_app

    return create_app
