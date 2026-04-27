"""Compatibility alias for :mod:`api_server.infra.logger`."""

import sys

from .infra import logger as _impl


sys.modules[__name__] = _impl
