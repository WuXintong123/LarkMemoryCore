//====- test_token_counter.cpp ---------------------------------------------===//
//
// SPDX-License-Identifier: Apache-2.0
//
//===----------------------------------------------------------------------===//
//
// Property-based tests and unit tests for TokenCounter.
//
// Feature: serving-framework-enhancement, Property 11: Token counting correctness
// Validates: Requirements 7.1, 7.4, 7.5
//
// Compile with:
//   g++ -std=c++17 -I../../compute_server/src \
//       test_token_counter.cpp ../../compute_server/src/TokenCounter.cpp \
//       -lgtest -lgtest_main -pthread -o test_token_counter
//
//===----------------------------------------------------------------------===//

#include "TokenCounter.h"

#include <gtest/gtest.h>

#include <algorithm>
#include <cctype>
#include <random>
#include <string>
#include <vector>

using compute::TokenCounter;

// ---------------------------------------------------------------------------
// Reference implementation for property-based testing
// ---------------------------------------------------------------------------

/// Independent reference implementation of token counting.
/// Counts the number of maximal non-whitespace, non-punctuation character
/// sequences (words) plus the number of punctuation characters.
/// Uses the same classification rules as TokenCounter but is implemented
/// with a different algorithm (two-pass) to serve as an oracle.
static int32_t referenceTokenCount(const std::string &text) {
  int32_t count = 0;

  size_t i = 0;
  while (i < text.size()) {
    char c = text[i];

    // Classify the character using the same rules as TokenCounter:
    // whitespace = separator (not counted)
    // punctuation = non-alphanumeric, non-whitespace => each is 1 token
    // alphanumeric = part of a word token

    bool ws = (c == ' ' || c == '\t' || c == '\n' || c == '\r' || c == '\v' ||
               c == '\f');
    if (ws) {
      // Skip whitespace — not a token.
      ++i;
      continue;
    }

    bool alnum = std::isalnum(static_cast<unsigned char>(c)) != 0;
    if (!alnum) {
      // Punctuation character — each one is an independent token.
      ++count;
      ++i;
      continue;
    }

    // Start of a word (maximal alphanumeric sequence).
    ++count;
    ++i;
    while (i < text.size()) {
      char next = text[i];
      bool next_ws = (next == ' ' || next == '\t' || next == '\n' ||
                      next == '\r' || next == '\v' || next == '\f');
      bool next_alnum = std::isalnum(static_cast<unsigned char>(next)) != 0;
      if (next_ws || !next_alnum) {
        break; // End of word.
      }
      ++i;
    }
  }

  return count;
}

// ---------------------------------------------------------------------------
// Random string generators
// ---------------------------------------------------------------------------

/// Generate a random string of length [0, maxLen] containing a mix of
/// alphanumeric characters, whitespace, and punctuation.
static std::string generateRandomString(std::mt19937 &rng,
                                        size_t maxLen = 200) {
  // Character palette: alphanumeric + whitespace + punctuation
  static const char kAlphaNum[] =
      "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789";
  static const char kWhitespace[] = " \t\n\r\v\f";
  static const char kPunctuation[] = ".,;:!?'\"()[]{}/-@#$%^&*~`\\|<>+=_";

  std::uniform_int_distribution<size_t> lenDist(0, maxLen);
  size_t len = lenDist(rng);

  std::string result;
  result.reserve(len);

  // Weighted distribution: ~50% alphanumeric, ~20% whitespace, ~30% punctuation
  // This ensures good coverage of all character classes.
  std::uniform_int_distribution<int> classDist(0, 9);

  for (size_t i = 0; i < len; ++i) {
    int cls = classDist(rng);
    if (cls < 5) {
      // Alphanumeric (50%)
      std::uniform_int_distribution<size_t> idx(0, sizeof(kAlphaNum) - 2);
      result.push_back(kAlphaNum[idx(rng)]);
    } else if (cls < 7) {
      // Whitespace (20%)
      std::uniform_int_distribution<size_t> idx(0, sizeof(kWhitespace) - 2);
      result.push_back(kWhitespace[idx(rng)]);
    } else {
      // Punctuation (30%)
      std::uniform_int_distribution<size_t> idx(0, sizeof(kPunctuation) - 2);
      result.push_back(kPunctuation[idx(rng)]);
    }
  }

  return result;
}

