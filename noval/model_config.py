"""Runtime-owned OpenAI-compatible model configuration.

The settings file is the credential authority for Phase 1. Public projections,
exceptions, and representations deliberately exclude stored credential values.
"""
from __future__ import annotations

import contextlib
import copy
import json
import os
import re
import tempfile
import threading
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from types import MappingProxyType
from typing import Any, Callable, Dict, Iterator, Mapping, Sequence, Tuple
from urllib.parse import urlsplit, urlunsplit

SETTINGS_SCHEMA_VERSION = 2
OPENAI_COMPATIBLE_ADAPTER = "openai-compatible"
CUSTOM_PROFILE_ID = "custom"
_ID_MAX_LENGTH = 128
_LABEL_MAX_LENGTH = 200
_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


class ModelConfigurationError(Exception):
    """A stable, credential-safe model configuration failure."""

    def __init__(self, code: str, path: str, message: str) -> None:
        self.code = code
        self.path = path
        self.safe_message = message
        super().__init__(f"{code}: {path}: {message}")


class ModelConfigurationConflict(ModelConfigurationError):
    """The requested mutation was based on an obsolete revision."""


@dataclass(frozen=True)
class ProviderModel:
    id: str
    label: str
    recommended: bool = False

    def public_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "recommended": self.recommended,
        }


@dataclass(frozen=True)
class ProviderProfile:
    id: str
    label: str
    base_url: str
    api_key_env: str
    models: Tuple[ProviderModel, ...]
    default_model: str
    judge_model: str
    adapter: str = OPENAI_COMPATIBLE_ADAPTER
    kind: str = "builtin"

    def public_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": SETTINGS_SCHEMA_VERSION,
            "id": self.id,
            "label": self.label,
            "kind": self.kind,
            "models": [model.public_dict() for model in self.models],
            "default_model": self.default_model,
        }


def _profile(
    profile_id: str,
    label: str,
    base_url: str,
    api_key_env: str,
    models: Sequence[Tuple[str, str]],
    default_model: str,
    judge_model: str,
) -> ProviderProfile:
    return ProviderProfile(
        id=profile_id,
        label=label,
        base_url=base_url,
        api_key_env=api_key_env,
        models=tuple(
            ProviderModel(
                id=model_id,
                label=model_label,
                recommended=model_id == default_model,
            )
            for model_id, model_label in models
        ),
        default_model=default_model,
        judge_model=judge_model,
    )


BUILTIN_PROFILES: Tuple[ProviderProfile, ...] = (
    _profile(
        "deepseek",
        "DeepSeek",
        "https://api.deepseek.com",
        "DEEPSEEK_API_KEY",
        (
            ("deepseek-v4-pro", "DeepSeek V4 Pro"),
            ("deepseek-v4-flash", "DeepSeek V4 Flash"),
        ),
        "deepseek-v4-pro",
        "deepseek-v4-flash",
    ),
    _profile(
        "qwen",
        "Qwen",
        "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "DASHSCOPE_API_KEY",
        (
            ("qwen3.7-plus", "Qwen 3.7 Plus"),
            ("qwen3.6-flash", "Qwen 3.6 Flash"),
        ),
        "qwen3.7-plus",
        "qwen3.6-flash",
    ),
    _profile(
        "moonshot",
        "Moonshot",
        "https://api.moonshot.cn/v1",
        "MOONSHOT_API_KEY",
        (("kimi-k2.6", "Kimi K2.6"),),
        "kimi-k2.6",
        "kimi-k2.6",
    ),
    _profile(
        "zhipu",
        "Zhipu",
        "https://open.bigmodel.cn/api/paas/v4",
        "ZAI_API_KEY",
        (("glm-5.2", "GLM 5.2"),),
        "glm-5.2",
        "glm-5.2",
    ),
    _profile(
        "openai",
        "OpenAI",
        "https://api.openai.com/v1",
        "OPENAI_API_KEY",
        (("gpt-5.2", "GPT-5.2"), ("gpt-5-mini", "GPT-5 mini")),
        "gpt-5.2",
        "gpt-5-mini",
    ),
    _profile(
        "google",
        "Google",
        "https://generativelanguage.googleapis.com/v1beta/openai/",
        "GEMINI_API_KEY",
        (
            ("gemini-3.6-flash", "Gemini 3.6 Flash"),
            ("gemini-3.5-flash-lite", "Gemini 3.5 Flash Lite"),
        ),
        "gemini-3.6-flash",
        "gemini-3.6-flash",
    ),
)
BUILTIN_PROFILE_BY_ID: Mapping[str, ProviderProfile] = MappingProxyType(
    {profile.id: profile for profile in BUILTIN_PROFILES}
)


