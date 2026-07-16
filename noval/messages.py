"""Provider-neutral conversation messages used by the Noval core."""
from __future__ import annotations

import copy
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Iterable, Optional, Tuple, Union


class MessageFormatError(ValueError):
    """A persisted or adapter-produced canonical message is invalid."""


class MessageRole(str, Enum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


@dataclass(frozen=True)
class TextBlock:
    text: str
    type: str = "text"

    def __post_init__(self) -> None:
        if self.type != "text" or not isinstance(self.text, str):
            raise MessageFormatError("text block requires string text")

    def to_dict(self) -> Dict[str, Any]:
        return {"type": self.type, "text": self.text}


@dataclass(frozen=True)
class ToolCallBlock:
    id: str
    name: str
    arguments: str
    type: str = "tool_call"

    def __post_init__(self) -> None:
        if self.type != "tool_call":
            raise MessageFormatError("tool_call block type is invalid")
        if not all(isinstance(value, str) for value in (self.id, self.name, self.arguments)):
            raise MessageFormatError("tool_call requires string id/name/arguments")
        if not self.id or not self.name:
            raise MessageFormatError("tool_call id and name must be non-empty")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": self.type,
            "id": self.id,
            "name": self.name,
            "arguments": self.arguments,
        }


@dataclass(frozen=True)
class ToolResultBlock:
    call_id: str
    content: str
    is_error: bool = False
    type: str = "tool_result"

    def __post_init__(self) -> None:
        if self.type != "tool_result":
            raise MessageFormatError("tool_result block type is invalid")
        if not isinstance(self.call_id, str) or not self.call_id:
            raise MessageFormatError("tool_result call_id must be non-empty")
        if not isinstance(self.content, str) or not isinstance(self.is_error, bool):
            raise MessageFormatError("tool_result content/is_error are invalid")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": self.type,
            "call_id": self.call_id,
            "content": self.content,
            "is_error": self.is_error,
        }


ContentBlock = Union[TextBlock, ToolCallBlock, ToolResultBlock]


@dataclass(frozen=True)
class AdapterReplayState:
    """Opaque Provider state retained only for its owning adapter."""

    adapter: str
    schema_version: int
    payload: Any

    def __post_init__(self) -> None:
        if not isinstance(self.adapter, str) or not self.adapter:
            raise MessageFormatError("replay_state.adapter must be a non-empty string")
        if (
            not isinstance(self.schema_version, int)
            or isinstance(self.schema_version, bool)
            or self.schema_version < 1
        ):
            raise MessageFormatError("replay_state.schema_version must be positive")
        object.__setattr__(self, "payload", copy.deepcopy(self.payload))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "adapter": self.adapter,
            "schema_version": self.schema_version,
            "payload": copy.deepcopy(self.payload),
        }

    @classmethod
    def from_dict(cls, data: Any) -> "AdapterReplayState":
        if not isinstance(data, dict):
            raise MessageFormatError("replay_state must be an object")
        adapter = data.get("adapter")
        version = data.get("schema_version")
        if not isinstance(adapter, str) or not adapter:
            raise MessageFormatError("replay_state.adapter must be a non-empty string")
        if not isinstance(version, int) or isinstance(version, bool) or version < 1:
            raise MessageFormatError("replay_state.schema_version must be a positive integer")
        return cls(adapter, version, copy.deepcopy(data.get("payload")))


@dataclass(frozen=True)
class MessageProvenance:
    provider: str
    model: str
    adapter: str
    adapter_schema_version: int

    def __post_init__(self) -> None:
        if not all(isinstance(value, str) and value for value in (
            self.provider, self.model, self.adapter,
        )):
            raise MessageFormatError("provenance identity fields must be non-empty strings")
        if (
            not isinstance(self.adapter_schema_version, int)
            or isinstance(self.adapter_schema_version, bool)
            or self.adapter_schema_version < 1
        ):
            raise MessageFormatError("provenance adapter schema version must be positive")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model,
            "adapter": self.adapter,
            "adapter_schema_version": self.adapter_schema_version,
        }

    @classmethod
    def from_dict(cls, data: Any) -> "MessageProvenance":
        if not isinstance(data, dict):
            raise MessageFormatError("provenance must be an object")
        values = (
            data.get("provider"), data.get("model"), data.get("adapter"),
            data.get("adapter_schema_version"),
        )
        if not all(isinstance(value, str) and value for value in values[:3]):
            raise MessageFormatError("provenance identity fields must be non-empty strings")
        if not isinstance(values[3], int) or isinstance(values[3], bool) or values[3] < 1:
            raise MessageFormatError("provenance adapter schema version must be positive")
        return cls(*values)