/// Generate a string composed entirely of whitespace characters.
static std::string generateWhitespaceString(std::mt19937 &rng,
                                            size_t maxLen = 50) {
  static const char kWhitespace[] = " \t\n\r\v\f";
  std::uniform_int_distribution<size_t> lenDist(0, maxLen);
  size_t len = lenDist(rng);

  std::string result;
  result.reserve(len);
  std::uniform_int_distribution<size_t> idx(0, sizeof(kWhitespace) - 2);
  for (size_t i = 0; i < len; ++i) {
    result.push_back(kWhitespace[idx(rng)]);
  }
  return result;
}

/// Generate a string composed entirely of alphanumeric characters.
static std::string generateAlphanumString(std::mt19937 &rng,
                                          size_t maxLen = 50) {
  static const char kAlphaNum[] =
      "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789";
  std::uniform_int_distribution<size_t> lenDist(1, maxLen);
  size_t len = lenDist(rng);

  std::string result;
  result.reserve(len);
  std::uniform_int_distribution<size_t> idx(0, sizeof(kAlphaNum) - 2);
  for (size_t i = 0; i < len; ++i) {
    result.push_back(kAlphaNum[idx(rng)]);
  }
  return result;
}

/// Generate a string composed entirely of punctuation characters.
static std::string generatePunctuationString(std::mt19937 &rng,
                                             size_t maxLen = 50) {
  static const char kPunctuation[] = ".,;:!?'\"()[]{}/-@#$%^&*~`\\|<>+=_";
  std::uniform_int_distribution<size_t> lenDist(1, maxLen);
  size_t len = lenDist(rng);

  std::string result;
  result.reserve(len);
  std::uniform_int_distribution<size_t> idx(0, sizeof(kPunctuation) - 2);
  for (size_t i = 0; i < len; ++i) {
    result.push_back(kPunctuation[idx(rng)]);
  }
  return result;
}

// ===========================================================================
// Property-Based Tests
// ===========================================================================

TEST(TokenCounterPropertyTest, Property11_TokenCountingCorrectness) {
  // Feature: serving-framework-enhancement, Property 11: Token counting correctness
  // Validates: Requirements 7.1, 7.4, 7.5
  //
  // For any input string, the Token_Counter SHALL return a count equal to
  // the number of maximal non-whitespace, non-punctuation character sequences
  // plus the number of punctuation characters in the string.
  // Empty strings and whitespace-only strings SHALL return 0.

  std::mt19937 rng(42);

  for (int i = 0; i < 100; ++i) {
    std::string input = generateRandomString(rng);
    int32_t actual = TokenCounter::count(input);
    int32_t expected = referenceTokenCount(input);
    EXPECT_EQ(actual, expected)
        << "Mismatch on iteration " << i << " for input: \"" << input << "\""
        << "\n  actual=" << actual << " expected=" << expected;
  }
}

TEST(TokenCounterPropertyTest, Property11_WhitespaceOnlyStringsReturnZero) {
  // Feature: serving-framework-enhancement, Property 11: Token counting correctness
  // Validates: Requirements 7.4, 7.5
  //
  // Whitespace-only strings SHALL return 0.

  std::mt19937 rng(123);

  for (int i = 0; i < 100; ++i) {
    std::string input = generateWhitespaceString(rng);
    int32_t actual = TokenCounter::count(input);
    EXPECT_EQ(actual, 0)
        << "Whitespace-only string should return 0, got " << actual
        << " for input of length " << input.size();
  }
}

TEST(TokenCounterPropertyTest, Property11_PureAlphanumIsOneToken) {
  // Feature: serving-framework-enhancement, Property 11: Token counting correctness
  // Validates: Requirements 7.1
  //
  // A string of only alphanumeric characters (no whitespace, no punctuation)
  // is a single maximal word sequence => count == 1.

  std::mt19937 rng(456);

  for (int i = 0; i < 100; ++i) {
    std::string input = generateAlphanumString(rng);
    int32_t actual = TokenCounter::count(input);
    EXPECT_EQ(actual, 1)
        << "Pure alphanumeric string should be 1 token, got " << actual
        << " for input: \"" << input << "\"";
  }
}

