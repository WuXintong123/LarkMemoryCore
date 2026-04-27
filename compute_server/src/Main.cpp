//====- Main.cpp ----------------------------------------------------------===//
//
// SPDX-License-Identifier: Apache-2.0
//
//===----------------------------------------------------------------------===//
//
// This file implements the main function.
//
//===----------------------------------------------------------------------===//

#include "ComputeServiceImpl.h"
#include "ModelConfig.h"
#include "StructuredLogger.h"
#include <atomic>
#include <cctype>
#include <chrono>
#include <csignal>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <grpcpp/grpcpp.h>
#include <iostream>
#include <memory>
#include <pthread.h>
#include <sstream>
#include <stdexcept>
#include <string>
#include <thread>

using compute::ComputeServiceImpl;
using compute::ModelConfigManager;
using grpc::Server;
using grpc::ServerBuilder;

// ---------------------------------------------------------------------------
// Graceful shutdown globals
// ---------------------------------------------------------------------------

/// Global flag indicating the server is shutting down. Checked by
/// ComputeServiceImpl (Task 5.3) to reject new requests during shutdown.
std::atomic<bool> g_shutting_down{false};

/// Raw pointer to the running gRPC server, used by the signal-wait thread to
/// initiate a graceful shutdown. Set in RunServer() before Wait().
std::atomic<grpc::Server *> g_server_ptr{nullptr};

/// Dedicated signal waiter that performs graceful shutdown work outside an
/// async signal handler context. This avoids unsafe logging and mutex usage
/// from a signal handler thread interruption point.
static void waitForShutdownSignal(sigset_t signal_set, int timeout_seconds) {
  int signum = 0;
  int rc = sigwait(&signal_set, &signum);
  if (rc != 0) {
    compute::StructuredLogger::getInstance().warning(
        "sigwait failed, graceful shutdown signal thread exiting");
    return;
  }

  bool expected = false;
  if (!g_shutting_down.compare_exchange_strong(expected, true)) {
    return;
  }

  const char *sig_name = (signum == SIGTERM) ? "SIGTERM" : "SIGINT";
  compute::StructuredLogger::getInstance().info(
      std::string("Received ") + sig_name + ", initiating graceful shutdown");

  grpc::Server *server = g_server_ptr.load(std::memory_order_acquire);
  if (!server) {
    return;
  }

  compute::StructuredLogger::getInstance().info(
      "Waiting up to " + std::to_string(timeout_seconds) +
      "s for in-flight requests to complete");

  auto deadline = std::chrono::system_clock::now() +
                  std::chrono::seconds(timeout_seconds);
  server->Shutdown(deadline);

  compute::StructuredLogger::getInstance().info(
      "gRPC server shutdown completed");
}

// Read an integer from an environment variable, returning default_val if unset.
static int getEnvInt(const char *name, int default_val) {
  const char *val = std::getenv(name);
  if (val && std::strlen(val) > 0) {
    try {
      return std::stoi(val);
    } catch (...) {
      compute::StructuredLogger::getInstance().warning(
          std::string("Invalid value for ") + name + ": " + val +
          ", using default " + std::to_string(default_val));
    }
  }
  return default_val;
}

static bool getEnvBool(const char *name, bool default_val = false) {
  const char *val = std::getenv(name);
  if (!val || std::strlen(val) == 0) {
    return default_val;
  }

  std::string normalized(val);
  for (char &c : normalized) {
    c = static_cast<char>(std::tolower(static_cast<unsigned char>(c)));
  }
  return normalized == "1" || normalized == "true" || normalized == "yes" ||
         normalized == "on";
}

static std::string readFileContents(const std::string &path) {
  std::ifstream file(path, std::ios::binary);
  if (!file.is_open()) {
    throw std::runtime_error("Failed to open file: " + path);
  }
  std::ostringstream buffer;
  buffer << file.rdbuf();
  return buffer.str();
}

static std::shared_ptr<grpc::ServerCredentials> buildServerCredentials() {
  if (!getEnvBool("COMPUTE_GRPC_TLS_ENABLE", false)) {
    return grpc::InsecureServerCredentials();
  }

  const char *cert_path = std::getenv("COMPUTE_GRPC_TLS_CERT_FILE");
  const char *key_path = std::getenv("COMPUTE_GRPC_TLS_KEY_FILE");
  if (!cert_path || !key_path || std::strlen(cert_path) == 0 ||
      std::strlen(key_path) == 0) {
    throw std::runtime_error(
        "TLS enabled but COMPUTE_GRPC_TLS_CERT_FILE / "
        "COMPUTE_GRPC_TLS_KEY_FILE not configured");
  }

  grpc::SslServerCredentialsOptions ssl_opts;
  grpc::SslServerCredentialsOptions::PemKeyCertPair key_cert = {
      readFileContents(key_path), readFileContents(cert_path)};
  ssl_opts.pem_key_cert_pairs.push_back(key_cert);

  const char *client_ca_path = std::getenv("COMPUTE_GRPC_TLS_CLIENT_CA_FILE");
  if (client_ca_path && std::strlen(client_ca_path) > 0) {
    ssl_opts.pem_root_certs = readFileContents(client_ca_path);
    ssl_opts.client_certificate_request =
        GRPC_SSL_REQUEST_AND_REQUIRE_CLIENT_CERTIFICATE_AND_VERIFY;
  } else {
    ssl_opts.client_certificate_request = GRPC_SSL_DONT_REQUEST_CLIENT_CERTIFICATE;
  }

  return grpc::SslServerCredentials(ssl_opts);
}

