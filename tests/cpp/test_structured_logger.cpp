//====- test_structured_logger.cpp ------------------------------------------===//
//
// SPDX-License-Identifier: Apache-2.0
//
//===----------------------------------------------------------------------===//
//
// Property-based tests for StructuredLogger.
//
// Feature: serving-framework-enhancement, Property 9: C++ structured log output format
// Validates: Requirements 5.2
//
// Compile with:
//   g++ -std=c++17 -I../../compute_server/src \
//       test_structured_logger.cpp ../../compute_server/src/StructuredLogger.cpp \
//       -lgtest -lgtest_main -pthread -o test_structured_logger
//
//===----------------------------------------------------------------------===//

#include "StructuredLogger.h"

#include <gtest/gtest.h>

#include <algorithm>
#include <cstdio>
#include <fcntl.h>
#include <random>
#include <sstream>
#include <string>
#include <unistd.h>
#include <vector>

using compute::LogLevel;
using compute::StructuredLogger;

// ---------------------------------------------------------------------------
// Minimal JSON parser helpers for test verification
// ---------------------------------------------------------------------------

/// Skip whitespace in a JSON string starting at position pos.
static void skipWhitespace(const std::string &json, size_t &pos) {
  while (pos < json.size() &&
         (json[pos] == ' ' || json[pos] == '\t' || json[pos] == '\n' ||
          json[pos] == '\r')) {
    ++pos;
  }
}

/// Parse a JSON string value starting at position pos (pos should point to the
/// opening '"'). Returns the unescaped string content and advances pos past the
/// closing '"'. Returns false if parsing fails.
static bool parseJsonString(const std::string &json, size_t &pos,
                            std::string &out) {
  if (pos >= json.size() || json[pos] != '"')
    return false;
  ++pos; // skip opening '"'

  out.clear();
  while (pos < json.size()) {
    char c = json[pos];
    if (c == '"') {
      ++pos; // skip closing '"'
      return true;
    }
    if (c == '\\') {
      ++pos;
      if (pos >= json.size())
        return false;
      char esc = json[pos];
      switch (esc) {
      case '"':
        out += '"';
        break;
      case '\\':
        out += '\\';
        break;
      case 'n':
        out += '\n';
        break;
      case 'r':
        out += '\r';
        break;
      case 't':
        out += '\t';
        break;
      case 'b':
        out += '\b';
        break;
      case 'f':
        out += '\f';
        break;
      case 'u': {
        // Skip \uXXXX sequences — just consume 4 hex digits.
        if (pos + 4 >= json.size())
          return false;
        // We don't need to decode the actual unicode codepoint for key
        // extraction; just store a placeholder.
        out += '?';
        pos += 4;
        break;
      }
      default:
        out += esc;
        break;
      }
    } else {
      out += c;
    }
    ++pos;
  }
  return false; // unterminated string
}

/// Skip a JSON value (string, number, object, array, true, false, null)
/// starting at position pos. Advances pos past the value.
/// Returns false if the value cannot be skipped.
static bool skipJsonValue(const std::string &json, size_t &pos) {
  skipWhitespace(json, pos);
  if (pos >= json.size())
    return false;

  char c = json[pos];
  if (c == '"') {
    // String value — parse and discard.
    std::string dummy;
    return parseJsonString(json, pos, dummy);
  } else if (c == '{') {
    // Object — find matching '}'.
    int depth = 1;
    ++pos;
    bool in_string = false;
    while (pos < json.size() && depth > 0) {
      char ch = json[pos];
      if (in_string) {
        if (ch == '\\') {
          ++pos; // skip escaped char
        } else if (ch == '"') {
          in_string = false;
        }
      } else {
        if (ch == '"')
          in_string = true;
        else if (ch == '{')
          ++depth;
        else if (ch == '}')
          --depth;
      }
      ++pos;
    }
    return depth == 0;
  } else if (c == '[') {
    // Array — find matching ']'.
    int depth = 1;
    ++pos;
    bool in_string = false;
    while (pos < json.size() && depth > 0) {
      char ch = json[pos];
      if (in_string) {
        if (ch == '\\') {
          ++pos;
        } else if (ch == '"') {
          in_string = false;
        }
      } else {
        if (ch == '"')
          in_string = true;
        else if (ch == '[')
          ++depth;
        else if (ch == ']')
          --depth;
      }
      ++pos;
    }
    return depth == 0;
  } else if (c == 't' || c == 'f' || c == 'n') {
    // true, false, null
    if (json.compare(pos, 4, "true") == 0) {
      pos += 4;
      return true;
    }
    if (json.compare(pos, 5, "false") == 0) {
      pos += 5;
      return true;
    }
    if (json.compare(pos, 4, "null") == 0) {
      pos += 4;
      return true;
    }
    return false;
  } else if (c == '-' || (c >= '0' && c <= '9')) {
    // Number
    if (c == '-')
      ++pos;
    while (pos < json.size() && json[pos] >= '0' && json[pos] <= '9')
      ++pos;
    if (pos < json.size() && json[pos] == '.') {
      ++pos;
      while (pos < json.size() && json[pos] >= '0' && json[pos] <= '9')
        ++pos;
    }
    if (pos < json.size() && (json[pos] == 'e' || json[pos] == 'E')) {
      ++pos;
      if (pos < json.size() && (json[pos] == '+' || json[pos] == '-'))
        ++pos;
      while (pos < json.size() && json[pos] >= '0' && json[pos] <= '9')
        ++pos;
    }
    return true;
  }
  return false;
}

