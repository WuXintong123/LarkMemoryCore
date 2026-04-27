# ===- auth.py -----------------------------------------------------------------
#
# SPDX-License-Identifier: Apache-2.0
#
# ===---------------------------------------------------------------------------
#
# API key authentication and authorization utilities.
#
# ===---------------------------------------------------------------------------

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import FrozenSet, Iterable, List, Optional, Sequence
import hashlib
import hmac
from importlib import import_module
import json

from fastapi import Depends, Header, HTTPException, Request

from ..infra.logger import setup_logger


logger = setup_logger("api_auth")

AUTH_HEADER = "Bearer"
ALL_API_SCOPES = frozenset({"models:read", "inference", "admin"})
DEFAULT_EXPLICIT_SCOPES = frozenset({"models:read", "inference"})
DEFAULT_LEGACY_SCOPES = frozenset({"models:read", "inference", "admin"})


@dataclass(frozen=True)
class ApiKeyPrincipal:
    key_id: str
    scopes: FrozenSet[str]
    allowed_models: Optional[FrozenSet[str]]

    @property
    def rate_limit_subject(self) -> str:
        return f"key_id:{self.key_id}"

    def has_any_scope(self, required_scopes: Iterable[str]) -> bool:
        return any(scope in self.scopes for scope in required_scopes)

    def can_access_model(self, model_id: str) -> bool:
        if self.allowed_models is None:
            return True
        return model_id in self.allowed_models


@dataclass(frozen=True)
class _ApiKeyRecord:
    key_id: str
    scopes: FrozenSet[str]
    allowed_models: Optional[FrozenSet[str]]
    secret: Optional[str]
    secret_sha256: Optional[str]
    disabled: bool
    expires_at: Optional[int]

    @property
    def secret_fingerprint(self) -> Optional[str]:
        if self.secret_sha256:
            return self.secret_sha256
        if self.secret:
            return _sha256_hex(self.secret)
        return None

    def matches(self, presented_secret: str) -> bool:
        if self.disabled:
            return False
        if self.expires_at is not None and int(datetime.now(tz=timezone.utc).timestamp()) >= self.expires_at:
            return False
        if self.secret is not None and hmac.compare_digest(presented_secret, self.secret):
            return True
        if self.secret_sha256 is not None:
            presented_hash = _sha256_hex(presented_secret)
            return hmac.compare_digest(presented_hash, self.secret_sha256)
        return False

    def as_principal(self) -> ApiKeyPrincipal:
        return ApiKeyPrincipal(
            key_id=self.key_id,
            scopes=self.scopes,
            allowed_models=self.allowed_models,
        )


def _normalize_scopes(raw_scopes: Optional[object], *, default_scopes: FrozenSet[str]) -> FrozenSet[str]:
    if raw_scopes is None:
        return default_scopes
    if isinstance(raw_scopes, str):
        values = [segment.strip() for segment in raw_scopes.split(",") if segment.strip()]
    elif isinstance(raw_scopes, Sequence):
        values = [str(segment).strip() for segment in raw_scopes if str(segment).strip()]
    else:
        raise RuntimeError("API key scopes must be a string or a list of strings")

    if not values:
        return default_scopes

    invalid = [value for value in values if value not in ALL_API_SCOPES]
    if invalid:
        raise RuntimeError(f"Unsupported API key scopes: {', '.join(sorted(set(invalid)))}")
    return frozenset(values)


def _normalize_model_allowlist(raw_models: Optional[object]) -> Optional[FrozenSet[str]]:
    if raw_models is None:
        return None
    if isinstance(raw_models, str):
        values = [segment.strip() for segment in raw_models.split(",") if segment.strip()]
    elif isinstance(raw_models, Sequence):
        values = [str(segment).strip() for segment in raw_models if str(segment).strip()]
    else:
        raise RuntimeError("API key model allowlist must be a string or a list of strings")

    if not values or "*" in values:
        return None
    return frozenset(values)


def _normalize_secret_hash(raw_hash: str) -> str:
    normalized = raw_hash.strip().lower()
    if len(normalized) != 64 or any(ch not in "0123456789abcdef" for ch in normalized):
        raise RuntimeError("API key secret_sha256 must be a 64-character lowercase hex string")
    return normalized


