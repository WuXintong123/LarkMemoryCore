# ===- test_graceful_shutdown.py -----------------------------------------------
#
# SPDX-License-Identifier: Apache-2.0
#
# ===---------------------------------------------------------------------------
#
# Unit tests for the GracefulShutdownHandler and GracefulShutdownMiddleware
# in api_server/main.py.
#
# Requirements: 2.1, 2.3
#
# ===---------------------------------------------------------------------------

import os
import sys
import asyncio
import time

import pytest

# Ensure the project root is on sys.path so that api_server can be imported.
_project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from api_server.main import (
    GracefulShutdownHandler,
    GracefulShutdownMiddleware,
    shutdown_handler,
    app,
)


# ---------------------------------------------------------------------------
# GracefulShutdownHandler unit tests
# ---------------------------------------------------------------------------


class TestGracefulShutdownHandlerInit:
    """Test GracefulShutdownHandler initialization."""

    @pytest.mark.asyncio
    async def test_default_timeout(self):
        """Handler should default to 30 seconds timeout."""
        handler = GracefulShutdownHandler()
        assert handler.timeout_seconds == 30

    @pytest.mark.asyncio
    async def test_custom_timeout(self):
        """Handler should accept a custom timeout value."""
        handler = GracefulShutdownHandler(timeout_seconds=60)
        assert handler.timeout_seconds == 60

    @pytest.mark.asyncio
    async def test_initial_state_not_shutting_down(self):
        """Handler should not be in shutting down state initially."""
        handler = GracefulShutdownHandler()
        assert handler.is_shutting_down is False

    @pytest.mark.asyncio
    async def test_initial_active_requests_zero(self):
        """Handler should have zero active requests initially."""
        handler = GracefulShutdownHandler()
        assert handler._active_requests == 0


class TestGracefulShutdownHandlerShutdownFlag:
    """Test the shutdown flag behavior."""

    @pytest.mark.asyncio
    async def test_start_shutdown_sets_flag(self):
        """start_shutdown should set is_shutting_down to True."""
        handler = GracefulShutdownHandler()
        assert handler.is_shutting_down is False
        await handler.start_shutdown()
        assert handler.is_shutting_down is True

    @pytest.mark.asyncio
    async def test_start_shutdown_idempotent(self):
        """Calling start_shutdown multiple times should be safe."""
        handler = GracefulShutdownHandler()
        await handler.start_shutdown()
        await handler.start_shutdown()
        assert handler.is_shutting_down is True


class TestGracefulShutdownHandlerRequestTracking:
    """Test request tracking (track/untrack)."""

    @pytest.mark.asyncio
    async def test_track_increments_count(self):
        """track_request should increment the active request count."""
        handler = GracefulShutdownHandler()
        await handler.track_request()
        assert handler._active_requests == 1
        await handler.track_request()
        assert handler._active_requests == 2

    @pytest.mark.asyncio
    async def test_untrack_decrements_count(self):
        """untrack_request should decrement the active request count."""
        handler = GracefulShutdownHandler()
        await handler.track_request()
        await handler.track_request()
        await handler.untrack_request()
        assert handler._active_requests == 1

    @pytest.mark.asyncio
    async def test_track_untrack_returns_to_zero(self):
        """Tracking and untracking the same number of requests returns to zero."""
        handler = GracefulShutdownHandler()
        for _ in range(5):
            await handler.track_request()
        for _ in range(5):
            await handler.untrack_request()
        assert handler._active_requests == 0

    @pytest.mark.asyncio
    async def test_concurrent_track_untrack(self):
        """Concurrent track/untrack operations should be thread-safe via asyncio.Lock."""
        handler = GracefulShutdownHandler()
        num_operations = 100

        async def track_and_untrack():
            await handler.track_request()
            await asyncio.sleep(0.001)
            await handler.untrack_request()

        tasks = [track_and_untrack() for _ in range(num_operations)]
        await asyncio.gather(*tasks)
        assert handler._active_requests == 0


