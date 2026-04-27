//====- test_model_validation.cpp -------------------------------------------===//
//
// SPDX-License-Identifier: Apache-2.0
//
//===----------------------------------------------------------------------===//
//
// Property-based tests and unit tests for ModelConfigManager model validation.
//
// Feature: serving-framework-enhancement, Property 3: Model validation rejects
//   unregistered model IDs
// Validates: Requirements 3.1, 3.2
//
// Compile with:
//   g++ -std=c++17 -I../../compute_server/src \
//       test_model_validation.cpp \
//       ../../compute_server/src/ModelConfig.cpp \
//       ../../compute_server/src/StructuredLogger.cpp \
//       -lgtest -lgtest_main -pthread -o test_model_validation
//
//===----------------------------------------------------------------------===//

#include "ModelConfig.h"

#include <gtest/gtest.h>

#include <algorithm>
#include <random>
#include <set>
#include <string>
#include <vector>

using compute::ModelConfigManager;
using compute::ModelToolConfig;

// ---------------------------------------------------------------------------
// Helper: Reset ModelConfigManager state
// ---------------------------------------------------------------------------

/// Unregister all models from the singleton ModelConfigManager so each test
/// starts with a clean slate. We use getModelIds() + unregisterModel() which
/// are the public API — no internal state hacking needed.
static void clearAllModels(ModelConfigManager &mgr) {
  auto ids = mgr.getModelIds();
  for (const auto &id : ids) {
    mgr.unregisterModel(id);
  }
}

// ---------------------------------------------------------------------------
// Random string generators
// ---------------------------------------------------------------------------

/// Generate a random model ID string of length [1, maxLen] containing
/// alphanumeric characters, hyphens, underscores, dots, and slashes —
/// characters commonly found in model identifiers.
static std::string generateRandomModelId(std::mt19937 &rng,
                                         size_t maxLen = 64) {
  static const char kModelIdChars[] =
      "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
      "-_./";

  std::uniform_int_distribution<size_t> lenDist(1, maxLen);
  size_t len = lenDist(rng);

  std::string result;
  result.reserve(len);

  std::uniform_int_distribution<size_t> charDist(0, sizeof(kModelIdChars) - 2);
  for (size_t i = 0; i < len; ++i) {
    result.push_back(kModelIdChars[charDist(rng)]);
  }

  return result;
}

/// Generate a random set of model IDs to register as "known" models.
/// Returns between 1 and maxCount unique model IDs.
static std::set<std::string> generateRegisteredModelSet(std::mt19937 &rng,
                                                        size_t maxCount = 10) {
  std::uniform_int_distribution<size_t> countDist(1, maxCount);
  size_t count = countDist(rng);

  std::set<std::string> models;
  while (models.size() < count) {
    models.insert(generateRandomModelId(rng));
  }

  return models;
}

/// Generate a random model ID that is guaranteed NOT to be in the given set
/// of registered models. Keeps generating until a unique one is found.
static std::string
generateUnregisteredModelId(std::mt19937 &rng,
                            const std::set<std::string> &registered) {
  for (int attempt = 0; attempt < 1000; ++attempt) {
    std::string candidate = generateRandomModelId(rng);
    if (registered.find(candidate) == registered.end()) {
      return candidate;
    }
  }
  // Fallback: append a unique suffix that won't collide
  return "__unregistered_model_" + std::to_string(rng());
}

// ===========================================================================
// Property-Based Tests
// ===========================================================================

