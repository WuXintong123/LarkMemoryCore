//====- test_compute_functions.cpp -----------------------------------------===//
//
// SPDX-License-Identifier: Apache-2.0
//
//===----------------------------------------------------------------------===//
//
// Regression tests for compute watchdog timeout resolution.
//
//===----------------------------------------------------------------------===//

#include "ComputeFunctions.h"
#include "ModelConfig.h"

#include <gtest/gtest.h>

#include <cstdlib>
#include <string>

using compute::InferenceExecutionResult;
using compute::InferenceOptions;
using compute::ModelConfigManager;
using compute::ModelToolConfig;
using compute::StreamExecutionResult;
using compute::processString;
using compute::processStringStream;

namespace {

class EnvGuard {
public:
  EnvGuard(const char *name, const char *value) : name_(name) {
    const char *current = std::getenv(name_);
    if (current) {
      had_original_ = true;
      original_ = current;
    }
    if (value) {
      setenv(name_, value, 1);
    } else {
      unsetenv(name_);
    }
  }

  ~EnvGuard() {
    if (had_original_) {
      setenv(name_, original_.c_str(), 1);
    } else {
      unsetenv(name_);
    }
  }

private:
  const char *name_;
  bool had_original_ = false;
  std::string original_;
};

void clearAllModels(ModelConfigManager &mgr) {
  for (const auto &model_id : mgr.getModelIds()) {
    mgr.unregisterModel(model_id);
  }
}

ModelToolConfig makePythonConfig(const std::string &script) {
  ModelToolConfig cfg;
  cfg.cli_path = "/usr/bin/env";
  cfg.numactl_nodes.clear();
  cfg.taskset_cpus.clear();
  cfg.extra_args = "python3 -c \"" + script + "\"";
  return cfg;
}

} // namespace

TEST(ComputeFunctionsRegressionTest,
     ExplicitRequestTimeoutOverridesDefaultNonStreamWatchdog) {
  EnvGuard max_execution_guard("NON_STREAM_MAX_EXECUTION_S", "1");
  ModelConfigManager &mgr = ModelConfigManager::getInstance();
  clearAllModels(mgr);

  const std::string model_id = "sleepy-non-stream";
  mgr.registerModel(
      model_id,
      makePythonConfig("import time,sys; time.sleep(1.2); sys.stdout.write('READY')"));

  InferenceOptions options;
  options.request_timeout_ms = 2500;

  InferenceExecutionResult result = processString("", model_id, options);

  EXPECT_EQ(result.output, "READY");
  EXPECT_EQ(result.completion_status, "completed");

  clearAllModels(mgr);
}

TEST(ComputeFunctionsRegressionTest,
     ProcessStringAppendsTrailingNewlineBeforeWritingToCli) {
  ModelConfigManager &mgr = ModelConfigManager::getInstance();
  clearAllModels(mgr);

  const std::string model_id = "stdin-newline-check";
  mgr.registerModel(
      model_id,
      makePythonConfig(
          "import sys; data = sys.stdin.read(); "
          "sys.stdout.write('1' if data and ord(data[-1]) == 10 else '0')"));

  InferenceExecutionResult result = processString("hello", model_id);

  EXPECT_EQ(result.output, "1");
  EXPECT_EQ(result.completion_status, "completed");

  clearAllModels(mgr);
}

TEST(ComputeFunctionsRegressionTest,
     ExplicitStreamIdleTimeoutOverridesDefaultStreamWatchdog) {
  EnvGuard stream_idle_guard("STREAM_IDLE_TIMEOUT_S", nullptr);
  ModelConfigManager &mgr = ModelConfigManager::getInstance();
  clearAllModels(mgr);

  const std::string model_id = "sleepy-stream";
  mgr.registerModel(
      model_id,
      makePythonConfig(
          "import time,sys; time.sleep(1.2); sys.stdout.write('READY'); "
          "sys.stdout.flush()"));

  InferenceOptions options;
  options.stream_idle_timeout_s = 3;

  std::string collected;
  StreamExecutionResult result = processStringStream(
      "", model_id,
      [&collected](const std::string &content, bool is_final) -> bool {
        if (!is_final) {
          collected += content;
        }
        return true;
      },
      options, 1);

  EXPECT_TRUE(result.success);
  EXPECT_EQ(result.completion_status, "completed");
  EXPECT_EQ(collected, "READY");

  clearAllModels(mgr);
}
