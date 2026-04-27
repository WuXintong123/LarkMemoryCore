"""Shared error and request-id helpers."""

from __future__ import annotations

import json
import uuid
from typing import Any, Dict, Optional, Tuple

from fastapi import HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .config import PARTIAL_REASON_VALUES
from ..infra.logger import setup_logger


logger = setup_logger("api_server")


def _request_id_from_request(request: Optional[Request]) -> str:
    if request is None:
        return ""
    request_id = getattr(getattr(request, "state", None), "request_id", "")
    return request_id if isinstance(request_id, str) else ""


def _ensure_request_id(request: Request) -> str:
    request_id = _request_id_from_request(request)
    if request_id:
        return request_id
    request_id = str(uuid.uuid4())
    request.state.request_id = request_id
    return request_id


def _merge_headers(*header_maps: Optional[Dict[str, str]]) -> Dict[str, str]:
    merged: Dict[str, str] = {}
    for header_map in header_maps:
        if not header_map:
            continue
        for key, value in header_map.items():
            merged[key] = value
    return merged


def _make_error_detail(
    message: str,
    *,
    error_type: str,
    request_id: str = "",
    param: Optional[str] = None,
    code: Optional[str] = None,
) -> Dict[str, Any]:
    error: Dict[str, Any] = {
        "message": message,
        "type": error_type,
    }
    if param is not None:
        error["param"] = param
    if code is not None:
        error["code"] = code
    if request_id:
        error["request_id"] = request_id
    return {"error": error}


def _error_type_for_status(status_code: int) -> str:
    if status_code in {400, 404, 422}:
        return "invalid_request_error"
    if status_code in {401, 403}:
        return "authentication_error"
    if status_code == 409:
        return "cancelled_error"
    if status_code == 429:
        return "rate_limit_error"
    if status_code == 503:
        return "service_unavailable_error"
    if status_code == 504:
        return "timeout_error"
    return "server_error"


def _normalize_error_detail(
    detail: Any,
    *,
    status_code: int,
    request_id: str,
) -> Dict[str, Any]:
    if isinstance(detail, dict) and "error" in detail and isinstance(detail["error"], dict):
        error = dict(detail["error"])
        error.setdefault("type", _error_type_for_status(status_code))
        if request_id:
            error["request_id"] = request_id
        return {"error": error}

    if isinstance(detail, dict) and {"message", "type"} <= set(detail.keys()):
        error = dict(detail)
        if request_id:
            error["request_id"] = request_id
        return {"error": error}

    if isinstance(detail, str):
        message = detail
    else:
        try:
            message = json.dumps(detail, ensure_ascii=False)
        except TypeError:
            message = str(detail)

    return _make_error_detail(
        message,
        error_type=_error_type_for_status(status_code),
        request_id=request_id,
    )


def _error_response(
    request: Request,
    *,
    status_code: int,
    message: str,
    error_type: str,
    param: Optional[str] = None,
    code: Optional[str] = None,
    headers: Optional[Dict[str, str]] = None,
) -> JSONResponse:
    request_id = _ensure_request_id(request)
    payload = _make_error_detail(
        message,
        error_type=error_type,
        request_id=request_id,
        param=param,
        code=code,
    )
    response_headers = _merge_headers(headers, {"X-Request-Id": request_id})
    return JSONResponse(status_code=status_code, content=payload, headers=response_headers)


def _validation_error_message(exc: RequestValidationError) -> Tuple[str, Optional[str]]:
    first_param: Optional[str] = None
    details = []
    for err in exc.errors():
        loc = [str(part) for part in err.get("loc", ()) if part != "body"]
        param = ".".join(loc) if loc else None
        if first_param is None and param:
            first_param = param
        msg = err.get("msg", "invalid value")
        details.append(f"{param}: {msg}" if param else msg)
    return "; ".join(details) if details else "Request validation failed", first_param


def _partial_reason(completion_status: str, completion_detail: str = "") -> Optional[str]:
    if completion_detail and completion_detail in PARTIAL_REASON_VALUES:
        return completion_detail
    if completion_status in PARTIAL_REASON_VALUES:
        return PARTIAL_REASON_VALUES[completion_status]
    if completion_status == "partial_timeout":
        return "partial_timeout"
    return None


def _request_base_url(request: Request) -> str:
    return str(request.base_url).rstrip("/")


def _backend_request_id(base_request_id: str, suffix: Optional[str] = None) -> str:
    if not base_request_id:
        return ""
    if suffix:
        return f"{base_request_id}:{suffix}"
    return base_request_id


async def openai_http_exception_handler(request: Request, exc: HTTPException):
    request_id = _ensure_request_id(request)
    payload = _normalize_error_detail(
        exc.detail,
        status_code=exc.status_code,
        request_id=request_id,
    )
    headers = _merge_headers(exc.headers, {"X-Request-Id": request_id})
    return JSONResponse(status_code=exc.status_code, content=payload, headers=headers)


async def openai_validation_exception_handler(request: Request, exc: RequestValidationError):
    message, param = _validation_error_message(exc)
    return _error_response(
        request,
        status_code=400,
        message=message,
        error_type="invalid_request_error",
        param=param,
        code="validation_error",
    )


async def openai_unhandled_exception_handler(request: Request, exc: Exception):
    logger.error(
        "Unhandled exception returned to client",
        extra={
            "component": "api_server",
            "request_id": _ensure_request_id(request),
            "path": request.url.path,
        },
    )
    return _error_response(
        request,
        status_code=500,
        message=str(exc) or "Internal server error",
        error_type="server_error",
    )
