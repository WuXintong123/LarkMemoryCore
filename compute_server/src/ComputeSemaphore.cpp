//====- ComputeSemaphore.cpp ----------------------------------------------===//
//
// SPDX-License-Identifier: Apache-2.0
//
//===----------------------------------------------------------------------===//
//
// This file implements the semaphore primitives used to limit concurrent
// compute work.
//
//===----------------------------------------------------------------------===//

#include "ComputeSemaphore.h"

#include <chrono>

namespace compute {

ComputeSemaphore::ComputeSemaphore(int max_concurrent)
    : max_concurrent_(max_concurrent) {}

bool ComputeSemaphore::tryAcquire(int timeout_ms) {
  std::unique_lock<std::mutex> lock(mutex_);
  if (timeout_ms == 0) {
    if (active_.load() >= max_concurrent_) {
      return false;
    }
    active_++;
    return true;
  }

  auto deadline = std::chrono::steady_clock::now() +
                  std::chrono::milliseconds(timeout_ms);
  bool ok = cv_.wait_until(lock, deadline, [this] {
    return active_.load() < max_concurrent_;
  });
  if (ok) {
    active_++;
  }
  return ok;
}

bool ComputeSemaphore::acquire(int timeout_ms) {
  if (timeout_ms < 0) {
    std::unique_lock<std::mutex> lock(mutex_);
    cv_.wait(lock, [this] { return active_.load() < max_concurrent_; });
    active_++;
    return true;
  }
  return tryAcquire(timeout_ms);
}

void ComputeSemaphore::release() {
  {
    std::lock_guard<std::mutex> lock(mutex_);
    active_--;
  }
  cv_.notify_one();
}

int ComputeSemaphore::activeCount() const { return active_.load(); }

int ComputeSemaphore::maxConcurrent() const { return max_concurrent_; }

ComputeGuard::ComputeGuard(ComputeSemaphore &sem, bool acquired, Status status)
    : sem_(sem), acquired_(acquired), status_(status) {}

ComputeGuard::~ComputeGuard() {
  if (acquired_) {
    sem_.release();
  }
}

} // namespace compute
