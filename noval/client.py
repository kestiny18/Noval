"""Provider adapters for Noval's canonical conversation model."""
from __future__ import annotations

import copy
import json
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Protocol, Sequence, Tuple

from .messages import (
    AdapterReplayState,
    ConversationMessage,
    MessageProvenance,
    MessageRole,
    TextBlock,
    ToolCallBlock,
    assistant_message,
)

OPENAI_ADAPTER = "openai-compatible"
ANTHROPIC_ADAPTER = "anthropic-messages"
ADAPTER_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class ToolDefinition:
    """Provider-visible tool data; executor state and callables never cross this seam."""

    name: str
    description: str
    input_schema: Dict[str, Any]


@dataclass(frozen=True)
class TokenUsage:
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    cache_hit_tokens: Optional[int] = None
    cache_miss_tokens: Optional[int] = None
    reasoning_tokens: Optional[int] = None


@dataclass(frozen=True)
class ProviderIdentity:
    provider: str
    model: str
    adapter: str
    adapter_schema_version: int = ADAPTER_SCHEMA_VERSION

    def provenance(self) -> MessageProvenance:
        return MessageProvenance(
            provider=self.provider,
            model=self.model,
            adapter=self.adapter,
            adapter_schema_version=self.adapter_schema_version,
        )


class ProviderErrorKind(str, Enum):
    AUTHENTICATION = "authentication"
    RATE_LIMIT = "rate_limit"
    TIMEOUT = "timeout"
    CONNECTION = "connection"
    INVALID_REQUEST = "invalid_request"
    SERVER = "server"
    PROTOCOL = "protocol"
    UNKNOWN = "unknown"


class ProviderError(RuntimeError):
    """Safe, normalized Provider failure exposed to the core and embedders."""

    def __init__(
        self,
        kind: ProviderErrorKind,
        safe_message: str,
        *,
        retryable: bool,
        identity: ProviderIdentity,
    ):
        super().__init__(safe_message)
        self.kind = kind
        self.safe_message = safe_message
        self.retryable = retryable
        self.identity = identity


@dataclass
class LLMResponse:
    message: ConversationMessage
    usage: Optional[TokenUsage] = None
    provider: ProviderIdentity = field(default_factory=lambda: ProviderIdentity(
        provider="mock", model="mock", adapter="mock",
    ))
    meta: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LLMStreamEvent:
    """One provider-neutral visible output observation."""

    text: str
    type: str = "text.delta"

    def __post_init__(self) -> None:
        if self.type != "text.delta":
            raise ValueError("unsupported LLM stream event type")
        if not isinstance(self.text, str) or not self.text:
            raise ValueError("text delta must be a non-empty string")


LLMStreamObserver = Callable[[LLMStreamEvent], None]


class LLMClient(Protocol):
    def complete(
        self,
        messages: Sequence[ConversationMessage],
        tools: Sequence[ToolDefinition],
    ) -> LLMResponse:
        ...


class StreamingLLMClient(Protocol):
    """Optional capability; final response semantics match ``complete``."""

    def stream_complete(
        self,
        messages: Sequence[ConversationMessage],
        tools: Sequence[ToolDefinition],
        on_event: LLMStreamObserver,
    ) -> LLMResponse:
        ...


def _value(obj: Any, name: str) -> Any:
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


def _optional_int(value: Any) -> Optional[int]:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _normalize_openai_usage(raw_usage: Any) -> Optional[TokenUsage]:
    if raw_usage is None:
        return None
    prompt_tokens = _optional_int(_value(raw_usage, "prompt_tokens"))
    completion_tokens = _optional_int(_value(raw_usage, "completion_tokens"))
    total_tokens = _optional_int(_value(raw_usage, "total_tokens"))
    if prompt_tokens is None or completion_tokens is None or total_tokens is None:
        return None
    completion_details = _value(raw_usage, "completion_tokens_details")
    return TokenUsage(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        cache_hit_tokens=_optional_int(_value(raw_usage, "prompt_cache_hit_tokens")),
        cache_miss_tokens=_optional_int(_value(raw_usage, "prompt_cache_miss_tokens")),
        reasoning_tokens=_optional_int(_value(completion_details, "reasoning_tokens")),
    )