TEST(TokenCounterPropertyTest, Property11_PurePunctuationCountEqualsLength) {
  // Feature: serving-framework-enhancement, Property 11: Token counting correctness
  // Validates: Requirements 7.1
  //
  // A string of only punctuation characters has each character as an
  // independent token => count == string length.

  std::mt19937 rng(789);

  for (int i = 0; i < 100; ++i) {
    std::string input = generatePunctuationString(rng);
    int32_t actual = TokenCounter::count(input);
    int32_t expected = static_cast<int32_t>(input.size());
    EXPECT_EQ(actual, expected)
        << "Pure punctuation string should have count == length, got "
        << actual << " for input of length " << input.size();
  }
}

TEST(TokenCounterPropertyTest, Property11_CountIsNonNegative) {
  // Feature: serving-framework-enhancement, Property 11: Token counting correctness
  // Validates: Requirements 7.1, 7.4, 7.5
  //
  // For any input string, the token count SHALL be non-negative.

  std::mt19937 rng(1001);

  for (int i = 0; i < 100; ++i) {
    std::string input = generateRandomString(rng);
    int32_t actual = TokenCounter::count(input);
    EXPECT_GE(actual, 0)
        << "Token count should be non-negative, got " << actual;
  }
}

// ===========================================================================
// Unit Tests — Specific Edge Cases
// ===========================================================================

TEST(TokenCounterUnitTest, EmptyString) {
  // Validates: Requirements 7.4
  EXPECT_EQ(TokenCounter::count(""), 0);
}

TEST(TokenCounterUnitTest, SingleSpace) {
  // Validates: Requirements 7.5
  EXPECT_EQ(TokenCounter::count(" "), 0);
}

TEST(TokenCounterUnitTest, MultipleSpaces) {
  // Validates: Requirements 7.5
  EXPECT_EQ(TokenCounter::count("     "), 0);
}

TEST(TokenCounterUnitTest, TabsAndNewlines) {
  // Validates: Requirements 7.5
  EXPECT_EQ(TokenCounter::count("\t\n\r\v\f"), 0);
}

TEST(TokenCounterUnitTest, SingleWord) {
  // Validates: Requirements 7.1
  EXPECT_EQ(TokenCounter::count("hello"), 1);
}

TEST(TokenCounterUnitTest, TwoWordsSpaceSeparated) {
  // Validates: Requirements 7.1
  EXPECT_EQ(TokenCounter::count("hello world"), 2);
}

TEST(TokenCounterUnitTest, MultipleWordsMultipleSpaces) {
  // Validates: Requirements 7.1
  EXPECT_EQ(TokenCounter::count("  hello   world  "), 2);
}

TEST(TokenCounterUnitTest, SinglePunctuation) {
  // Validates: Requirements 7.1
  EXPECT_EQ(TokenCounter::count("."), 1);
}

TEST(TokenCounterUnitTest, MultiplePunctuation) {
  // Validates: Requirements 7.1
  // "..." => 3 punctuation tokens
  EXPECT_EQ(TokenCounter::count("..."), 3);
}

TEST(TokenCounterUnitTest, WordFollowedByPunctuation) {
  // Validates: Requirements 7.1
  // "hello." => "hello" (1 word) + "." (1 punct) = 2
  EXPECT_EQ(TokenCounter::count("hello."), 2);
}

TEST(TokenCounterUnitTest, PunctuationFollowedByWord) {
  // Validates: Requirements 7.1
  // ".hello" => "." (1 punct) + "hello" (1 word) = 2
  EXPECT_EQ(TokenCounter::count(".hello"), 2);
}

TEST(TokenCounterUnitTest, WordPunctuationWord) {
  // Validates: Requirements 7.1
  // "hello,world" => "hello" (1) + "," (1) + "world" (1) = 3
  EXPECT_EQ(TokenCounter::count("hello,world"), 3);
}