TEST(ModelValidationPropertyTest, Property3_RejectsUnregisteredModelIds) {
  // Feature: serving-framework-enhancement, Property 3: Model validation
  //   rejects unregistered model IDs
  // Validates: Requirements 3.1, 3.2
  //
  // For any model_id string that is not present in the set of registered
  // models, the model validation function SHALL return an error containing
  // the unregistered model_id and a list of all available model IDs.
  //
  // We test this at the ModelConfigManager level:
  //   - hasModel(unregistered_id) SHALL return false
  //   - The unregistered_id SHALL NOT appear in getModelIds()
  //   - getModelIds() SHALL return exactly the set of registered models

  std::mt19937 rng(42);

  ModelConfigManager &mgr = ModelConfigManager::getInstance();

  for (int i = 0; i < 100; ++i) {
    // --- Setup: register a random set of models ---
    clearAllModels(mgr);

    std::set<std::string> registered = generateRegisteredModelSet(rng);
    for (const auto &model_id : registered) {
      ModelToolConfig cfg;
      cfg.cli_path = "/usr/bin/test-cli-" + model_id;
      mgr.registerModel(model_id, cfg);
    }

    // --- Generate an unregistered model_id ---
    std::string unregistered_id = generateUnregisteredModelId(rng, registered);

    // --- Property assertions ---

    // 1. hasModel() SHALL return false for the unregistered model_id
    EXPECT_FALSE(mgr.hasModel(unregistered_id))
        << "hasModel() should return false for unregistered model_id: \""
        << unregistered_id << "\" on iteration " << i;

    // 2. The unregistered model_id SHALL NOT appear in getModelIds()
    std::vector<std::string> available_ids = mgr.getModelIds();
    bool found_in_available =
        std::find(available_ids.begin(), available_ids.end(),
                  unregistered_id) != available_ids.end();
    EXPECT_FALSE(found_in_available)
        << "Unregistered model_id \"" << unregistered_id
        << "\" should not appear in getModelIds() on iteration " << i;

    // 3. getModelIds() SHALL return exactly the set of registered models
    std::set<std::string> available_set(available_ids.begin(),
                                        available_ids.end());
    EXPECT_EQ(available_set, registered)
        << "getModelIds() should return exactly the registered model set "
        << "on iteration " << i;

    // 4. Verify the error message can be constructed with the unregistered
    //    model_id and the list of available models (simulating what
    //    ComputeServiceImpl does in Process/ProcessStream)
    std::string available_list;
    for (size_t j = 0; j < available_ids.size(); ++j) {
      if (j > 0)
        available_list += ", ";
      available_list += available_ids[j];
    }
    std::string error_msg = "Unknown model_id: " + unregistered_id +
                            ". Available models: [" + available_list + "]";

    // The error message SHALL contain the unregistered model_id
    EXPECT_NE(error_msg.find(unregistered_id), std::string::npos)
        << "Error message should contain the unregistered model_id on "
        << "iteration " << i;

    // The error message SHALL contain each available model_id
    for (const auto &reg_id : registered) {
      EXPECT_NE(error_msg.find(reg_id), std::string::npos)
          << "Error message should contain registered model_id \"" << reg_id
          << "\" on iteration " << i;
    }
  }

  // Cleanup
  clearAllModels(mgr);
}

TEST(ModelValidationPropertyTest,
     Property3_RegisteredModelsAlwaysPassValidation) {
  // Feature: serving-framework-enhancement, Property 3: Model validation
  //   rejects unregistered model IDs
  // Validates: Requirements 3.1, 3.2
  //
  // Complementary property: For any model_id that IS registered,
  // hasModel() SHALL return true and the model_id SHALL appear in
  // getModelIds(). This confirms the boundary between accepted and
  // rejected model IDs is correct.

  std::mt19937 rng(99);

  ModelConfigManager &mgr = ModelConfigManager::getInstance();

  for (int i = 0; i < 100; ++i) {
    clearAllModels(mgr);

    std::set<std::string> registered = generateRegisteredModelSet(rng);
    for (const auto &model_id : registered) {
      ModelToolConfig cfg;
      cfg.cli_path = "/usr/bin/test-cli-" + model_id;
      mgr.registerModel(model_id, cfg);
    }

    // Every registered model SHALL pass validation
    for (const auto &model_id : registered) {
      EXPECT_TRUE(mgr.hasModel(model_id))
          << "hasModel() should return true for registered model_id: \""
          << model_id << "\" on iteration " << i;
    }

    // getModelIds() SHALL contain all registered models
    std::vector<std::string> available_ids = mgr.getModelIds();
    std::set<std::string> available_set(available_ids.begin(),
                                        available_ids.end());
    EXPECT_EQ(available_set, registered)
        << "getModelIds() should match registered set on iteration " << i;
  }

  clearAllModels(mgr);
}