def public_provider_profiles() -> Tuple[Dict[str, Any], ...]:
    profiles = [profile.public_dict() for profile in BUILTIN_PROFILES]
    profiles.append(
        {
            "schema_version": SETTINGS_SCHEMA_VERSION,
            "id": CUSTOM_PROFILE_ID,
            "label": "Custom",
            "kind": "custom",
            "adapter": OPENAI_COMPATIBLE_ADAPTER,
            "requires_base_url": True,
        }
    )
    return tuple(profiles)


@dataclass(frozen=True)
class Connection:
    id: str
    revision: int
    label: str
    profile_id: str
    adapter: str
    base_url: str
    api_key_env: str = ""
    api_key: str = field(default="", repr=False, compare=True)

    @property
    def api_key_configured(self) -> bool:
        return bool(self.api_key)

    def public_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "revision": self.revision,
            "label": self.label,
            "profile_id": self.profile_id,
            "adapter": self.adapter,
            "base_url": self.base_url,
            "api_key_env": self.api_key_env,
            "api_key_configured": self.api_key_configured,
            "credential_available": bool(
                self.api_key
                or (self.api_key_env and os.environ.get(self.api_key_env))
            ),
        }


@dataclass(frozen=True)
class ConfiguredModel:
    id: str
    label: str
    connection_id: str
    model: str

    def public_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "connection_id": self.connection_id,
            "model": self.model,
        }


@dataclass(frozen=True)
class ModelConfiguration:
    connections: Tuple[Connection, ...]
    configured: Tuple[ConfiguredModel, ...]
    default_model_id: str
    revision: int = 1

    def connection(self, connection_id: str) -> Connection:
        for connection in self.connections:
            if connection.id == connection_id:
                return connection
        raise ModelConfigurationError(
            "connection_not_found",
            "models.connections",
            f"Connection {connection_id!r} does not exist",
        )

    def configured_model(self, configured_model_id: str) -> ConfiguredModel:
        for configured in self.configured:
            if configured.id == configured_model_id:
                return configured
        raise ModelConfigurationError(
            "configured_model_not_found",
            "models.configured",
            f"Configured Model {configured_model_id!r} does not exist",
        )

    def public_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": SETTINGS_SCHEMA_VERSION,
            "revision": self.revision,
            "connections": [
                connection.public_dict() for connection in self.connections
            ],
            "configured": [model.public_dict() for model in self.configured],
            "default_model_id": self.default_model_id,
        }


def packaged_model_configuration() -> ModelConfiguration:
    profile = BUILTIN_PROFILE_BY_ID["deepseek"]
    connection = Connection(
        id="connection-deepseek-default",
        revision=1,
        label=profile.label,
        profile_id=profile.id,
        adapter=profile.adapter,
        base_url=profile.base_url,
        api_key_env=profile.api_key_env,
    )
    configured = tuple(
        ConfiguredModel(
            id=f"model-{model.id}-default",
            label=model.label,
            connection_id=connection.id,
            model=model.id,
        )
        for model in profile.models
    )
    return ModelConfiguration(
        connections=(connection,),
        configured=configured,
        default_model_id="model-deepseek-v4-pro-default",
    )


def packaged_settings() -> Dict[str, Any]:
    configuration = packaged_model_configuration()
    return {
        "schema_version": SETTINGS_SCHEMA_VERSION,
        "models": _configuration_to_settings(configuration),
        "max_steps": 40,
        "max_tool_output_chars": 8000,
        "persist_sessions": True,
        "sessions_dir": "",
        "persist_logs": True,
        "logs_dir": "",
        "log_retention_days": 14,
        "persist_usage": True,
        "usage_dir": "",
        "context_budget_tokens": 256000,
        "request_timeout_seconds": 120,
        "request_max_retries": 2,
        "anthropic_max_tokens": 8192,
    }


