//====- ComputeServiceImpl.h ----------------------------------------------===//
//
// SPDX-License-Identifier: Apache-2.0
//
//===----------------------------------------------------------------------===//
//
// This file declares the compute service implementation.
//
//===----------------------------------------------------------------------===//

#pragma once

#include "ComputeSemaphore.h"
#include "compute.grpc.pb.h"
#include <atomic>
#include <chrono>
#include <grpcpp/grpcpp.h>
#include <mutex>
#include <set>
#include <unordered_map>

namespace compute {

// Server metrics
struct ServerMetrics {
  std::atomic<int64_t> total_requests{0};
  std::atomic<int64_t> successful_requests{0};
  std::atomic<int64_t> failed_requests{0};
  std::atomic<int64_t> rejected_requests{0};
  std::atomic<int64_t> queued_requests{0};
  std::atomic<int64_t> total_tokens_processed{0};
  std::atomic<int64_t> total_latency_ms{0};
  std::atomic<int64_t> overload_rejections{0};
  std::atomic<int64_t> watchdog_timeouts{0};
  std::atomic<int64_t> partial_timeout_returns{0};
  std::atomic<int64_t> request_cancellations{0};

  // Per-model metrics
  struct ModelStats {
    std::atomic<int64_t> request_count{0};
    std::atomic<int64_t> total_tokens{0};
    std::atomic<int64_t> total_latency_ms{0};
  };
  std::unordered_map<std::string, ModelStats> model_stats;
  std::mutex model_stats_mutex;
};

class ComputeServiceImpl final : public ComputeService::Service {
public:
  // max_compute_concurrency: max number of concurrent inference operations.
  // compute_queue_timeout_ms: how long a request waits for a compute slot
  //   before being rejected. -1 = wait indefinitely, 0 = fail immediately.
  // shutting_down: reference to global atomic flag indicating graceful shutdown.
  explicit ComputeServiceImpl(std::atomic<bool> &shutting_down,
                              int max_compute_concurrency = 2,
                              int compute_queue_timeout_ms = 30000,
                              int max_queued_requests = 0);

  // --- Heavy compute RPCs (subject to concurrency control) ---

  grpc::Status Process(grpc::ServerContext *context,
                       const ProcessRequest *request,
                       ProcessResponse *response) override;

  grpc::Status ProcessStream(grpc::ServerContext *context,
                             const ProcessRequest *request,
                             grpc::ServerWriter<StreamChunk> *writer) override;

  // --- Lightweight query RPCs (always execute immediately) ---

  grpc::Status HealthCheck(grpc::ServerContext *context,
                           const HealthCheckRequest *request,
                           HealthCheckResponse *response) override;

  grpc::Status ListModels(grpc::ServerContext *context,
                          const ListModelsRequest *request,
                          ListModelsResponse *response) override;

  grpc::Status CancelRequest(grpc::ServerContext *context,
                             const CancelRequestMessage *request,
                             CancelResponse *response) override;

  grpc::Status GetMetrics(grpc::ServerContext *context,
                          const MetricsRequest *request,
                          MetricsResponse *response) override;

  grpc::Status ReloadModels(grpc::ServerContext *context,
                            const ReloadModelsRequest *request,
                            ReloadModelsResponse *response) override;

  // Expose for Main.cpp to configure gRPC thread pool size
  int recommendedMinThreads() const;

private:
  // Acquire a compute slot, returns a RAII guard.
  // If the slot cannot be acquired within the timeout, the guard's
  // acquired() returns false.
  ComputeGuard acquireComputeSlot();

  // Generate unique request ID if not provided
  std::string generateRequestId();

  // Track active requests for cancellation
  void registerRequest(const std::string &request_id);
  void unregisterRequest(const std::string &request_id);
  bool isRequestCancelled(const std::string &request_id);
  void cancelRequest(const std::string &request_id);

  // Update metrics
  void recordRequest(const std::string &model_id, bool success,
                     int64_t latency_ms, int32_t tokens);

  // Reference to global graceful shutdown flag
  std::atomic<bool> &shutting_down_;

  // Server start time
  std::chrono::steady_clock::time_point start_time_;

  // Compute concurrency control
  ComputeSemaphore compute_semaphore_;
  int compute_queue_timeout_ms_;
  int max_queued_requests_;

  // Active requests tracking
  std::mutex requests_mutex_;
  std::set<std::string> active_requests_;
  std::set<std::string> cancelled_requests_;

  // Request counter for ID generation
  std::atomic<uint64_t> request_counter_{0};

  // Server metrics
  ServerMetrics metrics_;

  // Version string
  static constexpr const char *VERSION = "1.2.0";
};

} // namespace compute
