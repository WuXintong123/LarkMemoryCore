# ===- grpc_client.py ----------------------------------------------------------
#
# SPDX-License-Identifier: Apache-2.0
#
# ===---------------------------------------------------------------------------
#
# gRPC client, used to connect to C++ gRPC service
#
# ===---------------------------------------------------------------------------

import grpc
import os
import sys
import time
import threading
import importlib.util
from typing import Generator, Optional, Dict, List, Any, Callable, TypeVar
from dataclasses import dataclass

# Get proto file path (api_server/proto)
_current_dir = os.path.dirname(os.path.abspath(__file__))
_api_server_dir = os.path.dirname(_current_dir)
_proto_dir = os.path.join(_api_server_dir, "proto")

from .logger import setup_logger

# Import generated proto files
compute_pb2_path = os.path.join(_proto_dir, "compute_pb2.py")
compute_pb2_grpc_path = os.path.join(_proto_dir, "compute_pb2_grpc.py")

if not os.path.exists(compute_pb2_path) or not os.path.exists(compute_pb2_grpc_path):
    raise ImportError(
        f"Proto files not found in {_proto_dir}.\n"
        "Please generate proto files first:\n"
        "  cmake --build --preset <your-preset>-build --target generate_python_proto"
    )

if _proto_dir not in sys.path:
    sys.path.insert(0, _proto_dir)

# Load compute_pb2
spec = importlib.util.spec_from_file_location("compute_pb2", compute_pb2_path)
compute_pb2 = importlib.util.module_from_spec(spec)
sys.modules["compute_pb2"] = compute_pb2
sys.modules["proto.compute_pb2"] = compute_pb2
spec.loader.exec_module(compute_pb2)

# Load compute_pb2_grpc
spec = importlib.util.spec_from_file_location("compute_pb2_grpc", compute_pb2_grpc_path)
compute_pb2_grpc = importlib.util.module_from_spec(spec)
sys.modules["compute_pb2_grpc"] = compute_pb2_grpc
sys.modules["proto.compute_pb2_grpc"] = compute_pb2_grpc
spec.loader.exec_module(compute_pb2_grpc)


# Type variable for generic return type in retry wrapper
T = TypeVar("T")

# Module-level logger for gRPC client retry/reconnection events
_logger = setup_logger("api_server.grpc_client")


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _read_optional_file(path: str) -> Optional[bytes]:
    if not path:
        return None
    with open(path, "rb") as f:
        return f.read()


@dataclass
class RetryConfig:
    """重试配置 - Configuration for gRPC retry and reconnection behavior.

    Controls exponential backoff parameters and which gRPC status codes
    are considered retryable (transient errors).

    Attributes:
        max_retries: Maximum number of retry attempts after the initial call.
        base_backoff_seconds: Base interval for exponential backoff calculation.
        max_backoff_seconds: Upper bound for the backoff interval.
        retryable_codes: Tuple of gRPC status codes that trigger a retry.
    """
    max_retries: int = 3
    base_backoff_seconds: float = 1.0
    max_backoff_seconds: float = 30.0
    retryable_codes: tuple = (
        grpc.StatusCode.UNAVAILABLE,
        grpc.StatusCode.DEADLINE_EXCEEDED,
    )


@dataclass(frozen=True)
class GrpcTlsConfig:
    use_tls: bool = False
    ca_cert_file: str = ""
    client_cert_file: str = ""
    client_key_file: str = ""
    server_name: str = ""

    @classmethod
    def from_env(cls) -> "GrpcTlsConfig":
        return cls(
            use_tls=_env_bool("GRPC_USE_TLS", False),
            ca_cert_file=os.getenv("GRPC_CA_CERT_FILE", ""),
            client_cert_file=os.getenv("GRPC_CLIENT_CERT_FILE", ""),
            client_key_file=os.getenv("GRPC_CLIENT_KEY_FILE", ""),
            server_name=os.getenv("GRPC_SERVER_NAME", ""),
        )


def _retry_config_from_env() -> RetryConfig:
    """Build a RetryConfig from environment variables.

    Reads the following environment variables (falls back to defaults):
      - GRPC_MAX_RETRIES: int, default 3
      - GRPC_BASE_BACKOFF_S: float, default 1.0
      - GRPC_MAX_BACKOFF_S: float, default 30.0

    Returns:
        A RetryConfig populated from the environment.
    """
    max_retries = int(os.environ.get("GRPC_MAX_RETRIES", "3"))
    base_backoff = float(os.environ.get("GRPC_BASE_BACKOFF_S", "1.0"))
    max_backoff = float(os.environ.get("GRPC_MAX_BACKOFF_S", "30.0"))
    return RetryConfig(
        max_retries=max_retries,
        base_backoff_seconds=base_backoff,
        max_backoff_seconds=max_backoff,
    )


