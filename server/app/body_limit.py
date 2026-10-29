"""Request-body size caps for named routes, enforced BEFORE the body is read.

FastAPI reads a request's whole body before it runs any dependency, so a size check in a
dependency or an endpoint only rejects a large body after it is already in memory. This is
pure ASGI, outside the router:

  * a declared Content-Length over the cap is refused with 413 without reading a byte;
  * a body with no declared length (chunked) is read only up to the cap — one byte past it
    and the request is refused with 413 — and otherwise replayed to the app unchanged.

Only the routes listed are affected; everything else passes straight through. HTTP only.
"""
import json

_TOO_LARGE = json.dumps({"detail": "Request body too large."}).encode()


def _declared_length(scope) -> int | None:
    for name, value in scope.get("headers", []):
        if name.lower() == b"content-length":
            try:
                return int(value)
            except ValueError:
                return None
    return None


async def _refuse(send) -> None:
    await send({
        "type": "http.response.start",
        "status": 413,
        "headers": [(b"content-type", b"application/json"),
                    (b"content-length", str(len(_TOO_LARGE)).encode())],
    })
    await send({"type": "http.response.body", "body": _TOO_LARGE})


class BodySizeLimit:
    def __init__(self, app, limits: dict[tuple[str, str], int]):
        self.app = app
        self.limits = dict(limits)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        cap = self.limits.get((scope["method"], scope["path"]))
        if cap is None:
            return await self.app(scope, receive, send)

        declared = _declared_length(scope)
        if declared is not None:
            if declared > cap:
                return await _refuse(send)
            # The HTTP server delivers no more than the declared length.
            return await self.app(scope, receive, send)

        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return None
            body += message.get("body", b"")
            if len(body) > cap:
                return await _refuse(send)
            if not message.get("more_body", False):
                break

        replayed = False

        async def replay():
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()

        return await self.app(scope, replay, send)