TEST(TokenCounterUnitTest, SentenceWithPunctuation) {
  // Validates: Requirements 7.1
  // "Hello, world!" => "Hello" (1) + "," (1) + "world" (1) + "!" (1) = 4
  EXPECT_EQ(TokenCounter::count("Hello, world!"), 4);
}

TEST(TokenCounterUnitTest, ComplexSentence) {
  // Validates: Requirements 7.1
  // "It's a test." => "It" (1) + "'" (1) + "s" (1) + "a" (1) + "test" (1) + "." (1) = 6
  EXPECT_EQ(TokenCounter::count("It's a test."), 6);
}

TEST(TokenCounterUnitTest, NumbersAreAlphanumeric) {
  // Validates: Requirements 7.1
  // "abc123" => single word token
  EXPECT_EQ(TokenCounter::count("abc123"), 1);
}

TEST(TokenCounterUnitTest, NumbersWithPunctuation) {
  // Validates: Requirements 7.1
  // "3.14" => "3" (1) + "." (1) + "14" (1) = 3
  EXPECT_EQ(TokenCounter::count("3.14"), 3);
}

TEST(TokenCounterUnitTest, MixedWhitespaceTypes) {
  // Validates: Requirements 7.1, 7.5
  // "a\tb\nc" => "a" (1) + "b" (1) + "c" (1) = 3
  EXPECT_EQ(TokenCounter::count("a\tb\nc"), 3);
}

TEST(TokenCounterUnitTest, LeadingAndTrailingWhitespace) {
  // Validates: Requirements 7.1
  EXPECT_EQ(TokenCounter::count("  hello  "), 1);
}

TEST(TokenCounterUnitTest, OnlyDigits) {
  // Validates: Requirements 7.1
  EXPECT_EQ(TokenCounter::count("12345"), 1);
}

TEST(TokenCounterUnitTest, SpecialPunctuationCharacters) {
  // Validates: Requirements 7.1
  // "@#$%^&*" => 7 punctuation tokens
  EXPECT_EQ(TokenCounter::count("@#$%^&*"), 7);
}

TEST(TokenCounterUnitTest, BracketsAndParens) {
  // Validates: Requirements 7.1
  // "(a)" => "(" (1) + "a" (1) + ")" (1) = 3
  EXPECT_EQ(TokenCounter::count("(a)"), 3);
}

TEST(TokenCounterUnitTest, PathLikeString) {
  // Validates: Requirements 7.1
  // "/usr/bin/test" => "/" (1) + "usr" (1) + "/" (1) + "bin" (1) + "/" (1) + "test" (1) = 6
  EXPECT_EQ(TokenCounter::count("/usr/bin/test"), 6);
}

TEST(TokenCounterUnitTest, EmailLikeString) {
  // Validates: Requirements 7.1
  // "user@host.com" => "user" (1) + "@" (1) + "host" (1) + "." (1) + "com" (1) = 5
  EXPECT_EQ(TokenCounter::count("user@host.com"), 5);
}

// ===========================================================================
// Helper method tests
// ===========================================================================

TEST(TokenCounterHelperTest, IsPunctuation) {
  // Punctuation characters
  EXPECT_TRUE(TokenCounter::isPunctuation('.'));
  EXPECT_TRUE(TokenCounter::isPunctuation(','));
  EXPECT_TRUE(TokenCounter::isPunctuation('!'));
  EXPECT_TRUE(TokenCounter::isPunctuation('?'));
  EXPECT_TRUE(TokenCounter::isPunctuation('@'));
  EXPECT_TRUE(TokenCounter::isPunctuation('#'));
  EXPECT_TRUE(TokenCounter::isPunctuation('('));
  EXPECT_TRUE(TokenCounter::isPunctuation(')'));
  EXPECT_TRUE(TokenCounter::isPunctuation('-'));
  EXPECT_TRUE(TokenCounter::isPunctuation('_'));

  // Non-punctuation
  EXPECT_FALSE(TokenCounter::isPunctuation('a'));
  EXPECT_FALSE(TokenCounter::isPunctuation('Z'));
  EXPECT_FALSE(TokenCounter::isPunctuation('0'));
  EXPECT_FALSE(TokenCounter::isPunctuation('9'));
  EXPECT_FALSE(TokenCounter::isPunctuation(' '));
  EXPECT_FALSE(TokenCounter::isPunctuation('\t'));
  EXPECT_FALSE(TokenCounter::isPunctuation('\n'));
}

