import asyncio

import pytest

from compiler_service.http_transport import CloseHttpConnectionMiddleware


@pytest.mark.parametrize("http_version", ["1.0", "1.1"])
def test_close_header_is_unique_and_response_body_is_unchanged(http_version):
    sent = []
    body = {"type": "http.response.body", "body": b"ok", "more_body": False}

    async def receive():
        return {"type": "http.disconnect"}

    async def send(message):
        sent.append(message)

    async def app(scope, receive, send):
        await send({
            "type": "http.response.start", "status": 200,
            "headers": [(b"Connection", b"keep-alive"),
                        (b"connection", b"upgrade"), (b"x-test", b"preserved")],
        })
        await send(body)

    asyncio.run(CloseHttpConnectionMiddleware(app)(
        {"type": "http", "http_version": http_version}, receive, send
    ))
    assert sent[0]["headers"] == [(b"x-test", b"preserved"), (b"connection", b"close")]
    assert sent[1] is body


@pytest.mark.parametrize("scope", [
    {"type": "lifespan"}, {"type": "websocket"},
    {"type": "http", "http_version": "2"},
    {"type": "http", "http_version": "3"},
])
def test_other_protocols_pass_through_unchanged(scope):

    async def receive():
        return {}

    async def send(message):
        pass

    async def app(actual_scope, actual_receive, actual_send):
        assert actual_scope is scope
        assert actual_receive is receive
        assert actual_send is send

    asyncio.run(CloseHttpConnectionMiddleware(app)(scope, receive, send))


@pytest.mark.parametrize("failure", [RuntimeError("failure"), asyncio.CancelledError()])
def test_failures_and_cancellation_are_not_swallowed(failure):
    async def app(scope, receive, send):
        raise failure

    with pytest.raises(type(failure)) as caught:
        asyncio.run(CloseHttpConnectionMiddleware(app)(
            {"type": "http", "http_version": "1.1"}, None, None
        ))
    assert caught.value is failure