def _parse_expires_at(raw_value: Optional[object]) -> Optional[int]:
    if raw_value is None or raw_value == "":
        return None
    if isinstance(raw_value, (int, float)):
        return int(raw_value)
    if isinstance(raw_value, str):
        value = raw_value.strip()
        if not value:
            return None
        if value.isdigit():
            return int(value)
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise RuntimeError(f"Invalid expires_at value: {raw_value}") from exc
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return int(parsed.timestamp())
    raise RuntimeError("API key expires_at must be epoch seconds or ISO-8601 string")


def _parse_authorization_header(authorization: Optional[str]) -> str:
    if not authorization:
        return ""
    value = authorization.strip()
    if not value:
        return ""
    parts = value.split(None, 1)
    if len(parts) == 2 and parts[0].lower() == AUTH_HEADER.lower():
        return parts[1].strip()
    return value


def _sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status_code=401,
        detail=detail,
        headers={"WWW-Authenticate": AUTH_HEADER},
    )


def _forbidden(detail: str) -> HTTPException:
    return HTTPException(status_code=403, detail=detail)


class ApiKeyAuthManager:
    def __init__(self, records: List[_ApiKeyRecord]):
        self._records = records

    @classmethod
    def from_config(
        cls,
        *,
        legacy_api_key: str,
        legacy_key_id: str,
        legacy_scopes: str,
        legacy_allowed_models: str,
        api_keys_file: str,
        api_keys_json: str,
    ) -> "ApiKeyAuthManager":
        records: List[_ApiKeyRecord] = []
        explicit_sources_present = bool(api_keys_file.strip() or api_keys_json.strip())

        if api_keys_file.strip():
            with open(api_keys_file, "r", encoding="utf-8") as handle:
                file_blob = handle.read()
            records.extend(
                cls._records_from_json_blob(
                    file_blob,
                    source=f"API_KEYS_FILE={api_keys_file}",
                )
            )

        if api_keys_json.strip():
            records.extend(
                cls._records_from_json_blob(
                    api_keys_json,
                    source="API_KEYS_JSON",
                )
            )

        if explicit_sources_present:
            if legacy_api_key.strip():
                logger.warning(
                    "Ignoring legacy API_KEY because explicit multi-key config is enabled",
                    extra={"component": "api_auth"},
                )
        elif legacy_api_key.strip():
            records.append(
                _ApiKeyRecord(
                    key_id=legacy_key_id.strip() or "default",
                    scopes=_normalize_scopes(
                        legacy_scopes,
                        default_scopes=DEFAULT_LEGACY_SCOPES,
                    ),
                    allowed_models=_normalize_model_allowlist(legacy_allowed_models),
                    secret=legacy_api_key.strip(),
                    secret_sha256=None,
                    disabled=False,
                    expires_at=None,
                )
            )

        cls._validate_unique_records(records)
        if records:
            logger.info(
                "Loaded API key configuration",
                extra={
                    "component": "api_auth",
                    "enabled": True,
                    "key_count": len(records),
                },
            )
        else:
            logger.info(
                "API key authentication disabled",
                extra={"component": "api_auth", "enabled": False},
            )
        return cls(records)

    @staticmethod
    def _records_from_json_blob(blob: str, *, source: str) -> List[_ApiKeyRecord]:
        try:
            parsed = json.loads(blob)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Failed to parse {source}: {exc}") from exc

        if isinstance(parsed, dict):
            raw_entries = parsed.get("keys")
        else:
            raw_entries = parsed

        if not isinstance(raw_entries, list):
            raise RuntimeError(f"{source} must be a JSON list or an object containing a 'keys' list")

        records: List[_ApiKeyRecord] = []
        for index, raw_entry in enumerate(raw_entries, start=1):
            if not isinstance(raw_entry, dict):
                raise RuntimeError(f"{source} entry #{index} must be a JSON object")

            key_id = str(raw_entry.get("key_id", "")).strip()
            if not key_id:
                raise RuntimeError(f"{source} entry #{index} is missing non-empty 'key_id'")

            secret = raw_entry.get("secret")
            secret_sha256 = raw_entry.get("secret_sha256")
            if bool(secret) == bool(secret_sha256):
                raise RuntimeError(
                    f"{source} entry '{key_id}' must provide exactly one of 'secret' or 'secret_sha256'"
                )

            records.append(
                _ApiKeyRecord(
                    key_id=key_id,
                    scopes=_normalize_scopes(
                        raw_entry.get("scopes"),
                        default_scopes=DEFAULT_EXPLICIT_SCOPES,
                    ),
                    allowed_models=_normalize_model_allowlist(raw_entry.get("models")),
                    secret=str(secret).strip() if secret is not None else None,
                    secret_sha256=_normalize_secret_hash(str(secret_sha256)) if secret_sha256 is not None else None,
                    disabled=bool(raw_entry.get("disabled", False)),
                    expires_at=_parse_expires_at(raw_entry.get("expires_at")),
                )
            )

        return records

    @staticmethod
    def _validate_unique_records(records: Sequence[_ApiKeyRecord]) -> None:
        seen_ids = set()
        seen_fingerprints = set()
        for record in records:
            if record.key_id in seen_ids:
                raise RuntimeError(f"Duplicate API key_id detected: {record.key_id}")
            seen_ids.add(record.key_id)

            fingerprint = record.secret_fingerprint
            if fingerprint is None:
                continue
            if fingerprint in seen_fingerprints:
                raise RuntimeError(
                    f"Duplicate API key secret detected for key_id={record.key_id}"
                )
            seen_fingerprints.add(fingerprint)

    @property
    def enabled(self) -> bool:
        return bool(self._records)

    def authenticate(self, authorization: Optional[str]) -> Optional[ApiKeyPrincipal]:
        if not self.enabled:
            return None

        presented = _parse_authorization_header(authorization)
        if not presented:
            raise _unauthorized("Missing Authorization header")

        for record in self._records:
            if record.matches(presented):
                return record.as_principal()

        raise _unauthorized("Invalid API key")

    def ensure_scopes(
        self,
        principal: Optional[ApiKeyPrincipal],
        required_scopes: Iterable[str],
    ) -> Optional[ApiKeyPrincipal]:
        if not self.enabled:
            return principal
        if principal is None:
            raise _unauthorized("Missing Authorization header")
        required = tuple(required_scopes)
        if not principal.has_any_scope(required):
            raise _forbidden(
                "API key is not permitted to access this endpoint"
            )
        return principal

    def ensure_model_access(
        self,
        principal: Optional[ApiKeyPrincipal],
        model_id: str,
        *,
        conceal_existence: bool = False,
    ) -> None:
        if not self.enabled or principal is None or principal.can_access_model(model_id):
            return
        if conceal_existence:
            raise HTTPException(status_code=404, detail=f"Model '{model_id}' not found")
        raise _forbidden("API key is not permitted to access this model")

    def filter_models_for_principal(
        self,
        principal: Optional[ApiKeyPrincipal],
        models: Sequence[dict],
    ) -> List[dict]:
        if not self.enabled or principal is None or principal.allowed_models is None:
            return list(models)
        return [model for model in models if principal.can_access_model(str(model.get("id", "")))]

    def rate_limit_subject(
        self,
        principal: Optional[ApiKeyPrincipal],
        authorization: Optional[str],
    ) -> str:
        if principal is not None:
            return principal.rate_limit_subject
        presented = _parse_authorization_header(authorization)
        if not presented:
            return "anonymous"
        return f"sha256:{_sha256_hex(presented)[:16]}"