/// Extract all top-level keys from a JSON object string.
/// Returns true if the string is a valid JSON object, false otherwise.
/// The keys are stored in the output vector.
static bool extractJsonKeys(const std::string &json,
                            std::vector<std::string> &keys) {
  keys.clear();
  size_t pos = 0;
  skipWhitespace(json, pos);

  if (pos >= json.size() || json[pos] != '{')
    return false;
  ++pos; // skip '{'

  skipWhitespace(json, pos);
  if (pos < json.size() && json[pos] == '}') {
    return true; // empty object
  }

  while (pos < json.size()) {
    skipWhitespace(json, pos);

    // Parse key
    std::string key;
    if (!parseJsonString(json, pos, key))
      return false;
    keys.push_back(key);

    // Expect ':'
    skipWhitespace(json, pos);
    if (pos >= json.size() || json[pos] != ':')
      return false;
    ++pos;

    // Skip value
    skipWhitespace(json, pos);
    if (!skipJsonValue(json, pos))
      return false;

    // Expect ',' or '}'
    skipWhitespace(json, pos);
    if (pos >= json.size())
      return false;
    if (json[pos] == '}') {
      return true;
    }
    if (json[pos] == ',') {
      ++pos;
      continue;
    }
    return false; // unexpected character
  }
  return false;
}

/// Extract the value for a given key from a JSON object string.
/// Returns true if the key is found, false otherwise.
/// The value is stored as a raw string (still JSON-encoded for strings).
static bool extractJsonStringValue(const std::string &json,
                                   const std::string &targetKey,
                                   std::string &value) {
  size_t pos = 0;
  skipWhitespace(json, pos);

  if (pos >= json.size() || json[pos] != '{')
    return false;
  ++pos;

  while (pos < json.size()) {
    skipWhitespace(json, pos);
    if (json[pos] == '}')
      return false;

    // Parse key
    std::string key;
    if (!parseJsonString(json, pos, key))
      return false;

    // Expect ':'
    skipWhitespace(json, pos);
    if (pos >= json.size() || json[pos] != ':')
      return false;
    ++pos;
    skipWhitespace(json, pos);

    if (key == targetKey) {
      // Parse the value as a string
      if (!parseJsonString(json, pos, value))
        return false;
      return true;
    }

    // Skip value
    if (!skipJsonValue(json, pos))
      return false;

    // Expect ',' or '}'
    skipWhitespace(json, pos);
    if (pos >= json.size())
      return false;
    if (json[pos] == ',') {
      ++pos;
      continue;
    }
    if (json[pos] == '}') {
      return false; // key not found
    }
    return false;
  }
  return false;
}

// ---------------------------------------------------------------------------
// Stderr capture helper
// ---------------------------------------------------------------------------

/// RAII helper to redirect stderr to a pipe so we can capture StructuredLogger
/// output. The logger writes to std::cerr which goes to file descriptor 2.
class StderrCapture {
public:
  StderrCapture() : capturing_(false), saved_fd_(-1) {}

  ~StderrCapture() {
    if (capturing_) {
      stop();
    }
  }

  /// Start capturing stderr output.
  void start() {
    if (capturing_)
      return;
    // Flush any pending stderr output.
    std::cerr.flush();
    fflush(stderr);

    // Save the original stderr file descriptor.
    saved_fd_ = dup(STDERR_FILENO);

    // Create a pipe.
    int pipefd[2];
    if (pipe(pipefd) != 0) {
      return;
    }
    read_fd_ = pipefd[0];

    // Redirect stderr to the write end of the pipe.
    dup2(pipefd[1], STDERR_FILENO);
    close(pipefd[1]);

    capturing_ = true;
  }