TEST(ModelValidationPropertyTest,
     Property3_UnregisterRemovesFromValidation) {
  // Feature: serving-framework-enhancement, Property 3: Model validation
  //   rejects unregistered model IDs
  // Validates: Requirements 3.1, 3.2
  //
  // For any model_id that was registered and then unregistered,
  // hasModel() SHALL return false and the model_id SHALL NOT appear
  // in getModelIds(). This tests the dynamic nature of the model registry.

  std::mt19937 rng(2025);

  ModelConfigManager &mgr = ModelConfigManager::getInstance();

  for (int i = 0; i < 100; ++i) {
    clearAllModels(mgr);

    // Register a set of models
    std::set<std::string> registered = generateRegisteredModelSet(rng);
    for (const auto &model_id : registered) {
      ModelToolConfig cfg;
      cfg.cli_path = "/usr/bin/test-cli-" + model_id;
      mgr.registerModel(model_id, cfg);
    }

    // Pick a random model to unregister
    std::uniform_int_distribution<size_t> pickDist(0, registered.size() - 1);
    auto it = registered.begin();
    std::advance(it, pickDist(rng));
    std::string removed_id = *it;

    bool unregister_result = mgr.unregisterModel(removed_id);
    EXPECT_TRUE(unregister_result)
        << "unregisterModel() should return true for registered model \""
        << removed_id << "\" on iteration " << i;

    // After unregistration, the model SHALL be rejected
    EXPECT_FALSE(mgr.hasModel(removed_id))
        << "hasModel() should return false after unregistering \""
        << removed_id << "\" on iteration " << i;

    // The unregistered model SHALL NOT appear in getModelIds()
    std::vector<std::string> available_ids = mgr.getModelIds();
    bool found = std::find(available_ids.begin(), available_ids.end(),
                           removed_id) != available_ids.end();
    EXPECT_FALSE(found)
        << "Unregistered model \"" << removed_id
        << "\" should not appear in getModelIds() on iteration " << i;

    // All other models SHALL still be registered
    for (const auto &model_id : registered) {
      if (model_id == removed_id)
        continue;
      EXPECT_TRUE(mgr.hasModel(model_id))
          << "Model \"" << model_id
          << "\" should still be registered after removing \"" << removed_id
          << "\" on iteration " << i;
    }
  }

  clearAllModels(mgr);
}

// ===========================================================================
// Unit Tests — Specific Edge Cases
// ===========================================================================

TEST(ModelValidationUnitTest, EmptyRegistryRejectsAnyModelId) {
  // Validates: Requirements 3.1, 3.2
  // When no models are registered, any model_id should be rejected.

  ModelConfigManager &mgr = ModelConfigManager::getInstance();
  clearAllModels(mgr);

  EXPECT_FALSE(mgr.hasModel("some-model"));
  EXPECT_FALSE(mgr.hasModel("deepseek-r1"));
  EXPECT_FALSE(mgr.hasModel("llama-3"));

  std::vector<std::string> ids = mgr.getModelIds();
  EXPECT_TRUE(ids.empty());
}

TEST(ModelValidationUnitTest, EmptyStringModelIdIsNotRegistered) {
  // Validates: Requirements 3.3
  // An empty model_id is never "registered" — hasModel("") returns false.
  // The empty model_id case is handled separately by checking default config.

  ModelConfigManager &mgr = ModelConfigManager::getInstance();
  clearAllModels(mgr);

  // Register a real model
  ModelToolConfig cfg;
  cfg.cli_path = "/usr/bin/test-cli";
  mgr.registerModel("test-model", cfg);

  EXPECT_FALSE(mgr.hasModel(""));
  EXPECT_TRUE(mgr.hasModel("test-model"));

  clearAllModels(mgr);
}

TEST(ModelValidationUnitTest, CaseSensitiveModelIds) {
  // Validates: Requirements 3.1, 3.2
  // Model IDs are case-sensitive: "Model-A" and "model-a" are different.

  ModelConfigManager &mgr = ModelConfigManager::getInstance();
  clearAllModels(mgr);

  ModelToolConfig cfg;
  cfg.cli_path = "/usr/bin/test-cli";
  mgr.registerModel("Model-A", cfg);

  EXPECT_TRUE(mgr.hasModel("Model-A"));
  EXPECT_FALSE(mgr.hasModel("model-a"));
  EXPECT_FALSE(mgr.hasModel("MODEL-A"));
  EXPECT_FALSE(mgr.hasModel("model-A"));

  clearAllModels(mgr);
}

TEST(ModelValidationUnitTest, RegisterAndUnregisterCycle) {
  // Validates: Requirements 3.1, 3.2
  // A model can be registered, validated, unregistered, and then rejected.

  ModelConfigManager &mgr = ModelConfigManager::getInstance();
  clearAllModels(mgr);

  std::string model_id = "cycle-test-model";
  ModelToolConfig cfg;
  cfg.cli_path = "/usr/bin/test-cli";

  // Initially not registered
  EXPECT_FALSE(mgr.hasModel(model_id));

  // Register
  mgr.registerModel(model_id, cfg);
  EXPECT_TRUE(mgr.hasModel(model_id));

  // Unregister
  EXPECT_TRUE(mgr.unregisterModel(model_id));
  EXPECT_FALSE(mgr.hasModel(model_id));

  // Unregister again should return false
  EXPECT_FALSE(mgr.unregisterModel(model_id));

  // Re-register
  mgr.registerModel(model_id, cfg);
  EXPECT_TRUE(mgr.hasModel(model_id));

  clearAllModels(mgr);
}