void RunServer() {
  // Load model configuration at startup
  ModelConfigManager::getInstance().loadConfig();

  // Server address
  const char *addr_env = std::getenv("COMPUTE_SERVER_ADDRESS");
  std::string server_address =
      (addr_env && std::strlen(addr_env) > 0) ? addr_env : "0.0.0.0:9000";

  // Concurrency configuration:
  //   MAX_COMPUTE_CONCURRENCY  - max simultaneous inference operations (default 3)
  //   COMPUTE_QUEUE_TIMEOUT_MS - how long a request waits for a slot (default 30000)
  //     -1 = wait forever, 0 = fail immediately if no slot available
  //   MAX_QUEUED_REQUESTS      - maximum queued inference requests before overload fast-fail (default 0 = disabled)
  int max_compute = getEnvInt("MAX_COMPUTE_CONCURRENCY", 3);
  int queue_timeout = getEnvInt("COMPUTE_QUEUE_TIMEOUT_MS", 30000);
  int max_queued_requests = getEnvInt("MAX_QUEUED_REQUESTS", 0);
  int shutdown_timeout_seconds = getEnvInt("GRACEFUL_SHUTDOWN_TIMEOUT_S", 60);

  ComputeServiceImpl service(g_shutting_down, max_compute, queue_timeout,
                             max_queued_requests);

  // Block shutdown signals in this thread before gRPC spins up worker
  // threads. The dedicated sigwait thread below will be the only place where
  // SIGTERM/SIGINT are consumed and acted on.
  sigset_t signal_set;
  sigemptyset(&signal_set);
  sigaddset(&signal_set, SIGTERM);
  sigaddset(&signal_set, SIGINT);
  int mask_rc = pthread_sigmask(SIG_BLOCK, &signal_set, nullptr);
  if (mask_rc != 0) {
    throw std::runtime_error("Failed to block shutdown signals: " +
                             std::to_string(mask_rc));
  }

  ServerBuilder builder;

  std::shared_ptr<grpc::ServerCredentials> server_creds;
  try {
    server_creds = buildServerCredentials();
  } catch (const std::exception &e) {
    compute::StructuredLogger::getInstance().error(
        std::string("Failed to initialize gRPC server credentials: ") + e.what());
    throw;
  }

  builder.AddListeningPort(server_address, server_creds);

  // Configure thread pool: enough for max concurrent computes + headroom for
  // lightweight RPCs (ListModels, HealthCheck, GetMetrics, CancelRequest).
  // This ensures query RPCs are never starved by long-running inference.
  int min_threads = service.recommendedMinThreads();
  int max_threads = getEnvInt("GRPC_MAX_THREADS", min_threads * 2);
  if (max_threads < min_threads) {
    max_threads = min_threads;
  }

  builder.SetSyncServerOption(ServerBuilder::SyncServerOption::MIN_POLLERS,
                              min_threads);
  builder.SetSyncServerOption(ServerBuilder::SyncServerOption::MAX_POLLERS,
                              max_threads);

  compute::StructuredLogger::getInstance().info(
      "gRPC thread pool: min=" + std::to_string(min_threads) +
      " max=" + std::to_string(max_threads));
  compute::StructuredLogger::getInstance().info(
      std::string("gRPC transport security: ") +
      (getEnvBool("COMPUTE_GRPC_TLS_ENABLE", false) ? "TLS/mTLS enabled"
                                                    : "insecure"));

  // Register service
  builder.RegisterService(&service);

  // Build and start server
  std::unique_ptr<Server> server(builder.BuildAndStart());

  compute::StructuredLogger::getInstance().info(
      "Compute Server listening on " + server_address);
  compute::StructuredLogger::getInstance().info(
      "Max compute concurrency: " + std::to_string(max_compute));
  compute::StructuredLogger::getInstance().info(
      "Queue timeout: " + std::to_string(queue_timeout) + "ms");
  compute::StructuredLogger::getInstance().info(
      "Max queued requests: " + std::to_string(max_queued_requests));
  compute::StructuredLogger::getInstance().info(
      "Press Ctrl+C to stop the server...");

  // Expose the server pointer so the sigwait thread can initiate shutdown.
  g_server_ptr.store(server.get(), std::memory_order_release);

  std::thread signal_thread(waitForShutdownSignal, signal_set,
                            shutdown_timeout_seconds);
  signal_thread.detach();

  // Wait for server to close (returns when Shutdown() completes or the
  // server is otherwise stopped).
  server->Wait();

  // If we reach here due to a signal-triggered shutdown, the flag is already
  // set.  If Wait() returned for another reason, ensure the flag is set so
  // any remaining components know the server is done.
  g_shutting_down.store(true);

  // Clear the global pointer – the server is no longer valid.
  g_server_ptr.store(nullptr, std::memory_order_release);

  compute::StructuredLogger::getInstance().info(
      "Compute Server has shut down");
}

int main(int argc, char **argv) {
  RunServer();
  return 0;
}
