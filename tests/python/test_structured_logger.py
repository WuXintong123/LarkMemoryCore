# ===- test_structured_logger.py -----------------------------------------------
#
# SPDX-License-Identifier: Apache-2.0
#
# ===---------------------------------------------------------------------------
#
# Property-based and unit tests for the Python StructuredFormatter and
# setup_logger from api_server/logger.py.
#
# Property 8: Python structured log output format
# Validates: Requirements 5.1
#
# ===---------------------------------------------------------------------------

import json
import logging
import os
import sys

import pytest
from hypothesis import given, settings, assume
from hypothesis import strategies as st

# Ensure the project root is on sys.path so that api_server can be imported.
_project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from api_server.logger import StructuredFormatter, setup_logger


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# Strategy for generating arbitrary log message strings.
# Includes printable ASCII, unicode, whitespace, control characters, and
# special JSON characters to stress the formatter.
log_message_strategy = st.text(
    alphabet=st.characters(
        whitelist_categories=("L", "M", "N", "P", "S", "Z"),
        whitelist_characters='\t\n\r "\\/{}\x00',
    ),
    min_size=0,
    max_size=500,
)

# Strategy for log level names recognised by the logging module.
log_level_strategy = st.sampled_from(["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"])

# Strategy for component names.
component_strategy = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N")),
    min_size=1,
    max_size=50,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_log_record(
    message: str,
    level_name: str = "INFO",
    component: str = "api_server",
) -> logging.LogRecord:
    """Create a logging.LogRecord with the given message, level, and component."""
    level_num = getattr(logging, level_name, logging.INFO)
    record = logging.LogRecord(
        name="test_logger",
        level=level_num,
        pathname="test_structured_logger.py",
        lineno=0,
        msg=message,
        args=(),
        exc_info=None,
    )
    record.component = component
    return record


# ---------------------------------------------------------------------------
# Property-Based Tests
# ---------------------------------------------------------------------------

class TestProperty8PythonStructuredLogOutputFormat:
    """Feature: serving-framework-enhancement, Property 8: Python structured log output format"""

    @settings(max_examples=100)
    @given(message=log_message_strategy, level=log_level_strategy)
    def test_property8_output_is_valid_json_with_required_keys(
        self, message: str, level: str
    ):
        """Feature: serving-framework-enhancement, Property 8: Python structured log output format

        **Validates: Requirements 5.1**

        For any log message string and any log level, the Python
        StructuredFormatter SHALL produce a valid JSON string containing at
        minimum the keys "timestamp", "level", "message", and "component".
        """
        formatter = StructuredFormatter()
        record = _make_log_record(message, level)
        output = formatter.format(record)

        # The output MUST be valid JSON.
        parsed = json.loads(output)

        # The output MUST contain the four mandatory keys.
        assert "timestamp" in parsed, "Missing 'timestamp' key in log output"
        assert "level" in parsed, "Missing 'level' key in log output"
        assert "message" in parsed, "Missing 'message' key in log output"
        assert "component" in parsed, "Missing 'component' key in log output"

    @settings(max_examples=100)
    @given(message=log_message_strategy, level=log_level_strategy)
    def test_property8_level_matches_input(self, message: str, level: str):
        """Feature: serving-framework-enhancement, Property 8: Python structured log output format

        **Validates: Requirements 5.1**

        The "level" field in the JSON output SHALL match the log level used
        when creating the record.
        """
        formatter = StructuredFormatter()
        record = _make_log_record(message, level)
        output = formatter.format(record)
        parsed = json.loads(output)

        assert parsed["level"] == level, (
            f"Expected level '{level}', got '{parsed['level']}'"
        )

    @settings(max_examples=100)
    @given(message=log_message_strategy, level=log_level_strategy)
    def test_property8_message_matches_input(self, message: str, level: str):
        """Feature: serving-framework-enhancement, Property 8: Python structured log output format

        **Validates: Requirements 5.1**

        The "message" field in the JSON output SHALL match the original log
        message string.
        """
        formatter = StructuredFormatter()
        record = _make_log_record(message, level)
        output = formatter.format(record)
        parsed = json.loads(output)

        assert parsed["message"] == message, (
            f"Message mismatch: expected {message!r}, got {parsed['message']!r}"
        )

    @settings(max_examples=100)
    @given(
        message=log_message_strategy,
        level=log_level_strategy,
        component=component_strategy,
    )
    def test_property8_component_matches_input(
        self, message: str, level: str, component: str
    ):
        """Feature: serving-framework-enhancement, Property 8: Python structured log output format

        **Validates: Requirements 5.1**

        The "component" field in the JSON output SHALL match the component
        attribute set on the log record.
        """
        formatter = StructuredFormatter()
        record = _make_log_record(message, level, component=component)
        output = formatter.format(record)
        parsed = json.loads(output)

        assert parsed["component"] == component, (
            f"Component mismatch: expected {component!r}, got {parsed['component']!r}"
        )

    @settings(max_examples=100)
    @given(message=log_message_strategy, level=log_level_strategy)
    def test_property8_timestamp_is_nonempty_string(
        self, message: str, level: str
    ):
        """Feature: serving-framework-enhancement, Property 8: Python structured log output format

        **Validates: Requirements 5.1**

        The "timestamp" field SHALL be a non-empty string.
        """
        formatter = StructuredFormatter()
        record = _make_log_record(message, level)
        output = formatter.format(record)
        parsed = json.loads(output)

        assert isinstance(parsed["timestamp"], str), "timestamp must be a string"
        assert len(parsed["timestamp"]) > 0, "timestamp must not be empty"


