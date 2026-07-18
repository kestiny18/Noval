import sys
from types import SimpleNamespace

import pytest

from noval.client import (
    ANTHROPIC_ADAPTER,
    OPENAI_ADAPTER,
    AnthropicMessagesClient,
    OpenAICompatibleClient,
    ProviderError,
    ProviderErrorKind,
    ProviderIdentity,
    ToolDefinition,
)
from noval.messages import (
    AdapterReplayState, ToolCallBlock, assistant_message, tool_result_message, user_message,
)
from noval.session import JsonlSessionStore


class FakeCreate:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        result = self.responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def openai_client(responses):
    create = FakeCreate(responses)
    client = OpenAICompatibleClient.__new__(OpenAICompatibleClient)
    client._client = SimpleNamespace(chat=SimpleNamespace(completions=create))
    client.model = "deepseek-v4-pro"
    client.identity = ProviderIdentity(OPENAI_ADAPTER, client.model, OPENAI_ADAPTER)
    return client, create


def anthropic_client(responses):
    create = FakeCreate(responses)
    client = AnthropicMessagesClient.__new__(AnthropicMessagesClient)
    client._client = SimpleNamespace(messages=create)
    client.model = "claude-test"
    client.max_tokens = 4096
    client.identity = ProviderIdentity("anthropic", client.model, ANTHROPIC_ADAPTER)
    return client, create


def openai_response(*, content=None, reasoning=None, calls=(), usage=True, model="provider-model"):
    message = SimpleNamespace(
        content=content,
        reasoning_content=reasoning,
        tool_calls=list(calls),
        annotations=["must not leak"],
    )
    raw_usage = None
    if usage:
        raw_usage = SimpleNamespace(
            prompt_tokens=100,
            completion_tokens=20,
            total_tokens=120,
            prompt_cache_hit_tokens=60,
            prompt_cache_miss_tokens=40,
            completion_tokens_details=SimpleNamespace(reasoning_tokens=12),
        )
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message)], usage=raw_usage, model=model,
    )


def openai_call(call_id="call-1", name="read_file", arguments='{"path":"a"}'):
    return SimpleNamespace(
        id=call_id,
        function=SimpleNamespace(name=name, arguments=arguments),
    )


def anthropic_response(content, *, model="claude-provider"):
    return SimpleNamespace(
        content=content,
        model=model,
        usage=SimpleNamespace(
            input_tokens=80,
            output_tokens=15,
            cache_read_input_tokens=50,
            cache_creation_input_tokens=30,
        ),
    )


def test_response_has_one_canonical_message_and_no_raw_sdk_object():
    client, _ = openai_client([openai_response(content="answer")])

    response = client.complete([user_message("question")], [])

    assert response.message.text == "answer"
    assert response.message.provenance.model == "provider-model"
    assert response.provider.model == "provider-model"
    assert not hasattr(response, "raw")
    assert not hasattr(response, "content")


def test_openai_client_sets_timeout_and_retries(monkeypatch):
    seen = {}

    class FakeOpenAI:
        def __init__(self, **kwargs):
            seen.update(kwargs)

    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(OpenAI=FakeOpenAI))
    client = OpenAICompatibleClient(
        "https://example.invalid", "key", "model-x", timeout=45.5, max_retries=0,
    )

    assert client.model == "model-x"
    assert seen == {
        "base_url": "https://example.invalid",
        "api_key": "key",
        "timeout": 45.5,
        "max_retries": 0,
    }


def test_plain_reasoning_is_not_retained_but_usage_is_normalized():
    client, _ = openai_client([
        openai_response(content="answer", reasoning="private final reasoning"),
    ])

    response = client.complete([user_message("question")], [])

    assert response.message.replay_state is None
    assert response.meta["thinking_enabled"] is True
    assert response.usage.prompt_tokens == 100
    assert response.usage.reasoning_tokens == 12


def test_openai_tool_reasoning_round_trips_as_adapter_owned_state():
    client, create = openai_client([
        openai_response(calls=[openai_call()], reasoning="need the file"),
        openai_response(content="done"),
    ])
    first = client.complete([user_message("read")], [])
    assert first.message.replay_state.adapter == OPENAI_ADAPTER
    assert first.message.replay_state.payload == {"reasoning_content": "need the file"}

    history = [
        user_message("read"),
        first.message,
        tool_result_message("call-1", "A"),
    ]
    client.complete(history, [])

    replayed = create.calls[1]["messages"][1]
    assert replayed["reasoning_content"] == "need the file"
    assert replayed["tool_calls"][0]["function"]["name"] == "read_file"


