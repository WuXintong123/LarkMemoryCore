"""Compatibility alias for :mod:`api_server.infra.grpc_client`."""

import sys

from .infra import grpc_client as _impl


sys.modules[__name__] = _impl