class TestProperty10LogLevelFiltering:
    """Feature: serving-framework-enhancement, Property 10: Log level filtering"""

    # Ordered list of standard log levels with their numeric values.
    LOG_LEVELS = [
        ("DEBUG", logging.DEBUG),       # 10
        ("INFO", logging.INFO),         # 20
        ("WARNING", logging.WARNING),   # 30
        ("ERROR", logging.ERROR),       # 40
        ("CRITICAL", logging.CRITICAL), # 50
    ]

    # Strategy for selecting a threshold level name.
    threshold_strategy = st.sampled_from(["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"])

    # Strategy for selecting a message level name.
    message_level_strategy = st.sampled_from(["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"])

    def _get_numeric_level(self, level_name: str) -> int:
        """Return the numeric value for a standard log level name."""
        return getattr(logging, level_name)

    def _create_fresh_logger(self, name: str, level: str) -> logging.Logger:
        """Create a fresh logger with a unique name to avoid handler conflicts.

        Removes any existing handlers and configures the logger with the
        specified level using setup_logger.
        """
        # Use a unique logger name to avoid interference between test runs.
        logger = logging.getLogger(name)
        # Clear any pre-existing handlers to ensure a clean state.
        logger.handlers.clear()
        # Now set up the logger with the desired level.
        configured_logger = setup_logger(name, level=level)
        return configured_logger

    @settings(max_examples=100)
    @given(
        threshold=threshold_strategy,
        msg_level=message_level_strategy,
        message=log_message_strategy,
    )
    def test_property10_below_threshold_suppressed(
        self, threshold: str, msg_level: str, message: str
    ):
        """Feature: serving-framework-enhancement, Property 10: Log level filtering

        **Validates: Requirements 5.5**

        For any configured log level threshold and any log message with a level
        below that threshold, the StructuredLogger SHALL suppress the message
        (produce no output). For any message at or above the threshold, the
        logger SHALL produce output.
        """
        import io

        threshold_num = self._get_numeric_level(threshold)
        msg_level_num = self._get_numeric_level(msg_level)

        # Create a unique logger name for this test invocation to avoid
        # cross-contamination between hypothesis examples.
        import uuid
        unique_name = f"test_prop10_{uuid.uuid4().hex}"
        logger = logging.getLogger(unique_name)
        logger.handlers.clear()
        logger.setLevel(threshold_num)
        logger.propagate = False

        # Attach a StreamHandler writing to a StringIO buffer so we can
        # capture the output without touching real stderr.
        buffer = io.StringIO()
        handler = logging.StreamHandler(buffer)
        handler.setLevel(threshold_num)
        handler.setFormatter(StructuredFormatter())
        logger.addHandler(handler)

        # Emit the log message at the specified level.
        logger.log(msg_level_num, message)

        output = buffer.getvalue()

        if msg_level_num < threshold_num:
            # Messages below the threshold MUST be suppressed.
            assert output == "", (
                f"Expected no output for message level {msg_level} ({msg_level_num}) "
                f"below threshold {threshold} ({threshold_num}), but got: {output!r}"
            )
        else:
            # Messages at or above the threshold MUST produce output.
            assert len(output) > 0, (
                f"Expected output for message level {msg_level} ({msg_level_num}) "
                f"at or above threshold {threshold} ({threshold_num}), but got nothing"
            )
            # The output must be valid JSON with the correct level.
            parsed = json.loads(output.strip())
            assert parsed["level"] == msg_level, (
                f"Expected level '{msg_level}' in output, got '{parsed['level']}'"
            )
            assert parsed["message"] == message, (
                f"Expected message {message!r}, got {parsed['message']!r}"
            )

    @settings(max_examples=100)
    @given(
        threshold=threshold_strategy,
        message=log_message_strategy,
    )
    def test_property10_at_threshold_produces_output(
        self, threshold: str, message: str
    ):
        """Feature: serving-framework-enhancement, Property 10: Log level filtering

        **Validates: Requirements 5.5**

        For any configured log level threshold and any log message at exactly
        that threshold level, the StructuredLogger SHALL produce output.
        """
        import io
        import uuid

        threshold_num = self._get_numeric_level(threshold)

        unique_name = f"test_prop10_at_{uuid.uuid4().hex}"
        logger = logging.getLogger(unique_name)
        logger.handlers.clear()
        logger.setLevel(threshold_num)
        logger.propagate = False

        buffer = io.StringIO()
        handler = logging.StreamHandler(buffer)
        handler.setLevel(threshold_num)
        handler.setFormatter(StructuredFormatter())
        logger.addHandler(handler)

        # Log at exactly the threshold level.
        logger.log(threshold_num, message)

        output = buffer.getvalue()
        assert len(output) > 0, (
            f"Expected output for message at threshold level {threshold} "
            f"({threshold_num}), but got nothing"
        )
        parsed = json.loads(output.strip())
        assert parsed["level"] == threshold, (
            f"Expected level '{threshold}', got '{parsed['level']}'"
        )

    @settings(max_examples=100)
    @given(
        threshold=threshold_strategy,
        message=log_message_strategy,
    )
    def test_property10_setup_logger_respects_level_filtering(
        self, threshold: str, message: str
    ):
        """Feature: serving-framework-enhancement, Property 10: Log level filtering

        **Validates: Requirements 5.5**

        When setup_logger is used to create a logger with a given level, the
        logger SHALL filter messages according to that level threshold. This
        tests the full setup_logger integration path.
        """
        import io
        import uuid

        threshold_num = self._get_numeric_level(threshold)

        # Create a logger via setup_logger with a unique name.
        unique_name = f"test_prop10_setup_{uuid.uuid4().hex}"
        logger = logging.getLogger(unique_name)
        logger.handlers.clear()
        logger = setup_logger(unique_name, level=threshold)

        # Replace the handler's stream with a StringIO buffer for capture.
        buffer = io.StringIO()
        for h in logger.handlers:
            h.stream = buffer

        # Test all five standard levels against the threshold.
        for level_name, level_num in self.LOG_LEVELS:
            buffer.truncate(0)
            buffer.seek(0)

            logger.log(level_num, message)

            output = buffer.getvalue()

            if level_num < threshold_num:
                assert output == "", (
                    f"setup_logger({threshold}): expected suppression for "
                    f"{level_name} ({level_num}), got: {output!r}"
                )
            else:
                assert len(output) > 0, (
                    f"setup_logger({threshold}): expected output for "
                    f"{level_name} ({level_num}), got nothing"
                )


