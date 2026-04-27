//====- ModelConfig.h -----------------------------------------------------===//
//
// SPDX-License-Identifier: Apache-2.0
//
//===----------------------------------------------------------------------===//
//
// Model configuration manager for C++ server
//
//===----------------------------------------------------------------------===//

#pragma once

#include <cstdint>
#include <map>
#include <memory>
#include <mutex>
#include <string>
#include <vector>

namespace compute {

struct ModelServingPolicyConfig {
  std::string api_mode = "both";
  std::string prompt_style = "chatml";
  int default_max_tokens = 0;
  int max_max_tokens = 0;
  int max_input_chars = 0;
  int request_timeout_ms = 0;
  int stream_idle_timeout_s = 0;
  bool allow_anonymous_models = false;
};

struct ModelToolConfig {
  std::string cli_path;      // Path to the CLI tool
  std::string numactl_nodes; // NUMA nodes (e.g., "0,1,2,3")
  std::string taskset_cpus;  // CPU cores (e.g., "0-47")
  std::string extra_args;    // Extra command line arguments
  std::string owned_by;      // Public model owner metadata
  int64_t created;           // Public model creation timestamp
  ModelServingPolicyConfig serving;

  ModelToolConfig()
      : cli_path(""), numactl_nodes("0,1,2,3"), taskset_cpus("0-47"),
        extra_args("--no-stats"), owned_by("ruyi-serving"), created(0) {}
};

class ModelConfigManager {
public:
  static ModelConfigManager &getInstance();

  // Load configuration from environment variable or config file
  void loadConfig();

  // Get tool configuration for a model
  ModelToolConfig getConfig(const std::string &model_id) const;

  // Check if model is configured
  bool hasModel(const std::string &model_id) const;

  // Get all registered model IDs
  std::vector<std::string> getModelIds() const;

  // Register a model configuration
  void registerModel(const std::string &model_id,
                     const ModelToolConfig &config);

  // Unregister a model
  bool unregisterModel(const std::string &model_id);

private:
  ModelConfigManager() = default;
  ~ModelConfigManager() = default;
  ModelConfigManager(const ModelConfigManager &) = delete;
  ModelConfigManager &operator=(const ModelConfigManager &) = delete;

  void loadFromEnv();
  void loadFromFile(const std::string &config_file);
  void loadDefaultConfig();

#ifdef HAS_NLOHMANN_JSON
  void parseJsonConfig(const nlohmann::json &json);
#endif

  std::map<std::string, ModelToolConfig> model_configs_;
  ModelToolConfig default_config_;
  mutable std::mutex mutex_;
};

} // namespace compute
