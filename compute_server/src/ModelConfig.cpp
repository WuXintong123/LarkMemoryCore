//====- ModelConfig.cpp ---------------------------------------------------===//
//
// SPDX-License-Identifier: Apache-2.0
//
//===----------------------------------------------------------------------===//
//
// Model configuration manager implementation
//
//===----------------------------------------------------------------------===//

#ifdef __has_include
#if __has_include(<nlohmann/json.hpp>)
#include <nlohmann/json.hpp>
#define HAS_NLOHMANN_JSON
#endif
#endif

#include "ModelConfig.h"
#include "StructuredLogger.h"
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <iostream>

namespace compute {

#ifdef HAS_NLOHMANN_JSON
namespace {

std::string normalizeApiMode(const std::string &raw) {
  if (raw == "chat" || raw == "completion" || raw == "both") {
    return raw;
  }
  return "both";
}

std::string normalizePromptStyle(const std::string &raw) {
  if (raw == "buddy_deepseek_r1" || raw == "chatml" || raw == "raw_completion") {
    return raw;
  }
  return "chatml";
}

int parsePositiveIntField(const nlohmann::json &value) {
  if (!value.is_number_integer()) {
    return 0;
  }
  int parsed = value.get<int>();
  return parsed > 0 ? parsed : 0;
}

} // namespace
#endif

ModelConfigManager &ModelConfigManager::getInstance() {
  static ModelConfigManager instance;
  return instance;
}

void ModelConfigManager::loadConfig() {
  std::lock_guard<std::mutex> lock(mutex_);
  model_configs_.clear();
  default_config_ = ModelToolConfig();
  loadFromEnv();
  if (model_configs_.empty()) {
    const char *path = std::getenv("MODELS_CONFIG_FILE");
    loadFromFile(path && path[0] ? path : "models.json");
  }
  if (model_configs_.empty()) {
    loadDefaultConfig();
  }

  // After all loading attempts, if no valid model configs exist and default
  // cli_path is empty, log a warning listing the configuration sources attempted
  if (model_configs_.empty() && default_config_.cli_path.empty()) {
    StructuredLogger::getInstance().warning(
        "No valid model configuration found. Attempted sources: "
        "environment variable MODELS_CONFIG, "
        "config file (MODELS_CONFIG_FILE or models.json), "
        "environment variable DEFAULT_MODEL_CLI_PATH. "
        "Please configure at least one model to enable inference.");
  }
}

void ModelConfigManager::loadFromEnv() {
  const char *raw = std::getenv("MODELS_CONFIG");
  if (!raw || !raw[0])
    return;
#ifdef HAS_NLOHMANN_JSON
  try {
    parseJsonConfig(nlohmann::json::parse(raw));
  } catch (const std::exception &e) {
    StructuredLogger::getInstance().warning(
        std::string("Failed to parse MODELS_CONFIG: ") + e.what());
  }
#endif
}

void ModelConfigManager::loadFromFile(const std::string &path) {
  std::ifstream f(path);
  if (!f.is_open())
    return;
#ifdef HAS_NLOHMANN_JSON
  try {
    nlohmann::json j;
    f >> j;
    parseJsonConfig(j);
    StructuredLogger::getInstance().info(
        "Loaded model config from: " + path);
  } catch (const std::exception &e) {
    StructuredLogger::getInstance().warning(
        "Failed to parse " + path + ": " + e.what());
  }
#endif
}

#ifdef HAS_NLOHMANN_JSON
void ModelConfigManager::parseJsonConfig(const nlohmann::json &json) {
  // Unified format only: {"models": [{"id": "...", "tool": {...}}, ...]}
  if (!json.is_object() || !json.contains("models"))
    return;
  auto arr = json["models"];
  if (!arr.is_array())
    return;
  for (const auto &m : arr) {
    if (!m.is_object() || !m.contains("id"))
      continue;
    std::string model_id = m["id"].get<std::string>();
    if (!m.contains("tool") || !m["tool"].is_object())
      continue;
    auto t = m["tool"];
    ModelToolConfig cfg;
    if (m.contains("owned_by"))
      cfg.owned_by = m["owned_by"].get<std::string>();
    if (m.contains("created"))
      cfg.created = m["created"].get<int64_t>();
    if (t.contains("cli_path"))
      cfg.cli_path = t["cli_path"].get<std::string>();
    if (t.contains("numactl_nodes"))
      cfg.numactl_nodes = t["numactl_nodes"].get<std::string>();
    if (t.contains("taskset_cpus"))
      cfg.taskset_cpus = t["taskset_cpus"].get<std::string>();
    if (t.contains("extra_args"))
      cfg.extra_args = t["extra_args"].get<std::string>();
    if (m.contains("serving") && m["serving"].is_object()) {
      auto serving = m["serving"];
      if (serving.contains("api_mode")) {
        cfg.serving.api_mode =
            normalizeApiMode(serving["api_mode"].get<std::string>());
      }
      if (serving.contains("prompt_style")) {
        cfg.serving.prompt_style =
            normalizePromptStyle(serving["prompt_style"].get<std::string>());
      }
      if (serving.contains("default_max_tokens")) {
        cfg.serving.default_max_tokens =
            parsePositiveIntField(serving["default_max_tokens"]);
      }
      if (serving.contains("max_max_tokens")) {
        cfg.serving.max_max_tokens =
            parsePositiveIntField(serving["max_max_tokens"]);
      }
      if (serving.contains("max_input_chars")) {
        cfg.serving.max_input_chars =
            parsePositiveIntField(serving["max_input_chars"]);
      }
      if (serving.contains("request_timeout_ms")) {
        cfg.serving.request_timeout_ms =
            parsePositiveIntField(serving["request_timeout_ms"]);
      }
      if (serving.contains("stream_idle_timeout_s")) {
        cfg.serving.stream_idle_timeout_s =
            parsePositiveIntField(serving["stream_idle_timeout_s"]);
      }
      if (serving.contains("allow_anonymous_models")) {
        cfg.serving.allow_anonymous_models =
            serving["allow_anonymous_models"].get<bool>();
      }
    }
    if (!cfg.cli_path.empty())
      registerModel(model_id, cfg);
  }
}
#endif

void ModelConfigManager::loadDefaultConfig() {
  // Load default from environment variable (no hardcoded fallback path)
  const char *default_cli = std::getenv("DEFAULT_MODEL_CLI_PATH");
  if (default_cli && std::strlen(default_cli) > 0) {
    default_config_.cli_path = default_cli;
  }
  // When DEFAULT_MODEL_CLI_PATH is not set, cli_path remains empty string

  const char *numactl_nodes = std::getenv("DEFAULT_NUMACTL_NODES");
  if (numactl_nodes && std::strlen(numactl_nodes) > 0) {
    default_config_.numactl_nodes = numactl_nodes;
  }

  const char *taskset_cpus = std::getenv("DEFAULT_TASKSET_CPUS");
  if (taskset_cpus && std::strlen(taskset_cpus) > 0) {
    default_config_.taskset_cpus = taskset_cpus;
  }

  const char *extra_args = std::getenv("DEFAULT_MODEL_EXTRA_ARGS");
  if (extra_args && std::strlen(extra_args) > 0) {
    default_config_.extra_args = extra_args;
  }

  const char *owned_by = std::getenv("DEFAULT_MODEL_OWNED_BY");
  if (owned_by && std::strlen(owned_by) > 0) {
    default_config_.owned_by = owned_by;
  }

  const char *created = std::getenv("DEFAULT_MODEL_CREATED");
  if (created && std::strlen(created) > 0) {
    try {
      default_config_.created = std::stoll(created);
    } catch (...) {
      StructuredLogger::getInstance().warning(
          "Invalid DEFAULT_MODEL_CREATED value: " + std::string(created) +
          ", keeping default metadata timestamp 0");
    }
  }

  if (!default_config_.cli_path.empty()) {
    StructuredLogger::getInstance().info(
        "Using default model config: " + default_config_.cli_path);
  } else {
    StructuredLogger::getInstance().warning(
        "DEFAULT_MODEL_CLI_PATH not set, default model cli_path is empty. "
        "Models without a specific cli_path will be unavailable.");
  }
}

ModelToolConfig ModelConfigManager::getConfig(const std::string &model_id) const {
  std::lock_guard<std::mutex> lock(mutex_);
  auto it = model_configs_.find(model_id);
  if (it != model_configs_.end()) {
    return it->second;
  }
  return default_config_;
}

bool ModelConfigManager::hasModel(const std::string &model_id) const {
  std::lock_guard<std::mutex> lock(mutex_);
  return model_configs_.find(model_id) != model_configs_.end();
}

std::vector<std::string> ModelConfigManager::getModelIds() const {
  std::lock_guard<std::mutex> lock(mutex_);
  std::vector<std::string> ids;
  ids.reserve(model_configs_.size());
  for (const auto &[id, config] : model_configs_) {
    ids.push_back(id);
  }
  return ids;
}

void ModelConfigManager::registerModel(const std::string &model_id,
                                       const ModelToolConfig &config) {
  model_configs_[model_id] = config;
  StructuredLogger::getInstance().info(
      "Registered model tool: " + model_id + " -> " + config.cli_path);
}

bool ModelConfigManager::unregisterModel(const std::string &model_id) {
  auto it = model_configs_.find(model_id);
  if (it != model_configs_.end()) {
    model_configs_.erase(it);
    StructuredLogger::getInstance().info(
        "Unregistered model: " + model_id);
    return true;
  }
  return false;
}

} // namespace compute
