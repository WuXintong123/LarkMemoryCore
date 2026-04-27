"""Middleware implementations for the API server."""

from __future__ import annotations

import time
from importlib import import_module

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

from .errors import _ensure_request_id, _error_response
from ..infra.logger import setup_logger


logger = setup_logger("api_server")


def _main_module():
    return import_module("api_server.main")


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Middleware that logs each HTTP request with structured context fields."""

    async def dispatch(self, request: Request, call_next):
        request_id = _ensure_request_id(request)
        start_time = time.time()

        try:
            response = await call_next(request)
        except Exception:
            latency_ms = round((time.time() - start_time) * 1000, 2)
            logger.error(
                "Request failed with unhandled exception",
                extra={
                    "component": "api_server",
                    "request_id": request_id,
                    "method": request.method,
                    "path": request.url.path,
                    "latency_ms": latency_ms,
                },
            )
            raise

        response.headers["X-Request-Id"] = request_id
        latency_ms = round((time.time() - start_time) * 1000, 2)
        logger.info(
            "Request completed",
            extra={
                "component": "api_server",
                "request_id": request_id,
                "auth_key_id": getattr(request.state, "auth_key_id", None),
                "method": request.method,
                "path": request.url.path,
                "latency_ms": latency_ms,
                "status_code": response.status_code,
            },
        )
        return response


class GracefulShutdownMiddleware(BaseHTTPMiddleware):
    """Middleware that rejects new requests during graceful shutdown."""

    async def dispatch(self, request: Request, call_next):
        handler = _main_module().shutdown_handler
        if handler.is_shutting_down:
            return _error_response(
                request,
                status_code=503,
                message="Server is shutting down",
                error_type="service_unavailable_error",
                code="server_shutting_down",
            )

        await handler.track_request()
        try:
            response = await call_next(request)
            return response
        finally:
            await handler.untrack_request()
