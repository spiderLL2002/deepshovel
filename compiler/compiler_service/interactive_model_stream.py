"""Cancellation-owned interactive model streams, without application deadlines."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from openai import AsyncOpenAI


async def consume_model_stream(
    sdk: Any,
    *,
    create_stream: Callable,
    on_event: Callable,
    on_stream: Callable | None = None,
    async_client_factory: Callable | None = None,
) -> None:
    """The caller owns racing/retries; task cancellation closes actual async IO."""
    client = (
        async_client_factory()
        if async_client_factory is not None
        else AsyncOpenAI(
            api_key=sdk.api_key,
            base_url=sdk.base_url,
            organization=getattr(sdk, "organization", None),
            project=getattr(sdk, "project", None),
            max_retries=0,
            timeout=None,
            default_headers=getattr(
                sdk, "default_headers", {"User-Agent": "DeepShovel/1.0"}
            ),
        )
    )
    async with client:
        stream = await create_stream(client)
        async with stream:
            if on_stream is not None:
                on_stream(stream)
            async for event in stream:
                if on_event(event):
                    break