def test_anthropic_thinking_round_trips_without_entering_semantic_view():
    thinking = {"type": "thinking", "thinking": "private", "signature": "sig"}
    client, create = anthropic_client([
        anthropic_response([
            thinking,
            {"type": "tool_use", "id": "call-1", "name": "read_file", "input": {"path": "a"}},
        ]),
        anthropic_response([{"type": "text", "text": "done"}]),
    ])
    first = client.complete([user_message("read")], [])

    assert first.message.replay_state.payload == {"blocks": [thinking]}
    assert "replay_state" not in first.message.semantic_dict()
    client.complete([
        user_message("read"), first.message, tool_result_message("call-1", "A"),
    ], [])

    replayed = create.calls[1]["messages"][1]["content"]
    assert replayed[0] == thinking
    assert replayed[1]["type"] == "tool_use"


def test_two_adapters_produce_equivalent_canonical_tool_transcript():
    openai, _ = openai_client([
        openai_response(content="inspect", calls=[openai_call()]),
    ])
    anthropic, _ = anthropic_client([
        anthropic_response([
            {"type": "text", "text": "inspect"},
            {"type": "tool_use", "id": "call-1", "name": "read_file", "input": {"path": "a"}},
        ]),
    ])

    left = openai.complete([user_message("read")], []).message.semantic_dict()
    right = anthropic.complete([user_message("read")], []).message.semantic_dict()

    assert left == right


def test_anthropic_encodes_tool_errors_and_tool_definitions():
    client, create = anthropic_client([
        anthropic_response([{"type": "text", "text": "handled"}]),
    ])
    tool = ToolDefinition("read_file", "read", {"type": "object"})

    client.complete([tool_result_message("call-1", "failed", is_error=True)], [tool])

    assert create.calls[0]["messages"][0]["content"][0]["is_error"] is True
    assert create.calls[0]["tools"] == [{
        "name": "read_file", "description": "read", "input_schema": {"type": "object"},
    }]


def test_provider_errors_are_safe_normalized_and_retryable():
    class RateLimitError(Exception):
        status_code = 429

    client, _ = openai_client([RateLimitError("secret provider body")])

    with pytest.raises(ProviderError) as caught:
        client.complete([user_message("question")], [])

    assert caught.value.kind is ProviderErrorKind.RATE_LIMIT
    assert caught.value.retryable is True
    assert "secret provider body" not in caught.value.safe_message


def test_foreign_replay_state_is_ignored_by_other_adapter():
    client, create = openai_client([openai_response(content="ok")])
    foreign = assistant_message(
        tool_calls=(ToolCallBlock("c1", "read_file", "{}"),),
        replay_state=AdapterReplayState(
            ANTHROPIC_ADAPTER, 1, {"blocks": [{"type": "thinking", "thinking": "x"}]},
        ),
    )

    client.complete([user_message("read"), foreign, tool_result_message("c1", "A")], [])

    replayed = create.calls[0]["messages"][1]
    assert "reasoning_content" not in replayed


def test_anthropic_rejects_unrepresentable_raw_tool_arguments():
    client, _ = anthropic_client([])
    invalid = assistant_message(tool_calls=(
        ToolCallBlock("c1", "read_file", "not-json"),
    ))

    with pytest.raises(ProviderError) as caught:
        client.complete([invalid], [])

    assert caught.value.kind is ProviderErrorKind.PROTOCOL
    assert caught.value.retryable is False


def test_openai_replay_survives_session_v2_recovery(tmp_path):
    client, _ = openai_client([
        openai_response(calls=[openai_call()], reasoning="exact deepseek state"),
    ])
    first = client.complete([user_message("read")], []).message
    workdir = tmp_path / "work"
    workdir.mkdir()
    store = JsonlSessionStore.create(tmp_path / "sessions", workdir, "model")
    for message in (user_message("read"), first, tool_result_message("call-1", "A")):
        store.append(message)
    store.close()

    recovered = JsonlSessionStore.open(
        store.base_dir, store.workdir, store.session_id, "model",
    ).load()
    resumed, create = openai_client([openai_response(content="done")])
    resumed.complete(recovered, [])

    assert create.calls[0]["messages"][1]["reasoning_content"] == "exact deepseek state"


def test_anthropic_replay_survives_session_v2_recovery(tmp_path):
    thinking = {"type": "thinking", "thinking": "private", "signature": "exact-sig"}
    client, _ = anthropic_client([
        anthropic_response([
            thinking,
            {"type": "tool_use", "id": "call-1", "name": "read_file", "input": {}},
        ]),
    ])
    first = client.complete([user_message("read")], []).message
    workdir = tmp_path / "work"
    workdir.mkdir()
    store = JsonlSessionStore.create(tmp_path / "sessions", workdir, "model")
    for message in (user_message("read"), first, tool_result_message("call-1", "A")):
        store.append(message)
    store.close()

    recovered = JsonlSessionStore.open(
        store.base_dir, store.workdir, store.session_id, "model",
    ).load()
    resumed, create = anthropic_client([
        anthropic_response([{"type": "text", "text": "done"}]),
    ])
    resumed.complete(recovered, [])

    assert create.calls[0]["messages"][1]["content"][0] == thinking
