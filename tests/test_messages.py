import pytest

from noval.messages import (
    AdapterReplayState,
    ConversationMessage,
    MessageFormatError,
    MessageProvenance,
    MessageRole,
    ToolCallBlock,
    assistant_message,
    tool_result_message,
    user_message,
)


def test_canonical_message_round_trip_preserves_replay_and_provenance():
    message = assistant_message(
        "inspect",
        tool_calls=(ToolCallBlock("c1", "read_file", '{"path":"a"}'),),
        replay_state=AdapterReplayState("adapter-x", 2, {"opaque": [1, {"x": True}]}),
        provenance=MessageProvenance("provider-x", "model-x", "adapter-x", 2),
    )

    assert ConversationMessage.from_dict(message.to_dict()) == message
    assert message.text == "inspect"
    assert message.tool_calls[0].arguments == '{"path":"a"}'


def test_semantic_view_excludes_private_replay_and_provenance():
    message = assistant_message(
        tool_calls=(ToolCallBlock("c1", "read_file", "{}"),),
        replay_state=AdapterReplayState("private", 1, {"secret-state": "opaque"}),
        provenance=MessageProvenance("p", "m", "a", 1),
    )

    semantic = message.semantic_dict()

    assert "replay_state" not in semantic
    assert "provenance" not in semantic
    assert "secret-state" not in str(semantic)


def test_role_and_block_invariants_are_enforced():
    with pytest.raises(MessageFormatError, match="tool_result"):
        ConversationMessage(
            MessageRole.USER,
            tool_result_message("c1", "ok").blocks,
        )
    with pytest.raises(MessageFormatError, match="assistant"):
        ConversationMessage(
            MessageRole.USER,
            (ToolCallBlock("c1", "read", "{}"),),
        )
    with pytest.raises(MessageFormatError, match="replay_state"):
        ConversationMessage(
            MessageRole.USER,
            user_message("x").blocks,
            replay_state=AdapterReplayState("a", 1, {}),
        )


def test_unknown_block_type_is_rejected_explicitly():
    with pytest.raises(MessageFormatError, match="unknown message block"):
        ConversationMessage.from_dict({
            "role": "assistant",
            "blocks": [{"type": "image", "url": "x"}],
        })
