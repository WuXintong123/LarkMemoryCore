//====- TokenCounter.cpp --------------------------------------------------===//
//
// SPDX-License-Identifier: Apache-2.0
//
//===----------------------------------------------------------------------===//
//
// This file implements the token counter for splitting text on whitespace
// and punctuation boundaries.
//
//===----------------------------------------------------------------------===//

#include "TokenCounter.h"

#include <cctype>

namespace compute {

int32_t TokenCounter::count(const std::string &text) {
  int32_t token_count = 0;
  bool in_word = false;

  for (size_t i = 0; i < text.size(); ++i) {
    char c = text[i];

    if (isWhitespace(c)) {
      // Whitespace terminates any current word token.
      if (in_word) {
        in_word = false;
      }
      // Whitespace itself is not a token; skip it.
    } else if (isPunctuation(c)) {
      // If we were accumulating a word, that word is now complete.
      if (in_word) {
        in_word = false;
      }
      // Each punctuation character is an independent token.
      token_count++;
    } else {
      // Alphanumeric or other non-whitespace, non-punctuation character.
      // Start or continue a word token.
      if (!in_word) {
        in_word = true;
        token_count++;
      }
    }
  }

  return token_count;
}

bool TokenCounter::isPunctuation(char c) {
  // A character is punctuation if it is not alphanumeric and not whitespace.
  // This covers: . , : ; ! ? ' " ( ) [ ] { } / - @ # $ % ^ & * ~ ` \ | < > + = _
  // and any other printable non-alphanumeric, non-whitespace characters.
  if (isWhitespace(c)) {
    return false;
  }
  if (std::isalnum(static_cast<unsigned char>(c))) {
    return false;
  }
  // Non-printable control characters (other than whitespace) are not
  // considered punctuation — they are treated as separators like whitespace.
  // However, per the design spec, only whitespace is a separator, and
  // punctuation is "each count as an independent token". Characters that are
  // not alphanumeric and not whitespace fall into the punctuation category.
  return true;
}

bool TokenCounter::isWhitespace(char c) {
  // Standard C/C++ whitespace characters.
  return c == ' ' || c == '\t' || c == '\n' || c == '\r' || c == '\v' ||
         c == '\f';
}

} // namespace compute