# ---------------------------------------------------------------------------
# Unit Tests — edge cases and specific scenarios
# ---------------------------------------------------------------------------

class TestStructuredFormatterUnitTests:
    """Unit tests for StructuredFormatter covering edge cases."""

    def test_empty_message(self):
        """An empty message string should produce valid JSON with an empty message field."""
        formatter = StructuredFormatter()
        record = _make_log_record("", "INFO")
        output = formatter.format(record)
        parsed = json.loads(output)

        assert parsed["message"] == ""
        assert "timestamp" in parsed
        assert parsed["level"] == "INFO"
        assert parsed["component"] == "api_server"

    def test_message_with_special_json_characters(self):
        """Messages containing JSON-special characters (quotes, backslashes, braces)
        should be properly escaped and produce valid JSON."""
        special_message = 'He said "hello\\world" and {key: [value]}'
        formatter = StructuredFormatter()
        record = _make_log_record(special_message, "WARNING")
        output = formatter.format(record)
        parsed = json.loads(output)

        assert parsed["message"] == special_message
        assert parsed["level"] == "WARNING"

    def test_message_with_unicode(self):
        """Messages containing unicode characters (CJK, emoji, etc.) should be
        preserved in the JSON output."""
        unicode_message = "日志消息 🚀 résumé naïve"
        formatter = StructuredFormatter()
        record = _make_log_record(unicode_message, "ERROR")
        output = formatter.format(record)
        parsed = json.loads(output)

        assert parsed["message"] == unicode_message

    def test_message_with_newlines_and_tabs(self):
        """Messages containing newlines and tabs should be properly escaped."""
        multiline_message = "line1\nline2\ttabbed"
        formatter = StructuredFormatter()
        record = _make_log_record(multiline_message, "DEBUG")
        output = formatter.format(record)
        parsed = json.loads(output)

        assert parsed["message"] == multiline_message

    def test_context_fields_included_when_present(self):
        """Optional context fields (request_id, model_id, etc.) should appear
        in the JSON output when set on the log record."""
        formatter = StructuredFormatter()
        record = _make_log_record("request processed", "INFO")
        record.request_id = "req-12345"
        record.model_id = "deepseek-r1"
        record.method = "POST"
        record.path = "/v1/chat/completions"
        record.latency_ms = 42.5
        record.status_code = 200
        output = formatter.format(record)
        parsed = json.loads(output)

        assert parsed["request_id"] == "req-12345"
        assert parsed["model_id"] == "deepseek-r1"
        assert parsed["method"] == "POST"
        assert parsed["path"] == "/v1/chat/completions"
        assert parsed["latency_ms"] == 42.5
        assert parsed["status_code"] == 200

    def test_context_fields_absent_when_not_set(self):
        """Optional context fields should NOT appear in the JSON output when
        they are not set on the log record."""
        formatter = StructuredFormatter()
        record = _make_log_record("simple message", "INFO")
        output = formatter.format(record)
        parsed = json.loads(output)

        for field in StructuredFormatter.CONTEXT_FIELDS:
            assert field not in parsed, (
                f"Field '{field}' should not be present when not set"
            )

    def test_arbitrary_extra_fields_are_preserved_when_json_serializable(self):
        """Custom logging extras should survive formatter output so debug trace
        payloads can be inspected directly in JSON logs."""
        formatter = StructuredFormatter()
        record = _make_log_record("trace message", "INFO")
        record.request_kind = "chat"
        record.prompt = "User: hello"
        record.prompt_chars = 11
        record.stream = False

        output = formatter.format(record)
        parsed = json.loads(output)

        assert parsed["request_kind"] == "chat"
        assert parsed["prompt"] == "User: hello"
        assert parsed["prompt_chars"] == 11
        assert parsed["stream"] is False

    def test_default_component_is_api_server(self):
        """When no component attribute is set, the default should be 'api_server'."""
        formatter = StructuredFormatter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="test.py",
            lineno=0,
            msg="test message",
            args=(),
            exc_info=None,
        )
        # Do NOT set record.component — rely on the default.
        output = formatter.format(record)
        parsed = json.loads(output)

        assert parsed["component"] == "api_server"

    def test_all_log_levels_produce_valid_output(self):
        """Every standard log level should produce valid JSON with the correct
        level field."""
        formatter = StructuredFormatter()
        for level_name in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
            record = _make_log_record("test", level_name)
            output = formatter.format(record)
            parsed = json.loads(output)
            assert parsed["level"] == level_name

    def test_timestamp_format_iso8601(self):
        """The timestamp should follow ISO 8601 format with millisecond precision."""
        import re

        formatter = StructuredFormatter()
        record = _make_log_record("timestamp test", "INFO")
        output = formatter.format(record)
        parsed = json.loads(output)

        # Expected format: YYYY-MM-DDTHH:MM:SS.mmmZ
        iso_pattern = r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$"
        assert re.match(iso_pattern, parsed["timestamp"]), (
            f"Timestamp '{parsed['timestamp']}' does not match ISO 8601 format"
        )

    def test_very_long_message(self):
        """A very long message should still produce valid JSON."""
        long_message = "A" * 100000
        formatter = StructuredFormatter()
        record = _make_log_record(long_message, "INFO")
        output = formatter.format(record)
        parsed = json.loads(output)

        assert parsed["message"] == long_message
        assert len(parsed["message"]) == 100000