def _normalize_anthropic_usage(raw_usage: Any) -> Optional[TokenUsage]:
    if raw_usage is None:
        return None
    prompt_tokens = _optional_int(_value(raw_usage, "input_tokens"))
    completion_tokens = _optional_int(_value(raw_usage, "output_tokens"))
    if prompt_tokens is None or completion_tokens is None:
        return None
    return TokenUsage(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=prompt_tokens + completion_tokens,
        cache_hit_tokens=_optional_int(_value(raw_usage, "cache_read_input_tokens")),
        cache_miss_tokens=_optional_int(_value(raw_usage, "cache_creation_input_tokens")),
    )


def _normalized_error(error: Exception, identity: ProviderIdentity) -> ProviderError:
    name = type(error).__name__.lower()
    status = _value(error, "status_code")
    if "authentication" in name or status in (401, 403):
        kind, retryable = ProviderErrorKind.AUTHENTICATION, False
    elif "ratelimit" in name or status == 429:
        kind, retryable = ProviderErrorKind.RATE_LIMIT, True
    elif "timeout" in name:
        kind, retryable = ProviderErrorKind.TIMEOUT, True
    elif "connection" in name:
        kind, retryable = ProviderErrorKind.CONNECTION, True
    elif isinstance(status, int) and status >= 500:
        kind, retryable = ProviderErrorKind.SERVER, True
    elif "badrequest" in name or (isinstance(status, int) and 400 <= status < 500):
        kind, retryable = ProviderErrorKind.INVALID_REQUEST, False
    else:
        kind, retryable = ProviderErrorKind.UNKNOWN, False
    return ProviderError(
        kind,
        f"{identity.provider} request failed ({kind.value})",
        retryable=retryable,
        identity=identity,
    )


def _openai_tools(tools: Sequence[ToolDefinition]) -> List[Dict[str, Any]]:
    return [{
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.input_schema,
        },
    } for tool in tools]


def _openai_messages(messages: Sequence[ConversationMessage]) -> List[Dict[str, Any]]:
    wire: List[Dict[str, Any]] = []
    for message in messages:
        if message.role in {MessageRole.SYSTEM, MessageRole.USER}:
            wire.append({"role": message.role.value, "content": message.text})
            continue
        if message.role is MessageRole.TOOL:
            for result in message.tool_results:
                wire.append({
                    "role": "tool",
                    "tool_call_id": result.call_id,
                    "content": result.content,
                })
            continue
        item: Dict[str, Any] = {
            "role": "assistant",
            "content": message.text if any(isinstance(b, TextBlock) for b in message.blocks) else None,
        }
        calls = message.tool_calls
        if calls:
            item["tool_calls"] = [{
                "id": call.id,
                "type": "function",
                "function": {"name": call.name, "arguments": call.arguments},
            } for call in calls]
        replay = message.replay_state
        if replay is not None and replay.adapter == OPENAI_ADAPTER:
            payload = replay.payload
            if replay.schema_version != ADAPTER_SCHEMA_VERSION or not isinstance(payload, dict):
                raise ProviderError(
                    ProviderErrorKind.PROTOCOL,
                    "openai-compatible replay state has an unsupported schema",
                    retryable=False,
                    identity=ProviderIdentity(OPENAI_ADAPTER, "unknown", OPENAI_ADAPTER),
                )
            reasoning = payload.get("reasoning_content")
            if reasoning is not None:
                item["reasoning_content"] = reasoning
        wire.append(item)
    return wire


class OpenAICompatibleClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        *,
        timeout: float = 120.0,
        max_retries: int = 2,
    ):
        from openai import OpenAI
        self._client = OpenAI(
            base_url=base_url,
            api_key=api_key,
            timeout=timeout,
            max_retries=max_retries,
        )
        self.model = model
        self.identity = ProviderIdentity(OPENAI_ADAPTER, model, OPENAI_ADAPTER)

    def render_request(
        self,
        messages: Sequence[ConversationMessage],
        tools: Sequence[ToolDefinition],
    ) -> Dict[str, Any]:
        """Render credential-free inspection input without opaque replay state."""
        semantic_messages = [
            ConversationMessage(message.role, message.blocks)
            for message in messages
        ]
        provider_tools = _openai_tools(tools)
        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": _openai_messages(semantic_messages),
        }
        if provider_tools:
            payload.update(tools=provider_tools, tool_choice="auto")
        return payload

    def complete(
        self,
        messages: Sequence[ConversationMessage],
        tools: Sequence[ToolDefinition],
    ) -> LLMResponse:
        provider_tools = _openai_tools(tools)
        kwargs: Dict[str, Any] = {
            "model": self.model,
            "messages": _openai_messages(messages),
        }
        if provider_tools:
            kwargs.update(tools=provider_tools, tool_choice="auto")
        started = time.perf_counter()
        try:
            response = self._client.chat.completions.create(**kwargs)
        except Exception as error:
            raise _normalized_error(error, self.identity) from error
        duration_ms = round((time.perf_counter() - started) * 1000, 1)
        model = _value(response, "model")
        identity = ProviderIdentity(
            OPENAI_ADAPTER,
            model if isinstance(model, str) and model else self.model,
            OPENAI_ADAPTER,
        )
        try:
            message = response.choices[0].message
            calls = tuple(
                ToolCallBlock(call.id, call.function.name, call.function.arguments)
                for call in (_value(message, "tool_calls") or [])
            )
            reasoning = _value(message, "reasoning_content")
            if reasoning is not None and not isinstance(reasoning, str):
                raise ValueError("reasoning_content must be a string")
            replay = None
            if calls and reasoning is not None:
                replay = AdapterReplayState(
                    OPENAI_ADAPTER,
                    ADAPTER_SCHEMA_VERSION,
                    {"reasoning_content": reasoning},
                )
            canonical = assistant_message(
                _value(message, "content"),
                tool_calls=calls,
                replay_state=replay,
                provenance=identity.provenance(),
            )
        except (AttributeError, IndexError, TypeError, ValueError) as error:
            raise ProviderError(
                ProviderErrorKind.PROTOCOL,
                "openai-compatible provider returned an invalid response",
                retryable=False,
                identity=identity,
            ) from error
        return LLMResponse(
            message=canonical,
            usage=_normalize_openai_usage(_value(response, "usage")),
            provider=identity,
            meta={
                "thinking_enabled": reasoning is not None,
                "duration_ms": duration_ms,
            },
        )


def _anthropic_tools(tools: Sequence[ToolDefinition]) -> List[Dict[str, Any]]:
    return [{
        "name": tool.name,
        "description": tool.description,
        "input_schema": tool.input_schema,
    } for tool in tools]


def _anthropic_replay_blocks(message: ConversationMessage) -> List[Dict[str, Any]]:
    replay = message.replay_state
    if replay is None or replay.adapter != ANTHROPIC_ADAPTER:
        return []
    if replay.schema_version != ADAPTER_SCHEMA_VERSION or not isinstance(replay.payload, dict):
        raise ValueError("anthropic replay state has an unsupported schema")
    blocks = replay.payload.get("blocks")
    if not isinstance(blocks, list) or not all(isinstance(block, dict) for block in blocks):
        raise ValueError("anthropic replay state blocks are invalid")
    return copy.deepcopy(blocks)


def _append_anthropic_message(
    wire: List[Dict[str, Any]], role: str, content: List[Dict[str, Any]],
) -> None:
    if not content:
        return
    if wire and wire[-1]["role"] == role:
        wire[-1]["content"].extend(content)
    else:
        wire.append({"role": role, "content": content})


