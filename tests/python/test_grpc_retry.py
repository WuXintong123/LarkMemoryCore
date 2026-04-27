# ===- test_grpc_retry.py -----------------------------------------------------
#
# SPDX-License-Identifier: Apache-2.0
#
# ===---------------------------------------------------------------------------
#
# Unit tests for gRPC retry and reconnection logic in
# api_server/grpc_client.py.
#
# Tests cover:
#   - RetryConfig dataclass defaults and environment variable loading
#   - _calculate_backoff exponential backoff calculation
#   - _execute_with_retry retry/reconnection wrapper
#   - Connection state tracking (is_connected property)
#   - Non-retryable errors propagate immediately
#   - Retryable errors trigger retry with backoff
#   - Exhausted retries produce clear error message
#   - Reconnection logging on recovery after failure
#
# Requirements: 1.1, 1.2, 1.3, 1.4, 1.5
#
# ===---------------------------------------------------------------------------

import os
import sys
import time
import threading
import unittest
from unittest.mock import MagicMock, patch, PropertyMock

import grpc

# Ensure the project root is on sys.path so that api_server can be imported.
_project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from api_server.grpc_client import (
    RetryConfig,
    ComputeClient,
    _retry_config_from_env,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_grpc_error(status_code: grpc.StatusCode, details: str = "test error"):
    """Create a mock grpc.RpcError with the given status code and details."""
    error = grpc.RpcError()
    error.code = MagicMock(return_value=status_code)
    error.details = MagicMock(return_value=details)
    # grpc.RpcError instances need to be raisable and have code() method
    # We create a proper subclass for realistic behavior.
    class _MockRpcError(grpc.RpcError):
        def __init__(self, code, details):
            self._code = code
            self._details = details

        def code(self):
            return self._code

        def details(self):
            return self._details

        def __str__(self):
            return f"<RpcError {self._code}: {self._details}>"

    return _MockRpcError(status_code, details)


# ---------------------------------------------------------------------------
# RetryConfig Tests
# ---------------------------------------------------------------------------

class TestRetryConfig(unittest.TestCase):
    """Unit tests for the RetryConfig dataclass."""

    def test_default_values(self):
        """RetryConfig should have sensible defaults matching the design spec."""
        config = RetryConfig()
        self.assertEqual(config.max_retries, 3)
        self.assertAlmostEqual(config.base_backoff_seconds, 1.0)
        self.assertAlmostEqual(config.max_backoff_seconds, 30.0)
        self.assertIn(grpc.StatusCode.UNAVAILABLE, config.retryable_codes)
        self.assertIn(grpc.StatusCode.DEADLINE_EXCEEDED, config.retryable_codes)

    def test_custom_values(self):
        """RetryConfig should accept custom values."""
        config = RetryConfig(
            max_retries=5,
            base_backoff_seconds=2.0,
            max_backoff_seconds=60.0,
            retryable_codes=(grpc.StatusCode.UNAVAILABLE,),
        )
        self.assertEqual(config.max_retries, 5)
        self.assertAlmostEqual(config.base_backoff_seconds, 2.0)
        self.assertAlmostEqual(config.max_backoff_seconds, 60.0)
        self.assertEqual(len(config.retryable_codes), 1)
        self.assertIn(grpc.StatusCode.UNAVAILABLE, config.retryable_codes)

    def test_non_retryable_codes_not_in_defaults(self):
        """Non-retryable status codes should NOT be in the default retryable_codes."""
        config = RetryConfig()
        non_retryable = [
            grpc.StatusCode.INVALID_ARGUMENT,
            grpc.StatusCode.NOT_FOUND,
            grpc.StatusCode.RESOURCE_EXHAUSTED,
            grpc.StatusCode.INTERNAL,
            grpc.StatusCode.PERMISSION_DENIED,
        ]
        for code in non_retryable:
            self.assertNotIn(code, config.retryable_codes,
                             f"{code} should not be retryable by default")


class TestRetryConfigFromEnv(unittest.TestCase):
    """Unit tests for _retry_config_from_env."""

    def test_defaults_when_env_not_set(self):
        """When environment variables are not set, defaults should be used."""
        env_vars = ["GRPC_MAX_RETRIES", "GRPC_BASE_BACKOFF_S", "GRPC_MAX_BACKOFF_S"]
        saved = {k: os.environ.get(k) for k in env_vars}
        try:
            for k in env_vars:
                os.environ.pop(k, None)
            config = _retry_config_from_env()
            self.assertEqual(config.max_retries, 3)
            self.assertAlmostEqual(config.base_backoff_seconds, 1.0)
            self.assertAlmostEqual(config.max_backoff_seconds, 30.0)
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

    def test_reads_env_variables(self):
        """_retry_config_from_env should read from environment variables."""
        env_vars = ["GRPC_MAX_RETRIES", "GRPC_BASE_BACKOFF_S", "GRPC_MAX_BACKOFF_S"]
        saved = {k: os.environ.get(k) for k in env_vars}
        try:
            os.environ["GRPC_MAX_RETRIES"] = "5"
            os.environ["GRPC_BASE_BACKOFF_S"] = "0.5"
            os.environ["GRPC_MAX_BACKOFF_S"] = "10.0"
            config = _retry_config_from_env()
            self.assertEqual(config.max_retries, 5)
            self.assertAlmostEqual(config.base_backoff_seconds, 0.5)
            self.assertAlmostEqual(config.max_backoff_seconds, 10.0)
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v


# ---------------------------------------------------------------------------
# _calculate_backoff Tests
# ---------------------------------------------------------------------------

class TestCalculateBackoff(unittest.TestCase):
    """Unit tests for ComputeClient._calculate_backoff."""

    def setUp(self):
        """Create a ComputeClient with known retry config for testing."""
        self.config = RetryConfig(
            base_backoff_seconds=1.0,
            max_backoff_seconds=30.0,
        )
        self.client = ComputeClient.__new__(ComputeClient)
        self.client.retry_config = self.config

    def test_attempt_0(self):
        """Attempt 0: backoff = min(1.0 * 2^0, 30.0) = 1.0"""
        self.assertAlmostEqual(self.client._calculate_backoff(0), 1.0)

    def test_attempt_1(self):
        """Attempt 1: backoff = min(1.0 * 2^1, 30.0) = 2.0"""
        self.assertAlmostEqual(self.client._calculate_backoff(1), 2.0)

    def test_attempt_2(self):
        """Attempt 2: backoff = min(1.0 * 2^2, 30.0) = 4.0"""
        self.assertAlmostEqual(self.client._calculate_backoff(2), 4.0)

    def test_attempt_3(self):
        """Attempt 3: backoff = min(1.0 * 2^3, 30.0) = 8.0"""
        self.assertAlmostEqual(self.client._calculate_backoff(3), 8.0)

    def test_attempt_4(self):
        """Attempt 4: backoff = min(1.0 * 2^4, 30.0) = 16.0"""
        self.assertAlmostEqual(self.client._calculate_backoff(4), 16.0)

    def test_attempt_5_clamped(self):
        """Attempt 5: backoff = min(1.0 * 2^5, 30.0) = 30.0 (clamped)"""
        self.assertAlmostEqual(self.client._calculate_backoff(5), 30.0)

    def test_large_attempt_clamped(self):
        """Very large attempt numbers should be clamped to max_backoff."""
        self.assertAlmostEqual(self.client._calculate_backoff(100), 30.0)

    def test_custom_base_backoff(self):
        """Custom base_backoff_seconds should be used in the calculation."""
        self.client.retry_config = RetryConfig(
            base_backoff_seconds=0.5,
            max_backoff_seconds=10.0,
        )
        # 0.5 * 2^0 = 0.5
        self.assertAlmostEqual(self.client._calculate_backoff(0), 0.5)
        # 0.5 * 2^1 = 1.0
        self.assertAlmostEqual(self.client._calculate_backoff(1), 1.0)
        # 0.5 * 2^2 = 2.0
        self.assertAlmostEqual(self.client._calculate_backoff(2), 2.0)
        # 0.5 * 2^4 = 8.0
        self.assertAlmostEqual(self.client._calculate_backoff(4), 8.0)
        # 0.5 * 2^5 = 16.0 -> clamped to 10.0
        self.assertAlmostEqual(self.client._calculate_backoff(5), 10.0)


# ---------------------------------------------------------------------------
# _execute_with_retry Tests
# ---------------------------------------------------------------------------

class TestExecuteWithRetry(unittest.TestCase):
    """Unit tests for ComputeClient._execute_with_retry."""

    def _make_client(self, max_retries=3, base_backoff=0.01, max_backoff=0.1):
        """Create a ComputeClient with fast backoff for testing."""
        config = RetryConfig(
            max_retries=max_retries,
            base_backoff_seconds=base_backoff,
            max_backoff_seconds=max_backoff,
        )
        client = ComputeClient.__new__(ComputeClient)
        client.server_address = "localhost:9000"
        client.retry_config = config
        client.channel = MagicMock()
        client.stub = MagicMock()
        client._connected = True
        client._connection_lock = threading.Lock()
        return client

    def test_success_on_first_attempt(self):
        """Operation succeeding on first attempt should return the result directly."""
        client = self._make_client()
        operation = MagicMock(return_value="success")

        result = client._execute_with_retry(operation, timeout=5.0)

        self.assertEqual(result, "success")
        operation.assert_called_once()
        self.assertTrue(client.is_connected)

    def test_non_retryable_error_propagates_immediately(self):
        """Non-retryable gRPC errors should be re-raised without retry."""
        client = self._make_client(max_retries=3)
        error = _make_grpc_error(grpc.StatusCode.INVALID_ARGUMENT, "bad input")
        operation = MagicMock(side_effect=error)

        with self.assertRaises(grpc.RpcError) as ctx:
            client._execute_with_retry(operation, timeout=5.0)

        # Should only be called once — no retries for non-retryable errors
        operation.assert_called_once()
        self.assertEqual(ctx.exception.code(), grpc.StatusCode.INVALID_ARGUMENT)

    def test_non_retryable_not_found_propagates(self):
        """NOT_FOUND errors should propagate immediately without retry."""
        client = self._make_client(max_retries=3)
        error = _make_grpc_error(grpc.StatusCode.NOT_FOUND, "model not found")
        operation = MagicMock(side_effect=error)

        with self.assertRaises(grpc.RpcError):
            client._execute_with_retry(operation, timeout=5.0)

        operation.assert_called_once()

    def test_non_retryable_resource_exhausted_propagates(self):
        """RESOURCE_EXHAUSTED errors should propagate immediately without retry."""
        client = self._make_client(max_retries=3)
        error = _make_grpc_error(grpc.StatusCode.RESOURCE_EXHAUSTED, "busy")
        operation = MagicMock(side_effect=error)

        with self.assertRaises(grpc.RpcError):
            client._execute_with_retry(operation, timeout=5.0)

        operation.assert_called_once()

    def test_non_retryable_internal_propagates(self):
        """INTERNAL errors should propagate immediately without retry."""
        client = self._make_client(max_retries=3)
        error = _make_grpc_error(grpc.StatusCode.INTERNAL, "internal error")
        operation = MagicMock(side_effect=error)

        with self.assertRaises(grpc.RpcError):
            client._execute_with_retry(operation, timeout=5.0)

        operation.assert_called_once()

    def test_non_retryable_permission_denied_propagates(self):
        """PERMISSION_DENIED errors should propagate immediately without retry."""
        client = self._make_client(max_retries=3)
        error = _make_grpc_error(grpc.StatusCode.PERMISSION_DENIED, "denied")
        operation = MagicMock(side_effect=error)

        with self.assertRaises(grpc.RpcError):
            client._execute_with_retry(operation, timeout=5.0)

        operation.assert_called_once()

    @patch("api_server.grpc_client.time.sleep")
    @patch.object(ComputeClient, "connect")
    def test_retryable_unavailable_retries_and_succeeds(self, mock_connect, mock_sleep):
        """UNAVAILABLE error should trigger retry; success on 2nd attempt."""
        client = self._make_client(max_retries=3)
        error = _make_grpc_error(grpc.StatusCode.UNAVAILABLE, "server down")
        operation = MagicMock(side_effect=[error, "recovered"])

        result = client._execute_with_retry(operation, timeout=5.0)

        self.assertEqual(result, "recovered")
        self.assertEqual(operation.call_count, 2)
        # Backoff sleep should have been called once (between attempt 0 and 1)
        mock_sleep.assert_called_once()
        self.assertTrue(client.is_connected)

    @patch("api_server.grpc_client.time.sleep")
    @patch.object(ComputeClient, "connect")
    def test_retryable_deadline_exceeded_retries_and_succeeds(self, mock_connect, mock_sleep):
        """DEADLINE_EXCEEDED error should trigger retry; success on 3rd attempt."""
        client = self._make_client(max_retries=3)
        error = _make_grpc_error(grpc.StatusCode.DEADLINE_EXCEEDED, "timeout")
        operation = MagicMock(side_effect=[error, error, "recovered"])

        result = client._execute_with_retry(operation, timeout=5.0)

        self.assertEqual(result, "recovered")
        self.assertEqual(operation.call_count, 3)
        self.assertEqual(mock_sleep.call_count, 2)
        self.assertTrue(client.is_connected)

    @patch("api_server.grpc_client.time.sleep")
    @patch.object(ComputeClient, "connect")
    def test_all_retries_exhausted_raises_runtime_error(self, mock_connect, mock_sleep):
        """When all retries are exhausted, a RuntimeError with clear message should be raised."""
        client = self._make_client(max_retries=2)
        error = _make_grpc_error(grpc.StatusCode.UNAVAILABLE, "server down")
        # 1 original + 2 retries = 3 total attempts, all fail
        operation = MagicMock(side_effect=[error, error, error])

        with self.assertRaises(RuntimeError) as ctx:
            client._execute_with_retry(operation, timeout=5.0)

        self.assertIn("Compute server unreachable", str(ctx.exception))
        self.assertIn("2 retries", str(ctx.exception))
        self.assertEqual(operation.call_count, 3)
        self.assertFalse(client.is_connected)

    @patch("api_server.grpc_client.time.sleep")
    @patch.object(ComputeClient, "connect")
    def test_total_attempts_equals_one_plus_max_retries(self, mock_connect, mock_sleep):
        """The operation should be invoked exactly (1 + max_retries) times when
        all attempts fail with retryable errors."""
        for max_retries in [0, 1, 2, 3, 5]:
            client = self._make_client(max_retries=max_retries)
            error = _make_grpc_error(grpc.StatusCode.UNAVAILABLE, "down")
            operation = MagicMock(side_effect=error)

            with self.assertRaises(RuntimeError):
                client._execute_with_retry(operation, timeout=5.0)

            expected_calls = 1 + max_retries
            self.assertEqual(
                operation.call_count, expected_calls,
                f"max_retries={max_retries}: expected {expected_calls} calls, "
                f"got {operation.call_count}"
            )

    @patch("api_server.grpc_client.time.sleep")
    @patch.object(ComputeClient, "connect")
    def test_connection_state_set_to_false_on_retryable_error(self, mock_connect, mock_sleep):
        """_connected should be set to False when a retryable error occurs."""
        client = self._make_client(max_retries=1)
        error = _make_grpc_error(grpc.StatusCode.UNAVAILABLE, "down")
        operation = MagicMock(side_effect=[error, error])

        with self.assertRaises(RuntimeError):
            client._execute_with_retry(operation, timeout=5.0)

        self.assertFalse(client.is_connected)

    @patch("api_server.grpc_client.time.sleep")
    @patch.object(ComputeClient, "connect")
    def test_connection_state_restored_on_recovery(self, mock_connect, mock_sleep):
        """_connected should be restored to True when a retry succeeds."""
        client = self._make_client(max_retries=3)
        error = _make_grpc_error(grpc.StatusCode.UNAVAILABLE, "down")
        operation = MagicMock(side_effect=[error, "recovered"])

        result = client._execute_with_retry(operation, timeout=5.0)

        self.assertEqual(result, "recovered")
        self.assertTrue(client.is_connected)

    @patch("api_server.grpc_client.time.sleep")
    @patch.object(ComputeClient, "connect")
    def test_zero_retries_means_single_attempt(self, mock_connect, mock_sleep):
        """With max_retries=0, only the original attempt should be made."""
        client = self._make_client(max_retries=0)
        error = _make_grpc_error(grpc.StatusCode.UNAVAILABLE, "down")
        operation = MagicMock(side_effect=error)

        with self.assertRaises(RuntimeError):
            client._execute_with_retry(operation, timeout=5.0)

        operation.assert_called_once()
        mock_sleep.assert_not_called()


# ---------------------------------------------------------------------------
# is_connected Property Tests
# ---------------------------------------------------------------------------

class TestIsConnected(unittest.TestCase):
    """Unit tests for the is_connected property."""

    def test_initially_false(self):
        """A newly created ComputeClient should have is_connected = False."""
        client = ComputeClient.__new__(ComputeClient)
        client._connected = False
        client._connection_lock = threading.Lock()
        self.assertFalse(client.is_connected)

    def test_true_after_connect(self):
        """After calling connect(), is_connected should be True."""
        client = ComputeClient.__new__(ComputeClient)
        client.server_address = "localhost:9000"
        client.channel = None
        client.stub = None
        client._connected = False
        client._connection_lock = threading.Lock()
        client.retry_config = RetryConfig()

        # Patch grpc.insecure_channel and the stub to avoid real connections
        with patch("api_server.grpc_client.grpc.insecure_channel") as mock_channel, \
             patch("api_server.grpc_client.compute_pb2_grpc.ComputeServiceStub") as mock_stub:
            client.connect()

        self.assertTrue(client.is_connected)

    def test_false_after_disconnect(self):
        """After calling disconnect(), is_connected should be False."""
        client = ComputeClient.__new__(ComputeClient)
        client.server_address = "localhost:9000"
        client._connected = True
        client._connection_lock = threading.Lock()
        client.channel = MagicMock()
        client.stub = MagicMock()

        client.disconnect()

        self.assertFalse(client.is_connected)

    def test_thread_safety(self):
        """is_connected should be safe to read from multiple threads."""
        client = ComputeClient.__new__(ComputeClient)
        client._connected = True
        client._connection_lock = threading.Lock()

        results = []

        def read_connected():
            for _ in range(100):
                results.append(client.is_connected)

        threads = [threading.Thread(target=read_connected) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # All reads should have returned True
        self.assertEqual(len(results), 400)
        self.assertTrue(all(r is True for r in results))


# ---------------------------------------------------------------------------
# Integration-style Tests (method-level retry wrapping)
# ---------------------------------------------------------------------------

class TestMethodRetryWrapping(unittest.TestCase):
    """Verify that the public methods correctly delegate to _execute_with_retry."""

    def _make_client(self):
        """Create a ComputeClient with fast backoff and mocked connection."""
        config = RetryConfig(
            max_retries=1,
            base_backoff_seconds=0.001,
            max_backoff_seconds=0.01,
        )
        client = ComputeClient.__new__(ComputeClient)
        client.server_address = "localhost:9000"
        client.retry_config = config
        client.channel = MagicMock()
        client.stub = MagicMock()
        client._connected = True
        client._connection_lock = threading.Lock()
        return client

    @patch("api_server.grpc_client.time.sleep")
    @patch.object(ComputeClient, "connect")
    def test_health_check_returns_unhealthy_on_exhausted_retries(self, mock_connect, mock_sleep):
        """health_check should return unhealthy HealthStatus when retries are exhausted,
        rather than raising an exception (Req 1.3)."""
        client = self._make_client()
        error = _make_grpc_error(grpc.StatusCode.UNAVAILABLE, "down")
        client.stub.HealthCheck = MagicMock(side_effect=error)

        result = client.health_check(timeout=1.0)

        self.assertFalse(result.healthy)
        self.assertIn("Health check failed", result.status_message)

    @patch("api_server.grpc_client.time.sleep")
    @patch.object(ComputeClient, "connect")
    def test_list_models_raises_on_exhausted_retries(self, mock_connect, mock_sleep):
        """list_models should raise RuntimeError when retries are exhausted (Req 1.5)."""
        client = self._make_client()
        error = _make_grpc_error(grpc.StatusCode.UNAVAILABLE, "down")
        client.stub.ListModels = MagicMock(side_effect=error)

        with self.assertRaises(RuntimeError) as ctx:
            client.list_models(timeout=1.0)

        self.assertIn("Compute server unreachable", str(ctx.exception))

    @patch("api_server.grpc_client.time.sleep")
    @patch.object(ComputeClient, "connect")
    def test_cancel_request_raises_on_exhausted_retries(self, mock_connect, mock_sleep):
        """cancel_request should raise RuntimeError when retries are exhausted (Req 1.5)."""
        client = self._make_client()
        error = _make_grpc_error(grpc.StatusCode.UNAVAILABLE, "down")
        client.stub.CancelRequest = MagicMock(side_effect=error)

        with self.assertRaises(RuntimeError) as ctx:
            client.cancel_request("req-123", timeout=1.0)

        self.assertIn("Compute server unreachable", str(ctx.exception))

    @patch("api_server.grpc_client.time.sleep")
    @patch.object(ComputeClient, "connect")
    def test_get_metrics_raises_on_exhausted_retries(self, mock_connect, mock_sleep):
        """get_metrics should raise RuntimeError when retries are exhausted (Req 1.5)."""
        client = self._make_client()
        error = _make_grpc_error(grpc.StatusCode.UNAVAILABLE, "down")
        client.stub.GetMetrics = MagicMock(side_effect=error)

        with self.assertRaises(RuntimeError) as ctx:
            client.get_metrics(timeout=1.0)

        self.assertIn("Compute server unreachable", str(ctx.exception))

    def test_process_stream_has_no_retry(self):
        """process_stream should NOT use _execute_with_retry — streaming calls
        are not retried to avoid duplicate output."""
        client = self._make_client()
        error = _make_grpc_error(grpc.StatusCode.UNAVAILABLE, "down")
        client.stub.ProcessStream = MagicMock(side_effect=error)

        # process_stream is a generator; consuming it should raise the gRPC error
        # directly without retry wrapping.
        with self.assertRaises(grpc.RpcError):
            list(client.process_stream("test input", timeout=1.0))

        # The stub should have been called exactly once — no retries
        client.stub.ProcessStream.assert_called_once()


# ---------------------------------------------------------------------------
# Property-Based Tests (hypothesis)
# ---------------------------------------------------------------------------

from hypothesis import given, strategies as st, settings


class TestProperty1ExponentialBackoff(unittest.TestCase):
    """Property-based tests for exponential backoff interval calculation.

    Feature: serving-framework-enhancement, Property 1: Exponential backoff
    interval calculation

    For any retry attempt number n (0 ≤ n < max_retries) and any base_backoff
    and max_backoff configuration, the computed backoff interval SHALL equal
    min(base_backoff × 2^n, max_backoff).

    **Validates: Requirements 1.1**
    """

    @settings(max_examples=100)
    @given(
        attempt=st.integers(min_value=0, max_value=20),
        base_backoff=st.floats(min_value=0.01, max_value=100.0,
                               allow_nan=False, allow_infinity=False),
        max_backoff=st.floats(min_value=0.01, max_value=1000.0,
                              allow_nan=False, allow_infinity=False),
    )
    def test_backoff_equals_min_of_exponential_and_max(
        self, attempt, base_backoff, max_backoff
    ):
        """Feature: serving-framework-enhancement, Property 1: Exponential
        backoff interval calculation

        For any attempt n, base_backoff, and max_backoff, the computed backoff
        SHALL equal min(base_backoff × 2^n, max_backoff).

        **Validates: Requirements 1.1**
        """
        config = RetryConfig(
            base_backoff_seconds=base_backoff,
            max_backoff_seconds=max_backoff,
        )
        client = ComputeClient.__new__(ComputeClient)
        client.retry_config = config

        actual = client._calculate_backoff(attempt)
        expected = min(base_backoff * (2 ** attempt), max_backoff)

        self.assertAlmostEqual(
            actual, expected, places=6,
            msg=(
                f"_calculate_backoff({attempt}) with base={base_backoff}, "
                f"max={max_backoff}: expected {expected}, got {actual}"
            ),
        )


class TestProperty2RetryCountEnforcement(unittest.TestCase):
    """Property-based tests for retry count enforcement.

    Feature: serving-framework-enhancement, Property 2: Retry count enforcement

    For any configured max_retries value and any operation that consistently
    returns a retryable error, the retry wrapper SHALL invoke the operation
    exactly (max_retries + 1) times (1 original attempt + max_retries retries)
    before raising an error.

    **Validates: Requirements 1.2**
    """

    @settings(max_examples=100)
    @given(
        max_retries=st.integers(min_value=0, max_value=10),
    )
    def test_operation_invoked_exactly_max_retries_plus_one_times(
        self, max_retries
    ):
        """Feature: serving-framework-enhancement, Property 2: Retry count
        enforcement

        For any max_retries value, when the operation always fails with a
        retryable error, _execute_with_retry SHALL invoke the operation
        exactly (max_retries + 1) times before raising RuntimeError.

        **Validates: Requirements 1.2**
        """
        config = RetryConfig(
            max_retries=max_retries,
            base_backoff_seconds=0.001,
            max_backoff_seconds=0.01,
        )
        client = ComputeClient.__new__(ComputeClient)
        client.server_address = "localhost:9000"
        client.retry_config = config
        client.channel = MagicMock()
        client.stub = MagicMock()
        client._connected = True
        client._connection_lock = threading.Lock()

        error = _make_grpc_error(grpc.StatusCode.UNAVAILABLE, "server down")
        call_count = 0

        def failing_operation():
            nonlocal call_count
            call_count += 1
            raise error

        with patch("api_server.grpc_client.time.sleep"):
            with patch.object(ComputeClient, "connect"):
                with self.assertRaises(RuntimeError):
                    client._execute_with_retry(failing_operation, timeout=5.0)

        expected_calls = max_retries + 1
        self.assertEqual(
            call_count, expected_calls,
            f"max_retries={max_retries}: expected {expected_calls} calls, "
            f"got {call_count}",
        )


if __name__ == "__main__":
    unittest.main()