TEST(TokenCounterHelperTest, IsWhitespace) {
  // Whitespace characters
  EXPECT_TRUE(TokenCounter::isWhitespace(' '));
  EXPECT_TRUE(TokenCounter::isWhitespace('\t'));
  EXPECT_TRUE(TokenCounter::isWhitespace('\n'));
  EXPECT_TRUE(TokenCounter::isWhitespace('\r'));
  EXPECT_TRUE(TokenCounter::isWhitespace('\v'));
  EXPECT_TRUE(TokenCounter::isWhitespace('\f'));

  // Non-whitespace
  EXPECT_FALSE(TokenCounter::isWhitespace('a'));
  EXPECT_FALSE(TokenCounter::isWhitespace('0'));
  EXPECT_FALSE(TokenCounter::isWhitespace('.'));
  EXPECT_FALSE(TokenCounter::isWhitespace('!'));
}

// ===========================================================================
// Property 12: Token count chunk accumulation
// ===========================================================================

/// Split a string into random contiguous chunks at token boundaries only.
/// A token boundary is a position where the character class changes between
/// whitespace, punctuation, and alphanumeric, or between consecutive
/// punctuation characters (each punct is its own token).
/// Returns a vector of substrings whose concatenation equals the original.
static std::vector<size_t> findTokenBoundaries(const std::string &text) {
  std::vector<size_t> boundaries;
  boundaries.push_back(0);

  for (size_t j = 1; j < text.size(); ++j) {
    char prev = text[j - 1];
    char curr = text[j];

    bool prev_ws = TokenCounter::isWhitespace(prev);
    bool curr_ws = TokenCounter::isWhitespace(curr);
    bool prev_punct = TokenCounter::isPunctuation(prev);
    bool curr_punct = TokenCounter::isPunctuation(curr);
    bool prev_alnum = std::isalnum(static_cast<unsigned char>(prev)) != 0;
    bool curr_alnum = std::isalnum(static_cast<unsigned char>(curr)) != 0;

    // A token boundary exists when the character class changes.
    bool boundary = false;
    if (prev_ws != curr_ws || prev_punct != curr_punct ||
        prev_alnum != curr_alnum) {
      boundary = true;
    }
    // Between two consecutive punctuation chars is always a boundary
    // (each punct is a separate token).
    if (prev_punct && curr_punct) {
      boundary = true;
    }

    if (boundary) {
      boundaries.push_back(j);
    }
  }

  boundaries.push_back(text.size());

  // Remove duplicates and sort.
  std::sort(boundaries.begin(), boundaries.end());
  boundaries.erase(std::unique(boundaries.begin(), boundaries.end()),
                   boundaries.end());

  return boundaries;
}

/// Split a string into random contiguous chunks at arbitrary positions.
/// Returns a vector of substrings whose concatenation equals the original.
static std::vector<std::string> splitIntoRandomChunks(const std::string &text,
                                                      std::mt19937 &rng) {
  std::vector<std::string> chunks;
  if (text.empty()) {
    chunks.push_back("");
    return chunks;
  }

  size_t pos = 0;
  while (pos < text.size()) {
    // Choose a random chunk length from 1 to remaining length.
    std::uniform_int_distribution<size_t> lenDist(1, text.size() - pos);
    size_t chunkLen = lenDist(rng);
    chunks.push_back(text.substr(pos, chunkLen));
    pos += chunkLen;
  }

  return chunks;
}

