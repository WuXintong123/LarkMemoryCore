# ===- logger.py ---------------------------------------------------------------
#
# SPDX-License-Identifier: Apache-2.0
#
# ===---------------------------------------------------------------------------
#
# Structured JSON logger for the API server.
# Outputs log entries as JSON objects to stderr, compatible with log
# aggregation tools. Each entry contains at minimum: timestamp, level,
# message, and component fields.
#
# ===---------------------------------------------------------------------------

import json
import logging
import os
import time
from datetime import datetime, timezone


class StructuredFormatter(logging.Formatter):
    """Formatter that outputs log records as JSON objects.

    Each log entry contains the following mandatory fields:
      - timestamp: ISO 8601 formatted timestamp with millisecond precision
      - level: log level name (DEBUG, INFO, WARNING, ERROR, CRITICAL)
      - message: the log message text
      - component: the component name (defaults to "api_server")

    Additional context fields are included when present on the log record:
      - request_id: unique identifier for the current request
      - model_id: the model being used for inference
      - method: HTTP method (GET, POST, etc.)
      - path: HTTP request path
      - latency_ms: request processing latency in milliseconds
      - status_code: HTTP response status code
    """

    # Context fields that are optionally included in the JSON output
    # when they are present as attributes on the log record.
    CONTEXT_FIELDS = (
        "request_id",
        "model_id",
        "method",
        "path",
        "latency_ms",
        "status_code",
    )
    RESERVED_LOG_RECORD_FIELDS = frozenset(
        logging.LogRecord(
            name="",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="",
            args=(),
            exc_info=None,
        ).__dict__.keys()
    )

    def format(self, record: logging.LogRecord) -> str:
        """Format a log record as a JSON string.

        Args:
            record: The log record to format.

        Returns:
            A JSON string representing the log entry.
        """
        # Build the mandatory fields.
        log_entry = {
            "timestamp": self._format_timestamp(record),
            "level": record.levelname,
            "message": record.getMessage(),
            "component": getattr(record, "component", "api_server"),
        }

        # Append optional context fields when they are present on the record.
        for key in self.CONTEXT_FIELDS:
            value = getattr(record, key, None)
            if value is not None:
                log_entry[key] = value

        for key, value in record.__dict__.items():
            if (
                key in self.RESERVED_LOG_RECORD_FIELDS
                or key in log_entry
                or key.startswith("_")
            ):
                continue
            log_entry[key] = self._normalize_extra_value(value)

        return json.dumps(log_entry, ensure_ascii=False)

    def _format_timestamp(self, record: logging.LogRecord) -> str:
        """Format the record's creation time as an ISO 8601 timestamp.

        Uses the record's created attribute (a float epoch timestamp) to
        produce a UTC timestamp with millisecond precision in the format:
        YYYY-MM-DDTHH:MM:SS.mmmZ

        Args:
            record: The log record whose timestamp to format.

        Returns:
            An ISO 8601 formatted timestamp string.
        """
        dt = datetime.fromtimestamp(record.created, tz=timezone.utc)
        # Format with millisecond precision: YYYY-MM-DDTHH:MM:SS.mmmZ
        return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{int(dt.microsecond / 1000):03d}Z"

    def _normalize_extra_value(self, value):
        """Coerce arbitrary logging extras into JSON-serializable values."""
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        try:
            json.dumps(value, ensure_ascii=False)
            return value
        except TypeError:
            return str(value)


def setup_logger(name: str, level: str = None) -> logging.Logger:
    """Create and configure a logger with structured JSON output.

    Creates a logger with the given name, attaches a StreamHandler (stderr)
    with the StructuredFormatter, and sets the log level. The log level is
    determined by the following priority:
      1. The `level` parameter if explicitly provided
      2. The LOG_LEVEL environment variable
      3. Default: "INFO"

    Args:
        name: The name for the logger (e.g., "api_server", "api_server.grpc").
        level: Optional log level string. If None, reads from the LOG_LEVEL
               environment variable, defaulting to "INFO".

    Returns:
        A configured logging.Logger instance with structured JSON output.
    """
    # Determine the effective log level.
    if level is None:
        level = os.environ.get("LOG_LEVEL", "INFO")

    # Normalize the level string to uppercase for consistent parsing.
    level_upper = level.upper()

    # Map the level string to a logging constant, defaulting to INFO
    # for unrecognized values.
    numeric_level = getattr(logging, level_upper, None)
    if not isinstance(numeric_level, int):
        numeric_level = logging.INFO

    logger = logging.getLogger(name)
    logger.setLevel(numeric_level)

    # Avoid adding duplicate handlers if setup_logger is called multiple
    # times with the same logger name.
    if not logger.handlers:
        # Output to stderr as specified in the design document.
        handler = logging.StreamHandler()  # defaults to sys.stderr
        handler.setLevel(numeric_level)
        handler.setFormatter(StructuredFormatter())
        logger.addHandler(handler)
    else:
        # Update existing handler levels if the logger already has handlers.
        for handler in logger.handlers:
            handler.setLevel(numeric_level)
            # Ensure the formatter is a StructuredFormatter.
            if not isinstance(handler.formatter, StructuredFormatter):
                handler.setFormatter(StructuredFormatter())

    # Prevent log propagation to the root logger to avoid duplicate output.
    logger.propagate = False

    return logger