  /// Stop capturing and return the captured output.
  std::string stop() {
    if (!capturing_)
      return "";

    // Flush stderr to ensure all output is in the pipe.
    std::cerr.flush();
    fflush(stderr);

    // Restore original stderr.
    dup2(saved_fd_, STDERR_FILENO);
    close(saved_fd_);
    saved_fd_ = -1;

    // Read all captured data from the pipe.
    std::string captured;
    char buf[4096];
    // Set read_fd_ to non-blocking to avoid hanging.
    int flags = fcntl(read_fd_, F_GETFL, 0);
    fcntl(read_fd_, F_SETFL, flags | O_NONBLOCK);

    ssize_t n;
    while ((n = read(read_fd_, buf, sizeof(buf))) > 0) {
      captured.append(buf, static_cast<size_t>(n));
    }
    close(read_fd_);
    read_fd_ = -1;

    capturing_ = false;
    return captured;
  }

private:
  bool capturing_;
  int saved_fd_;
  int read_fd_;
};

// ---------------------------------------------------------------------------
// Random generators
// ---------------------------------------------------------------------------

/// All log levels for random selection.
static const LogLevel kAllLevels[] = {LogLevel::DEBUG, LogLevel::INFO,
                                      LogLevel::WARNING, LogLevel::ERROR};

/// Generate a random log message string containing a mix of ASCII characters,
/// including special characters that need JSON escaping.
static std::string generateRandomLogMessage(std::mt19937 &rng,
                                            size_t maxLen = 150) {
  // Character palette includes printable ASCII, some whitespace, and
  // characters that require JSON escaping (quotes, backslashes, newlines).
  static const std::string kPrintable =
      "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
      " !@#$%^&*()-_=+[]{}|;:',.<>?/~`";

  std::uniform_int_distribution<size_t> lenDist(0, maxLen);
  size_t len = lenDist(rng);

  std::string result;
  result.reserve(len);

  // 90% printable, 10% special chars needing escaping
  std::uniform_int_distribution<int> classDist(0, 9);
  std::uniform_int_distribution<size_t> printIdx(0, kPrintable.size() - 1);

  static const char kSpecial[] = "\"\\\n\r\t";
  std::uniform_int_distribution<size_t> specIdx(0, sizeof(kSpecial) - 2);

  for (size_t i = 0; i < len; ++i) {
    int cls = classDist(rng);
    if (cls < 9) {
      result.push_back(kPrintable[printIdx(rng)]);
    } else {
      result.push_back(kSpecial[specIdx(rng)]);
    }
  }

  return result;
}

/// Select a random log level.
static LogLevel randomLogLevel(std::mt19937 &rng) {
  std::uniform_int_distribution<int> dist(0, 3);
  return kAllLevels[dist(rng)];
}

// ===========================================================================
// Property-Based Tests
// ===========================================================================

TEST(StructuredLoggerPropertyTest, Property9_StructuredLogOutputFormat) {
  // Feature: serving-framework-enhancement, Property 9: C++ structured log output format
  // Validates: Requirements 5.2
  //
  // For any log message string and any log level, the C++ StructuredLogger
  // SHALL produce a valid JSON string containing at minimum the keys
  // "timestamp", "level", "message", and "component".

  std::mt19937 rng(42);

  // Ensure the logger is set to DEBUG so all levels produce output.
  StructuredLogger &logger = StructuredLogger::getInstance();
  logger.setLevel(LogLevel::DEBUG);

  for (int i = 0; i < 100; ++i) {
    std::string message = generateRandomLogMessage(rng);
    LogLevel level = randomLogLevel(rng);

    // Capture stderr output from the logger.
    StderrCapture capture;
    capture.start();
    logger.log(level, message);
    std::string output = capture.stop();

    // Trim trailing newline(s) from the captured output.
    while (!output.empty() &&
           (output.back() == '\n' || output.back() == '\r')) {
      output.pop_back();
    }

    // 1. Output must not be empty.
    ASSERT_FALSE(output.empty())
        << "Logger produced no output on iteration " << i
        << " for level=" << StructuredLogger::levelToString(level)
        << " message length=" << message.size();

    // 2. Output must be a valid JSON object with extractable keys.
    std::vector<std::string> keys;
    bool validJson = extractJsonKeys(output, keys);
    ASSERT_TRUE(validJson)
        << "Logger output is not valid JSON on iteration " << i
        << "\n  output: " << output;

    // 3. Must contain the four required keys.
    auto hasKey = [&keys](const std::string &key) {
      return std::find(keys.begin(), keys.end(), key) != keys.end();
    };

    EXPECT_TRUE(hasKey("timestamp"))
        << "Missing 'timestamp' key on iteration " << i
        << "\n  output: " << output;
    EXPECT_TRUE(hasKey("level"))
        << "Missing 'level' key on iteration " << i
        << "\n  output: " << output;
    EXPECT_TRUE(hasKey("message"))
        << "Missing 'message' key on iteration " << i
        << "\n  output: " << output;
    EXPECT_TRUE(hasKey("component"))
        << "Missing 'component' key on iteration " << i
        << "\n  output: " << output;

    // 4. Verify the "level" value matches the log level used.
    std::string levelValue;
    if (extractJsonStringValue(output, "level", levelValue)) {
      EXPECT_EQ(levelValue, StructuredLogger::levelToString(level))
          << "Level mismatch on iteration " << i;
    }

    // 5. Verify the "component" value is "compute_server".
    std::string componentValue;
    if (extractJsonStringValue(output, "component", componentValue)) {
      EXPECT_EQ(componentValue, "compute_server")
          << "Component mismatch on iteration " << i;
    }
  }
}

