"""HTTP caller cancellation reaches model workers without a request deadline."""

import asyncio
import threading
import time
from types import SimpleNamespace

import pytest
from fastapi import FastAPI

from compiler_service.api import _register_analysis_routes, _run_interactive_request
from compiler_service.errors import JobCancelled
from compiler_service.job_control import ModelJobControl


def test_disconnected_caller_cancels_worker_control():
    finished = threading.Event()

    class Request:
        async def is_disconnected(self):
            return True

    def work():
        control = ModelJobControl()
        try:
            while True:
                control.checkpoint(enforce_deadline=False)
                time.sleep(0.005)
        finally:
            finished.set()

    with pytest.raises(JobCancelled):
        asyncio.run(_run_interactive_request(Request(), work))
    assert finished.is_set()


def test_connected_caller_keeps_waiting_until_work_completes():
    release = threading.Event()

    class Request:
        checks = 0

        async def is_disconnected(self):
            self.checks += 1
            return False

    def work():
        control = ModelJobControl()
        release.wait()
        control.checkpoint()
        return "model result"

    async def scenario():
        request = Request()
        pending = asyncio.create_task(_run_interactive_request(request, work))
        try:
            await asyncio.sleep(0.25)
            assert not pending.done() and request.checks >= 2
        finally:
            release.set()
        assert await pending == "model result"

    asyncio.run(scenario())


def test_citation_route_disconnect_reaches_the_model_control():
    finished = threading.Event()
    payload = object()

    class Request:
        async def is_disconnected(self):
            return True

    class Analyzer:
        def analyze(self, received):
            assert received is payload
            control = ModelJobControl()
            try:
                while True:
                    control.checkpoint(enforce_deadline=False)
                    time.sleep(0.005)
            finally:
                finished.set()

    app = FastAPI()
    _register_analysis_routes(app, SimpleNamespace(citation_analyzer=Analyzer()))
    endpoint = next(
        route.endpoint
        for route in app.routes
        if route.path == "/v1/citation-receptions/analyze"
    )
    with pytest.raises(JobCancelled):
        asyncio.run(endpoint(Request(), payload))
    assert finished.is_set()