def _required_mapping(value: Any, path: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise ModelConfigurationError(
            "invalid_settings", path, "must be a JSON object"
        )
    return value


def _required_list(value: Any, path: str) -> Sequence[Any]:
    if not isinstance(value, list):
        raise ModelConfigurationError(
            "invalid_settings", path, "must be a JSON array"
        )
    return value


def _string(value: Any, path: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise ModelConfigurationError(
            "invalid_settings", path, "must be a string"
        )
    normalized = value.strip()
    if not allow_empty and not normalized:
        raise ModelConfigurationError(
            "invalid_settings", path, "must not be empty"
        )
    return normalized


def _bounded_identifier(value: Any, path: str) -> str:
    identifier = _string(value, path)
    if len(identifier) > _ID_MAX_LENGTH:
        raise ModelConfigurationError(
            "invalid_settings",
            path,
            f"must be at most {_ID_MAX_LENGTH} characters",
        )
    return identifier


def _bounded_label(value: Any, path: str) -> str:
    label = _string(value, path)
    if len(label) > _LABEL_MAX_LENGTH:
        raise ModelConfigurationError(
            "invalid_settings",
            path,
            f"must be at most {_LABEL_MAX_LENGTH} characters",
        )
    return label


def normalize_base_url(value: Any, path: str) -> str:
    raw = _string(value, path)
    parsed = urlsplit(raw)
    if (
        not parsed.scheme
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ModelConfigurationError(
            "invalid_base_url",
            path,
            "must be an absolute URL without credentials, query, or fragment",
        )
    scheme = parsed.scheme.lower()
    host = parsed.hostname.lower()
    if scheme != "https" and not (scheme == "http" and host in _LOOPBACK_HOSTS):
        raise ModelConfigurationError(
            "invalid_base_url",
            path,
            "must use HTTPS, except HTTP is allowed for loopback hosts",
        )
    netloc = host
    if ":" in host and not host.startswith("["):
        netloc = f"[{host}]"
    if parsed.port is not None:
        netloc = f"{netloc}:{parsed.port}"
    normalized_path = parsed.path.rstrip("/")
    return urlunsplit((scheme, netloc, normalized_path, "", ""))


def _parse_connection(value: Any, index: int) -> Connection:
    path = f"models.connections[{index}]"
    obj = _required_mapping(value, path)
    profile_id = _bounded_identifier(obj.get("profile_id"), f"{path}.profile_id")
    adapter = _string(obj.get("adapter"), f"{path}.adapter")
    base_url = normalize_base_url(obj.get("base_url"), f"{path}.base_url")
    api_key = _string(
        obj.get("api_key", ""), f"{path}.api_key", allow_empty=True
    )
    api_key_env = _string(
        obj.get("api_key_env", ""), f"{path}.api_key_env", allow_empty=True
    )
    if api_key_env and not _ENV_NAME.fullmatch(api_key_env):
        raise ModelConfigurationError(
            "invalid_settings",
            f"{path}.api_key_env",
            "must be a portable environment-variable name",
        )
    revision = obj.get("revision")
    if not isinstance(revision, int) or isinstance(revision, bool) or revision < 1:
        raise ModelConfigurationError(
            "invalid_settings", f"{path}.revision", "must be an integer at least 1"
        )
    connection = Connection(
        id=_bounded_identifier(obj.get("id"), f"{path}.id"),
        revision=revision,
        label=_bounded_label(obj.get("label"), f"{path}.label"),
        profile_id=profile_id,
        adapter=adapter,
        base_url=base_url,
        api_key_env=api_key_env,
        api_key=api_key,
    )
    if profile_id == CUSTOM_PROFILE_ID:
        if adapter != OPENAI_COMPATIBLE_ADAPTER:
            raise ModelConfigurationError(
                "unsupported_adapter",
                f"{path}.adapter",
                "Phase 1 custom Connections must use openai-compatible",
            )
        return connection
    profile = BUILTIN_PROFILE_BY_ID.get(profile_id)
    if profile is None:
        raise ModelConfigurationError(
            "unknown_profile",
            f"{path}.profile_id",
            f"unknown built-in Profile {profile_id!r}",
        )
    if (
        connection.adapter != profile.adapter
        or connection.base_url != normalize_base_url(profile.base_url, path)
        or connection.api_key_env != profile.api_key_env
    ):
        raise ModelConfigurationError(
            "builtin_profile_mismatch",
            path,
            "built-in Connection transport fields must match its Profile",
        )
    return connection


def _parse_configured_model(value: Any, index: int) -> ConfiguredModel:
    path = f"models.configured[{index}]"
    obj = _required_mapping(value, path)
    return ConfiguredModel(
        id=_bounded_identifier(obj.get("id"), f"{path}.id"),
        label=_bounded_label(obj.get("label"), f"{path}.label"),
        connection_id=_bounded_identifier(
            obj.get("connection_id"), f"{path}.connection_id"
        ),
        model=_bounded_identifier(obj.get("model"), f"{path}.model"),
    )


def parse_model_configuration(value: Any) -> ModelConfiguration:
    models = _required_mapping(value, "models")
    connections = tuple(
        _parse_connection(item, index)
        for index, item in enumerate(
            _required_list(models.get("connections"), "models.connections")
        )
    )
    configured = tuple(
        _parse_configured_model(item, index)
        for index, item in enumerate(
            _required_list(models.get("configured"), "models.configured")
        )
    )
    revision = models.get("revision", 1)
    if not isinstance(revision, int) or isinstance(revision, bool) or revision < 1:
        raise ModelConfigurationError(
            "invalid_settings", "models.revision", "must be an integer at least 1"
        )
    default_model_id = _bounded_identifier(
        models.get("default_model_id"), "models.default_model_id"
    )
    connection_ids = [connection.id for connection in connections]
    if len(connection_ids) != len(set(connection_ids)):
        raise ModelConfigurationError(
            "duplicate_id", "models.connections", "Connection ids must be unique"
        )
    configured_ids = [model.id for model in configured]
    if len(configured_ids) != len(set(configured_ids)):
        raise ModelConfigurationError(
            "duplicate_id", "models.configured", "Configured Model ids must be unique"
        )
    connection_by_id = {connection.id: connection for connection in connections}
    for index, model in enumerate(configured):
        connection = connection_by_id.get(model.connection_id)
        if connection is None:
            raise ModelConfigurationError(
                "connection_not_found",
                f"models.configured[{index}].connection_id",
                f"Connection {model.connection_id!r} does not exist",
            )
        profile = BUILTIN_PROFILE_BY_ID.get(connection.profile_id)
        if profile is not None and model.model not in {
            supported.id for supported in profile.models
        }:
            raise ModelConfigurationError(
                "unsupported_profile_model",
                f"models.configured[{index}].model",
                f"model is not declared by Profile {profile.id!r}",
            )
    if default_model_id not in set(configured_ids):
        raise ModelConfigurationError(
            "configured_model_not_found",
            "models.default_model_id",
            "must reference an existing Configured Model",
        )
    return ModelConfiguration(
        connections=connections,
        configured=configured,
        default_model_id=default_model_id,
        revision=revision,
    )


def parse_settings_document(value: Any, path: Path) -> Dict[str, Any]:
    obj = dict(_required_mapping(value, str(path)))
    version = obj.get("schema_version")
    if version != SETTINGS_SCHEMA_VERSION:
        raise ModelConfigurationError(
            "unsupported_settings_schema",
            str(path),
            "expected schema_version 2; recreate this internal pre-release settings file",
        )
    removed = {
        "provider",
        "model",
        "judge_model",
        "base_url",
        "api_key",
        "api_key_env",
        "anthropic_base_url",
    }.intersection(obj)
    if removed:
        fields = ", ".join(sorted(removed))
        raise ModelConfigurationError(
            "legacy_settings_fields",
            str(path),
            f"removed flat fields are not accepted in schema v2: {fields}",
        )
    obj["models"] = _configuration_to_settings(
        parse_model_configuration(obj.get("models"))
    )
    return obj


def load_settings_document(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return packaged_settings()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ModelConfigurationError(
            "invalid_json", str(path), f"settings file is not valid JSON at line {exc.lineno}"
        ) from None
    return parse_settings_document(raw, path)


def _connection_to_settings(connection: Connection) -> Dict[str, Any]:
    return {
        "id": connection.id,
        "revision": connection.revision,
        "label": connection.label,
        "profile_id": connection.profile_id,
        "adapter": connection.adapter,
        "base_url": connection.base_url,
        "api_key": connection.api_key,
        "api_key_env": connection.api_key_env,
    }


def _configuration_to_settings(
    configuration: ModelConfiguration,
) -> Dict[str, Any]:
    return {
        "revision": configuration.revision,
        "connections": [
            _connection_to_settings(connection)
            for connection in configuration.connections
        ],
        "configured": [
            {
                "id": model.id,
                "label": model.label,
                "connection_id": model.connection_id,
                "model": model.model,
            }
            for model in configuration.configured
        ],
        "default_model_id": configuration.default_model_id,
    }


@contextlib.contextmanager
def _writer_lease(path: Path, timeout_seconds: float) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with contextlib.suppress(OSError):
        path.parent.chmod(0o700)
    lease_path = path.with_name(f"{path.name}.lock")
    deadline = time.monotonic() + timeout_seconds
    descriptor = os.open(lease_path, os.O_CREAT | os.O_RDWR, 0o600)
    acquired = False
    while not acquired:
        try:
            os.lseek(descriptor, 0, os.SEEK_SET)
            if os.name == "nt":
                import msvcrt

                if os.fstat(descriptor).st_size == 0:
                    os.write(descriptor, b"\0")
                    os.fsync(descriptor)
                    os.lseek(descriptor, 0, os.SEEK_SET)
                msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = True
        except OSError:
            if time.monotonic() >= deadline:
                os.close(descriptor)
                raise ModelConfigurationError(
                    "settings_busy",
                    str(path),
                    "another Runtime is updating settings",
                ) from None
            time.sleep(0.01)
    try:
        yield
    finally:
        os.lseek(descriptor, 0, os.SEEK_SET)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _atomic_write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with contextlib.suppress(OSError):
        path.parent.chmod(0o700)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary_path = Path(temporary_name)
    try:
        with contextlib.suppress(OSError):
            os.chmod(temporary_path, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
        with contextlib.suppress(OSError):
            path.chmod(0o600)
        if os.name != "nt":
            directory = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        with contextlib.suppress(FileNotFoundError):
            temporary_path.unlink()


class ModelConfigurationStore:
    """Validated snapshot and transactional writer for settings schema v2."""

    def __init__(self, path: Path, *, lease_timeout_seconds: float = 2.0) -> None:
        self._path = path
        self._lease_timeout_seconds = lease_timeout_seconds
        self._mutation_lock = threading.RLock()
        document = load_settings_document(path)
        self._document = copy.deepcopy(document)
        self._snapshot = parse_model_configuration(document["models"])

    @property
    def path(self) -> Path:
        return self._path

    def snapshot(self) -> ModelConfiguration:
        with self._mutation_lock:
            return self._snapshot

    def reload(self) -> ModelConfiguration:
        with self._mutation_lock:
            document = load_settings_document(self._path)
            snapshot = parse_model_configuration(document["models"])
            self._document = copy.deepcopy(document)
            self._snapshot = snapshot
            return snapshot

    def mutate(
        self,
        expected_revision: int,
        transform: Callable[[ModelConfiguration], ModelConfiguration],
    ) -> ModelConfiguration:
        with self._mutation_lock:
            with _writer_lease(self._path, self._lease_timeout_seconds):
                document = load_settings_document(self._path)
                current = parse_model_configuration(document["models"])
                if current.revision != expected_revision:
                    raise ModelConfigurationConflict(
                        "configuration_conflict",
                        "models.revision",
                        f"expected revision {expected_revision}, found {current.revision}",
                    )
                candidate = transform(current)
                if not isinstance(candidate, ModelConfiguration):
                    raise TypeError("configuration transform must return ModelConfiguration")
                candidate = replace(candidate, revision=current.revision + 1)
                validated = parse_model_configuration(
                    _configuration_to_settings(candidate)
                )
                updated = copy.deepcopy(document)
                updated["models"] = _configuration_to_settings(validated)
                _atomic_write_json(self._path, updated)
                self._document = updated
                self._snapshot = validated
                return validated

    def upsert_connection(
        self,
        connection: Connection,
        *,
        expected_revision: int,
    ) -> ModelConfiguration:
        def transform(current: ModelConfiguration) -> ModelConfiguration:
            existing = next(
                (
                    item
                    for item in current.connections
                    if item.id == connection.id
                ),
                None,
            )
            if existing is None:
                candidate = replace(connection, revision=1)
                return replace(
                    current, connections=current.connections + (candidate,)
                )
            transport_changed = (
                existing.profile_id,
                existing.adapter,
                existing.base_url,
                existing.api_key_env,
                existing.api_key,
            ) != (
                connection.profile_id,
                connection.adapter,
                connection.base_url,
                connection.api_key_env,
                connection.api_key,
            )
            candidate = replace(
                connection,
                revision=existing.revision + int(transport_changed),
            )
            return replace(
                current,
                connections=tuple(
                    candidate if item.id == candidate.id else item
                    for item in current.connections
                ),
            )

        return self.mutate(expected_revision, transform)

    def delete_connection(
        self,
        connection_id: str,
        *,
        expected_revision: int,
    ) -> ModelConfiguration:
        def transform(current: ModelConfiguration) -> ModelConfiguration:
            current.connection(connection_id)
            if any(
                model.connection_id == connection_id
                for model in current.configured
            ):
                raise ModelConfigurationError(
                    "connection_in_use",
                    "models.connections",
                    f"Connection {connection_id!r} is referenced by a Configured Model",
                )
            return replace(
                current,
                connections=tuple(
                    item
                    for item in current.connections
                    if item.id != connection_id
                ),
            )

        return self.mutate(expected_revision, transform)

    def upsert_configured_model(
        self,
        configured_model: ConfiguredModel,
        *,
        expected_revision: int,
    ) -> ModelConfiguration:
        def transform(current: ModelConfiguration) -> ModelConfiguration:
            current.connection(configured_model.connection_id)
            exists = any(
                item.id == configured_model.id for item in current.configured
            )
            configured = tuple(
                configured_model if item.id == configured_model.id else item
                for item in current.configured
            )
            if not exists:
                configured += (configured_model,)
            return replace(current, configured=configured)

        return self.mutate(expected_revision, transform)

    def delete_configured_model(
        self,
        configured_model_id: str,
        *,
        expected_revision: int,
    ) -> ModelConfiguration:
        def transform(current: ModelConfiguration) -> ModelConfiguration:
            current.configured_model(configured_model_id)
            if current.default_model_id == configured_model_id:
                raise ModelConfigurationError(
                    "default_model_in_use",
                    "models.default_model_id",
                    "activate another Configured Model before deleting the default",
                )
            return replace(
                current,
                configured=tuple(
                    item
                    for item in current.configured
                    if item.id != configured_model_id
                ),
            )

        return self.mutate(expected_revision, transform)

    def set_default_model(
        self,
        configured_model_id: str,
        *,
        expected_revision: int,
    ) -> ModelConfiguration:
        def transform(current: ModelConfiguration) -> ModelConfiguration:
            current.configured_model(configured_model_id)
            return replace(current, default_model_id=configured_model_id)

        return self.mutate(expected_revision, transform)

    def resolve_api_key(self, connection_id: str) -> str:
        connection = self.snapshot().connection(connection_id)
        if connection.api_key:
            return connection.api_key
        if connection.api_key_env:
            value = os.environ.get(connection.api_key_env)
            if value:
                return value
        raise ModelConfigurationError(
            "credential_unavailable",
            "models.connections",
            f"Connection {connection_id!r} has no available credential",
        )