TEST(StructuredLoggerPropertyTest,
     Property9_StructuredLogOutputFormatWithExtraFields) {
  // Feature: serving-framework-enhancement, Property 9: C++ structured log output format
  // Validates: Requirements 5.2
  //
  // Supplementary: Even when extra context fields are provided, the output
  // SHALL still be valid JSON containing the four required keys.

  std::mt19937 rng(99);

  StructuredLogger &logger = StructuredLogger::getInstance();
  logger.setLevel(LogLevel::DEBUG);

  // Pool of extra field keys to randomly select from.
  static const std::vector<std::string> kExtraKeys = {
      "request_id", "model_id", "latency_ms", "token_count", "method", "path"};

  for (int i = 0; i < 100; ++i) {
    std::string message = generateRandomLogMessage(rng);
    LogLevel level = randomLogLevel(rng);

    // Generate random extra fields (0 to 4 fields).
    std::map<std::string, std::string> extra;
    std::uniform_int_distribution<int> numExtraDist(0, 4);
    int numExtra = numExtraDist(rng);
    for (int j = 0; j < numExtra; ++j) {
      std::uniform_int_distribution<size_t> keyIdx(0, kExtraKeys.size() - 1);
      std::string key = kExtraKeys[keyIdx(rng)];
      std::string value = generateRandomLogMessage(rng, 30);
      extra[key] = value;
    }

    // Capture stderr output.
    StderrCapture capture;
    capture.start();
    logger.log(level, message, extra);
    std::string output = capture.stop();

    // Trim trailing newline(s).
    while (!output.empty() &&
           (output.back() == '\n' || output.back() == '\r')) {
      output.pop_back();
    }

    // Output must not be empty.
    ASSERT_FALSE(output.empty())
        << "Logger produced no output on iteration " << i;

    // Output must be valid JSON.
    std::vector<std::string> keys;
    bool validJson = extractJsonKeys(output, keys);
    ASSERT_TRUE(validJson)
        << "Logger output is not valid JSON on iteration " << i
        << "\n  output: " << output;

    // Must contain the four required keys.
    auto hasKey = [&keys](const std::string &key) {
      return std::find(keys.begin(), keys.end(), key) != keys.end();
    };

    EXPECT_TRUE(hasKey("timestamp"))
        << "Missing 'timestamp' key on iteration " << i;
    EXPECT_TRUE(hasKey("level"))
        << "Missing 'level' key on iteration " << i;
    EXPECT_TRUE(hasKey("message"))
        << "Missing 'message' key on iteration " << i;
    EXPECT_TRUE(hasKey("component"))
        << "Missing 'component' key on iteration " << i;

    // Extra fields should also be present.
    for (const auto &kv : extra) {
      EXPECT_TRUE(hasKey(kv.first))
          << "Missing extra key '" << kv.first << "' on iteration " << i;
    }
  }
}

// ===========================================================================
// Unit Tests — Specific Edge Cases
// ===========================================================================

