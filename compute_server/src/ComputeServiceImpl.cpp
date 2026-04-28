//====- ComputeServiceImpl.cpp --------------------------------------------===//
//
// SPDX-License-Identifier: Apache-2.0
//
//===----------------------------------------------------------------------===//
//
// This file implements the compute service implementation.
//
//===----------------------------------------------------------------------===//

#include "ComputeServiceImpl.h"
#include "ComputeFunctions.h"
#include "ModelConfig.h"
#include "StructuredLogger.h"
#include "TokenCounter.h"
#include <cctype>
#include <cstdlib>
#include <iomanip>
#include <iostream>
#include <random>
#include <sstream>

namespace compute {

namespace {

bool promptTraceEnabled() {
  const char *value = std::getenv("LARK_MEMORY_CORE_DEBUG_PROMPT_IO");
  if (!value) {
    return false;
  }
  std::string normalized(value);
  for (char &c : normalized) {
    c = static_cast<char>(std::tolower(static_cast<unsigned char>(c)));
  }
  return normalized == "1" || normalized == "true" || normalized == "yes" ||
         normalized == "on";
}

void logPromptTrace(const std::string &message, const std::string &request_id,
                    const std::string &model_id,
                    const std::string &request_kind,
                    const std::string &field_name,
                    const std::string &payload,
                    const std::string &completion_status = "",
                    const std::string &completion_detail = "") {
  if (!promptTraceEnabled()) {
    return;
  }

  std::map<std::string, std::string> extra{
      {"request_id", request_id},
      {"model_id", model_id},
      {"request_kind", request_kind},
      {field_name, payload},
      {field_name + "_chars", std::to_string(payload.size())},
  };
  if (!completion_status.empty()) {
    extra["completion_status"] = completion_status;
  }
  if (!completion_detail.empty()) {
    extra["completion_detail"] = completion_detail;
  }
  StructuredLogger::getInstance().info(message, extra);
}

} // namespace

// ---------------------------------------------------------------------------
// ComputeServiceImpl
// ---------------------------------------------------------------------------

ComputeServiceImpl::ComputeServiceImpl(std::atomic<bool> &shutting_down,
                                       int max_compute_concurrency,
                                       int compute_queue_timeout_ms,
                                       int max_queued_requests)
    : shutting_down_(shutting_down),
      start_time_(std::chrono::steady_clock::now()),
      compute_semaphore_(max_compute_concurrency),
      compute_queue_timeout_ms_(compute_queue_timeout_ms),
      max_queued_requests_(max_queued_requests) {
  StructuredLogger::getInstance().info(
      "Compute concurrency limit: " + std::to_string(max_compute_concurrency) +
      ", queue timeout: " + std::to_string(compute_queue_timeout_ms) +
      "ms, max queued requests: " + std::to_string(max_queued_requests));
}

int ComputeServiceImpl::recommendedMinThreads() const {
  // Enough threads for max concurrent computes + headroom for lightweight RPCs
  return compute_semaphore_.maxConcurrent() + 4;
}

ComputeGuard ComputeServiceImpl::acquireComputeSlot() {
  if (max_queued_requests_ > 0 &&
      metrics_.queued_requests.load(std::memory_order_relaxed) >=
          max_queued_requests_) {
    return ComputeGuard(compute_semaphore_, false,
                        ComputeGuard::Status::overload);
  }
  metrics_.queued_requests++;
  bool acquired = compute_semaphore_.tryAcquire(compute_queue_timeout_ms_);
  metrics_.queued_requests--;
  return ComputeGuard(
      compute_semaphore_, acquired,
      acquired ? ComputeGuard::Status::acquired : ComputeGuard::Status::timeout);
}

std::string ComputeServiceImpl::generateRequestId() {
  uint64_t counter = request_counter_.fetch_add(1);
  auto now = std::chrono::system_clock::now();
  auto ms = std::chrono::duration_cast<std::chrono::milliseconds>(
                now.time_since_epoch())
                .count();

  std::ostringstream oss;
  oss << "req-" << std::hex << ms << "-" << counter;
  return oss.str();
}

void ComputeServiceImpl::registerRequest(const std::string &request_id) {
  std::lock_guard<std::mutex> lock(requests_mutex_);
  active_requests_.insert(request_id);
}

void ComputeServiceImpl::unregisterRequest(const std::string &request_id) {
  std::lock_guard<std::mutex> lock(requests_mutex_);
  active_requests_.erase(request_id);
  cancelled_requests_.erase(request_id);
}

bool ComputeServiceImpl::isRequestCancelled(const std::string &request_id) {
  std::lock_guard<std::mutex> lock(requests_mutex_);
  return cancelled_requests_.find(request_id) != cancelled_requests_.end();
}

void ComputeServiceImpl::cancelRequest(const std::string &request_id) {
  std::lock_guard<std::mutex> lock(requests_mutex_);
  if (active_requests_.find(request_id) != active_requests_.end()) {
    cancelled_requests_.insert(request_id);
  }
}

void ComputeServiceImpl::recordRequest(const std::string &model_id,
                                       bool success, int64_t latency_ms,
                                       int32_t tokens) {
  metrics_.total_requests.fetch_add(1, std::memory_order_relaxed);
  if (success) {
    metrics_.successful_requests.fetch_add(1, std::memory_order_relaxed);
  } else {
    metrics_.failed_requests.fetch_add(1, std::memory_order_relaxed);
  }
  metrics_.total_tokens_processed.fetch_add(tokens, std::memory_order_relaxed);
  metrics_.total_latency_ms.fetch_add(latency_ms, std::memory_order_relaxed);

  // Update per-model metrics
  if (!model_id.empty()) {
    std::lock_guard<std::mutex> lock(metrics_.model_stats_mutex);
    auto &stats = metrics_.model_stats[model_id];
    stats.request_count.fetch_add(1, std::memory_order_relaxed);
    stats.total_tokens.fetch_add(tokens, std::memory_order_relaxed);
    stats.total_latency_ms.fetch_add(latency_ms, std::memory_order_relaxed);
  }
}

grpc::Status ComputeServiceImpl::Process(grpc::ServerContext *context,
                                         const ProcessRequest *request,
                                         ProcessResponse *response) {
  auto start = std::chrono::steady_clock::now();

  std::string request_id = request->has_request_id() ? request->request_id()
                                                     : generateRequestId();
  registerRequest(request_id);

  // --- Graceful shutdown check (reject new requests during shutdown) ---
  if (shutting_down_.load(std::memory_order_relaxed)) {
    std::string error_msg = "Server is shutting down";
    StructuredLogger::getInstance().log(
        LogLevel::WARNING,
        "Rejecting request: server is shutting down",
        {{"request_id", request_id}});
    unregisterRequest(request_id);
    response->set_success(false);
    response->set_error_message(error_msg);
    response->set_request_id(request_id);
    response->set_completion_status("backend_error");
    return grpc::Status(grpc::StatusCode::UNAVAILABLE, error_msg);
  }

  // --- Model ID validation (before acquiring compute slot) ---
  {
    std::string req_model_id = request->model_id();
    ModelConfigManager &config_mgr = ModelConfigManager::getInstance();

    if (!req_model_id.empty() && !config_mgr.hasModel(req_model_id)) {
      // Unregistered model_id -> NOT_FOUND with available models list
      auto available_ids = config_mgr.getModelIds();
      std::string available_list;
      for (size_t i = 0; i < available_ids.size(); ++i) {
        if (i > 0) available_list += ", ";
        available_list += available_ids[i];
      }
      std::string error_msg = "Unknown model_id: " + req_model_id +
                              ". Available models: [" + available_list + "]";

      StructuredLogger::getInstance().log(
          LogLevel::WARNING,
          "Model validation failed: unregistered model_id",
          {{"request_id", request_id},
           {"model_id", req_model_id}});

      unregisterRequest(request_id);
      response->set_success(false);
      response->set_error_message(error_msg);
      response->set_request_id(request_id);
      response->set_completion_status("backend_error");
      return grpc::Status(grpc::StatusCode::NOT_FOUND, error_msg);
    }

    if (req_model_id.empty()) {
      // Check if default config has a valid cli_path
      const ModelToolConfig default_cfg = config_mgr.getConfig("");
      if (default_cfg.cli_path.empty()) {
        std::string error_msg =
            "model_id is required, no default model configured";

        StructuredLogger::getInstance().log(
            LogLevel::WARNING,
            "Model validation failed: empty model_id with no default config",
            {{"request_id", request_id}});

        unregisterRequest(request_id);
        response->set_success(false);
        response->set_error_message(error_msg);
        response->set_request_id(request_id);
        response->set_completion_status("backend_error");
        return grpc::Status(grpc::StatusCode::INVALID_ARGUMENT, error_msg);
      }
    }
  }

  // Acquire a compute slot — lightweight RPCs are not affected
  auto guard = acquireComputeSlot();
  if (!guard.acquired()) {
    metrics_.rejected_requests++;
    std::string error_message =
        "Server busy: all compute slots occupied. Try again later.";
    if (guard.status() == ComputeGuard::Status::overload) {
      metrics_.overload_rejections.fetch_add(1, std::memory_order_relaxed);
      error_message = "Server overloaded: queue depth limit reached.";
    }
    unregisterRequest(request_id);
    response->set_success(false);
    response->set_error_message(error_message);
    response->set_request_id(request_id);
    response->set_completion_status("backend_error");
    return grpc::Status(grpc::StatusCode::RESOURCE_EXHAUSTED,
                        error_message);
  }

  try {
    std::string input = request->input();
    std::string model_id = request->model_id();
    InferenceOptions inference_options;
    if (request->has_max_tokens()) {
      inference_options.max_tokens = request->max_tokens();
    }
    if (request->has_timeout_ms()) {
      inference_options.request_timeout_ms = request->timeout_ms();
    }
    logPromptTrace("Compute server received prompt", request_id, model_id,
                   "non_stream", "prompt", input);

    StructuredLogger::getInstance().log(
        LogLevel::INFO,
        "Processing request (active computes: " +
            std::to_string(compute_semaphore_.activeCount()) + "/" +
            std::to_string(compute_semaphore_.maxConcurrent()) + ")",
        {{"request_id", request_id},
         {"model_id", model_id}});

    InferenceExecutionResult execution_result = processString(
        input, model_id, inference_options,
        [this, context, &request_id]() -> bool {
          return context->IsCancelled() || isRequestCancelled(request_id);
        });
    std::string output = execution_result.output;

    auto end = std::chrono::steady_clock::now();
    int64_t latency_ms =
        std::chrono::duration_cast<std::chrono::milliseconds>(end - start)
            .count();

    // Count tokens using TokenCounter (whitespace + punctuation tokenization)
    int32_t prompt_tokens = TokenCounter::count(input);
    int32_t completion_tokens = TokenCounter::count(output);

    response->set_output(output);
    response->set_success(true);
    response->set_request_id(request_id);
    response->set_completion_status(execution_result.completion_status);
    response->set_completion_detail(execution_result.completion_detail);
    logPromptTrace("Compute server returning result", request_id, model_id,
                   "non_stream", "result", output,
                   execution_result.completion_status,
                   execution_result.completion_detail);

    // Set usage stats
    auto *usage = response->mutable_usage();
    usage->set_prompt_tokens(prompt_tokens);
    usage->set_completion_tokens(completion_tokens);
    usage->set_latency_ms(latency_ms);
    if (latency_ms > 0) {
      usage->set_tokens_per_second(
          static_cast<float>(completion_tokens) /
          (static_cast<float>(latency_ms) / 1000.0f));
    }

    recordRequest(model_id, true, latency_ms, prompt_tokens + completion_tokens);
    if (execution_result.completion_status == "partial_timeout") {
      metrics_.watchdog_timeouts.fetch_add(1, std::memory_order_relaxed);
      metrics_.partial_timeout_returns.fetch_add(1, std::memory_order_relaxed);
    }
    unregisterRequest(request_id);

    StructuredLogger::getInstance().log(
        LogLevel::INFO,
        "Request completed successfully",
        {{"request_id", request_id},
         {"model_id", model_id},
         {"latency_ms", std::to_string(latency_ms)},
         {"token_count", std::to_string(prompt_tokens + completion_tokens)}});

    return grpc::Status::OK;
  } catch (const std::exception &e) {
    auto end = std::chrono::steady_clock::now();
    int64_t latency_ms =
        std::chrono::duration_cast<std::chrono::milliseconds>(end - start)
            .count();

    std::string error_message = e.what();

    if (error_message == "Request cancelled") {
      metrics_.request_cancellations.fetch_add(1, std::memory_order_relaxed);
      response->set_success(false);
      response->set_error_message(error_message);
      response->set_request_id(request_id);
      response->set_completion_status("cancelled");
      logPromptTrace("Compute server returning error", request_id,
                     request->model_id(), "non_stream", "result",
                     error_message, "cancelled");
      unregisterRequest(request_id);
      return grpc::Status(grpc::StatusCode::CANCELLED, error_message);
    }

    if (error_message.find("idle timeout") != std::string::npos ||
        error_message.find("max execution time") != std::string::npos) {
      metrics_.watchdog_timeouts.fetch_add(1, std::memory_order_relaxed);
      response->set_success(false);
      response->set_error_message(error_message);
      response->set_request_id(request_id);
      response->set_completion_status("partial_timeout");
      response->set_completion_detail(
          error_message.find("idle timeout") != std::string::npos
              ? "idle_timeout"
              : "watchdog_timeout");
      logPromptTrace("Compute server returning error", request_id,
                     request->model_id(), "non_stream", "result",
                     error_message, "partial_timeout",
                     response->completion_detail());
      recordRequest(request->model_id(), false, latency_ms, 0);
      unregisterRequest(request_id);
      return grpc::Status(grpc::StatusCode::DEADLINE_EXCEEDED, error_message);
    }

    response->set_success(false);
    response->set_error_message(error_message);
    response->set_request_id(request_id);
    response->set_completion_status("backend_error");
    logPromptTrace("Compute server returning error", request_id,
                   request->model_id(), "non_stream", "result",
                   error_message, "backend_error");

    recordRequest(request->model_id(), false, latency_ms, 0);
    unregisterRequest(request_id);

    StructuredLogger::getInstance().log(
        LogLevel::ERROR,
        std::string("Request failed: ") + error_message,
        {{"request_id", request_id},
         {"model_id", request->model_id()},
         {"latency_ms", std::to_string(latency_ms)},
         {"token_count", "0"}});

    return grpc::Status(grpc::StatusCode::INTERNAL, error_message);
  }
}

grpc::Status
ComputeServiceImpl::ProcessStream(grpc::ServerContext *context,
                                  const ProcessRequest *request,
                                  grpc::ServerWriter<StreamChunk> *writer) {
  auto start = std::chrono::steady_clock::now();

  std::string request_id = request->has_request_id() ? request->request_id()
                                                     : generateRequestId();
  registerRequest(request_id);

  // --- Graceful shutdown check (reject new requests during shutdown) ---
  if (shutting_down_.load(std::memory_order_relaxed)) {
    std::string error_msg = "Server is shutting down";
    StructuredLogger::getInstance().log(
        LogLevel::WARNING,
        "Rejecting stream request: server is shutting down",
        {{"request_id", request_id}});
    unregisterRequest(request_id);
    // Send error StreamChunk with is_final=true before returning
    StreamChunk error_chunk;
    error_chunk.set_is_final(true);
    error_chunk.set_error_message(error_msg);
    error_chunk.set_request_id(request_id);
    error_chunk.set_completion_status("backend_error");
    writer->Write(error_chunk);
    return grpc::Status(grpc::StatusCode::UNAVAILABLE, error_msg);
  }

  // --- Model ID validation (before acquiring compute slot) ---
  {
    std::string req_model_id = request->model_id();
    ModelConfigManager &config_mgr = ModelConfigManager::getInstance();

    if (!req_model_id.empty() && !config_mgr.hasModel(req_model_id)) {
      // Unregistered model_id -> NOT_FOUND, send error StreamChunk with is_final=true
      auto available_ids = config_mgr.getModelIds();
      std::string available_list;
      for (size_t i = 0; i < available_ids.size(); ++i) {
        if (i > 0) available_list += ", ";
        available_list += available_ids[i];
      }
      std::string error_msg = "Unknown model_id: " + req_model_id +
                              ". Available models: [" + available_list + "]";

      StructuredLogger::getInstance().log(
          LogLevel::WARNING,
          "Model validation failed: unregistered model_id",
          {{"request_id", request_id},
           {"model_id", req_model_id}});

      unregisterRequest(request_id);
      StreamChunk error_chunk;
      error_chunk.set_is_final(true);
      error_chunk.set_error_message(error_msg);
      error_chunk.set_request_id(request_id);
      error_chunk.set_completion_status("backend_error");
      writer->Write(error_chunk);
      return grpc::Status(grpc::StatusCode::NOT_FOUND, error_msg);
    }

    if (req_model_id.empty()) {
      // Check if default config has a valid cli_path
      const ModelToolConfig default_cfg = config_mgr.getConfig("");
      if (default_cfg.cli_path.empty()) {
        std::string error_msg =
            "model_id is required, no default model configured";

        StructuredLogger::getInstance().log(
            LogLevel::WARNING,
            "Model validation failed: empty model_id with no default config",
            {{"request_id", request_id}});

        unregisterRequest(request_id);
        StreamChunk error_chunk;
        error_chunk.set_is_final(true);
        error_chunk.set_error_message(error_msg);
        error_chunk.set_request_id(request_id);
        error_chunk.set_completion_status("backend_error");
        writer->Write(error_chunk);
        return grpc::Status(grpc::StatusCode::INVALID_ARGUMENT, error_msg);
      }
    }
  }

  // Acquire a compute slot — lightweight RPCs are not affected
  auto guard = acquireComputeSlot();
  if (!guard.acquired()) {
    metrics_.rejected_requests++;
    std::string error_message =
        "Server busy: all compute slots occupied. Try again later.";
    if (guard.status() == ComputeGuard::Status::overload) {
      metrics_.overload_rejections.fetch_add(1, std::memory_order_relaxed);
      error_message = "Server overloaded: queue depth limit reached.";
    }
    unregisterRequest(request_id);
    StreamChunk error_chunk;
    error_chunk.set_is_final(true);
    error_chunk.set_error_message(error_message);
    error_chunk.set_request_id(request_id);
    error_chunk.set_completion_status("backend_error");
    writer->Write(error_chunk);
    return grpc::Status(grpc::StatusCode::RESOURCE_EXHAUSTED,
                        error_message);
  }

  try {
    std::string input = request->input();
    std::string model_id = request->model_id();
    InferenceOptions inference_options;
    if (request->has_max_tokens()) {
      inference_options.max_tokens = request->max_tokens();
    }
    if (request->has_timeout_ms()) {
      inference_options.request_timeout_ms = request->timeout_ms();
    }
    const ModelToolConfig model_config =
        ModelConfigManager::getInstance().getConfig(model_id);
    inference_options.stream_idle_timeout_s =
        model_config.serving.stream_idle_timeout_s;
    logPromptTrace("Compute server received prompt", request_id, model_id,
                   "stream", "prompt", input);

    StructuredLogger::getInstance().log(
        LogLevel::INFO,
        "Starting streaming (active computes: " +
            std::to_string(compute_semaphore_.activeCount()) + "/" +
            std::to_string(compute_semaphore_.maxConcurrent()) + ")",
        {{"request_id", request_id},
         {"model_id", model_id}});

    int32_t prompt_tokens = TokenCounter::count(input);
    std::atomic<int32_t> completion_tokens{0};
    std::string streamed_output;
    std::string stream_error_output;

    StreamExecutionResult stream_result = processStringStream(
        input, model_id,
        [this, writer, context, &request_id,
         &completion_tokens, &streamed_output,
         &stream_error_output](const std::string &content, bool is_final) -> bool {
          // Check if client cancelled or request was cancelled
          if (context->IsCancelled() || isRequestCancelled(request_id)) {
            return false;
          }

          StreamChunk chunk;
          chunk.set_is_final(is_final);
          chunk.set_request_id(request_id);
          if (is_final && content.rfind("Error:", 0) == 0) {
            chunk.set_error_message(content);
            chunk.set_completion_status("backend_error");
            stream_error_output = content;
          } else {
            chunk.set_content(content);
            if (is_final) {
              chunk.set_completion_status("completed");
            }
          }

          // Count tokens in this chunk using TokenCounter
          if (!content.empty()) {
            completion_tokens += TokenCounter::count(content);
            if (!(is_final && content.rfind("Error:", 0) == 0)) {
              streamed_output += content;
            }
          }

          if (!writer->Write(chunk)) {
            StructuredLogger::getInstance().warning(
                "Failed to write chunk to stream",
                {{"request_id", request_id}});
            return false;
          }

          return true;
        },
        inference_options);
    bool success = stream_result.success;

    auto end = std::chrono::steady_clock::now();
    int64_t latency_ms =
        std::chrono::duration_cast<std::chrono::milliseconds>(end - start)
            .count();

    recordRequest(model_id, success, latency_ms,
                  prompt_tokens + completion_tokens.load());
    if (stream_result.completion_status == "partial_timeout") {
      metrics_.watchdog_timeouts.fetch_add(1, std::memory_order_relaxed);
      metrics_.partial_timeout_returns.fetch_add(1, std::memory_order_relaxed);
    }
    if (stream_result.completion_status == "cancelled") {
      metrics_.request_cancellations.fetch_add(1, std::memory_order_relaxed);
    }
    if (!stream_error_output.empty()) {
      logPromptTrace("Compute server returning error", request_id, model_id,
                     "stream", "result", stream_error_output,
                     stream_result.completion_status,
                     stream_result.completion_detail);
    } else {
      logPromptTrace("Compute server returning result", request_id, model_id,
                     "stream", "result", streamed_output,
                     stream_result.completion_status,
                     stream_result.completion_detail);
    }
    unregisterRequest(request_id);

    StructuredLogger::getInstance().log(
        LogLevel::INFO,
        success ? "Stream completed successfully" : "Stream processing failed",
        {{"request_id", request_id},
         {"model_id", model_id},
         {"latency_ms", std::to_string(latency_ms)},
         {"token_count", std::to_string(prompt_tokens + completion_tokens.load())}});

    if (!success) {
      grpc::StatusCode status_code = grpc::StatusCode::INTERNAL;
      if (stream_result.completion_status == "cancelled") {
        status_code = grpc::StatusCode::CANCELLED;
      } else if (stream_result.completion_status == "partial_timeout") {
        status_code = grpc::StatusCode::DEADLINE_EXCEEDED;
      }
      return grpc::Status(
          status_code,
          stream_result.completion_detail.empty()
              ? "Stream processing failed"
              : stream_result.completion_detail);
    }

    return grpc::Status::OK;
  } catch (const std::exception &e) {
    // Send error chunk
    StreamChunk error_chunk;
    error_chunk.set_is_final(true);
    error_chunk.set_error_message(e.what());
    error_chunk.set_request_id(request_id);
    error_chunk.set_completion_status("backend_error");
    writer->Write(error_chunk);

    auto end = std::chrono::steady_clock::now();
    int64_t latency_ms =
        std::chrono::duration_cast<std::chrono::milliseconds>(end - start)
            .count();
    logPromptTrace("Compute server returning error", request_id,
                   request->model_id(), "stream", "result", e.what(),
                   "backend_error");
    recordRequest(request->model_id(), false, latency_ms, 0);
    unregisterRequest(request_id);
    return grpc::Status(grpc::StatusCode::INTERNAL, e.what());
  }
}

grpc::Status ComputeServiceImpl::HealthCheck(grpc::ServerContext *context,
                                             const HealthCheckRequest *request,
                                             HealthCheckResponse *response) {
  // Lightweight RPC — no compute slot needed, always responds immediately
  auto now = std::chrono::steady_clock::now();
  auto uptime = std::chrono::duration_cast<std::chrono::seconds>(
                    now - start_time_)
                    .count();

  int32_t active_count;
  {
    std::lock_guard<std::mutex> lock(requests_mutex_);
    active_count = static_cast<int32_t>(active_requests_.size());
  }

  response->set_healthy(true);
  response->set_version(VERSION);
  response->set_uptime_seconds(uptime);
  response->set_active_requests(active_count);

  std::ostringstream msg;
  msg << "Server is running. Compute slots: "
      << compute_semaphore_.activeCount() << "/"
      << compute_semaphore_.maxConcurrent() << " in use";
  response->set_status_message(msg.str());

  return grpc::Status::OK;
}

grpc::Status ComputeServiceImpl::ListModels(grpc::ServerContext *context,
                                            const ListModelsRequest *request,
                                            ListModelsResponse *response) {
  ModelConfigManager &config_mgr = ModelConfigManager::getInstance();
  auto model_ids = config_mgr.getModelIds();

  for (const auto &model_id : model_ids) {
    const auto config = config_mgr.getConfig(model_id);
    auto *model_info = response->add_models();
    model_info->set_model_id(model_id);
    model_info->set_ready(true);
    model_info->set_owned_by(config.owned_by);
    model_info->set_created(config.created);
    auto *serving = model_info->mutable_serving();
    serving->set_api_mode(config.serving.api_mode);
    serving->set_prompt_style(config.serving.prompt_style);
    serving->set_default_max_tokens(config.serving.default_max_tokens);
    serving->set_max_max_tokens(config.serving.max_max_tokens);
    serving->set_max_input_chars(config.serving.max_input_chars);
    serving->set_request_timeout_ms(config.serving.request_timeout_ms);
    serving->set_stream_idle_timeout_s(config.serving.stream_idle_timeout_s);
    serving->set_allow_anonymous_models(
        config.serving.allow_anonymous_models);
  }

  return grpc::Status::OK;
}

grpc::Status
ComputeServiceImpl::CancelRequest(grpc::ServerContext *context,
                                  const CancelRequestMessage *request,
                                  CancelResponse *response) {
  std::string request_id = request->request_id();

  {
    std::lock_guard<std::mutex> lock(requests_mutex_);
    if (active_requests_.find(request_id) != active_requests_.end()) {
      cancelled_requests_.insert(request_id);
      response->set_success(true);
      response->set_message("Request " + request_id +
                            " marked for cancellation");
      StructuredLogger::getInstance().info(
          "Request cancelled",
          {{"request_id", request_id}});
    } else {
      response->set_success(false);
      response->set_message("Request " + request_id + " not found");
    }
  }

  return grpc::Status::OK;
}



grpc::Status ComputeServiceImpl::GetMetrics(grpc::ServerContext *context,
                                            const MetricsRequest *request,
                                            MetricsResponse *response) {
  response->set_total_requests(metrics_.total_requests.load());
  response->set_successful_requests(metrics_.successful_requests.load());
  response->set_failed_requests(metrics_.failed_requests.load());
  response->set_total_tokens_processed(metrics_.total_tokens_processed.load());
  response->set_rejected_requests(metrics_.rejected_requests.load());
  response->set_queued_requests(metrics_.queued_requests.load());
  response->set_active_compute_slots(compute_semaphore_.activeCount());
  response->set_max_compute_slots(compute_semaphore_.maxConcurrent());
  response->set_overload_rejections(metrics_.overload_rejections.load());
  response->set_watchdog_timeouts(metrics_.watchdog_timeouts.load());
  response->set_partial_timeout_returns(
      metrics_.partial_timeout_returns.load());
  response->set_request_cancellations(metrics_.request_cancellations.load());

  int64_t total = metrics_.total_requests.load();
  int64_t total_latency_ms = metrics_.total_latency_ms.load();
  int64_t total_tokens = metrics_.total_tokens_processed.load();
  if (total > 0) {
    response->set_average_latency_ms(
        static_cast<float>(total_latency_ms) / static_cast<float>(total));
  }
  if (total_latency_ms > 0 && total_tokens > 0) {
    response->set_average_tokens_per_second(
        static_cast<float>(total_tokens) /
        (static_cast<float>(total_latency_ms) / 1000.0f));
  } else {
    response->set_average_tokens_per_second(0.0f);
  }

  // Per-model metrics
  {
    std::lock_guard<std::mutex> lock(metrics_.model_stats_mutex);
    auto *model_metrics = response->mutable_model_metrics();
    for (const auto &[model_id, stats] : metrics_.model_stats) {
      ModelMetrics mm;
      mm.set_request_count(stats.request_count.load());
      mm.set_total_tokens(stats.total_tokens.load());
      if (stats.request_count.load() > 0) {
        mm.set_average_latency_ms(
            static_cast<float>(stats.total_latency_ms.load()) /
            static_cast<float>(stats.request_count.load()));
      }
      (*model_metrics)[model_id] = mm;
    }
  }

  return grpc::Status::OK;
}

grpc::Status ComputeServiceImpl::ReloadModels(
    grpc::ServerContext *context, const ReloadModelsRequest *request,
    ReloadModelsResponse *response) {
  (void)context;
  (void)request;
  try {
    ModelConfigManager &config_mgr = ModelConfigManager::getInstance();
    config_mgr.loadConfig();
    const int model_count =
        static_cast<int>(config_mgr.getModelIds().size());
    response->set_success(true);
    response->set_model_count(model_count);
    response->set_message("Model configuration reloaded successfully");
    StructuredLogger::getInstance().info(
        "Model configuration reloaded",
        {{"model_count", std::to_string(model_count)}});
    return grpc::Status::OK;
  } catch (const std::exception &e) {
    response->set_success(false);
    response->set_model_count(0);
    response->set_message(std::string("Failed to reload model configuration: ") +
                          e.what());
    StructuredLogger::getInstance().error(
        std::string("Failed to reload model configuration: ") + e.what());
    return grpc::Status(grpc::StatusCode::INTERNAL, e.what());
  }
}

} // namespace compute
