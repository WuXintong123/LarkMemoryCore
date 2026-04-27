//====- test_compute_semaphore.cpp -----------------------------------------===//
//
// SPDX-License-Identifier: Apache-2.0
//
//===----------------------------------------------------------------------===//
//
// Regression tests for ComputeSemaphore acquire/tryAcquire behavior.
//
//===----------------------------------------------------------------------===//

#include "ComputeSemaphore.h"

#include <future>
#include <gtest/gtest.h>

using compute::ComputeSemaphore;

TEST(ComputeSemaphoreTest, AcquireWithTimeoutDoesNotDeadlock) {
  ComputeSemaphore semaphore(1);

  auto future =
      std::async(std::launch::async, [&semaphore] { return semaphore.acquire(10); });

  EXPECT_EQ(future.wait_for(std::chrono::milliseconds(500)),
            std::future_status::ready);
  EXPECT_TRUE(future.get());
  EXPECT_EQ(semaphore.activeCount(), 1);

  semaphore.release();
  EXPECT_EQ(semaphore.activeCount(), 0);
}

TEST(ComputeSemaphoreTest, TryAcquireTimesOutWhenSlotStaysOccupied) {
  ComputeSemaphore semaphore(1);
  ASSERT_TRUE(semaphore.tryAcquire(0));

  auto future =
      std::async(std::launch::async, [&semaphore] { return semaphore.acquire(20); });

  EXPECT_EQ(future.wait_for(std::chrono::milliseconds(500)),
            std::future_status::ready);
  EXPECT_FALSE(future.get());

  semaphore.release();
  EXPECT_EQ(semaphore.activeCount(), 0);
}
