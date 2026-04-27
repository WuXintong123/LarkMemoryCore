"""Compatibility alias for :mod:`api_server.domain.chat_template`."""

import sys

from .domain import chat_template as _impl


sys.modules[__name__] = _impl