def _anthropic_messages(
    messages: Sequence[ConversationMessage],
) -> Tuple[Optional[str], List[Dict[str, Any]]]:
    systems: List[str] = []
    wire: List[Dict[str, Any]] = []
    for message in messages:
        if message.role is MessageRole.SYSTEM:
            systems.append(message.text)
            continue
        if message.role is MessageRole.USER:
            _append_anthropic_message(wire, "user", [{"type": "text", "text": message.text}])
            continue
        if message.role is MessageRole.TOOL:
            content = [{
                "type": "tool_result",
                "tool_use_id": result.call_id,
                "content": result.content,
                "is_error": result.is_error,
            } for result in message.tool_results]
            _append_anthropic_message(wire, "user", content)
            continue
        content = _anthropic_replay_blocks(message)
        content.extend(
            {"type": "text", "text": block.text}
            for block in message.blocks if isinstance(block, TextBlock)
        )
        content.extend({
            "type": "tool_use",
            "id": call.id,
            "name": call.name,
            "input": json.loads(call.arguments),
        } for call in message.tool_calls)
        _append_anthropic_message(wire, "assistant", content)
    return ("\n\n".join(systems) if systems else None), wire


def _plain_anthropic_block(block: Any) -> Dict[str, Any]:
    if isinstance(block, dict):
        return copy.deepcopy(block)
    dump = getattr(block, "model_dump", None)
    if callable(dump):
        data = dump()
        if isinstance(data, dict):
            return data
    data = {"type": _value(block, "type")}
    for name in ("text", "id", "name", "input", "thinking", "signature", "data"):
        value = _value(block, name)
        if value is not None:
            data[name] = value
    return data


class AnthropicMessagesClient:
    def __init__(
        self,
        api_key: str,
        model: str,
        *,
        base_url: Optional[str] = None,
        timeout: float = 120.0,
        max_retries: int = 2,
        max_tokens: int = 8192,
    ):
        try:
            from anthropic import Anthropic
        except ImportError as error:
            raise RuntimeError(
                "Anthropic provider requires the optional dependency: pip install 'noval[anthropic]'"
            ) from error
        kwargs: Dict[str, Any] = {
            "api_key": api_key,
            "timeout": timeout,
            "max_retries": max_retries,
        }
        if base_url:
            kwargs["base_url"] = base_url
        self._client = Anthropic(**kwargs)
        self.model = model
        self.max_tokens = max_tokens
        self.identity = ProviderIdentity("anthropic", model, ANTHROPIC_ADAPTER)

    def render_request(
        self,
        messages: Sequence[ConversationMessage],
        tools: Sequence[ToolDefinition],
    ) -> Dict[str, Any]:
        """Render credential-free inspection input without thinking replay blocks."""
        semantic_messages = [
            ConversationMessage(message.role, message.blocks)
            for message in messages
        ]
        system, provider_messages = _anthropic_messages(semantic_messages)
        payload: Dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "messages": provider_messages,
        }
        if system:
            payload["system"] = system
        provider_tools = _anthropic_tools(tools)
        if provider_tools:
            payload["tools"] = provider_tools
        return payload

    def complete(
        self,
        messages: Sequence[ConversationMessage],
        tools: Sequence[ToolDefinition],
    ) -> LLMResponse:
        try:
            system, provider_messages = _anthropic_messages(messages)
        except ProviderError:
            raise
        except (ValueError, json.JSONDecodeError) as error:
            raise ProviderError(
                ProviderErrorKind.PROTOCOL,
                "canonical messages cannot be represented by the anthropic adapter",
                retryable=False,
                identity=self.identity,
            ) from error
        kwargs: Dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "messages": provider_messages,
        }
        if system:
            kwargs["system"] = system
        provider_tools = _anthropic_tools(tools)
        if provider_tools:
            kwargs["tools"] = provider_tools
        started = time.perf_counter()
        try:
            response = self._client.messages.create(**kwargs)
        except Exception as error:
            raise _normalized_error(error, self.identity) from error
        duration_ms = round((time.perf_counter() - started) * 1000, 1)
        model = _value(response, "model")
        identity = ProviderIdentity(
            "anthropic",
            model if isinstance(model, str) and model else self.model,
            ANTHROPIC_ADAPTER,
        )
        try:
            text_parts: List[str] = []
            calls: List[ToolCallBlock] = []
            replay_blocks: List[Dict[str, Any]] = []
            for raw_block in (_value(response, "content") or []):
                block = _plain_anthropic_block(raw_block)
                block_type = block.get("type")
                if block_type == "text" and isinstance(block.get("text"), str):
                    text_parts.append(block["text"])
                elif block_type == "tool_use":
                    calls.append(ToolCallBlock(
                        str(block.get("id") or ""),
                        str(block.get("name") or ""),
                        json.dumps(
                            block.get("input", {}),
                            ensure_ascii=False,
                            separators=(",", ":"),
                        ),
                    ))
                elif block_type in {"thinking", "redacted_thinking"}:
                    replay_blocks.append(block)
                else:
                    raise ValueError(f"unsupported content block: {block_type!r}")
            replay = (
                AdapterReplayState(
                    ANTHROPIC_ADAPTER,
                    ADAPTER_SCHEMA_VERSION,
                    {"blocks": replay_blocks},
                )
                if replay_blocks else None
            )
            canonical = assistant_message(
                "".join(text_parts) if text_parts else None,
                tool_calls=calls,
                replay_state=replay,
                provenance=identity.provenance(),
            )
        except (TypeError, ValueError) as error:
            raise ProviderError(
                ProviderErrorKind.PROTOCOL,
                "anthropic provider returned an invalid response",
                retryable=False,
                identity=identity,
            ) from error
        return LLMResponse(
            message=canonical,
            usage=_normalize_anthropic_usage(_value(response, "usage")),
            provider=identity,
            meta={
                "thinking_enabled": bool(replay_blocks),
                "duration_ms": duration_ms,
            },
        )