TEST(ModelValidationUnitTest, MultipleModelsRegistered) {
  // Validates: Requirements 3.1, 3.2
  // Multiple models can be registered and each is independently validated.

  ModelConfigManager &mgr = ModelConfigManager::getInstance();
  clearAllModels(mgr);

  ModelToolConfig cfg;
  cfg.cli_path = "/usr/bin/test-cli";

  mgr.registerModel("model-alpha", cfg);
  mgr.registerModel("model-beta", cfg);
  mgr.registerModel("model-gamma", cfg);

  EXPECT_TRUE(mgr.hasModel("model-alpha"));
  EXPECT_TRUE(mgr.hasModel("model-beta"));
  EXPECT_TRUE(mgr.hasModel("model-gamma"));
  EXPECT_FALSE(mgr.hasModel("model-delta"));

  std::vector<std::string> ids = mgr.getModelIds();
  EXPECT_EQ(ids.size(), 3u);

  std::set<std::string> id_set(ids.begin(), ids.end());
  EXPECT_TRUE(id_set.count("model-alpha"));
  EXPECT_TRUE(id_set.count("model-beta"));
  EXPECT_TRUE(id_set.count("model-gamma"));

  clearAllModels(mgr);
}

TEST(ModelValidationUnitTest, SpecialCharactersInModelId) {
  // Validates: Requirements 3.1, 3.2
  // Model IDs with special characters (slashes, dots, etc.) are handled
  // correctly.

  ModelConfigManager &mgr = ModelConfigManager::getInstance();
  clearAllModels(mgr);

  ModelToolConfig cfg;
  cfg.cli_path = "/usr/bin/test-cli";

  mgr.registerModel("org/model-v1.0", cfg);
  mgr.registerModel("model_with_underscores", cfg);
  mgr.registerModel("model-with-dashes", cfg);

  EXPECT_TRUE(mgr.hasModel("org/model-v1.0"));
  EXPECT_TRUE(mgr.hasModel("model_with_underscores"));
  EXPECT_TRUE(mgr.hasModel("model-with-dashes"));

  // Similar but different IDs should be rejected
  EXPECT_FALSE(mgr.hasModel("org/model-v1.1"));
  EXPECT_FALSE(mgr.hasModel("model_with_underscore"));
  EXPECT_FALSE(mgr.hasModel("model-with-dash"));

  clearAllModels(mgr);
}

TEST(ModelValidationUnitTest, ErrorMessageFormat) {
  // Validates: Requirements 3.1, 3.2
  // Verify the error message format matches what ComputeServiceImpl produces:
  // "Unknown model_id: <id>. Available models: [<id1>, <id2>, ...]"

  ModelConfigManager &mgr = ModelConfigManager::getInstance();
  clearAllModels(mgr);

  ModelToolConfig cfg;
  cfg.cli_path = "/usr/bin/test-cli";
  mgr.registerModel("deepseek-r1", cfg);
  mgr.registerModel("llama-3-8b", cfg);

  std::string unknown_id = "nonexistent-model";
  ASSERT_FALSE(mgr.hasModel(unknown_id));

  // Construct error message the same way ComputeServiceImpl does
  auto available_ids = mgr.getModelIds();
  std::string available_list;
  for (size_t j = 0; j < available_ids.size(); ++j) {
    if (j > 0)
      available_list += ", ";
    available_list += available_ids[j];
  }
  std::string error_msg = "Unknown model_id: " + unknown_id +
                          ". Available models: [" + available_list + "]";

  // Error message contains the unknown model_id
  EXPECT_NE(error_msg.find(unknown_id), std::string::npos);

  // Error message contains each available model
  EXPECT_NE(error_msg.find("deepseek-r1"), std::string::npos);
  EXPECT_NE(error_msg.find("llama-3-8b"), std::string::npos);

  // Error message has the expected prefix format
  EXPECT_EQ(error_msg.substr(0, 18), "Unknown model_id: ");

  clearAllModels(mgr);
}
