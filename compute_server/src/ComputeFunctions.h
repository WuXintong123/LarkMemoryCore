//====- ComputeFunctions.h ------------------------------------------------===//
//
// SPDX-License-Identifier: Apache-2.0
//
//===----------------------------------------------------------------------===//
//
// This file implements the compute functions.
//
//===----------------------------------------------------------------------===//

#pragma once

#include <functional>
#include <string>

namespace compute {

struct InferenceOptions {
  // <= 0 means unset.
  int max_tokens = -1;
  int request_timeout_ms = 0;
  int stream_idle_timeout_s = 0;
};

struct InferenceExecutionResult {
  std::string output;
  std::string completion_status = "completed";
  std::string completion_detail;
};

struct StreamExecutionResult {
  bool success = true;
  std::string completion_status = "completed";
  std::string completion_detail;
};

// Process string input with optional model ID for tool selection.
// Uses fork/execvp with argv (no "sh -c") for safer command execution.
InferenceExecutionResult processString(const std::string &input,
                                       const std::string &model_id = "",
                                       const InferenceOptions &options = {},
                                       std::function<bool()> should_cancel = {});

// Process string input with streaming output via callback
// callback(content, is_final) - called for each chunk of output
// idle_timeout_seconds: max seconds to wait for output before killing child
//   process (default 120, overridden by env STREAM_IDLE_TIMEOUT_S)
// Returns true on success, false on error
StreamExecutionResult processStringStream(
    const std::string &input, const std::string &model_id,
    std::function<bool(const std::string &content, bool is_final)> callback,
    const InferenceOptions &options = {},
    int idle_timeout_seconds = 120);

} // namespace compute
