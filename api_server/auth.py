"""Compatibility alias for :mod:`api_server.dependencies.auth`."""

import sys

from .dependencies import auth as _impl


sys.modules[__name__] = _impl
