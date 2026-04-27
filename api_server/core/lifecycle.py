"""Application lifecycle and graceful shutdown helpers."""

from __future__ import annotations

import asyncio
import functools
import signal
import time
from contextlib import asynccontextmanager
from importlib import import_module

from fastapi import FastAPI

from .config import GRACEFUL_SHUTDOWN_TIMEOUT_S
from ..infra.logger import setup_logger


logger = setup_logger("api_server")


def _main_module():
    return import_module("api_server.main")


class GracefulShutdownHandler:
    """Handles graceful shutdown of the API server."""

    def __init__(self, timeout_seconds: int = 30):
        self.timeout_seconds = timeout_seconds
        self._shutting_down = False
        self._active_requests = 0
        self._lock = asyncio.Lock()

    @property
    def is_shutting_down(self) -> bool:
        return self._shutting_down

    async def start_shutdown(self) -> None:
        async with self._lock:
            self._shutting_down = True

    async def track_request(self) -> None:
        async with self._lock:
            self._active_requests += 1

    async def untrack_request(self) -> None:
        async with self._lock:
            self._active_requests -= 1

    async def wait_for_completion(self) -> int:
        start_time = time.time()
        while True:
            async with self._lock:
                active = self._active_requests
            if active <= 0:
                return 0
            elapsed = time.time() - start_time
            if elapsed >= self.timeout_seconds:
                logger.warning(
                    "Graceful shutdown timeout expired with %d requests still in-flight, force-terminating remaining requests",
                    active,
                    extra={
                        "component": "api_server",
                        "active_requests": active,
                        "timeout_seconds": self.timeout_seconds,
                    },
                )
                return active
            await asyncio.sleep(0.5)


shutdown_handler = GracefulShutdownHandler(timeout_seconds=GRACEFUL_SHUTDOWN_TIMEOUT_S)


@asynccontextmanager
async def lifespan(app: FastAPI):
    main_module = _main_module()
    try:
        await main_module._to_thread(main_module.compute_client.connect)
        health = await main_module._load_backend_health()
        if main_module.STARTUP_REQUIRE_COMPUTE and not health.healthy:
            raise RuntimeError(
                f"Compute startup requirement not satisfied: {health.status_message}"
            )
        logger.info(
            "Compute client initialized",
            extra={
                "component": "api_server",
                "grpc_server_address": main_module.GRPC_SERVER_ADDRESS,
                "backend_healthy": health.healthy,
            },
        )
    except Exception as exc:
        logger.warning(
            "Failed to initialize compute client: %s",
            str(exc),
            extra={
                "component": "api_server",
                "grpc_server_address": main_module.GRPC_SERVER_ADDRESS,
            },
        )
        if main_module.STARTUP_REQUIRE_COMPUTE:
            raise RuntimeError(
                f"Compute server startup requirement not satisfied: {exc}"
            ) from exc

    loop = asyncio.get_running_loop()

    async def _handle_shutdown_signal(sig: signal.Signals) -> None:
        current_main = _main_module()
        handler = current_main.shutdown_handler
        logger.info(
            "Received signal %s, initiating graceful shutdown",
            sig.name,
            extra={
                "component": "api_server",
                "signal": sig.name,
                "timeout_seconds": handler.timeout_seconds,
            },
        )
        await handler.start_shutdown()
        terminated = await handler.wait_for_completion()
        if terminated > 0:
            logger.warning(
                "Force-terminated %d in-flight requests after timeout",
                terminated,
                extra={
                    "component": "api_server",
                    "terminated_requests": terminated,
                },
            )
        else:
            logger.info(
                "All in-flight requests completed gracefully",
                extra={"component": "api_server"},
            )

    def _signal_handler(sig: signal.Signals) -> None:
        loop.create_task(_handle_shutdown_signal(sig))

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, functools.partial(_signal_handler, sig))
        except NotImplementedError:
            logger.warning(
                "Signal handlers are not supported in this runtime",
                extra={"component": "api_server"},
            )
            break

    logger.info(
        "Registered graceful shutdown signal handlers (SIGTERM, SIGINT)",
        extra={
            "component": "api_server",
            "shutdown_timeout_seconds": _main_module().shutdown_handler.timeout_seconds,
        },
    )

    yield

    await _main_module()._to_thread(_main_module().compute_client.disconnect)
    logger.info("API server shutdown complete", extra={"component": "api_server"})
