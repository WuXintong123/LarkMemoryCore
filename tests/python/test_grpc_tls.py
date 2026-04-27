# ===- test_grpc_tls.py -------------------------------------------------------
#
# SPDX-License-Identifier: Apache-2.0
#
# ===---------------------------------------------------------------------------
#
# Unit tests for TLS channel selection in api_server.grpc_client.ComputeClient.
#
# ===---------------------------------------------------------------------------

import os
import sys
from unittest.mock import patch, MagicMock

import pytest

_project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from api_server.grpc_client import ComputeClient


@pytest.mark.parametrize("value", ["1", "true", "yes", "on"])
def test_connect_uses_secure_channel_when_tls_enabled(value):
    env = {
        "GRPC_USE_TLS": value,
        "GRPC_CA_CERT_FILE": "",
        "GRPC_CLIENT_CERT_FILE": "",
        "GRPC_CLIENT_KEY_FILE": "",
        "GRPC_SERVER_NAME": "",
    }
    with patch.dict(os.environ, env, clear=False), \
         patch("api_server.grpc_client.grpc.secure_channel", return_value=MagicMock()) as mock_secure, \
         patch("api_server.grpc_client.grpc.ssl_channel_credentials", return_value=MagicMock()), \
         patch("api_server.grpc_client.compute_pb2_grpc.ComputeServiceStub", return_value=MagicMock()):
        client = ComputeClient("localhost:9000")
        client.connect()
        assert mock_secure.called


def test_connect_uses_insecure_channel_when_tls_disabled():
    with patch.dict(os.environ, {"GRPC_USE_TLS": "0"}, clear=False), \
         patch("api_server.grpc_client.grpc.insecure_channel", return_value=MagicMock()) as mock_insecure, \
         patch("api_server.grpc_client.compute_pb2_grpc.ComputeServiceStub", return_value=MagicMock()):
        client = ComputeClient("localhost:9000")
        client.connect()
        assert mock_insecure.called
