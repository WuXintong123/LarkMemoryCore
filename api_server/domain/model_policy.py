# ===- model_policy.py --------------------------------------------------------
#
# SPDX-License-Identifier: Apache-2.0
#
# ===---------------------------------------------------------------------------
#
# Internal model-serving policy helpers.
#
# ===---------------------------------------------------------------------------

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, Optional


VALID_API_MODES = {"chat", "completion", "both"}
VALID_PROMPT_STYLES = {"buddy_deepseek_r1", "chatml", "raw_completion"}
SUPPORTED_INFERENCE_PARAMETERS = (
    "temperature",
    "max_tokens",
    "top_p",
    "top_k",
    "repetition_penalty",
    "seed",
    "stream",
)
UNSUPPORTED_INFERENCE_PARAMETERS = (
    "frequency_penalty",
    "presence_penalty",
    "stop",
)


@dataclass(frozen=True)
class ModelServingPolicy:
    api_mode: str = "both"
    prompt_style: str = "chatml"
    default_max_tokens: int = 0
    max_max_tokens: int = 0
    max_input_chars: int = 0
    request_timeout_ms: int = 0
    stream_idle_timeout_s: int = 0
    allow_anonymous_models: bool = False

    @classmethod
    def from_payload(cls, payload: Optional[Dict[str, Any]]) -> "ModelServingPolicy":
        if not isinstance(payload, dict):
            return cls()

        api_mode = str(payload.get("api_mode", "both") or "both").strip().lower()
        if api_mode not in VALID_API_MODES:
            api_mode = "both"

        prompt_style = str(payload.get("prompt_style", "chatml") or "chatml").strip().lower()
        if prompt_style not in VALID_PROMPT_STYLES:
            prompt_style = "chatml"

        return cls(
            api_mode=api_mode,
            prompt_style=prompt_style,
            default_max_tokens=_positive_int(payload.get("default_max_tokens")),
            max_max_tokens=_positive_int(payload.get("max_max_tokens")),
            max_input_chars=_positive_int(payload.get("max_input_chars")),
            request_timeout_ms=_positive_int(payload.get("request_timeout_ms")),
            stream_idle_timeout_s=_positive_int(payload.get("stream_idle_timeout_s")),
            allow_anonymous_models=bool(payload.get("allow_anonymous_models", False)),
        )

    def to_payload(self) -> Dict[str, Any]:
        return {
            "api_mode": self.api_mode,
            "prompt_style": self.prompt_style,
            "default_max_tokens": self.default_max_tokens,
            "max_max_tokens": self.max_max_tokens,
            "max_input_chars": self.max_input_chars,
            "request_timeout_ms": self.request_timeout_ms,
            "stream_idle_timeout_s": self.stream_idle_timeout_s,
            "allow_anonymous_models": self.allow_anonymous_models,
        }

    def allows_endpoint(self, endpoint_kind: str) -> bool:
        if self.api_mode == "both":
            return True
        return self.api_mode == endpoint_kind


def _positive_int(raw_value: Any) -> int:
    if raw_value is None or raw_value == "":
        return 0
    try:
        parsed = int(raw_value)
    except (TypeError, ValueError):
        return 0
    return parsed if parsed > 0 else 0


def public_model_dict(model_record: Dict[str, Any]) -> Dict[str, Any]:
    public_record = {
        key: value
        for key, value in model_record.items()
        if not key.startswith("_")
    }
    public_record["lark_memory_core"] = build_lark_memory_core_model_capabilities(model_record)
    return public_record


def build_lark_memory_core_model_capabilities(model_record: Dict[str, Any]) -> Dict[str, Any]:
    policy = ModelServingPolicy.from_payload(model_record.get("_serving_policy"))
    return {
        "api_mode": policy.api_mode,
        "supported_endpoints": _supported_endpoints(policy.api_mode),
        "supported_parameters": list(SUPPORTED_INFERENCE_PARAMETERS),
        "unsupported_parameters": list(UNSUPPORTED_INFERENCE_PARAMETERS),
        "prompt_style": policy.prompt_style,
        "default_max_tokens": policy.default_max_tokens,
        "max_max_tokens": policy.max_max_tokens,
        "max_input_chars": policy.max_input_chars,
        "request_timeout_ms": policy.request_timeout_ms,
        "stream_idle_timeout_s": policy.stream_idle_timeout_s,
        "allow_anonymous_models": policy.allow_anonymous_models,
        "ready": bool(model_record.get("_ready", False)),
    }


def _supported_endpoints(api_mode: str) -> list[str]:
    if api_mode == "chat":
        return ["/v1/chat/completions"]
    if api_mode == "completion":
        return ["/v1/completions"]
    return ["/v1/chat/completions", "/v1/completions"]


def filter_anonymous_models(models: Iterable[Dict[str, Any]]) -> list[Dict[str, Any]]:
    return [
        model
        for model in models
        if bool(model.get("_serving_policy", {}).get("allow_anonymous_models", False))
    ]