@dataclass(frozen=True)
class ConversationMessage:
    role: MessageRole
    blocks: Tuple[ContentBlock, ...]
    replay_state: Optional[AdapterReplayState] = None
    provenance: Optional[MessageProvenance] = None

    def __post_init__(self) -> None:
        if not isinstance(self.role, MessageRole):
            raise MessageFormatError("message role must be MessageRole")
        if not isinstance(self.blocks, tuple):
            raise MessageFormatError("message blocks must be an immutable tuple")
        if self.role is MessageRole.TOOL:
            if not self.blocks or not all(isinstance(block, ToolResultBlock) for block in self.blocks):
                raise MessageFormatError("tool messages may contain only tool_result blocks")
        elif any(isinstance(block, ToolResultBlock) for block in self.blocks):
            raise MessageFormatError("tool_result blocks require the tool role")
        if any(isinstance(block, ToolCallBlock) for block in self.blocks):
            if self.role is not MessageRole.ASSISTANT:
                raise MessageFormatError("tool_call blocks require the assistant role")
        if self.replay_state is not None and self.role is not MessageRole.ASSISTANT:
            raise MessageFormatError("replay_state is valid only on assistant messages")
        if self.provenance is not None and self.role is not MessageRole.ASSISTANT:
            raise MessageFormatError("provenance is valid only on assistant messages")

    @property
    def text(self) -> str:
        return "".join(block.text for block in self.blocks if isinstance(block, TextBlock))

    @property
    def tool_calls(self) -> Tuple[ToolCallBlock, ...]:
        return tuple(block for block in self.blocks if isinstance(block, ToolCallBlock))

    @property
    def tool_results(self) -> Tuple[ToolResultBlock, ...]:
        return tuple(block for block in self.blocks if isinstance(block, ToolResultBlock))

    def to_dict(self) -> Dict[str, Any]:
        data: Dict[str, Any] = {
            "role": self.role.value,
            "blocks": [block.to_dict() for block in self.blocks],
        }
        if self.replay_state is not None:
            data["replay_state"] = self.replay_state.to_dict()
        if self.provenance is not None:
            data["provenance"] = self.provenance.to_dict()
        return data

    def semantic_dict(self) -> Dict[str, Any]:
        """Return only cross-Provider meaning for compactors, judges, and logs."""
        return {
            "role": self.role.value,
            "blocks": [block.to_dict() for block in self.blocks],
        }

    @classmethod
    def from_dict(cls, data: Any) -> "ConversationMessage":
        if not isinstance(data, dict):
            raise MessageFormatError("message must be an object")
        try:
            role = MessageRole(data.get("role"))
        except (TypeError, ValueError) as error:
            raise MessageFormatError(f"unknown message role: {data.get('role')!r}") from error
        raw_blocks = data.get("blocks")
        if not isinstance(raw_blocks, list):
            raise MessageFormatError("message.blocks must be an array")
        blocks = tuple(_block_from_dict(block) for block in raw_blocks)
        replay = data.get("replay_state")
        provenance = data.get("provenance")
        return cls(
            role=role,
            blocks=blocks,
            replay_state=(AdapterReplayState.from_dict(replay) if replay is not None else None),
            provenance=(MessageProvenance.from_dict(provenance) if provenance is not None else None),
        )


def text_message(role: MessageRole, text: str) -> ConversationMessage:
    return ConversationMessage(role, (TextBlock(str(text)),))


def system_message(text: str) -> ConversationMessage:
    return text_message(MessageRole.SYSTEM, text)


def user_message(text: str) -> ConversationMessage:
    return text_message(MessageRole.USER, text)


def assistant_message(
    text: Optional[str] = None,
    *,
    tool_calls: Iterable[ToolCallBlock] = (),
    replay_state: Optional[AdapterReplayState] = None,
    provenance: Optional[MessageProvenance] = None,
) -> ConversationMessage:
    blocks: list[ContentBlock] = []
    if text is not None:
        blocks.append(TextBlock(text))
    blocks.extend(tool_calls)
    return ConversationMessage(
        MessageRole.ASSISTANT,
        tuple(blocks),
        replay_state=replay_state,
        provenance=provenance,
    )


def tool_result_message(call_id: str, content: str, *, is_error: bool = False) -> ConversationMessage:
    return ConversationMessage(
        MessageRole.TOOL,
        (ToolResultBlock(call_id=call_id, content=content, is_error=is_error),),
    )


def _block_from_dict(data: Any) -> ContentBlock:
    if not isinstance(data, dict):
        raise MessageFormatError("message block must be an object")
    block_type = data.get("type")
    if block_type == "text":
        text = data.get("text")
        if not isinstance(text, str):
            raise MessageFormatError("text block requires string text")
        return TextBlock(text)
    if block_type == "tool_call":
        values = (data.get("id"), data.get("name"), data.get("arguments"))
        if not all(isinstance(value, str) for value in values):
            raise MessageFormatError("tool_call block requires string id/name/arguments")
        if not values[0] or not values[1]:
            raise MessageFormatError("tool_call id and name must be non-empty")
        return ToolCallBlock(*values)
    if block_type == "tool_result":
        call_id = data.get("call_id")
        content = data.get("content")
        is_error = data.get("is_error", False)
        if not isinstance(call_id, str) or not call_id or not isinstance(content, str):
            raise MessageFormatError("tool_result block requires call_id and content")
        if not isinstance(is_error, bool):
            raise MessageFormatError("tool_result.is_error must be boolean")
        return ToolResultBlock(call_id, content, is_error)
    raise MessageFormatError(f"unknown message block type: {block_type!r}")
