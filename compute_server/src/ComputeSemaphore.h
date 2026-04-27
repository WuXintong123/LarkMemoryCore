//====- ComputeSemaphore.h ------------------------------------------------===//
//
// SPDX-License-Identifier: Apache-2.0
//
//===----------------------------------------------------------------------===//
//
// This file declares the compute semaphore primitives shared by the compute
// service and the focused semaphore unit tests.
//
//===----------------------------------------------------------------------===//

#pragma once

#include <atomic>
#include <condition_variable>
#include <mutex>

namespace compute {

// Counting semaphore for limiting concurrent compute operations.
// Lightweight query RPCs (ListModels, HealthCheck, GetMetrics) bypass this
// entirely, so they are never blocked by long-running inference requests.
class ComputeSemaphore {
public:
  explicit ComputeSemaphore(int max_concurrent);

  // Try to acquire a slot. Returns true if acquired, false if would block
  // and timeout_ms has elapsed (0 = non-blocking try).
  bool tryAcquire(int timeout_ms = 0);

  // Blocking acquire with optional timeout. Returns false on timeout.
  bool acquire(int timeout_ms = -1);

  // Release a slot.
  void release();

  // Current number of active compute operations.
  int activeCount() const;

  // Maximum allowed concurrent compute operations.
  int maxConcurrent() const;

private:
  const int max_concurrent_;
  std::atomic<int> active_{0};
  std::mutex mutex_;
  std::condition_variable cv_;
};

// RAII guard for ComputeSemaphore.
class ComputeGuard {
public:
  enum class Status {
    acquired,
    timeout,
    overload,
  };

  ComputeGuard(ComputeSemaphore &sem, bool acquired,
               Status status = Status::timeout);
  ~ComputeGuard();
  ComputeGuard(const ComputeGuard &) = delete;
  ComputeGuard &operator=(const ComputeGuard &) = delete;

  bool acquired() const { return acquired_; }
  Status status() const { return status_; }

private:
  ComputeSemaphore &sem_;
  bool acquired_;
  Status status_;
};

} // namespace compute