def create_provider_client(
    provider: str,
    *,
    api_key: str,
    model: str,
    base_url: str = "",
    anthropic_base_url: str = "",
    timeout: float = 120.0,
    max_retries: int = 2,
    anthropic_max_tokens: int = 8192,
) -> LLMClient:
    if provider == "anthropic":
        return AnthropicMessagesClient(
            api_key,
            model,
            base_url=anthropic_base_url or None,
            timeout=timeout,
            max_retries=max_retries,
            max_tokens=anthropic_max_tokens,
        )
    if provider == OPENAI_ADAPTER:
        return OpenAICompatibleClient(
            base_url,
            api_key,
            model,
            timeout=timeout,
            max_retries=max_retries,
        )
    raise ValueError(f"unsupported provider: {provider!r}")


class MockClient:
    def __init__(self, script: Sequence[LLMResponse]):
        self._script = list(script)
        self.seen_messages: List[List[ConversationMessage]] = []
        self.seen_tools: List[List[ToolDefinition]] = []

    def complete(
        self,
        messages: Sequence[ConversationMessage],
        tools: Sequence[ToolDefinition],
    ) -> LLMResponse:
        self.seen_messages.append(copy.deepcopy(list(messages)))
        self.seen_tools.append(copy.deepcopy(list(tools)))
        if not self._script:
            raise AssertionError("MockClient script exhausted while the loop kept requesting")
        return self._script.pop(0)


def mock_text(
    text: str,
    *,
    meta: Optional[Dict[str, Any]] = None,
    usage: Optional[TokenUsage] = None,
) -> LLMResponse:
    identity = ProviderIdentity("mock", "mock", "mock")
    return LLMResponse(
        message=assistant_message(text, provenance=identity.provenance()),
        usage=usage,
        provider=identity,
        meta=dict(meta or {}),
    )


def mock_tool_call(
    call_id: str,
    name: str,
    arguments_json: str,
    *,
    reasoning_content: Optional[str] = None,
    meta: Optional[Dict[str, Any]] = None,
    usage: Optional[TokenUsage] = None,
) -> LLMResponse:
    identity = ProviderIdentity(OPENAI_ADAPTER, "mock", OPENAI_ADAPTER)
    replay = None
    if reasoning_content is not None:
        replay = AdapterReplayState(
            OPENAI_ADAPTER,
            ADAPTER_SCHEMA_VERSION,
            {"reasoning_content": reasoning_content},
        )
    return LLMResponse(
        message=assistant_message(
            tool_calls=(ToolCallBlock(call_id, name, arguments_json),),
            replay_state=replay,
            provenance=identity.provenance(),
        ),
        usage=usage,
        provider=identity,
        meta=dict(meta or {}),
    )
