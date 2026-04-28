#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
source "${SCRIPT_DIR}/common.sh"

require_systemd_user
require_systemd_user_linger
require_repo_env

cd "${REPO_ROOT}"

REPO_ROOT="${REPO_ROOT}" "${LARK_MEMORY_CORE_PYTHON_BIN}" - <<'PY'
import json
import os
import requests
import subprocess
import sys
import time
from pathlib import Path

repo_root = Path(os.environ["REPO_ROOT"])
sys.path.insert(0, str(repo_root))

env_values = {}
for raw_line in (repo_root / ".env").read_text(encoding="utf-8").splitlines():
    line = raw_line.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    key, value = line.split("=", 1)
    env_values[key.strip()] = value.strip().strip("'\"")

client_key_path = Path.home() / ".config/lark-memory-core/credentials/client_api_key.txt"
admin_key_path = Path.home() / ".config/lark-memory-core/credentials/admin_api_key.txt"
if client_key_path.exists():
    client_key = client_key_path.read_text(encoding="utf-8").strip()
else:
    client_key = ""
if admin_key_path.exists():
    admin_key = admin_key_path.read_text(encoding="utf-8").strip()
else:
    admin_key = ""

base_url = os.getenv("BASE_URL", "http://127.0.0.1:8000")
https_url = os.getenv("HTTPS_URL", "https://127.0.0.1:18443")

def curl_json(args):
    return subprocess.check_output(["curl", "-fsS", *args], text=True)

def request_headers(extra: dict[str, str] | None = None) -> dict[str, str]:
    headers: dict[str, str] = {}
    if client_key:
        headers["Authorization"] = f"Bearer {client_key}"
    if extra:
        headers.update(extra)
    return headers