__all__ = [
    "ALL_API_SCOPES",
    "ApiKeyAuthManager",
    "ApiKeyPrincipal",
    "_parse_authorization_header",
]


def _main_module():
    return import_module("api_server.main")


async def verify_api_key(
    request: Request,
    authorization: Optional[str] = Header(None),
) -> Optional[ApiKeyPrincipal]:
    main_module = _main_module()
    try:
        principal = main_module.auth_manager.authenticate(authorization)
    except HTTPException:
        main_module._increment_api_metric("auth_failures")
        raise
    request.state.auth_key_id = principal.key_id if principal is not None else None
    return principal


async def resolve_models_principal(
    request: Request,
    authorization: Optional[str] = Header(None),
) -> Optional[ApiKeyPrincipal]:
    main_module = _main_module()
    if not main_module.auth_manager.enabled:
        request.state.auth_key_id = None
        return None

    presented = _parse_authorization_header(authorization)
    if not presented:
        request.state.auth_key_id = None
        return None

    try:
        principal = main_module.auth_manager.authenticate(authorization)
        request.state.auth_key_id = principal.key_id
        return principal
    except HTTPException:
        main_module._increment_api_metric("auth_failures")
        raise


def require_api_scopes(*required_scopes: str):
    async def _require_api_scopes(
        principal: Optional[ApiKeyPrincipal] = Depends(verify_api_key),
    ) -> Optional[ApiKeyPrincipal]:
        _main_module().auth_manager.ensure_scopes(principal, required_scopes)
        return principal

    return _require_api_scopes
