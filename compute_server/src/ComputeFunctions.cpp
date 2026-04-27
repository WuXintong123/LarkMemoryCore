//====- ComputeFunctions.cpp ----------------------------------------------===//
//
// SPDX-License-Identifier: Apache-2.0
//
//===----------------------------------------------------------------------===//
//
// This file implements the compute functions.
//
//===----------------------------------------------------------------------===//

#include "ComputeFunctions.h"
#include "ModelConfig.h"
#include "StructuredLogger.h"
#include "TokenCounter.h"
#include <array>
#include <cctype>
#include <cerrno>
#include <chrono>
#include <cstdlib>
#include <cstring>
#include <fcntl.h>
#include <functional>
#include <signal.h>
#include <sstream>
#include <stdexcept>
#include <string>
#include <sys/select.h>
#include <sys/wait.h>
#include <unistd.h>
#include <vector>

namespace compute {

namespace {

constexpr int kDefaultNonStreamIdleTimeoutSeconds = 8;
constexpr int kDefaultNonStreamMaxExecutionSeconds = 110;

struct NonStreamWatchdogConfig {
  int idle_timeout_seconds;
  int max_execution_seconds;
};

std::vector<std::string> splitExtraArgs(const std::string &extra_args) {
  std::vector<std::string> tokens;
  std::string current;
  bool in_single_quote = false;
  bool in_double_quote = false;
  bool escaping = false;

  for (char c : extra_args) {
    if (escaping) {
      current.push_back(c);
      escaping = false;
      continue;
    }

    if (c == '\\') {
      escaping = true;
      continue;
    }

    if (in_single_quote) {
      if (c == '\'') {
        in_single_quote = false;
      } else {
        current.push_back(c);
      }
      continue;
    }

    if (in_double_quote) {
      if (c == '"') {
        in_double_quote = false;
      } else {
        current.push_back(c);
      }
      continue;
    }

    if (c == '\'') {
      in_single_quote = true;
      continue;
    }

    if (c == '"') {
      in_double_quote = true;
      continue;
    }

    if (std::isspace(static_cast<unsigned char>(c))) {
      if (!current.empty()) {
        tokens.push_back(current);
        current.clear();
      }
      continue;
    }

    current.push_back(c);
  }

  if (escaping || in_single_quote || in_double_quote) {
    throw std::runtime_error(
        "Invalid extra_args: unmatched quote or trailing escape");
  }

  if (!current.empty()) {
    tokens.push_back(current);
  }
  return tokens;
}

int getPositiveEnvInt(const char *name, int default_value) {
  const char *env_value = std::getenv(name);
  if (!env_value || std::strlen(env_value) == 0) {
    return default_value;
  }
  try {
    int parsed = std::stoi(env_value);
    if (parsed <= 0) {
      throw std::runtime_error("must be > 0");
    }
    return parsed;
  } catch (...) {
    StructuredLogger::getInstance().warning(
        "Invalid " + std::string(name) + " value: " + std::string(env_value) +
            ", using default: " + std::to_string(default_value));
    return default_value;
  }
}

NonStreamWatchdogConfig getNonStreamWatchdogConfig() {
  return NonStreamWatchdogConfig{
      getPositiveEnvInt("NON_STREAM_IDLE_TIMEOUT_S",
                        kDefaultNonStreamIdleTimeoutSeconds),
      getPositiveEnvInt("NON_STREAM_MAX_EXECUTION_S",
                        kDefaultNonStreamMaxExecutionSeconds),
  };
}

int resolveMaxExecutionMs(const InferenceOptions &options,
                          const NonStreamWatchdogConfig &watchdog) {
  if (options.request_timeout_ms > 0) {
    return options.request_timeout_ms;
  }
  return watchdog.max_execution_seconds * 1000;
}

int resolveStreamIdleTimeoutSeconds(const InferenceOptions &options,
                                    int default_idle_timeout_seconds) {
  if (options.stream_idle_timeout_s > 0) {
    return options.stream_idle_timeout_s;
  }
  return default_idle_timeout_seconds;
}

std::vector<std::string> buildCommandArgs(const ModelToolConfig &config,
                                          const InferenceOptions &options) {
  std::vector<std::string> args;

  if (!config.numactl_nodes.empty()) {
    args.push_back("numactl");
    args.push_back("--cpunodebind=" + config.numactl_nodes);
    args.push_back("--interleave=" + config.numactl_nodes);
  }

  if (!config.taskset_cpus.empty()) {
    args.push_back("taskset");
    args.push_back("-c");
    args.push_back(config.taskset_cpus);
  }

  args.push_back(config.cli_path);
  if (!config.extra_args.empty()) {
    auto extra = splitExtraArgs(config.extra_args);
    args.insert(args.end(), extra.begin(), extra.end());
  }
  if (options.max_tokens > 0) {
    args.push_back("--max-tokens");
    args.push_back(std::to_string(options.max_tokens));
  }
  return args;
}

std::vector<char *> buildExecArgv(std::vector<std::string> &args) {
  std::vector<char *> argv;
  argv.reserve(args.size() + 1);
  for (auto &arg : args) {
    argv.push_back(const_cast<char *>(arg.c_str()));
  }
  argv.push_back(nullptr);
  return argv;
}

void writeAll(int fd, const std::string &data) {
  size_t total_written = 0;
  while (total_written < data.size()) {
    ssize_t written = write(fd, data.data() + total_written,
                            data.size() - total_written);
    if (written < 0) {
      if (errno == EINTR) {
        continue;
      }
      throw std::runtime_error("write() failed: " + std::string(strerror(errno)));
    }
    total_written += static_cast<size_t>(written);
  }
}

std::string normalizePromptForCliStdin(const std::string &input) {
  if (input.empty() || input.back() == '\n') {
    return input;
  }
  return input + "\n";
}

int waitChildExitCode(pid_t child_pid) {
  int status = 0;
  while (waitpid(child_pid, &status, 0) < 0) {
    if (errno == EINTR) {
      continue;
    }
    throw std::runtime_error("waitpid() failed: " + std::string(strerror(errno)));
  }

  if (WIFEXITED(status)) {
    return WEXITSTATUS(status);
  }
  if (WIFSIGNALED(status)) {
    return 128 + WTERMSIG(status);
  }
  return 1;
}

std::string formatCommandForLog(const std::vector<std::string> &args) {
  std::ostringstream oss;
  for (size_t i = 0; i < args.size(); ++i) {
    if (i > 0) {
      oss << ' ';
    }
    oss << '"' << args[i] << '"';
  }
  return oss.str();
}

std::string trimTrailingNewlines(std::string value) {
  while (!value.empty() &&
         (value.back() == '\n' || value.back() == '\r')) {
    value.pop_back();
  }
  return value;
}

void setChildProcessGroup(pid_t child_pid) {
  if (child_pid <= 0) {
    return;
  }
  if (setpgid(child_pid, child_pid) == 0) {
    return;
  }
  if (errno == EACCES || errno == ESRCH) {
    return;
  }
  StructuredLogger::getInstance().warning(
      "setpgid failed for child pid " + std::to_string(child_pid) + ": " +
      std::string(strerror(errno)));
}

void terminateChildProcessTree(pid_t child_pid, int signal_num) {
  if (child_pid <= 0) {
    return;
  }
  if (kill(-child_pid, signal_num) == 0) {
    return;
  }
  if (errno != ESRCH) {
    StructuredLogger::getInstance().warning(
        "killpg failed for child pid " + std::to_string(child_pid) + ": " +
        std::string(strerror(errno)) + ", fallback to kill(pid)");
  }
  kill(child_pid, signal_num);
}

} // namespace

InferenceExecutionResult processString(const std::string &input,
                                       const std::string &model_id,
                                       const InferenceOptions &options,
                                       std::function<bool()> should_cancel) {
  ModelConfigManager &config_mgr = ModelConfigManager::getInstance();
  const ModelToolConfig config = config_mgr.getConfig(model_id);
  if (config.cli_path.empty()) {
    throw std::runtime_error("model cli_path is empty for model_id: " + model_id);
  }

  std::vector<std::string> command_args = buildCommandArgs(config, options);
  StructuredLogger::getInstance().debug("Executing command: " +
                                        formatCommandForLog(command_args));

  int stdin_pipe[2];
  int output_pipe[2];
  if (pipe(stdin_pipe) == -1) {
    throw std::runtime_error("pipe(stdin) failed: " +
                             std::string(strerror(errno)));
  }
  if (pipe(output_pipe) == -1) {
    close(stdin_pipe[0]);
    close(stdin_pipe[1]);
    throw std::runtime_error("pipe(stdout) failed: " +
                             std::string(strerror(errno)));
  }

  pid_t child_pid = fork();
  if (child_pid == -1) {
    close(stdin_pipe[0]);
    close(stdin_pipe[1]);
    close(output_pipe[0]);
    close(output_pipe[1]);
    throw std::runtime_error("fork() failed: " + std::string(strerror(errno)));
  }

  if (child_pid == 0) {
    setpgid(0, 0);
    close(stdin_pipe[1]);
    close(output_pipe[0]);

    dup2(stdin_pipe[0], STDIN_FILENO);
    dup2(output_pipe[1], STDOUT_FILENO);
    dup2(output_pipe[1], STDERR_FILENO);

    close(stdin_pipe[0]);
    close(output_pipe[1]);

    std::vector<char *> argv = buildExecArgv(command_args);
    execvp(argv[0], argv.data());
    _exit(127);
  }

  setChildProcessGroup(child_pid);
  close(stdin_pipe[0]);
  close(output_pipe[1]);

  try {
    writeAll(stdin_pipe[1], normalizePromptForCliStdin(input));
  } catch (...) {
    close(stdin_pipe[1]);
    close(output_pipe[0]);
    terminateChildProcessTree(child_pid, SIGKILL);
    waitpid(child_pid, nullptr, 0);
    throw;
  }
  close(stdin_pipe[1]);

  int fd = output_pipe[0];
  int flags = fcntl(fd, F_GETFL, 0);
  if (flags >= 0) {
    fcntl(fd, F_SETFL, flags | O_NONBLOCK);
  }

  const NonStreamWatchdogConfig watchdog = getNonStreamWatchdogConfig();
  std::array<char, 4096> buffer;
  std::string result;
  bool cancelled = false;
  bool idle_timeout_exceeded = false;
  bool max_execution_exceeded = false;
  auto start_time = std::chrono::steady_clock::now();
  auto last_data_time = start_time;

  while (true) {
    if (should_cancel && should_cancel()) {
      cancelled = true;
      terminateChildProcessTree(child_pid, SIGKILL);
      break;
    }

    fd_set read_fds;
    FD_ZERO(&read_fds);
    FD_SET(fd, &read_fds);

    struct timeval timeout;
    timeout.tv_sec = 0;
    timeout.tv_usec = 100000; // 100ms

    int ret = select(fd + 1, &read_fds, nullptr, nullptr, &timeout);
    if (ret < 0) {
      if (errno == EINTR) {
        continue;
      }
      close(fd);
      terminateChildProcessTree(child_pid, SIGKILL);
      waitpid(child_pid, nullptr, 0);
      throw std::runtime_error("select() failed: " + std::string(strerror(errno)));
    }

    auto now = std::chrono::steady_clock::now();
    auto total_elapsed_ms =
        std::chrono::duration_cast<std::chrono::milliseconds>(now - start_time)
            .count();
    if (total_elapsed_ms >= resolveMaxExecutionMs(options, watchdog)) {
      max_execution_exceeded = true;
      StructuredLogger::getInstance().warning(
          "Non-stream execution timeout, terminating child process",
          {{"model_id", model_id}});
      terminateChildProcessTree(child_pid, SIGKILL);
      break;
    }

    if (ret == 0) {
      if (!result.empty()) {
        auto idle_elapsed_seconds =
            std::chrono::duration_cast<std::chrono::seconds>(now - last_data_time)
                .count();
        if (idle_elapsed_seconds >= watchdog.idle_timeout_seconds) {
          idle_timeout_exceeded = true;
          StructuredLogger::getInstance().warning(
              "Non-stream idle timeout (" +
                  std::to_string(watchdog.idle_timeout_seconds) +
                  "s after last output), terminating child process",
              {{"model_id", model_id}});
          terminateChildProcessTree(child_pid, SIGKILL);
          break;
        }
      }
      continue;
    }

    ssize_t bytes_read = read(fd, buffer.data(), buffer.size());
    if (bytes_read > 0) {
      result.append(buffer.data(), static_cast<size_t>(bytes_read));
      last_data_time = std::chrono::steady_clock::now();
      continue;
    }
    if (bytes_read == 0) {
      break;
    }
    if (errno == EINTR || errno == EAGAIN || errno == EWOULDBLOCK) {
      continue;
    }
    close(fd);
    terminateChildProcessTree(child_pid, SIGKILL);
    waitpid(child_pid, nullptr, 0);
    throw std::runtime_error("read() failed: " + std::string(strerror(errno)));
  }
  close(fd);

  int exit_code = waitChildExitCode(child_pid);
  if (cancelled) {
    throw std::runtime_error("Request cancelled");
  }

  if ((idle_timeout_exceeded || max_execution_exceeded) && !result.empty()) {
    StructuredLogger::getInstance().warning(
        "Returning partial output after watchdog termination",
        {{"model_id", model_id},
         {"watchdog_idle_timeout",
          idle_timeout_exceeded ? "true" : "false"},
         {"watchdog_execution_timeout",
          max_execution_exceeded ? "true" : "false"}});
    return InferenceExecutionResult{
        trimTrailingNewlines(result),
        "partial_timeout",
        max_execution_exceeded ? "watchdog_timeout" : "idle_timeout",
    };
  }

  if (idle_timeout_exceeded) {
    throw std::runtime_error(
        "Command produced no output progress before idle timeout: " +
        std::to_string(watchdog.idle_timeout_seconds) + "s");
  }
  if (max_execution_exceeded) {
    throw std::runtime_error(
        "Command exceeded max execution time: " +
        std::to_string(watchdog.max_execution_seconds) + "s");
  }

  if (exit_code != 0) {
    throw std::runtime_error("Command execution failed with exit code: " +
                             std::to_string(exit_code));
  }
  std::string trimmed = trimTrailingNewlines(result);
  int32_t completion_tokens = TokenCounter::count(trimmed);
  std::string completion_status = "completed";
  if (options.max_tokens > 0 && completion_tokens >= options.max_tokens) {
    completion_status = "max_tokens";
  }
  return InferenceExecutionResult{trimmed, completion_status, ""};
}

StreamExecutionResult processStringStream(
    const std::string &input, const std::string &model_id,
    std::function<bool(const std::string &content, bool is_final)> callback,
    const InferenceOptions &options,
    int idle_timeout_seconds) {

  // Read idle timeout from environment variable if available
  const char *env_timeout = std::getenv("STREAM_IDLE_TIMEOUT_S");
  if (env_timeout && std::strlen(env_timeout) > 0) {
    try {
      int env_val = std::stoi(env_timeout);
      if (env_val > 0) {
        idle_timeout_seconds = env_val;
      }
    } catch (const std::exception &) {
      StructuredLogger::getInstance().warning(
          "Invalid STREAM_IDLE_TIMEOUT_S value: " + std::string(env_timeout) +
          ", using default: " + std::to_string(idle_timeout_seconds));
    }
  }

  ModelConfigManager &config_mgr = ModelConfigManager::getInstance();
  const ModelToolConfig config = config_mgr.getConfig(model_id);
  if (config.cli_path.empty()) {
    callback("Error: model cli_path is empty for model_id: " + model_id, true);
    return StreamExecutionResult{false, "backend_error",
                                 "model_cli_path_missing"};
  }

  std::vector<std::string> command_args = buildCommandArgs(config, options);
  StructuredLogger::getInstance().debug("Executing streaming command: " +
                                        formatCommandForLog(command_args));

  // stdin pipe: parent writes prompt, child reads as STDIN
  int stdin_pipe[2];
  if (pipe(stdin_pipe) == -1) {
    StructuredLogger::getInstance().error(
        std::string("pipe(stdin) failed: ") + strerror(errno));
    callback("Error: pipe(stdin) failed: " + std::string(strerror(errno)), true);
    return StreamExecutionResult{false, "backend_error", "pipe_stdin_failed"};
  }

  // output pipe: child stdout/stderr -> parent reads chunks
  int output_pipe[2];
  if (pipe(output_pipe) == -1) {
    close(stdin_pipe[0]);
    close(stdin_pipe[1]);
    StructuredLogger::getInstance().error(
        std::string("pipe(output) failed: ") + strerror(errno));
    callback("Error: pipe(output) failed: " + std::string(strerror(errno)), true);
    return StreamExecutionResult{false, "backend_error", "pipe_output_failed"};
  }

  pid_t child_pid = fork();
  if (child_pid == -1) {
    close(stdin_pipe[0]);
    close(stdin_pipe[1]);
    close(output_pipe[0]);
    close(output_pipe[1]);
    StructuredLogger::getInstance().error(
        std::string("fork() failed: ") + strerror(errno));
    callback("Error: fork() failed: " + std::string(strerror(errno)), true);
    return StreamExecutionResult{false, "backend_error", "fork_failed"};
  }

  if (child_pid == 0) {
    setpgid(0, 0);
    close(stdin_pipe[1]);
    close(output_pipe[0]);

    dup2(stdin_pipe[0], STDIN_FILENO);
    dup2(output_pipe[1], STDOUT_FILENO);
    dup2(output_pipe[1], STDERR_FILENO);

    close(stdin_pipe[0]);
    close(output_pipe[1]);

    std::vector<char *> argv = buildExecArgv(command_args);
    execvp(argv[0], argv.data());
    _exit(127);
  }

  setChildProcessGroup(child_pid);
  close(stdin_pipe[0]);
  close(output_pipe[1]);

  try {
    writeAll(stdin_pipe[1], normalizePromptForCliStdin(input));
  } catch (const std::exception &e) {
    close(stdin_pipe[1]);
    close(output_pipe[0]);
    terminateChildProcessTree(child_pid, SIGKILL);
    waitpid(child_pid, nullptr, 0);
    callback(std::string("Error: failed to write input: ") + e.what(), true);
    return StreamExecutionResult{false, "backend_error", "write_input_failed"};
  }
  close(stdin_pipe[1]);

  idle_timeout_seconds =
      resolveStreamIdleTimeoutSeconds(options, idle_timeout_seconds);

  int fd = output_pipe[0];
  int flags = fcntl(fd, F_GETFL, 0);
  if (flags >= 0) {
    fcntl(fd, F_SETFL, flags | O_NONBLOCK);
  }

  std::array<char, 256> buffer;
  bool client_cancelled = false;
  bool idle_timeout_exceeded = false;
  bool max_execution_exceeded = false;
  auto last_data_time = std::chrono::steady_clock::now();
  auto start_time = last_data_time;
  const int max_execution_ms = resolveMaxExecutionMs(
      options,
      NonStreamWatchdogConfig{idle_timeout_seconds,
                              std::max(1, options.request_timeout_ms / 1000)});

  while (true) {
    fd_set read_fds;
    FD_ZERO(&read_fds);
    FD_SET(fd, &read_fds);

    struct timeval timeout;
    timeout.tv_sec = 0;
    timeout.tv_usec = 100000; // 100ms

    int ret = select(fd + 1, &read_fds, nullptr, nullptr, &timeout);

    if (ret < 0) {
      if (errno == EINTR)
        continue;
      StructuredLogger::getInstance().error(
          std::string("select() failed: ") + strerror(errno));
      break;
    }

    auto now = std::chrono::steady_clock::now();
    auto total_elapsed_ms =
        std::chrono::duration_cast<std::chrono::milliseconds>(now - start_time)
            .count();
    if (options.request_timeout_ms > 0 && total_elapsed_ms >= max_execution_ms) {
      max_execution_exceeded = true;
      StructuredLogger::getInstance().error(
          "Streaming request timeout exceeded, killing child process",
          {{"model_id", model_id}});
      terminateChildProcessTree(child_pid, SIGKILL);
      break;
    }

    if (ret == 0) {
      // select() timed out (100ms), check idle timeout
      auto elapsed_seconds =
          std::chrono::duration_cast<std::chrono::seconds>(now - last_data_time)
              .count();
      if (elapsed_seconds >= idle_timeout_seconds) {
        // Idle timeout exceeded - kill the child process
        idle_timeout_exceeded = true;
        StructuredLogger::getInstance().error(
            "Idle timeout: no output for " +
                std::to_string(idle_timeout_seconds) + " seconds, killing child process",
            {{"model_id", model_id}});
        terminateChildProcessTree(child_pid, SIGKILL);
        break;
      }
      continue;
    }

    ssize_t bytes_read = read(fd, buffer.data(), buffer.size() - 1);

    if (bytes_read > 0) {
      buffer[bytes_read] = '\0';
      std::string chunk(buffer.data(), bytes_read);

      // Update last data received time
      last_data_time = std::chrono::steady_clock::now();

      // Send chunk to client
      if (!callback(chunk, false)) {
        client_cancelled = true;
        StructuredLogger::getInstance().info("Client cancelled stream");
        // Kill the child process since client no longer needs output
        terminateChildProcessTree(child_pid, SIGKILL);
        break;
      }
    } else if (bytes_read == 0) {
      // EOF
      break;
    } else {
      if (errno == EAGAIN || errno == EWOULDBLOCK) {
        // No data available yet, check idle timeout
        auto now = std::chrono::steady_clock::now();
        auto elapsed_seconds =
            std::chrono::duration_cast<std::chrono::seconds>(
                now - last_data_time)
                .count();
        if (elapsed_seconds >= idle_timeout_seconds) {
          // Idle timeout exceeded - kill the child process
          idle_timeout_exceeded = true;
          StructuredLogger::getInstance().error(
              "Idle timeout: no output for " +
                  std::to_string(idle_timeout_seconds) +
                  " seconds, killing child process",
              {{"model_id", model_id}});
          terminateChildProcessTree(child_pid, SIGKILL);
          break;
        }
        continue;
      }
      StructuredLogger::getInstance().error(
          std::string("read() failed: ") + strerror(errno));
      break;
    }
  }

  // Close the read end of the output pipe
  close(fd);

  int exit_code = 0;
  try {
    exit_code = waitChildExitCode(child_pid);
  } catch (const std::exception &e) {
    callback(std::string("Error: ") + e.what(), true);
    return StreamExecutionResult{false, "backend_error", "waitpid_failed"};
  }

  // Handle idle timeout: send error via callback
  if (idle_timeout_exceeded) {
    std::string error_msg = "Error: Idle timeout exceeded (" +
                            std::to_string(idle_timeout_seconds) +
                            " seconds). Child process terminated.";
    callback(error_msg, true);
    return StreamExecutionResult{false, "partial_timeout", "idle_timeout"};
  }

  if (max_execution_exceeded) {
    callback("Error: Request timeout exceeded.", true);
    return StreamExecutionResult{false, "partial_timeout", "watchdog_timeout"};
  }

  // Handle pclose (child exit) non-zero: send error via callback
  if (exit_code != 0 && !client_cancelled) {
    std::string error_msg = "Error: Command execution failed with exit code: " +
                            std::to_string(exit_code);
    StructuredLogger::getInstance().warning(
        "Command exited with code: " + std::to_string(exit_code),
        {{"model_id", model_id}});
    callback(error_msg, true);
    return StreamExecutionResult{false, "backend_error", "command_failed"};
  }

  if (!client_cancelled) {
    // Send final chunk on success
    callback("", true);
  }

  if (client_cancelled) {
    return StreamExecutionResult{false, "cancelled", "client_cancelled"};
  }
  return StreamExecutionResult{true, "completed", ""};
}

} // namespace compute
