//====- StructuredLogger.cpp -----------------------------------------------===//
//
// SPDX-License-Identifier: Apache-2.0
//
//===----------------------------------------------------------------------===//
//
// This file implements the structured JSON logger for the compute server.
// Log entries are output as JSON objects to stderr, manually constructed
// without any external JSON library dependency.
//
//===----------------------------------------------------------------------===//

#include "StructuredLogger.h"

#include <algorithm>
#include <chrono>
#include <cstdlib>
#include <cstring>
#include <ctime>
#include <iomanip>
#include <iostream>
#include <sstream>

namespace compute {

StructuredLogger &StructuredLogger::getInstance() {
  static StructuredLogger instance;
  return instance;
}

StructuredLogger::StructuredLogger() : current_level_(LogLevel::INFO) {
  initFromEnvironment();
}

void StructuredLogger::initFromEnvironment() {
  const char *env_val = std::getenv("LOG_LEVEL");
  if (env_val && std::strlen(env_val) > 0) {
    current_level_ = parseLevelString(env_val, LogLevel::INFO);
  }
}

void StructuredLogger::setLevel(LogLevel level) {
  std::lock_guard<std::mutex> lock(log_mutex_);
  current_level_ = level;
}

LogLevel StructuredLogger::getLevel() const {
  std::lock_guard<std::mutex> lock(log_mutex_);
  return current_level_;
}

void StructuredLogger::log(LogLevel level, const std::string &message,
                           const std::map<std::string, std::string> &extra) {
  std::lock_guard<std::mutex> lock(log_mutex_);

  // Suppress messages below the current threshold.
  if (static_cast<int>(level) < static_cast<int>(current_level_)) {
    return;
  }

  std::string timestamp = formatTimestamp();
  const char *level_str = levelToString(level);
  std::string json_entry = buildJsonEntry(timestamp, level_str, message, extra);

  // Output to stderr as required by the design.
  std::cerr << json_entry << std::endl;
}

void StructuredLogger::debug(const std::string &message,
                             const std::map<std::string, std::string> &extra) {
  log(LogLevel::DEBUG, message, extra);
}

void StructuredLogger::info(const std::string &message,
                            const std::map<std::string, std::string> &extra) {
  log(LogLevel::INFO, message, extra);
}

void StructuredLogger::warning(
    const std::string &message,
    const std::map<std::string, std::string> &extra) {
  log(LogLevel::WARNING, message, extra);
}

void StructuredLogger::error(const std::string &message,
                             const std::map<std::string, std::string> &extra) {
  log(LogLevel::ERROR, message, extra);
}

LogLevel StructuredLogger::parseLevelString(const std::string &level_str,
                                            LogLevel default_level) {
  // Convert to uppercase for case-insensitive comparison.
  std::string upper = level_str;
  std::transform(upper.begin(), upper.end(), upper.begin(),
                 [](unsigned char c) { return std::toupper(c); });

  if (upper == "DEBUG") {
    return LogLevel::DEBUG;
  } else if (upper == "INFO") {
    return LogLevel::INFO;
  } else if (upper == "WARNING" || upper == "WARN") {
    return LogLevel::WARNING;
  } else if (upper == "ERROR") {
    return LogLevel::ERROR;
  }
  return default_level;
}

const char *StructuredLogger::levelToString(LogLevel level) {
  switch (level) {
  case LogLevel::DEBUG:
    return "DEBUG";
  case LogLevel::INFO:
    return "INFO";
  case LogLevel::WARNING:
    return "WARNING";
  case LogLevel::ERROR:
    return "ERROR";
  }
  return "UNKNOWN";
}

std::string StructuredLogger::formatTimestamp() const {
  auto now = std::chrono::system_clock::now();
  auto time_t_now = std::chrono::system_clock::to_time_t(now);
  auto ms = std::chrono::duration_cast<std::chrono::milliseconds>(
                now.time_since_epoch()) %
            1000;

  std::tm tm_buf;
#if defined(_WIN32)
  gmtime_s(&tm_buf, &time_t_now);
#else
  gmtime_r(&time_t_now, &tm_buf);
#endif

  std::ostringstream oss;
  oss << std::put_time(&tm_buf, "%Y-%m-%dT%H:%M:%S");
  oss << '.' << std::setfill('0') << std::setw(3) << ms.count();
  oss << 'Z';
  return oss.str();
}

std::string StructuredLogger::escapeJsonString(const std::string &s) const {
  std::string result;
  result.reserve(s.size() + 16); // Pre-allocate with some headroom.

  for (size_t i = 0; i < s.size(); ++i) {
    char c = s[i];
    switch (c) {
    case '"':
      result += "\\\"";
      break;
    case '\\':
      result += "\\\\";
      break;
    case '\n':
      result += "\\n";
      break;
    case '\r':
      result += "\\r";
      break;
    case '\t':
      result += "\\t";
      break;
    case '\b':
      result += "\\b";
      break;
    case '\f':
      result += "\\f";
      break;
    default:
      if (static_cast<unsigned char>(c) < 0x20) {
        // Escape other control characters as \u00XX.
        char hex_buf[8];
        std::snprintf(hex_buf, sizeof(hex_buf), "\\u%04x",
                      static_cast<unsigned int>(static_cast<unsigned char>(c)));
        result += hex_buf;
      } else {
        result += c;
      }
      break;
    }
  }
  return result;
}

std::string StructuredLogger::buildJsonEntry(
    const std::string &timestamp, const std::string &level_str,
    const std::string &message,
    const std::map<std::string, std::string> &extra) const {
  // Manually construct JSON without any external library.
  // Fields are output in a deterministic order: timestamp, level, message,
  // component, then any extra fields in sorted order (std::map is sorted).
  std::ostringstream oss;
  oss << '{';
  oss << "\"timestamp\":\"" << escapeJsonString(timestamp) << "\"";
  oss << ",\"level\":\"" << escapeJsonString(level_str) << "\"";
  oss << ",\"message\":\"" << escapeJsonString(message) << "\"";
  oss << ",\"component\":\"" << escapeJsonString(COMPONENT) << "\"";

  for (const auto &kv : extra) {
    oss << ",\"" << escapeJsonString(kv.first) << "\":\""
        << escapeJsonString(kv.second) << "\"";
  }

  oss << '}';
  return oss.str();
}

} // namespace compute