/// Split a string into random contiguous chunks, but only at token boundary
/// positions. Returns a vector of substrings whose concatenation equals the
/// original.
static std::vector<std::string>
splitAtTokenBoundaries(const std::string &text, std::mt19937 &rng) {
  std::vector<std::string> chunks;
  if (text.empty()) {
    chunks.push_back("");
    return chunks;
  }

  std::vector<size_t> boundaries = findTokenBoundaries(text);
  if (boundaries.size() < 2) {
    chunks.push_back(text);
    return chunks;
  }

  // Select a random subset of boundary positions to create chunks.
  // Always include 0 and text.size().
  std::vector<size_t> split_points;
  split_points.push_back(0);
  for (size_t j = 1; j < boundaries.size() - 1; ++j) {
    // Include each boundary with 50% probability.
    std::uniform_int_distribution<int> coin(0, 1);
    if (coin(rng)) {
      split_points.push_back(boundaries[j]);
    }
  }
  split_points.push_back(text.size());

  // Build chunks from split points.
  for (size_t j = 0; j + 1 < split_points.size(); ++j) {
    chunks.push_back(
        text.substr(split_points[j], split_points[j + 1] - split_points[j]));
  }

  return chunks;
}

TEST(TokenCounterPropertyTest, Property12_TokenCountChunkAccumulation) {
  // Feature: serving-framework-enhancement, Property 12: Token count chunk accumulation
  // Validates: Requirements 7.3
  //
  // Refined property statement:
  // For any string that is split into a sequence of contiguous substrings
  // (chunks) where each split occurs at a token boundary (a position where
  // the character class changes between whitespace, punctuation, or
  // alphanumeric), the sum of Token_Counter.count() applied to each chunk
  // SHALL equal Token_Counter.count() applied to the concatenation of all
  // chunks.
  //
  // Rationale: In the real streaming use case (Requirement 7.3), the compute
  // server streams text at natural boundaries (words/lines from subprocess
  // output), not at arbitrary byte positions within words.

  std::mt19937 rng(2025);

  for (int i = 0; i < 100; ++i) {
    std::string input = generateRandomString(rng);
    if (input.empty())
      continue;

    // Split only at token boundaries.
    std::vector<std::string> chunks = splitAtTokenBoundaries(input, rng);

    // Verify chunks concatenate back to the original.
    std::string reconstructed;
    for (const auto &chunk : chunks) {
      reconstructed += chunk;
    }
    ASSERT_EQ(reconstructed, input)
        << "Bug in splitAtTokenBoundaries: reconstruction mismatch";

    // The property SHALL hold for token-boundary splits.
    int32_t chunk_sum = 0;
    for (const auto &chunk : chunks) {
      chunk_sum += TokenCounter::count(chunk);
    }
    int32_t total_count = TokenCounter::count(input);

    EXPECT_EQ(chunk_sum, total_count)
        << "Token-boundary split should preserve count on iteration " << i
        << "\n  input: \"" << input << "\""
        << "\n  total_count=" << total_count << " chunk_sum=" << chunk_sum;
  }
}

TEST(TokenCounterPropertyTest,
     Property12_ArbitrarySplitYieldsGreaterOrEqualCount) {
  // Feature: serving-framework-enhancement, Property 12: Token count chunk accumulation
  // Validates: Requirements 7.3
  //
  // Supplementary property: For any arbitrary split of a string into
  // contiguous chunks, the sum of chunk counts is always >= the full string
  // count. This is because splitting within a word can only create additional
  // tokens, never fewer. This confirms the monotonicity invariant.

  std::mt19937 rng(2024);

  for (int i = 0; i < 100; ++i) {
    std::string input = generateRandomString(rng);
    std::vector<std::string> chunks = splitIntoRandomChunks(input, rng);

    // Verify chunks concatenate back to the original.
    std::string reconstructed;
    for (const auto &chunk : chunks) {
      reconstructed += chunk;
    }
    ASSERT_EQ(reconstructed, input)
        << "Bug in splitIntoRandomChunks: reconstruction mismatch";

    // Sum token counts of individual chunks.
    int32_t chunk_sum = 0;
    for (const auto &chunk : chunks) {
      chunk_sum += TokenCounter::count(chunk);
    }

    // Token count of the full string.
    int32_t total_count = TokenCounter::count(input);

    // Arbitrary splits can only increase the count (or keep it equal),
    // never decrease it.
    EXPECT_GE(chunk_sum, total_count)
        << "Arbitrary split chunk sum should be >= full count on iteration "
        << i << "\n  input: \"" << input << "\""
        << "\n  total_count=" << total_count << " chunk_sum=" << chunk_sum;
  }
}