@dataclass
class UsageStats:
    """Usage statistics from inference"""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: int = 0
    tokens_per_second: float = 0.0


@dataclass
class ProcessResult:
    """Result from process call"""
    output: str
    success: bool
    error_message: str = ""
    usage: Optional[UsageStats] = None
    request_id: str = ""
    completion_status: str = "completed"
    completion_detail: str = ""


@dataclass
class HealthStatus:
    """Server health status"""
    healthy: bool
    version: str
    uptime_seconds: int
    active_requests: int
    status_message: str


@dataclass
class ModelInfo:
    """Model information"""
    model_id: str
    ready: bool
    owned_by: str = ""
    created: int = 0
    serving_policy: Optional[Dict[str, Any]] = None


@dataclass
class StreamEvent:
    content: str = ""
    is_final: bool = False
    error_message: str = ""
    request_id: str = ""
    completion_status: str = "completed"
    completion_detail: str = ""


@dataclass
class ServerMetrics:
    """Server metrics"""
    total_requests: int
    successful_requests: int
    failed_requests: int
    total_tokens_processed: int
    average_latency_ms: float
    average_tokens_per_second: float
    model_metrics: Dict[str, Dict[str, Any]]
    rejected_requests: int = 0
    queued_requests: int = 0
    active_compute_slots: int = 0
    max_compute_slots: int = 0
    overload_rejections: int = 0
    watchdog_timeouts: int = 0
    partial_timeout_returns: int = 0
    request_cancellations: int = 0


