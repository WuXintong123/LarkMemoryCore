//====- StructuredLogger.h -------------------------------------------------===//
//
// SPDX-License-Identifier: Apache-2.0
//
//===----------------------------------------------------------------------===//
//
// Structured JSON logger for the compute server. Outputs log entries as JSON
// objects to stderr, containing timestamp, level, message, component, and
// optional context fields (request_id, model_id, latency_ms, token_count).
//
//===----------------------------------------------------------------------===//

#pragma once

#include <map>
#include <mutex>
#include <string>

namespace compute {

/// Log severity levels, ordered from least to most severe.
enum class LogLevel { DEBUG, INFO, WARNING, ERROR };

/// Singleton structured logger that outputs JSON-formatted log entries to
/// stderr. Thread-safe via internal mutex.
///
/// JSON output format (manually constructed, no external JSON library):
/// {
///   "timestamp": "2024-01-15T10:30:45.123Z",
///   "level": "INFO",
///   "message": "Processing request",
///   "component": "compute_server",
///   ...extra fields...
/// }
///
/// Log level is configurable via the LOG_LEVEL environment variable.
/// Valid values: DEBUG, INFO, WARNING, ERROR (case-insensitive).
/// Default level: INFO.
class StructuredLogger {
public:
  /// Get the singleton instance. On first call, reads LOG_LEVEL from the
  /// environment to set the initial log level.
  static StructuredLogger &getInstance();

  /// Set the current log level threshold. Messages below this level are
  /// suppressed.
  void setLevel(LogLevel level);

  /// Get the current log level threshold.
  LogLevel getLevel() const;

  /// Log a message at the specified level with optional extra context fields.
  /// If the message level is below the current threshold, the call is a no-op.
  ///
  /// @param level    Severity level of this log entry.
  /// @param message  Human-readable log message.
  /// @param extra    Optional key-value pairs for additional context
  ///                 (e.g., request_id, model_id, latency_ms, token_count).
  void log(LogLevel level, const std::string &message,
           const std::map<std::string, std::string> &extra = {});

  /// Convenience method: log at DEBUG level.
  void debug(const std::string &message,
             const std::map<std::string, std::string> &extra = {});

  /// Convenience method: log at INFO level.
  void info(const std::string &message,
            const std::map<std::string, std::string> &extra = {});

  /// Convenience method: log at WARNING level.
  void warning(const std::string &message,
               const std::map<std::string, std::string> &extra = {});

  /// Convenience method: log at ERROR level.
  void error(const std::string &message,
             const std::map<std::string, std::string> &extra = {});

  /// Parse a log level string (case-insensitive) into a LogLevel enum.
  /// Returns the parsed level, or the provided default if the string is
  /// unrecognized.
  static LogLevel parseLevelString(const std::string &level_str,
                                   LogLevel default_level = LogLevel::INFO);

  /// Convert a LogLevel enum to its string representation.
  static const char *levelToString(LogLevel level);

private:
  StructuredLogger();
  ~StructuredLogger() = default;
  StructuredLogger(const StructuredLogger &) = delete;
  StructuredLogger &operator=(const StructuredLogger &) = delete;

  /// Read LOG_LEVEL from environment and set the initial level.
  void initFromEnvironment();

  /// Format the current time as an ISO 8601 timestamp with millisecond
  /// precision (e.g., "2024-01-15T10:30:45.123Z").
  std::string formatTimestamp() const;

  /// Escape special characters in a string for safe inclusion in a JSON
  /// string value. Handles: \, ", \n, \r, \t, \b, \f, and control characters.
  std::string escapeJsonString(const std::string &s) const;

  /// Build a complete JSON log entry string from the given fields.
  std::string buildJsonEntry(const std::string &timestamp,
                             const std::string &level_str,
                             const std::string &message,
                             const std::map<std::string, std::string> &extra) const;

  LogLevel current_level_;
  mutable std::mutex log_mutex_;

  /// Component name included in every log entry.
  static constexpr const char *COMPONENT = "compute_server";
};

} // namespace compute
