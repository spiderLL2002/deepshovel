"""Connection lifecycle for the standalone loopback HTTP/1 Compiler service."""

from starlette.types import ASGIApp, Message, Receive, Scope, Send


class CloseHttpConnectionMiddleware:
    """Prevent clients from pooling a connection across synchronous Worker work.

    Workerd can miss the server's idle FIN while processing a projection and
    then reuse the expired socket. Advertising close on the response prevents
    that reuse without deadlines, retries, or changes to model execution.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("http_version") not in {"1.0", "1.1"}:
            await self.app(scope, receive, send)
            return

        async def send_close(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = [
                    (name, value)
                    for name, value in message.get("headers", [])
                    if name.lower() != b"connection"
                ]
                message = {
                    **message,
                    "headers": [*headers, (b"connection", b"close")],
                }
            await send(message)

        await self.app(scope, receive, send_close)