def wait_http(url: str, attempts: int = 30, delay_s: float = 1.0) -> None:
    last_error = None
    for _ in range(attempts):
        proc = subprocess.run(
            ["curl", "-fsS", "--max-time", "5", url],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode == 0:
            return
        last_error = proc.stderr.strip() or proc.stdout.strip() or f"curl exited with {proc.returncode}"
        time.sleep(delay_s)
    raise RuntimeError(f"timed out waiting for {url}: {last_error}")

wait_http(f"{base_url}/health")

print("health=", curl_json([f"{base_url}/health"]).strip())
print("ready=", curl_json([f"{base_url}/ready"]).strip())
if https_url:
    print("health_https=", subprocess.check_output(["curl", "-kfsS", f"{https_url}/health"], text=True).strip())

if client_key:
    models = json.loads(curl_json([f"{base_url}/v1/models", "-H", f"Authorization: Bearer {client_key}"]))
else:
    models = json.loads(curl_json([f"{base_url}/v1/models"]))
assert models["data"], "models list is empty"
model_id = models["data"][0]["id"]
assert "lark_memory_core" in models["data"][0], "model capability extension missing"

if client_key:
    model_detail = json.loads(curl_json([f"{base_url}/v1/models/{model_id}", "-H", f"Authorization: Bearer {client_key}"]))
else:
    model_detail = json.loads(curl_json([f"{base_url}/v1/models/{model_id}"]))
assert model_detail["id"] == model_id, "model detail endpoint returned wrong model"
assert "supported_parameters" in model_detail["lark_memory_core"], "model detail capability metadata missing"

if admin_key:
    metrics = json.loads(curl_json([f"{base_url}/v1/admin/metrics", "-H", f"Authorization: Bearer {admin_key}"]))
    for key in [
        "rejected_requests",
        "queued_requests",
        "active_compute_slots",
        "max_compute_slots",
        "overload_rejections",
        "watchdog_timeouts",
        "partial_timeout_returns",
        "request_cancellations",
        "auth_failures",
    ]:
        assert key in metrics, f"missing metrics field: {key}"
    metrics_text = subprocess.check_output(
        ["curl", "-fsS", f"{base_url}/metrics", "-H", f"Authorization: Bearer {admin_key}"],
        text=True,
    )
    assert "lark_memory_core_total_requests" in metrics_text, "prometheus metrics missing expected counter"
    backends = json.loads(curl_json([f"{base_url}/v1/admin/backends", "-H", f"Authorization: Bearer {admin_key}"]))
    assert backends["data"], "backend list is empty"
    for backend in backends["data"]:
        assert backend.get("node_id"), "backend node_id missing"
        assert backend.get("grpc_target"), "backend grpc_target missing"
        assert "healthy" in backend, "backend health field missing"
        assert backend["healthy"] is True, (
            f"backend is not healthy: {backend.get('node_id') or backend.get('id')}"
        )
        assert "routes" in backend, "backend route inventory missing"

headers = ["-H", "Content-Type: application/json"]
if client_key:
    headers.extend(["-H", f"Authorization: Bearer {client_key}"])

chat_payload = json.dumps({
    "model": model_id,
    "messages": [{"role": "user", "content": "Say READY only."}],
    "max_tokens": 16,
    "temperature": 0.0,
})
chat_proc = subprocess.run(
    ["curl", "-fsS", "-D", "-", f"{base_url}/v1/chat/completions", *headers, "-d", chat_payload],
    capture_output=True,
    text=True,
    check=True,
)
if "\r\n\r\n" in chat_proc.stdout:
    raw_headers, raw_body = chat_proc.stdout.split("\r\n\r\n", 1)
else:
    raw_headers, raw_body = chat_proc.stdout.split("\n\n", 1)
assert "X-Request-Id:" in raw_headers or "x-request-id:" in raw_headers.lower(), "X-Request-Id header missing"
chat = json.loads(raw_body)
assert chat["choices"], "chat completions returned no choices"

stream_payload = json.dumps({
    "model": model_id,
    "messages": [{"role": "user", "content": "Say READY only."}],
    "max_tokens": 4,
    "stream": True,
})
with requests.post(
    f"{base_url}/v1/chat/completions",
    headers=request_headers({"Content-Type": "application/json"}),
    data=stream_payload,
    stream=True,
    timeout=(5, 15),
) as stream_response:
    assert stream_response.status_code == 200, (
        f"streaming response failed with status {stream_response.status_code}: {stream_response.text}"
    )
    content_type = stream_response.headers.get("Content-Type", "")
    assert "text/event-stream" in content_type, (
        f"streaming response returned unexpected content type: {content_type}"
    )
    assert stream_response.headers.get("X-Request-Id"), "streaming response missing X-Request-Id"

if admin_key:
    cancel_stream_payload = {
        "model": model_id,
        "messages": [{"role": "user", "content": "Write a very long answer about arithmetic and keep elaborating."}],
        "max_tokens": 256,
        "temperature": 0.0,
        "stream": True,
    }
    with requests.post(
        f"{base_url}/v1/chat/completions",
        headers=request_headers({"Content-Type": "application/json"}),
        json=cancel_stream_payload,
        stream=True,
        timeout=(5, 30),
    ) as cancel_stream_response:
        assert cancel_stream_response.status_code == 200, (
            f"cancel stream setup failed with status {cancel_stream_response.status_code}"
        )
        request_id = cancel_stream_response.headers.get("X-Request-Id", "").strip()
        assert request_id, "streaming response did not return X-Request-Id before cancellation"

        cancel_result = requests.post(
            f"{base_url}/v1/admin/cancel",
            headers={
                "Authorization": f"Bearer {admin_key}",
                "Content-Type": "application/json",
            },
            json={"request_id": request_id},
            timeout=10,
        )
        assert cancel_result.status_code == 200, (
            f"admin cancel endpoint failed with status {cancel_result.status_code}: {cancel_result.text}"
        )
        cancel_response = cancel_result.json()
        assert cancel_response["success"] is True, "admin cancel endpoint returned false"

rate_limit_rpm = int(env_values.get("RATE_LIMIT_RPM", "0") or "0")
smoke_check_rate_limit = str(os.getenv("SMOKE_CHECK_RATE_LIMIT", "0")).strip().lower() in {"1", "true", "yes", "on"}
if smoke_check_rate_limit and client_key and rate_limit_rpm > 0:
    invalid_payload = json.dumps({"model": model_id, "messages": []})
    seen_429 = False
    for _ in range(rate_limit_rpm + 5):
        proc = subprocess.run(
            ["curl", "-sS", "-o", "/tmp/lark-memory-core-rate-limit.json", "-w", "%{http_code}", f"{base_url}/v1/chat/completions", *headers, "-d", invalid_payload],
            capture_output=True,
            text=True,
        )
        if proc.stdout.strip() == "429":
            seen_429 = True
            break
    assert seen_429, "rate limit was not triggered"

print("smoke_prod: ok")
PY