class ComputeClient:
    """gRPC compute service client with retry and reconnection support.

    Provides automatic retry with exponential backoff for transient gRPC
    errors (UNAVAILABLE, DEADLINE_EXCEEDED) on non-streaming RPC calls.
    Streaming calls (process_stream) are NOT retried to avoid duplicate output.

    Connection state is tracked via the ``is_connected`` property and the
    internal ``_connected`` flag, protected by ``_connection_lock``.
    """

    def __init__(
        self,
        server_address: str = "localhost:9000",
        retry_config: Optional[RetryConfig] = None,
        tls_config: Optional[GrpcTlsConfig] = None,
    ):
        self.server_address = server_address
        self.retry_config = retry_config or _retry_config_from_env()
        self.tls_config = tls_config
        self.channel = None
        self.stub = None
        self._connected: bool = False
        self._connection_lock = threading.Lock()

    def connect(self):
        """Establish connection and update connection state."""
        with self._connection_lock:
            tls_config = getattr(self, "tls_config", None) or GrpcTlsConfig.from_env()
            use_tls = tls_config.use_tls
            if use_tls:
                root_certificates = _read_optional_file(tls_config.ca_cert_file)
                certificate_chain = _read_optional_file(
                    tls_config.client_cert_file
                )
                private_key = _read_optional_file(tls_config.client_key_file)

                if bool(certificate_chain) != bool(private_key):
                    raise RuntimeError(
                        "GRPC_CLIENT_CERT_FILE and GRPC_CLIENT_KEY_FILE must be set together"
                    )

                credentials = grpc.ssl_channel_credentials(
                    root_certificates=root_certificates,
                    private_key=private_key,
                    certificate_chain=certificate_chain,
                )
                channel_options = []
                if tls_config.server_name:
                    channel_options.append(
                        (
                            "grpc.ssl_target_name_override",
                            tls_config.server_name,
                        )
                    )

                self.channel = grpc.secure_channel(
                    self.server_address,
                    credentials,
                    options=channel_options,
                )
            else:
                self.channel = grpc.insecure_channel(self.server_address)
            self.stub = compute_pb2_grpc.ComputeServiceStub(self.channel)
            self._connected = True
            _logger.info(
                "gRPC channel established",
                extra={"component": "grpc_client",
                       "server_address": self.server_address,
                       "tls_enabled": use_tls},
            )

    def disconnect(self):
        """Close connection and update connection state."""
        with self._connection_lock:
            if self.channel:
                self.channel.close()
                self.channel = None
                self.stub = None
            self._connected = False
            _logger.info(
                "gRPC channel closed",
                extra={"component": "grpc_client",
                       "server_address": self.server_address},
            )

    @property
    def is_connected(self) -> bool:
        """Query the current connection state.

        Returns True if the client believes it has an active connection
        to the compute server. The flag is set to True on successful
        connect() and reset to False on disconnect() or when a
        non-retryable connection failure is detected.

        Returns:
            True if connected, False otherwise.
        """
        with self._connection_lock:
            return self._connected

    def _ensure_connected(self):
        """Ensure client is connected"""
        if not self.stub:
            self.connect()

    def _calculate_backoff(self, attempt: int) -> float:
        """Calculate the exponential backoff interval for a given retry attempt.

        Uses the formula: min(base_backoff × 2^attempt, max_backoff).

        Args:
            attempt: Zero-based retry attempt number (0 for the first retry,
                     1 for the second, etc.).

        Returns:
            The backoff interval in seconds, clamped to max_backoff_seconds.
        """
        backoff = self.retry_config.base_backoff_seconds * (2 ** attempt)
        return min(backoff, self.retry_config.max_backoff_seconds)

    def _execute_with_retry(self, operation: Callable[[], T],
                            timeout: float) -> T:
        """Execute a gRPC operation with retry and exponential backoff.

        Wraps a callable that performs a single gRPC RPC call. On transient
        errors (those whose status code is in ``retry_config.retryable_codes``),
        the operation is retried up to ``retry_config.max_retries`` times with
        exponential backoff between attempts.

        Non-retryable gRPC errors are re-raised immediately.

        When a retry succeeds after previous failures, a reconnection event
        is logged and the connection state is restored to connected.

        If all retry attempts are exhausted, a ``RuntimeError`` is raised with
        a clear message indicating the compute server is unreachable.

        Args:
            operation: A zero-argument callable that performs the gRPC call
                       and returns its result. The callable is expected to
                       raise ``grpc.RpcError`` on failure.
            timeout: The overall timeout hint (informational; the actual
                     per-call timeout is controlled by the caller who
                     constructs the operation callable).

        Returns:
            The result of the successful operation call.

        Raises:
            grpc.RpcError: If a non-retryable gRPC error is encountered.
            RuntimeError: If all retry attempts are exhausted.
        """
        last_exception: Optional[grpc.RpcError] = None
        max_attempts = 1 + self.retry_config.max_retries  # 1 original + N retries
        had_failure = False

        for attempt in range(max_attempts):
            try:
                result = operation()
                # If we had a previous failure and now succeeded, log reconnection
                if had_failure:
                    with self._connection_lock:
                        self._connected = True
                    _logger.info(
                        "gRPC reconnection successful after %d retries",
                        attempt,
                        extra={"component": "grpc_client",
                               "retry_attempt": attempt,
                               "server_address": self.server_address},
                    )
                else:
                    # Successful first attempt — ensure connected flag is set
                    with self._connection_lock:
                        if not self._connected:
                            self._connected = True
                return result
            except grpc.RpcError as e:
                last_exception = e
                status_code = e.code()

                # Non-retryable error — propagate immediately
                if status_code not in self.retry_config.retryable_codes:
                    raise

                # Mark that we experienced a failure
                had_failure = True
                with self._connection_lock:
                    self._connected = False

                # If this was the last allowed attempt, break out to raise
                if attempt >= max_attempts - 1:
                    break

                backoff = self._calculate_backoff(attempt)
                _logger.warning(
                    "gRPC call failed with %s, retrying in %.2f seconds "
                    "(attempt %d/%d)",
                    status_code.name,
                    backoff,
                    attempt + 1,
                    self.retry_config.max_retries,
                    extra={"component": "grpc_client",
                           "grpc_status": status_code.name,
                           "retry_attempt": attempt + 1,
                           "backoff_seconds": backoff,
                           "server_address": self.server_address},
                )
                time.sleep(backoff)

                # Attempt to re-establish the channel before the next retry
                try:
                    self.connect()
                except Exception:
                    _logger.warning(
                        "Reconnection attempt failed, will retry",
                        extra={"component": "grpc_client",
                               "retry_attempt": attempt + 1,
                               "server_address": self.server_address},
                    )

        # All retries exhausted — mark as disconnected and raise clear error
        with self._connection_lock:
            self._connected = False
        _logger.error(
            "gRPC call failed after %d attempts, compute server unreachable",
            max_attempts,
            extra={"component": "grpc_client",
                   "max_retries": self.retry_config.max_retries,
                   "server_address": self.server_address},
        )
        raise RuntimeError(
            f"Compute server unreachable after {self.retry_config.max_retries} "
            f"retries at {self.server_address}: {last_exception}"
        )

    def process(
        self,
        input_text: str,
        model_id: str = "",
        timeout: float = 600.0,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        top_p: Optional[float] = None,
        top_k: Optional[int] = None,
        repetition_penalty: Optional[float] = None,
        seed: Optional[int] = None,
        request_id: Optional[str] = None,
        request_timeout_ms: Optional[int] = None,
    ) -> str:
        """Process string input (non-streaming), returns output string only"""
        result = self.process_with_stats(
            input_text=input_text,
            model_id=model_id,
            timeout=timeout,
            temperature=temperature,
            max_tokens=max_tokens,
            top_p=top_p,
            top_k=top_k,
            repetition_penalty=repetition_penalty,
            seed=seed,
            request_id=request_id,
            request_timeout_ms=request_timeout_ms,
        )
        return result.output

    def process_with_stats(
        self,
        input_text: str,
        model_id: str = "",
        timeout: float = 600.0,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        top_p: Optional[float] = None,
        top_k: Optional[int] = None,
        repetition_penalty: Optional[float] = None,
        seed: Optional[int] = None,
        request_id: Optional[str] = None,
        request_timeout_ms: Optional[int] = None,
    ) -> ProcessResult:
        """Process string input (non-streaming), returns full result with stats.

        This method is wrapped with retry logic for transient gRPC errors.
        """
        self._ensure_connected()

        request = compute_pb2.ProcessRequest(input=input_text, model_id=model_id)

        # Set optional parameters
        if temperature is not None:
            request.temperature = temperature
        if max_tokens is not None:
            request.max_tokens = max_tokens
        if top_p is not None:
            request.top_p = top_p
        if top_k is not None:
            request.top_k = top_k
        if repetition_penalty is not None:
            request.repetition_penalty = repetition_penalty
        if seed is not None:
            request.seed = seed
        if request_id is not None:
            request.request_id = request_id
        effective_timeout_ms = request_timeout_ms
        if effective_timeout_ms is None and timeout > 0:
            effective_timeout_ms = int(timeout * 1000)
        if effective_timeout_ms is not None and effective_timeout_ms > 0:
            request.timeout_ms = effective_timeout_ms

        def _rpc_call():
            response = self.stub.Process(request, timeout=timeout)
            usage = None
            if response.HasField("usage"):
                usage = UsageStats(
                    prompt_tokens=response.usage.prompt_tokens,
                    completion_tokens=response.usage.completion_tokens,
                    latency_ms=response.usage.latency_ms,
                    tokens_per_second=response.usage.tokens_per_second,
                )
            if response.success:
                return ProcessResult(
                    output=response.output,
                    success=True,
                    usage=usage,
                    request_id=response.request_id,
                    completion_status=response.completion_status or "completed",
                    completion_detail=response.completion_detail,
                )
            else:
                raise RuntimeError(
                    f"Compute service error: {response.error_message}"
                )

        return self._execute_with_retry(_rpc_call, timeout)

    def process_stream(
        self,
        input_text: str,
        model_id: str = "",
        timeout: float = 600.0,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        top_p: Optional[float] = None,
        top_k: Optional[int] = None,
        repetition_penalty: Optional[float] = None,
        seed: Optional[int] = None,
        request_id: Optional[str] = None,
        request_timeout_ms: Optional[int] = None,
    ) -> Generator[StreamEvent, None, None]:
        """Process string input with streaming response"""
        self._ensure_connected()

        request = compute_pb2.ProcessRequest(input=input_text, model_id=model_id)

        # Set optional parameters
        if temperature is not None:
            request.temperature = temperature
        if max_tokens is not None:
            request.max_tokens = max_tokens
        if top_p is not None:
            request.top_p = top_p
        if top_k is not None:
            request.top_k = top_k
        if repetition_penalty is not None:
            request.repetition_penalty = repetition_penalty
        if seed is not None:
            request.seed = seed
        if request_id is not None:
            request.request_id = request_id
        effective_timeout_ms = request_timeout_ms
        if effective_timeout_ms is None and timeout > 0:
            effective_timeout_ms = int(timeout * 1000)
        if effective_timeout_ms is not None and effective_timeout_ms > 0:
            request.timeout_ms = effective_timeout_ms

        try:
            for chunk in self.stub.ProcessStream(request, timeout=timeout):
                yield StreamEvent(
                    content=chunk.content,
                    is_final=chunk.is_final,
                    error_message=chunk.error_message,
                    request_id=chunk.request_id,
                    completion_status=chunk.completion_status or "completed",
                    completion_detail=chunk.completion_detail,
                )
                if chunk.is_final:
                    break
        except grpc.RpcError as e:
            if e.code() == grpc.StatusCode.DEADLINE_EXCEEDED:
                raise RuntimeError(f"gRPC stream timed out after {timeout} seconds.")
            raise

    def health_check(self, timeout: float = 5.0) -> HealthStatus:
        """Check server health, with retry for transient errors.

        When the client is in a disconnected state (all retries exhausted or
        connection lost), this method returns an unhealthy status rather than
        raising, so callers can use it as a connectivity probe.
        """
        self._ensure_connected()

        def _rpc_call():
            response = self.stub.HealthCheck(
                compute_pb2.HealthCheckRequest(), timeout=timeout
            )
            return HealthStatus(
                healthy=response.healthy,
                version=response.version,
                uptime_seconds=response.uptime_seconds,
                active_requests=response.active_requests,
                status_message=response.status_message,
            )

        try:
            return self._execute_with_retry(_rpc_call, timeout)
        except (grpc.RpcError, RuntimeError) as e:
            # When retries are exhausted or a non-retryable error occurs,
            # report unhealthy status instead of propagating the exception.
            return HealthStatus(
                healthy=False,
                version="",
                uptime_seconds=0,
                active_requests=0,
                status_message=f"Health check failed: {e}",
            )

    def list_models(self, timeout: float = 5.0) -> List[ModelInfo]:
        """List available models from compute server, with retry for transient errors."""
        self._ensure_connected()

        def _rpc_call():
            response = self.stub.ListModels(
                compute_pb2.ListModelsRequest(), timeout=timeout
            )
            return [
                ModelInfo(
                    model_id=m.model_id,
                    ready=m.ready,
                    owned_by=m.owned_by,
                    created=m.created,
                    serving_policy=(
                        {
                            "api_mode": m.serving.api_mode,
                            "prompt_style": m.serving.prompt_style,
                            "default_max_tokens": m.serving.default_max_tokens,
                            "max_max_tokens": m.serving.max_max_tokens,
                            "max_input_chars": m.serving.max_input_chars,
                            "request_timeout_ms": m.serving.request_timeout_ms,
                            "stream_idle_timeout_s": m.serving.stream_idle_timeout_s,
                            "allow_anonymous_models": m.serving.allow_anonymous_models,
                        }
                        if m.HasField("serving")
                        else None
                    ),
                )
                for m in response.models
            ]

        return self._execute_with_retry(_rpc_call, timeout)

    def cancel_request(self, request_id: str, timeout: float = 5.0) -> bool:
        """Cancel an ongoing request, with retry for transient errors."""
        self._ensure_connected()

        def _rpc_call():
            response = self.stub.CancelRequest(
                compute_pb2.CancelRequestMessage(request_id=request_id),
                timeout=timeout,
            )
            return response.success

        return self._execute_with_retry(_rpc_call, timeout)

    def get_metrics(self, timeout: float = 5.0) -> ServerMetrics:
        """Get server metrics, with retry for transient errors."""
        self._ensure_connected()

        def _rpc_call():
            response = self.stub.GetMetrics(
                compute_pb2.MetricsRequest(), timeout=timeout
            )
            model_metrics = {}
            for model_id, mm in response.model_metrics.items():
                model_metrics[model_id] = {
                    "request_count": mm.request_count,
                    "total_tokens": mm.total_tokens,
                    "average_latency_ms": mm.average_latency_ms,
                }
            return ServerMetrics(
                total_requests=response.total_requests,
                successful_requests=response.successful_requests,
                failed_requests=response.failed_requests,
                total_tokens_processed=response.total_tokens_processed,
                average_latency_ms=response.average_latency_ms,
                average_tokens_per_second=response.average_tokens_per_second,
                model_metrics=model_metrics,
                rejected_requests=response.rejected_requests,
                queued_requests=response.queued_requests,
                active_compute_slots=response.active_compute_slots,
                max_compute_slots=response.max_compute_slots,
                overload_rejections=response.overload_rejections,
                watchdog_timeouts=response.watchdog_timeouts,
                partial_timeout_returns=response.partial_timeout_returns,
                request_cancellations=response.request_cancellations,
            )

        return self._execute_with_retry(_rpc_call, timeout)

    def reload_models(self, timeout: float = 10.0) -> Dict[str, Any]:
        """Reload model configuration on the compute server."""
        self._ensure_connected()

        def _rpc_call():
            response = self.stub.ReloadModels(
                compute_pb2.ReloadModelsRequest(), timeout=timeout
            )
            return {
                "success": response.success,
                "model_count": response.model_count,
                "message": response.message,
            }

        return self._execute_with_retry(_rpc_call, timeout)

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.disconnect()


_default_client = None


def get_client(server_address: str = "localhost:9000") -> ComputeClient:
    """Get global client instance (singleton pattern)"""
    global _default_client
    if _default_client is None:
        _default_client = ComputeClient(server_address)
        _default_client.connect()
    return _default_client
