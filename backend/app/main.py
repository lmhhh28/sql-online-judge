import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import MutableHeaders
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.config import settings
from app.database import get_db, verify_database_schema_current
from app.deps import get_current_user
from app.models import User
from app.routers import admin, auth, problems
from app.schemas import UserRead
from app.services.judge import (
    recover_interrupted_judges,
    shutdown_judge_executor,
    start_judge_executor,
)

logger = logging.getLogger(__name__)
MAX_REQUEST_BODY_SIZE = 10 * 1024 * 1024


class _RequestBodyTooLarge(Exception):
    pass


class RequestBodyLimitMiddleware:
    def __init__(self, app, max_body_size: int) -> None:
        self.app = app
        self.max_body_size = max_body_size

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        content_lengths = [
            value
            for name, value in scope.get("headers", [])
            if name.lower() == b"content-length"
        ]
        if len(content_lengths) > 1:
            await self._send_error(scope, receive, send, 400, "Invalid Content-Length")
            return

        content_length = content_lengths[0] if content_lengths else None
        if content_length is not None:
            if not content_length or not content_length.isdigit():
                await self._send_error(
                    scope, receive, send, 400, "Invalid Content-Length"
                )
                return
            # Avoid converting an attacker-controlled, arbitrarily long
            # decimal string to int: Python rejects very long conversions and
            # older runtimes could spend disproportionate CPU on them.
            normalized_length = content_length.lstrip(b"0") or b"0"
            maximum_length = str(self.max_body_size).encode("ascii")
            if len(normalized_length) > len(maximum_length) or (
                len(normalized_length) == len(maximum_length)
                and normalized_length > maximum_length
            ):
                await self._send_error(
                    scope, receive, send, 413, "Request body is too large"
                )
                return

        # When a request has no declared length (for example HTTP/1.1
        # chunked transfer), downstream authentication or routing may return
        # without ever calling receive(). Read and bound that body before
        # dispatch so the limit is genuinely global, then replay the ASGI
        # messages to the application.
        if content_length is None:
            upstream_receive = receive
            buffered_body = bytearray()
            request_started = False
            request_complete = False
            disconnected = False
            while True:
                message = await upstream_receive()
                if message["type"] == "http.disconnect":
                    disconnected = True
                    break
                if message["type"] != "http.request":
                    continue
                request_started = True
                buffered_body.extend(message.get("body", b""))
                if len(buffered_body) > self.max_body_size:
                    await self._send_error(
                        scope, receive, send, 413, "Request body is too large"
                    )
                    return
                if not message.get("more_body", False):
                    request_complete = True
                    break

            replayed_request = False

            async def replay_receive():
                nonlocal replayed_request
                if request_started and not replayed_request:
                    replayed_request = True
                    return {
                        "type": "http.request",
                        "body": bytes(buffered_body),
                        "more_body": not request_complete,
                    }
                if disconnected:
                    return {"type": "http.disconnect"}
                return await upstream_receive()

            receive = replay_receive

        received_size = 0

        async def limited_receive():
            nonlocal received_size
            message = await receive()
            if message["type"] == "http.request":
                received_size += len(message.get("body", b""))
                if received_size > self.max_body_size:
                    raise _RequestBodyTooLarge
            return message

        response_started = False

        async def tracked_send(message):
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, tracked_send)
        except _RequestBodyTooLarge:
            if response_started:
                # ASGI status and headers cannot be replaced after
                # response.start. Abort this invalid exchange without sending
                # a second response start; normal FastAPI body parsing reaches
                # the limit before starting a response.
                raise
            await self._send_error(
                scope, receive, send, 413, "Request body is too large"
            )

    @staticmethod
    async def _send_error(scope, receive, send, status_code: int, detail: str) -> None:
        response = JSONResponse(
            status_code=status_code,
            content={"detail": detail},
            headers={"Connection": "close"},
        )
        await response(scope, receive, send)


class SecurityHeadersMiddleware:
    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        has_authorization = any(
            name.lower() == b"authorization" for name, _ in scope.get("headers", [])
        )

        async def send_with_security_headers(message):
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers.setdefault("X-Content-Type-Options", "nosniff")
                headers.setdefault("X-Frame-Options", "DENY")
                headers.setdefault("Referrer-Policy", "no-referrer")
                if has_authorization:
                    headers.setdefault("Cache-Control", "no-store")
            await send(message)

        await self.app(scope, receive, send_with_security_headers)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await run_in_threadpool(verify_database_schema_current)
    start_judge_executor()
    try:
        if settings.jwt_uses_ephemeral_secret:
            logger.warning(
                "JWT_SECRET_KEY is unset or uses the development placeholder; using an ephemeral key. "
                "Existing tokens will become invalid when this process restarts."
            )
        try:
            submissions, databases = await run_in_threadpool(recover_interrupted_judges)
        except Exception:
            logger.warning("Judge recovery failed during startup", exc_info=True)
        else:
            if submissions or databases:
                logger.info(
                    "Recovered %s interrupted submissions and dropped %s stale judge databases",
                    submissions,
                    databases,
                )
        yield
    finally:
        await run_in_threadpool(shutdown_judge_executor, True)


app = FastAPI(title="SQL Online Judge", version="0.1.0", lifespan=lifespan)

# Starlette runs the last-added middleware first. Keep host validation outside
# body buffering, and keep the global body limit outside CORS short-circuits.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
    expose_headers=["X-Total-Count"],
)
app.add_middleware(
    RequestBodyLimitMiddleware,
    max_body_size=MAX_REQUEST_BODY_SIZE,
)
app.add_middleware(
    TrustedHostMiddleware, allowed_hosts=settings.trusted_host_list, www_redirect=False
)
app.add_middleware(SecurityHeadersMiddleware)

app.include_router(auth.router)
app.include_router(problems.router)
app.include_router(admin.router)


@app.exception_handler(RequestValidationError)
async def request_validation_error_handler(
    _, exc: RequestValidationError
) -> JSONResponse:
    errors = [
        {key: error[key] for key in ("type", "loc", "msg") if key in error}
        for error in exc.errors()
    ]
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, content={"detail": errors}
    )


@app.get("/api/me", response_model=UserRead)
def me(current_user: User = Depends(get_current_user)) -> User:
    return current_user


@app.get("/api/health")
def health(db: Session = Depends(get_db)) -> dict[str, str]:
    try:
        db.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database unavailable",
        ) from exc
    return {"status": "ok"}
