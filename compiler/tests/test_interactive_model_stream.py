"""Task cancellation closes pending async IO; no clock deadline is installed."""

import asyncio
from types import SimpleNamespace

import pytest

from compiler_service.interactive_model_stream import consume_model_stream


@pytest.mark.parametrize("first_byte", [False, True])
def test_cancel_pending_io_closes_resources(first_byte):
    state = []

    async def run():
        entered = asyncio.Event()

        class Stream:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                state.append("stream_closed")

            def __aiter__(self):
                return self.events()

            async def events(self):
                entered.set()
                try:
                    yield "heartbeat"
                    await asyncio.Event().wait()
                finally:
                    state.append("read_cancelled")

        class Client:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                state.append("client_closed")

        async def create(client):
            if first_byte:
                entered.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    state.append("request_cancelled")
            return Stream()

        task = asyncio.create_task(
            consume_model_stream(
                SimpleNamespace(),
                create_stream=create,
                on_event=lambda _: False,
                async_client_factory=Client,
            )
        )
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(run())
    assert state == (
        ["request_cancelled", "client_closed"]
        if first_byte
        else ["read_cancelled", "stream_closed", "client_closed"]
    )