class TestSetupLoggerUnitTests:
    """Unit tests for the setup_logger factory function."""

    def test_setup_logger_returns_logger(self):
        """setup_logger should return a logging.Logger instance."""
        logger = setup_logger("test_setup_returns_logger")
        assert isinstance(logger, logging.Logger)

    def test_setup_logger_uses_structured_formatter(self):
        """The logger's handler should use StructuredFormatter."""
        logger = setup_logger("test_setup_formatter")
        assert len(logger.handlers) > 0
        handler = logger.handlers[0]
        assert isinstance(handler.formatter, StructuredFormatter)

    def test_setup_logger_respects_level_parameter(self):
        """setup_logger should set the logger level to the specified level."""
        logger = setup_logger("test_setup_level_param", level="DEBUG")
        assert logger.level == logging.DEBUG

    def test_setup_logger_reads_env_variable(self):
        """setup_logger should read LOG_LEVEL from the environment when no
        level parameter is provided."""
        original = os.environ.get("LOG_LEVEL")
        try:
            os.environ["LOG_LEVEL"] = "ERROR"
            logger = setup_logger("test_setup_env_level")
            assert logger.level == logging.ERROR
        finally:
            if original is None:
                os.environ.pop("LOG_LEVEL", None)
            else:
                os.environ["LOG_LEVEL"] = original

    def test_setup_logger_defaults_to_info(self):
        """setup_logger should default to INFO when no level is specified and
        LOG_LEVEL is not set."""
        original = os.environ.get("LOG_LEVEL")
        try:
            os.environ.pop("LOG_LEVEL", None)
            logger = setup_logger("test_setup_default_info")
            assert logger.level == logging.INFO
        finally:
            if original is not None:
                os.environ["LOG_LEVEL"] = original

    def test_setup_logger_no_propagation(self):
        """setup_logger should disable propagation to avoid duplicate output."""
        logger = setup_logger("test_setup_no_propagation")
        assert logger.propagate is False

    def test_setup_logger_no_duplicate_handlers(self):
        """Calling setup_logger twice with the same name should not add
        duplicate handlers."""
        name = "test_setup_no_dup_handlers"
        logger1 = setup_logger(name)
        handler_count_1 = len(logger1.handlers)
        logger2 = setup_logger(name)
        handler_count_2 = len(logger2.handlers)
        assert handler_count_1 == handler_count_2
        assert logger1 is logger2
