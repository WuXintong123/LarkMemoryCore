"""Compatibility alias for :mod:`api_server.domain.model_policy`."""

import sys

from .domain import model_policy as _impl


sys.modules[__name__] = _impl