TEST(StructuredLoggerUnitTest, EmptyMessage) {
  // Validates: Requirements 5.2
  // An empty message should still produce valid JSON with all required keys.

  StructuredLogger &logger = StructuredLogger::getInstance();
  logger.setLevel(LogLevel::DEBUG);

  StderrCapture capture;
  capture.start();
  logger.info("");
  std::string output = capture.stop();

  while (!output.empty() && (output.back() == '\n' || output.back() == '\r')) {
    output.pop_back();
  }

  ASSERT_FALSE(output.empty());

  std::vector<std::string> keys;
  ASSERT_TRUE(extractJsonKeys(output, keys));

  auto hasKey = [&keys](const std::string &key) {
    return std::find(keys.begin(), keys.end(), key) != keys.end();
  };
  EXPECT_TRUE(hasKey("timestamp"));
  EXPECT_TRUE(hasKey("level"));
  EXPECT_TRUE(hasKey("message"));
  EXPECT_TRUE(hasKey("component"));

  std::string msgValue;
  ASSERT_TRUE(extractJsonStringValue(output, "message", msgValue));
  EXPECT_EQ(msgValue, "");
}

TEST(StructuredLoggerUnitTest, MessageWithJsonSpecialChars) {
  // Validates: Requirements 5.2
  // A message containing JSON special characters (quotes, backslashes,
  // newlines) should still produce valid JSON.

  StructuredLogger &logger = StructuredLogger::getInstance();
  logger.setLevel(LogLevel::DEBUG);

  std::string specialMsg = "He said \"hello\\world\"\nNew line\there\ttab";

  StderrCapture capture;
  capture.start();
  logger.info(specialMsg);
  std::string output = capture.stop();

  while (!output.empty() && (output.back() == '\n' || output.back() == '\r')) {
    output.pop_back();
  }

  ASSERT_FALSE(output.empty());

  std::vector<std::string> keys;
  ASSERT_TRUE(extractJsonKeys(output, keys))
      << "Output with special chars is not valid JSON:\n  " << output;

  auto hasKey = [&keys](const std::string &key) {
    return std::find(keys.begin(), keys.end(), key) != keys.end();
  };
  EXPECT_TRUE(hasKey("timestamp"));
  EXPECT_TRUE(hasKey("level"));
  EXPECT_TRUE(hasKey("message"));
  EXPECT_TRUE(hasKey("component"));
}

TEST(StructuredLoggerUnitTest, AllLogLevelsProduceOutput) {
  // Validates: Requirements 5.2
  // Each log level should produce valid JSON output when the threshold
  // is set to DEBUG.

  StructuredLogger &logger = StructuredLogger::getInstance();
  logger.setLevel(LogLevel::DEBUG);

  for (LogLevel level : kAllLevels) {
    StderrCapture capture;
    capture.start();
    logger.log(level, "test message for level");
    std::string output = capture.stop();

    while (!output.empty() &&
           (output.back() == '\n' || output.back() == '\r')) {
      output.pop_back();
    }

    ASSERT_FALSE(output.empty())
        << "No output for level " << StructuredLogger::levelToString(level);

    std::vector<std::string> keys;
    ASSERT_TRUE(extractJsonKeys(output, keys))
        << "Invalid JSON for level " << StructuredLogger::levelToString(level)
        << "\n  output: " << output;

    std::string levelValue;
    ASSERT_TRUE(extractJsonStringValue(output, "level", levelValue));
    EXPECT_EQ(levelValue, StructuredLogger::levelToString(level));
  }
}

TEST(StructuredLoggerUnitTest, MessageWithControlCharacters) {
  // Validates: Requirements 5.2
  // Control characters (ASCII < 0x20) should be properly escaped in JSON.

  StructuredLogger &logger = StructuredLogger::getInstance();
  logger.setLevel(LogLevel::DEBUG);

  // Build a message with various control characters.
  std::string ctrlMsg;
  ctrlMsg += "start";
  ctrlMsg += '\x01'; // SOH
  ctrlMsg += '\x02'; // STX
  ctrlMsg += '\x1f'; // US (unit separator)
  ctrlMsg += "end";

  StderrCapture capture;
  capture.start();
  logger.info(ctrlMsg);
  std::string output = capture.stop();

  while (!output.empty() && (output.back() == '\n' || output.back() == '\r')) {
    output.pop_back();
  }

  ASSERT_FALSE(output.empty());

  std::vector<std::string> keys;
  ASSERT_TRUE(extractJsonKeys(output, keys))
      << "Output with control chars is not valid JSON:\n  " << output;

  auto hasKey = [&keys](const std::string &key) {
    return std::find(keys.begin(), keys.end(), key) != keys.end();
  };
  EXPECT_TRUE(hasKey("timestamp"));
  EXPECT_TRUE(hasKey("level"));
  EXPECT_TRUE(hasKey("message"));
  EXPECT_TRUE(hasKey("component"));
}
