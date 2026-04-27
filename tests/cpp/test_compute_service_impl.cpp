//====- test_compute_service_impl.cpp --------------------------------------===//
//
// SPDX-License-Identifier: Apache-2.0
//
//===----------------------------------------------------------------------===//
//
// Tests for ComputeServiceImpl prompt trace logging.
//
//===----------------------------------------------------------------------===//

#include "ComputeServiceImpl.h"
#include "ModelConfig.h"
#include "StructuredLogger.h"

#include <gtest/gtest.h>

#include <atomic>
#include <cstdlib>
#include <fcntl.h>
#include <grpcpp/grpcpp.h>
#include <iostream>
#include <string>
#include <unistd.h>

using compute::ComputeServiceImpl;
using compute::LogLevel;
using compute::ModelConfigManager;
using compute::ModelToolConfig;
using compute::ProcessRequest;
using compute::ProcessResponse;
using compute::StructuredLogger;

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

class StderrCapture {
public:
  StderrCapture() = default;

  ~StderrCapture() {
    if (capturing_) {
      stop();
    }
  }

  void start() {
    if (capturing_) {
      return;
    }

    std::cerr.flush();
    fflush(stderr);

    saved_fd_ = dup(STDERR_FILENO);
    ASSERT_NE(saved_fd_, -1);

    int pipefd[2];
    ASSERT_EQ(pipe(pipefd), 0);
    read_fd_ = pipefd[0];

    ASSERT_NE(dup2(pipefd[1], STDERR_FILENO), -1);
    close(pipefd[1]);
    capturing_ = true;
  }

  std::string stop() {
    if (!capturing_) {
      return "";
    }

    std::cerr.flush();
    fflush(stderr);

    dup2(saved_fd_, STDERR_FILENO);
    close(saved_fd_);
    saved_fd_ = -1;

    std::string captured;
    char buffer[4096];
    int flags = fcntl(read_fd_, F_GETFL, 0);
    fcntl(read_fd_, F_SETFL, flags | O_NONBLOCK);

    ssize_t bytes = 0;
    while ((bytes = read(read_fd_, buffer, sizeof(buffer))) > 0) {
      captured.append(buffer, static_cast<size_t>(bytes));
    }
    close(read_fd_);
    read_fd_ = -1;
    capturing_ = false;
    return captured;
  }

private:
  bool capturing_ = false;
  int saved_fd_ = -1;
  int read_fd_ = -1;
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

TEST(ComputeServiceImplPromptTraceTest, ProcessLogsPromptAndResultWhenEnabled) {
  EnvGuard prompt_trace_guard("RUYI_DEBUG_PROMPT_IO", "1");
  StructuredLogger::getInstance().setLevel(LogLevel::INFO);
  ModelConfigManager &mgr = ModelConfigManager::getInstance();
  clearAllModels(mgr);

  const std::string model_id = "trace-success";
  mgr.registerModel(
      model_id,
      makePythonConfig(
          "import sys; data=sys.stdin.read(); sys.stdout.write(data.upper())"));

  std::atomic<bool> shutting_down{false};
  ComputeServiceImpl service(shutting_down, 1, 30000, 0);

  ProcessRequest request;
  request.set_input("trace sentinel");
  request.set_model_id(model_id);
  request.set_request_id("req-trace-success");

  ProcessResponse response;
  grpc::ServerContext context;
  StderrCapture capture;
  capture.start();
  grpc::Status status = service.Process(&context, &request, &response);
  std::string logs = capture.stop();

  EXPECT_TRUE(status.ok());
  EXPECT_TRUE(response.success());
  EXPECT_EQ(response.output(), "TRACE SENTINEL");
  EXPECT_NE(logs.find("\"message\":\"Compute server received prompt\""),
            std::string::npos);
  EXPECT_NE(logs.find("\"message\":\"Compute server returning result\""),
            std::string::npos);
  EXPECT_NE(logs.find("\"request_id\":\"req-trace-success\""),
            std::string::npos);
  EXPECT_NE(logs.find("\"prompt\":\"trace sentinel\""), std::string::npos);
  EXPECT_NE(logs.find("\"result\":\"TRACE SENTINEL\""), std::string::npos);
  EXPECT_NE(logs.find("\"request_kind\":\"non_stream\""), std::string::npos);

  clearAllModels(mgr);
}

TEST(ComputeServiceImplPromptTraceTest, ProcessLogsPromptAndErrorWhenEnabled) {
  EnvGuard prompt_trace_guard("RUYI_DEBUG_PROMPT_IO", "1");
  StructuredLogger::getInstance().setLevel(LogLevel::INFO);
  ModelConfigManager &mgr = ModelConfigManager::getInstance();
  clearAllModels(mgr);

  const std::string model_id = "trace-failure";
  mgr.registerModel(
      model_id,
      makePythonConfig("import sys; sys.exit(3)"));

  std::atomic<bool> shutting_down{false};
  ComputeServiceImpl service(shutting_down, 1, 30000, 0);

  ProcessRequest request;
  request.set_input("trace failure sentinel");
  request.set_model_id(model_id);
  request.set_request_id("req-trace-failure");

  ProcessResponse response;
  grpc::ServerContext context;
  StderrCapture capture;
  capture.start();
  grpc::Status status = service.Process(&context, &request, &response);
  std::string logs = capture.stop();

  EXPECT_EQ(status.error_code(), grpc::StatusCode::INTERNAL);
  EXPECT_FALSE(response.success());
  EXPECT_EQ(response.completion_status(), "backend_error");
  EXPECT_NE(logs.find("\"message\":\"Compute server received prompt\""),
            std::string::npos);
  EXPECT_NE(logs.find("\"message\":\"Compute server returning error\""),
            std::string::npos);
  EXPECT_NE(logs.find("\"prompt\":\"trace failure sentinel\""),
            std::string::npos);
  EXPECT_NE(logs.find("Command execution failed with exit code: 3"),
            std::string::npos);
  EXPECT_NE(logs.find("\"request_kind\":\"non_stream\""), std::string::npos);

  clearAllModels(mgr);
}
