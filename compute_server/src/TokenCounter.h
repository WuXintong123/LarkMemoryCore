//====- TokenCounter.h ----------------------------------------------------===//
//
// SPDX-License-Identifier: Apache-2.0
//
//===----------------------------------------------------------------------===//
//
// Token counter for splitting text on whitespace and punctuation boundaries.
// Each word and each punctuation character counts as a separate token.
//
//===----------------------------------------------------------------------===//

#pragma once

#include <cstdint>
#include <string>

namespace compute {

class TokenCounter {
public:
  /// Count the number of tokens in the given text.
  ///
  /// Tokenization rules:
  /// - Whitespace characters (space, tab, newline, carriage return, etc.)
  ///   serve as separators and are not counted as tokens.
  /// - Punctuation characters each count as an independent token.
  /// - Consecutive letter/digit sequences count as one token.
  /// - Empty strings and whitespace-only strings return 0.
  static int32_t count(const std::string &text);

  /// Return true if the character is a punctuation character.
  /// Punctuation includes: . , : ; ! ? ' " ( ) [ ] { } / - @ # $ % ^ & * ~
  ///                       ` \ | < > + = _ and other non-alphanumeric,
  ///                       non-whitespace characters.
  static bool isPunctuation(char c);

  /// Return true if the character is a whitespace character.
  /// Whitespace includes: space, tab (\t), newline (\n), carriage return (\r),
  ///                      vertical tab (\v), form feed (\f).
  static bool isWhitespace(char c);
};

} // namespace compute