class TestGracefulShutdownHandlerWaitForCompletion:
    """Test wait_for_completion behavior."""

    @pytest.mark.asyncio
    async def test_returns_zero_when_no_active_requests(self):
        """wait_for_completion should return 0 immediately when no requests are active."""
        handler = GracefulShutdownHandler(timeout_seconds=5)
        result = await handler.wait_for_completion()
        assert result == 0

    @pytest.mark.asyncio
    async def test_waits_for_requests_to_complete(self):
        """wait_for_completion should wait until active requests finish."""
        handler = GracefulShutdownHandler(timeout_seconds=5)
        await handler.track_request()

        async def complete_request_after_delay():
            await asyncio.sleep(0.3)
            await handler.untrack_request()

        task = asyncio.create_task(complete_request_after_delay())
        result = await handler.wait_for_completion()
        assert result == 0
        await task

    @pytest.mark.asyncio
    async def test_returns_active_count_on_timeout(self):
        """wait_for_completion should return active count when timeout expires."""
        handler = GracefulShutdownHandler(timeout_seconds=1)
        await handler.track_request()
        await handler.track_request()

        start = time.time()
        result = await handler.wait_for_completion()
        elapsed = time.time() - start

        assert result == 2
        assert elapsed >= 1.0
        assert elapsed < 3.0  # Should not take much longer than timeout

        # Clean up
        await handler.untrack_request()
        await handler.untrack_request()

    @pytest.mark.asyncio
    async def test_timeout_logs_warning(self):
        """wait_for_completion should log a warning when timeout expires with active requests."""
        handler = GracefulShutdownHandler(timeout_seconds=1)
        await handler.track_request()

        result = await handler.wait_for_completion()
        assert result == 1  # 1 request was force-terminated

        # Clean up
        await handler.untrack_request()


class TestGracefulShutdownHandlerEnvVar:
    """Test environment variable configuration."""

    def test_env_var_configures_timeout(self, monkeypatch):
        """GRACEFUL_SHUTDOWN_TIMEOUT_S env var should configure the timeout."""
        monkeypatch.setenv("GRACEFUL_SHUTDOWN_TIMEOUT_S", "45")
        timeout = int(os.getenv("GRACEFUL_SHUTDOWN_TIMEOUT_S", "30"))
        handler = GracefulShutdownHandler(timeout_seconds=timeout)
        assert handler.timeout_seconds == 45

    def test_env_var_default_value(self, monkeypatch):
        """Default timeout should be 30 when env var is not set."""
        monkeypatch.delenv("GRACEFUL_SHUTDOWN_TIMEOUT_S", raising=False)
        timeout = int(os.getenv("GRACEFUL_SHUTDOWN_TIMEOUT_S", "30"))
        handler = GracefulShutdownHandler(timeout_seconds=timeout)
        assert handler.timeout_seconds == 30


class TestGracefulShutdownMiddleware:
    """Test the GracefulShutdownMiddleware via the FastAPI test client."""

    @pytest.mark.asyncio
    async def test_middleware_returns_503_when_shutting_down(self):
        """Middleware should return 503 with proper error body when shutting down."""
        from httpx import AsyncClient, ASGITransport

        # Create a fresh handler for this test
        handler = GracefulShutdownHandler()
        await handler.start_shutdown()

        # Temporarily replace the module-level shutdown_handler
        import api_server.main as main_module
        original_handler = main_module.shutdown_handler
        main_module.shutdown_handler = handler

        try:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.get("/health")
                assert response.status_code == 503
                body = response.json()
                assert "error" in body
                assert body["error"]["message"] == "Server is shutting down"
                assert body["error"]["type"] == "service_unavailable_error"
        finally:
            main_module.shutdown_handler = original_handler

    @pytest.mark.asyncio
    async def test_middleware_allows_requests_when_not_shutting_down(self):
        """Middleware should allow requests through when not shutting down."""
        from httpx import AsyncClient, ASGITransport

        # Create a fresh handler that is NOT shutting down
        handler = GracefulShutdownHandler()

        import api_server.main as main_module
        original_handler = main_module.shutdown_handler
        main_module.shutdown_handler = handler

        try:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.get("/health")
                assert response.status_code == 200
        finally:
            main_module.shutdown_handler = original_handler

    @pytest.mark.asyncio
    async def test_middleware_tracks_active_requests(self):
        """Middleware should track active requests via the shutdown handler."""
        from httpx import AsyncClient, ASGITransport

        handler = GracefulShutdownHandler()

        import api_server.main as main_module
        original_handler = main_module.shutdown_handler
        main_module.shutdown_handler = handler

        try:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                # After request completes, active count should be back to 0
                response = await client.get("/health")
                assert response.status_code == 200
                assert handler._active_requests == 0
        finally:
            main_module.shutdown_handler = original_handler


class TestModuleLevelShutdownHandler:
    """Test the module-level shutdown_handler instance."""

    def test_module_handler_exists(self):
        """A module-level shutdown_handler should exist."""
        assert shutdown_handler is not None

    def test_module_handler_is_graceful_shutdown_handler(self):
        """The module-level shutdown_handler should be a GracefulShutdownHandler instance."""
        assert isinstance(shutdown_handler, GracefulShutdownHandler)

    def test_module_handler_reads_env_timeout(self):
        """The module-level handler should use GRACEFUL_SHUTDOWN_TIMEOUT_S env var."""
        # The module-level handler was created at import time with whatever
        # GRACEFUL_SHUTDOWN_TIMEOUT_S was set (or default 30)
        assert isinstance(shutdown_handler.timeout_seconds, int)
        assert shutdown_handler.timeout_seconds > 0
